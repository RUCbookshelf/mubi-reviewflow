"""2.1.3 actor isolation, relationships, undo and export on disposable data."""
import os
import sys
import json
import time
import uuid
import hashlib
import sqlite3
import io as _io
import zipfile
import unittest
import tempfile
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_evidence_test_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"
from fastapi.testclient import TestClient
from custom_backend.main import app
from custom_backend.report_evidence_api import DEFAULT, connect
from coscreen import db, research_trace, task_collaboration, review_workflow
from coscreen.models import Article


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for i in range(3):
            r = cls.client.post('/api/auth/register', json={'username':f'evidence{i}@example.test','password':'Evidence-Test-2026'}).json()
            cls.users.append({'user_id':r['user']['id'],'username':r['user']['username'],'token':r['token']})
        from coscreen import tasks as tasks_mod
        # 门禁墙钟（类级模板库）：原 setUp 每用例走一遍「POST + 全量首建
        # 迁移链 + 种入 R1/R2」（实测 ~196ms/用例）。类 setUp 建一个真实
        # API 模板任务并把库文件字节快照下来；每用例 POST 新任务后仅把
        # 模板库复制过去（~3ms）——首建链只跑一次，库内容与逐用例自建
        # 逐字节同源（同一条产品路径产出），用例间隔离不变（各自独立副本）。
        cls._slug = tasks_mod.slugify_screener(cls.users[0]['username'])
        template_task = cls.client.post('/api/tasks', json={'name': 'evidence-template'},
                                        headers={'Authorization': 'Bearer ' + cls.users[0]['token']}).json()['task_id']
        ctx0 = app.state.review_workflow.context(template_task, cls.users[0])
        db.upsert_articles(ctx0.owner_db, [Article(zotero_key='R1', title='First', doi='10.test/1', raw={'source_file': '01-Wiley.ris'}), Article(zotero_key='R2', title='Second', doi='10.test/2')], [])
        cls._template_db_bytes = Path(ctx0.owner_db).read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def auth(self, i=0):
        return {'Authorization':'Bearer '+self.users[i]['token']}

    def setUp(self):
        self.task = self.client.post('/api/tasks',json={'name':self._testMethodName},headers=self.auth()).json()['task_id']
        from coscreen import tasks as tasks_mod
        # main.DATA_DIR 是任务真实落盘根（全套 pytest 下环境变量会被后续
        # 模块的 fixture 改写，不能用 os.environ）
        from custom_backend.main import DATA_DIR as _APP_DATA_DIR
        info = tasks_mod.find_task(self.task, _APP_DATA_DIR)
        (info.dir / (self._slug + '.db')).write_bytes(self._template_db_bytes)
        self.ctx = app.state.review_workflow.context(self.task,self.users[0])
        self.base = f'/api/tasks/{self.task}/report-evidence'

    def state(self):
        return json.loads(json.dumps(DEFAULT))

    def save(self,state,revision=0,actor=0,operation=None):
        return self.client.put(self.base,json={'expected_revision':revision,'operation_id':operation or str(uuid.uuid4()),'state':state,'label':'Save evidence'},headers=self.auth(actor))

    def link(self,state):
        state['links']=[{'id':'L1','a':'R1','b':'R2','type':'study','same':True,'evidence':'Report explicitly states shared interviews','locator':'p.4','status':'mine'}]
        return state

    def test_save_reload_and_version_conflict(self):
        s=self.link(self.state());r=self.save(s)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.client.get(self.base,headers=self.auth()).json()['state']['links'],s['links'])
        self.assertEqual(self.save(s,0).status_code,409)
        r=self.client.post(self.base+'/undo',json={'expected_revision':1},headers=self.auth())
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['state']['links'],[])

    def test_idempotency_and_validation(self):
        op=str(uuid.uuid4());s=self.link(self.state());self.assertEqual(self.save(s,operation=op).status_code,200)
        self.assertEqual(self.save(s,operation=op).json()['revision'],1)
        s['links'][0]['evidence']='changed';self.assertEqual(self.save(s,operation=op).status_code,409)
        s['links'][0]['status']='agreed';self.assertEqual(self.save(s,1).status_code,422)
        s['links'][0]['status']='mine';s['links'][0]['a']='OTHER';self.assertEqual(self.save(s,1).status_code,422)

    def test_confirm_unlink_and_undo_preserves_reports(self):
        self.save(self.link(self.state()))
        body={'expected_revision':0,'actor_id':str(self.users[0]['user_id']),'link_id':'L1','action':'confirm'}
        r=self.client.post(self.base+'/relations',json=body,headers=self.auth());self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['confirmed'][0]['status'],'agreed')
        body.update(expected_revision=1,action='unlink');r=self.client.post(self.base+'/relations',json=body,headers=self.auth())
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['confirmed'],[])
        r=self.client.post(self.base+'/relations/undo',json={'expected_revision':2},headers=self.auth());self.assertEqual(len(r.json()['confirmed']),1)
        self.assertEqual(len(db.list_articles(self.ctx.owner_db)),2)

    def test_team_isolation_and_paused_adjudication(self):
        task_collaboration.grant_role(self.ctx.collaboration_db,actor_user_id=self.users[0]['user_id'],user_id=self.users[1]['user_id'],role='coder',task_id=self.task)
        self.save(self.link(self.state()),actor=1)
        owner=self.client.get(self.base,headers=self.auth()).json();self.assertNotIn('proposals',owner);self.assertEqual(owner['state']['links'],[])
        body={'expected_revision':0,'actor_id':str(self.users[1]['user_id']),'link_id':'L1','action':'confirm'}
        self.assertEqual(self.client.post(self.base+'/relations',json=body,headers=self.auth()).status_code,403)
        with closing(review_workflow.connect(self.ctx.owner_db)) as c,c:
            row=c.execute('SELECT stages_json FROM workflow_state').fetchone();stages=json.loads(row[0])
            for s in stages.values():s['mode']='paused'
            c.execute('UPDATE workflow_state SET stages_json=?',(json.dumps(stages),))
        owner=self.client.get(self.base,headers=self.auth()).json();self.assertEqual(len(owner['proposals']),1)
        self.assertEqual(self.client.post(self.base+'/relations',json=body,headers=self.auth()).status_code,200)
        self.assertEqual(self.client.get(self.base,headers=self.auth(2)).status_code,403)
        self.assertEqual(self.client.post(self.base+'/relations',json={**body,'expected_revision':1},headers=self.auth(1)).status_code,403)

    def test_large_workspace_trace_and_shared_read(self):
        state=self.state();state['drafts']=[{'id':str(i),'key':'R1','finding':'Finding','quote':'x'*12000} for i in range(100)]
        r=self.save(state);self.assertEqual(r.status_code,200,r.text)
        r=self.client.get(f'/api/tasks/{self.task}/trace/events?limit=50',headers=self.auth());self.assertEqual(r.status_code,200,r.text)
        event=next(e for e in r.json()['events'] if e['action']=='report_evidence_changed');self.assertEqual(event['subject_id'],'R1');self.assertIn('workspace_sha256',event['after'])
        self.assertEqual(self.client.get(f'/api/tasks/{self.task}/trace/events?q=First',headers=self.auth()).status_code,200)

    def test_personal_ml_profile_interval_and_legacy_minimum(self):
        task_collaboration.grant_role(self.ctx.collaboration_db,actor_user_id=self.users[0]['user_id'],user_id=self.users[1]['user_id'],role='coder',task_id=self.task)
        url=f'/api/tasks/{self.task}/al/'
        r=self.client.put(url+'settings',json={'reorder_interval':7,'min_labeled':999},headers=self.auth(1));self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['min_labeled'],5)
        self.assertEqual(self.client.get(url+'settings',headers=self.auth()).json()['reorder_interval'],20)
        self.assertEqual(self.client.get(url+'settings',headers=self.auth(1)).json()['reorder_interval'],7)
        self.assertEqual(self.client.put(url+'settings?screener='+self.users[0]['username'],json={'reorder_interval':1},headers=self.auth(1)).status_code,403)
        self.assertEqual(self.client.put(url+'settings',json={'reorder_interval':1.5},headers=self.auth()).status_code,422)
        self.assertEqual(self.client.put(url+'profile',json={'profile':'cjk'},headers=self.auth(1)).status_code,200)
        self.assertEqual(self.client.get(url+'profiles',headers=self.auth(1)).json()['al_profile'],'cjk')
        self.assertIsNone(self.client.get(url+'profiles',headers=self.auth()).json()['al_profile'])
        self.assertEqual(self.client.get(url+'shared-ranking',headers=self.auth()).status_code,409)
        actor_ctx=app.state.review_workflow.context(self.task,self.users[1])
        db.upsert_articles(actor_ctx.actor_db,[Article(zotero_key='R1',title='First'),Article(zotero_key='R2',title='Second')],[])
        from coscreen.db import save_decision
        save_decision(actor_ctx.actor_db,'R1','include')
        self.assertEqual(self.client.get(url+'rank',headers=self.auth()).json()['n_labeled'],0)
        self.assertEqual(self.client.get(url+'rank',headers=self.auth(1)).json()['n_labeled'],1)

    def test_evidence_export_methods_appraisal_and_trace(self):
        s=self.state();s['review']='mixed';s['method']='thematic';s['drafts']=[{'id':'F1','key':'R1','finding':'Meaning','quote':'=not a formula','locator':'p.4','context':'Teachers','theme':'Agency','interpretation':'Interpretation','credibility':'credible'}]
        s['interpretations']={'thematic':{'coding':'Code','descriptive':'Theme','analytic':'Reason','integration':'Memo'}}
        s['appraisals']={'casp|R1':{'items':{'0':{'judgment':'yes','reason':'Evidence','locator':'p.4'}},'summary':'Partial'}}
        s['confidence']={'thematic':{'finding':'Synthesis','overall':'moderate','reason':'Manual rationale'}}
        self.assertEqual(self.save(s).status_code,200)
        r=self.client.get(self.base+'/export',headers=self.auth());self.assertIn('01-Wiley.ris',r.text);self.assertIn("'=not a formula",r.text)
        r=self.client.get(self.base+'/export?format=json',headers=self.auth());self.assertEqual(r.json()['state'],s)
        r=self.client.get(f'/api/tasks/{self.task}/trace/events?limit=50',headers=self.auth());self.assertEqual(r.status_code,200,r.text);self.assertTrue(any(e['action']=='report_evidence_changed' for e in r.json()['events']))

    def trace_changed_events(self,actor=0):
        r=self.client.get(f'/api/tasks/{self.task}/trace/events?limit=200',headers=self.auth(actor))
        self.assertEqual(r.status_code,200,r.text)
        return [e for e in r.json()['events'] if e['action']=='report_evidence_changed']

    def test_shared_link_id_collision_unlinks_only_target(self):
        # 回归（P1 证据API-2）：两成员用相同 link id 提交不同关系并被先后确认后，unlink 只解除目标关系。
        # 共享 id 与提案保持一致（不改写、不注入提案人 user id），unlink 按 (id, actor_id) 定位目标。
        task_collaboration.grant_role(self.ctx.collaboration_db,actor_user_id=self.users[0]['user_id'],user_id=self.users[1]['user_id'],role='coder',task_id=self.task)
        db.upsert_articles(self.ctx.owner_db,[Article(zotero_key='R3',title='Third')],[])
        s0=self.state();s0['links']=[{'id':'L1','a':'R1','b':'R3','type':'study','same':False,'evidence':'Owner pair','locator':'p.1','status':'mine'}]
        self.assertEqual(self.save(s0).status_code,200)
        s1=self.state();s1['links']=[{'id':'L1','a':'R1','b':'R2','type':'sample','same':True,'evidence':'Coder pair','locator':'p.2','status':'mine'}]
        self.assertEqual(self.save(s1,actor=1).status_code,200)
        with closing(review_workflow.connect(self.ctx.owner_db)) as c,c:
            row=c.execute('SELECT stages_json FROM workflow_state').fetchone();stages=json.loads(row[0])
            for stage in stages.values():stage['mode']='paused'
            c.execute('UPDATE workflow_state SET stages_json=?',(json.dumps(stages),))
        owner=str(self.users[0]['user_id']);coder=str(self.users[1]['user_id'])
        body={'expected_revision':0,'actor_id':coder,'link_id':'L1','action':'confirm'}
        base=len(self.trace_changed_events())  # 此前两次个人保存的轨迹事件数
        self.assertEqual(self.client.post(self.base+'/relations',json=body,headers=self.auth()).status_code,200)
        confirm1=self.trace_changed_events()[base:]  # 跨成员同 id：第一次确认（coder 提案）留痕
        self.assertEqual(sorted(e['subject_id'] for e in confirm1),['R1','R2'])
        for e in confirm1:
            links=e['after']['changes']['links']
            self.assertEqual(len(links),1);self.assertEqual(links[0]['actor_id'],coder)
            self.assertEqual(links[0]['status'],'agreed')
            blob=json.dumps(e,ensure_ascii=False)
            self.assertNotIn('Coder pair',blob)  # 提案人私有文本按哈希摘要脱敏
            self.assertIn(hashlib.sha256('Coder pair'.encode()).hexdigest(),blob)
        body.update(expected_revision=1,actor_id=owner)
        r=self.client.post(self.base+'/relations',json=body,headers=self.auth());self.assertEqual(r.status_code,200,r.text)
        confirm2=self.trace_changed_events()[base+len(confirm1):]  # 第二次确认（owner 同 id 提案）留痕
        self.assertEqual(sorted(e['subject_id'] for e in confirm2),['R1','R3'])
        for e in confirm2:
            links=e['after']['changes']['links']
            self.assertEqual(len(links),1);self.assertEqual(links[0]['actor_id'],owner)
            self.assertIn('Owner pair',json.dumps(e,ensure_ascii=False))  # 本人提案不脱敏
        confirmed=r.json()['confirmed']
        self.assertEqual(len(confirmed),2)
        self.assertEqual([l['id'] for l in confirmed],['L1','L1'])  # id 与提案一致，未注入提案人 user id
        dropped=next(l for l in confirmed if l['actor_id']==coder)
        kept=next(l for l in confirmed if l['actor_id']==owner)
        self.assertEqual({dropped['a'],dropped['b']},{'R1','R2'});self.assertEqual({kept['a'],kept['b']},{'R1','R3'})
        body.update(expected_revision=2,link_id=dropped['id'],actor_id=dropped['actor_id'],action='unlink')
        r=self.client.post(self.base+'/relations',json=body,headers=self.auth());self.assertEqual(r.status_code,200,r.text)
        unlink_events=self.trace_changed_events()[base+len(confirm1)+len(confirm2):]  # 解除留痕且只归因目标
        self.assertEqual(sorted(e['subject_id'] for e in unlink_events),['R1','R2'])
        for e in unlink_events:
            removed=e['before']['changes']['links']
            self.assertEqual(len(removed),1);self.assertEqual(removed[0]['actor_id'],coder)
            self.assertEqual(e['after']['changes']['links'],[])
        rest=r.json()['confirmed']
        self.assertEqual(len(rest),1)  # 只解除目标关系，另一条保留
        self.assertEqual(rest[0]['id'],'L1');self.assertEqual(rest[0]['actor_id'],owner)
        self.assertEqual({rest[0]['a'],rest[0]['b']},{'R1','R3'})

    def _preset_shared_links(self,links):
        # 直接预置 2.1.3 形态的已确认共享关系（不经 confirm），覆盖已落库的重复 shared links。
        state=json.loads(json.dumps(DEFAULT));state['links']=links
        with closing(connect(self.ctx.owner_db)) as c:
            c.execute("INSERT INTO report_evidence_state VALUES('*',0,?,NULL,NULL)",(json.dumps(state),))
            c.commit()

    def test_preset_duplicate_shared_links_unlink_removes_only_target(self):
        # 回归（评审）：已落库的跨成员同 id 共享关系不经迁移/改写，unlink 也只删目标一条。
        owner=str(self.users[0]['user_id']);coder=str(self.users[1]['user_id'])
        def agreed(a,b,actor,evidence):
            return {'id':'L1','a':a,'b':b,'type':'study','same':False,'evidence':evidence,'locator':'p.1','status':'agreed','actor_id':actor}
        self._preset_shared_links([agreed('R1','R2',coder,'Coder preset pair'),agreed('R1','R3',owner,'Owner preset pair')])
        body={'expected_revision':0,'actor_id':coder,'link_id':'L1','action':'unlink'}
        r=self.client.post(self.base+'/relations',json=body,headers=self.auth());self.assertEqual(r.status_code,200,r.text)
        rest=r.json()['confirmed']
        self.assertEqual(len(rest),1)
        self.assertEqual(rest[0]['actor_id'],owner)
        self.assertEqual({rest[0]['a'],rest[0]['b']},{'R1','R3'})

    def test_preset_same_actor_duplicate_links_unlink_requires_pair(self):
        # 同成员同 id 重复（仅直连 API 可构造）：不带 a/b 时明确 409，携带关系对后精确删除一条。
        owner=str(self.users[0]['user_id'])
        def agreed(a,b,evidence):
            return {'id':'L1','a':a,'b':b,'type':'study','same':False,'evidence':evidence,'locator':'p.1','status':'agreed','actor_id':owner}
        self._preset_shared_links([agreed('R1','R2','First pair'),agreed('R1','R3','Second pair')])
        body={'expected_revision':0,'actor_id':owner,'link_id':'L1','action':'unlink'}
        r=self.client.post(self.base+'/relations',json=body,headers=self.auth());self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(len(self.client.get(self.base,headers=self.auth()).json()['confirmed']),2)
        body.update(a='R1',b='R2')
        r=self.client.post(self.base+'/relations',json=body,headers=self.auth());self.assertEqual(r.status_code,200,r.text)
        rest=r.json()['confirmed']
        self.assertEqual(len(rest),1);self.assertEqual({rest[0]['a'],rest[0]['b']},{'R1','R3'})

    def test_trace_increment_entries_and_save_budget(self):
        # 回归#1 写放大：每事件只记录该主体的增量条目（不再内嵌整份工作区），单次保存轨迹总量受约2MB预算封顶。
        keys=[f'B{i}' for i in range(6)]
        db.upsert_articles(self.ctx.owner_db,[Article(zotero_key=k,title=f'Bulk {k}') for k in keys],[])
        state=self.state()
        state['drafts']=[{'id':f'{k}-{j}','key':k,'finding':'a'*20000} for k in keys for j in range(10)]
        self.assertEqual(self.save(state).status_code,200)
        first=self.trace_changed_events()
        self.assertEqual(sorted(e['subject_id'] for e in first),sorted(keys))
        for e in first:
            self.assertNotIn('workspace',e['after'])
            entries=e['after']['changes']['drafts']
            self.assertTrue(entries);self.assertTrue(all(d['key']==e['subject_id'] for d in entries))
        state['drafts']=[{'id':f'{k}-{j}','key':k,'finding':'b'*20000} for k in keys for j in range(10)]
        self.assertEqual(self.save(state,revision=1).status_code,200)
        events=self.trace_changed_events();last=events[len(first):]
        self.assertEqual(len(last),6)
        self.assertTrue(any('workspace_sha256' in e['after'] for e in last))
        total=sum(len(json.dumps(e['before'],ensure_ascii=False))+len(json.dumps(e['after'],ensure_ascii=False)) for e in last)
        self.assertLess(total,2_100_000)

    def test_trace_subject_truncation_marker(self):
        # 回归#2 静默截断：一次保存影响超过100个主体时，截断标记作为 report_evidence_changed
        # 的载荷字段显式留痕（不新增动作键），不再静默丢弃。
        keys=[f'T{i:03d}' for i in range(105)]
        db.upsert_articles(self.ctx.owner_db,[Article(zotero_key=k,title=f'Trunc {k}') for k in keys],[])
        state=self.state()
        state['drafts']=[{'id':f'd{i}','key':k,'finding':f'Finding {k}'} for i,k in enumerate(keys)]
        self.assertEqual(self.save(state).status_code,200)
        r=self.client.get(f'/api/tasks/{self.task}/trace/events?limit=200&action=report_evidence_changed',headers=self.auth())
        self.assertEqual(r.status_code,200,r.text);changed=r.json()['events']
        self.assertFalse(r.json()['has_more']);self.assertEqual(len(changed),101)  # 100 个主体事件 + 1 条截断标记
        self.assertFalse(any(e['action']!='report_evidence_changed' for e in changed))
        marker=next(e for e in changed if e['subject_id']=='report-evidence' and 'truncated_subjects' in e['after'])
        self.assertEqual(marker['after']['total_subjects'],105)
        self.assertEqual(marker['after']['truncated_subjects'],5)
        self.assertEqual(marker['stage'],'coding')
        self.assertNotIn('changes',marker['after'])  # 纯标记载荷，不携带条目

    def test_undo_trace_matches_save_stage(self):
        # 回归#3 撤销阶段：轨迹阶段收窄为 screen 时，保存与撤销都记录在同一阶段，撤销不再被静默过滤。
        actor=self.users[0]['user_id']
        with closing(review_workflow.connect(self.ctx.owner_db)) as c,c:
            current=research_trace.get_settings(c,self.task,actor)
            research_trace.update_settings(c,research_trace.TraceContext(self.task,actor,source='api'),
                                           stages=['screen'],status='recording',operation_id='test-narrow-screen',
                                           expected_version=current['version'])
        body={'expected_revision':0,'operation_id':str(uuid.uuid4()),'state':self.link(self.state()),'label':'Screen save','stage':'screen'}
        self.assertEqual(self.client.put(self.base,json=body,headers=self.auth()).status_code,200)
        self.assertEqual(self.client.post(self.base+'/undo',json={'expected_revision':1},headers=self.auth()).status_code,200)
        changed=self.trace_changed_events()
        self.assertEqual(len(changed),4)
        self.assertTrue(all(e['stage']=='screen' for e in changed))

    def test_shared_adjudication_and_undo_trace_stages(self):
        # 回归（G17）：共享裁决 /relations 的 stage 随请求体记录；共享撤销
        # /relations/undo 复用对应裁决动作随共享撤销栈保存的 previous_stage，
        # 裁决与撤销事件阶段一致（收窄 screen 时不再被静默过滤）。
        actor=self.users[0]['user_id']
        with closing(review_workflow.connect(self.ctx.owner_db)) as c,c:
            current=research_trace.get_settings(c,self.task,actor)
            research_trace.update_settings(c,research_trace.TraceContext(self.task,actor,source='api'),
                                           stages=['screen'],status='recording',operation_id='test-shared-stage',
                                           expected_version=current['version'])
        save_body={'expected_revision':0,'operation_id':str(uuid.uuid4()),'state':self.link(self.state()),'label':'Screen save','stage':'screen'}
        self.assertEqual(self.client.put(self.base,json=save_body,headers=self.auth()).status_code,200)
        confirm={'expected_revision':0,'actor_id':str(actor),'link_id':'L1','action':'confirm','stage':'screen'}
        r=self.client.post(self.base+'/relations',json=confirm,headers=self.auth());self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.client.post(self.base+'/relations/undo',json={'expected_revision':1},headers=self.auth()).status_code,200)
        changed=self.trace_changed_events()
        labels=[e['after'].get('value') for e in changed]
        self.assertEqual(labels.count('确认报告关系'),2)  # 裁决按受影响主体 R1/R2 各一条
        self.assertEqual(labels.count('撤销报告关系裁决'),2)  # 共享撤销同样逐主体留痕
        self.assertTrue(all(e['stage']=='screen' for e in changed),[e['stage'] for e in changed])

    def test_confirm_trace_redacts_other_member_private_text(self):
        # 回归#4 隐私：裁决确认/解除/撤销不把提案人私有依据与位置写入裁决人轨迹，公开导出只含哈希摘要。
        task_collaboration.grant_role(self.ctx.collaboration_db,actor_user_id=self.users[0]['user_id'],user_id=self.users[1]['user_id'],role='coder',task_id=self.task)
        secret='Coder-only field notes quote';proposer=str(self.users[1]['user_id'])
        s=self.state();s['links']=[{'id':'L1','a':'R1','b':'R2','type':'study','same':True,'evidence':secret,'locator':'p.4','status':'mine'}]
        self.assertEqual(self.save(s,actor=1).status_code,200)
        with closing(review_workflow.connect(self.ctx.owner_db)) as c,c:
            row=c.execute('SELECT stages_json FROM workflow_state').fetchone();stages=json.loads(row[0])
            for stage in stages.values():stage['mode']='paused'
            c.execute('UPDATE workflow_state SET stages_json=?',(json.dumps(stages),))
        body={'expected_revision':0,'actor_id':proposer,'link_id':'L1','action':'confirm'}
        r=self.client.post(self.base+'/relations',json=body,headers=self.auth());self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['confirmed'][0]['evidence'],secret)
        blob=json.dumps(self.trace_changed_events(),ensure_ascii=False)
        self.assertNotIn(secret,blob);self.assertNotIn('p.4',blob)
        self.assertIn(hashlib.sha256(secret.encode()).hexdigest(),blob)
        # locator 属低熵文本：哈希可被枚举比对，脱敏后不得以任何形式外带（F19 复核）。
        self.assertNotIn(hashlib.sha256(b'p.4').hexdigest(),blob)
        entries=[l for e in self.trace_changed_events() for l in e['after'].get('changes',{}).get('links',[])]
        self.assertTrue(entries);self.assertTrue(all(l['id']=='L1' for l in entries))  # 不改写提案 id，不注入提案人 user id
        body.update(expected_revision=1,action='unlink')
        self.assertEqual(self.client.post(self.base+'/relations',json=body,headers=self.auth()).status_code,200)
        self.assertEqual(self.client.post(self.base+'/relations/undo',json={'expected_revision':2},headers=self.auth()).status_code,200)
        blob=json.dumps(self.trace_changed_events(),ensure_ascii=False)
        self.assertNotIn(secret,blob);self.assertNotIn('p.4',blob)
        package=self.client.post(f'/api/tasks/{self.task}/trace/exports',json={'preset':'public'},headers=self.auth())
        self.assertEqual(package.status_code,200,package.text[:200])
        with zipfile.ZipFile(_io.BytesIO(package.content)) as z:
            exported=b''.join(z.read(name) for name in z.namelist())
            events_jsonl=z.read('events.jsonl')
        self.assertNotIn(secret.encode(),exported)
        self.assertIn(hashlib.sha256(secret.encode()).hexdigest().encode(),exported)
        self.assertNotIn(f'{proposer}-L1'.encode(),exported)  # 提案人 user id 不得以任何拼接形式进入公开导出
        self.assertNotIn(b'"actor_id"',events_jsonl)  # 事件行与链接条目中的 actor_id 键均已脱敏删除

    def test_size_guard_rejects_oversized_request_body(self):
        # 回归（F05）：超过 2MB 的请求体在进入模型校验前即被守卫 413 拦截。
        state=self.state();state['drafts']=[{'id':'F1','key':'R1','finding':'x','quote':'y'*2_200_000}]
        r=self.save(state)
        self.assertEqual(r.status_code,413,r.text[:200])
        self.assertIn('Workspace request exceeds 2 MB',r.text)

    def test_request_journal_is_bounded_per_actor(self):
        # 回归（F03）：幂等去重日志按成员只保留最近若干条；最新操作仍可重放，旧操作自然过期。
        from custom_backend import report_evidence_api as api_module
        original=api_module.REQUEST_JOURNAL_LIMIT
        api_module.REQUEST_JOURNAL_LIMIT=3
        try:
            ops=[str(uuid.uuid4()) for _ in range(5)]
            for i,op in enumerate(ops):
                self.assertEqual(self.save(self.state(),revision=i,operation=op).status_code,200)
            with closing(connect(self.ctx.owner_db)) as c:
                kept=[row[0] for row in c.execute(
                    'SELECT operation FROM report_evidence_requests WHERE actor=? ORDER BY rowid',
                    (str(self.users[0]['user_id']),))]
            self.assertEqual(kept,ops[2:])  # 仅保留最近3条，不再只增不减
            self.assertEqual(self.save(self.state(),revision=4,operation=ops[4]).status_code,200)  # 最新操作幂等重放
        finally:
            api_module.REQUEST_JOURNAL_LIMIT=original

    def test_confirm_rejects_proposal_after_proposer_role_revoked(self):
        # 回归（F04）：提案人被撤销编码角色后，其遗留提案不得经确认进入共享库；重新授权后可确认。
        task_collaboration.grant_role(self.ctx.collaboration_db,actor_user_id=self.users[0]['user_id'],user_id=self.users[1]['user_id'],role='coder',task_id=self.task)
        self.save(self.link(self.state()),actor=1)
        with closing(review_workflow.connect(self.ctx.owner_db)) as c,c:
            row=c.execute('SELECT stages_json FROM workflow_state').fetchone();stages=json.loads(row[0])
            for s in stages.values():s['mode']='paused'
            c.execute('UPDATE workflow_state SET stages_json=?',(json.dumps(stages),))
        self.assertTrue(task_collaboration.revoke_role(self.ctx.collaboration_db,actor_user_id=self.users[0]['user_id'],user_id=self.users[1]['user_id'],role='coder',task_id=self.task))
        body={'expected_revision':0,'actor_id':str(self.users[1]['user_id']),'link_id':'L1','action':'confirm'}
        r=self.client.post(self.base+'/relations',json=body,headers=self.auth())
        self.assertEqual(r.status_code,409,r.text);self.assertIn('no longer an active coding member',r.text)
        self.assertEqual(len(self.client.get(self.base,headers=self.auth()).json()['proposals']),1)  # 提案仍保留在个人工作区
        task_collaboration.grant_role(self.ctx.collaboration_db,actor_user_id=self.users[0]['user_id'],user_id=self.users[1]['user_id'],role='coder',task_id=self.task)
        self.assertEqual(self.client.post(self.base+'/relations',json=body,headers=self.auth()).status_code,200)

    def test_same_id_shared_unlink_and_undo_leave_trace(self):
        # 回归（M1）：跨成员同 id 重复共享关系按 (id, actor_id) 多重映射计算增量——
        # unlink 必须留痕且只归因被解除的那一条，撤销恢复同样留痕；
        # 不再出现零事件（审计缺失）或把仍存在的关系误记为新增（归因倒置）。
        owner=str(self.users[0]['user_id']);coder=str(self.users[1]['user_id'])
        db.upsert_articles(self.ctx.owner_db,[Article(zotero_key='R3',title='Third')],[])  # 撤销恢复校验 a/b 仍为活跃报告
        def agreed(a,b,actor,evidence):
            return {'id':'L1','a':a,'b':b,'type':'study','same':False,'evidence':evidence,'locator':'p.1','status':'agreed','actor_id':actor}
        self._preset_shared_links([agreed('R1','R2',coder,'Coder preset pair'),agreed('R1','R3',owner,'Owner preset pair')])
        body={'expected_revision':0,'actor_id':coder,'link_id':'L1','action':'unlink'}
        r=self.client.post(self.base+'/relations',json=body,headers=self.auth());self.assertEqual(r.status_code,200,r.text)
        unlink_events=self.trace_changed_events()
        self.assertEqual(sorted(e['subject_id'] for e in unlink_events),['R1','R2'])  # 解除必须留痕
        for e in unlink_events:
            removed=e['before']['changes']['links']
            self.assertEqual(len(removed),1)
            self.assertEqual(removed[0]['actor_id'],coder)  # 只归因被解除的成员提案
            self.assertEqual(e['after']['changes']['links'],[])
            blob=json.dumps(e,ensure_ascii=False)
            self.assertNotIn('Coder preset pair',blob)  # 他人私有文本按哈希摘要脱敏
            self.assertIn(hashlib.sha256('Coder preset pair'.encode()).hexdigest(),blob)
        self.assertNotIn('Owner preset pair',json.dumps(unlink_events,ensure_ascii=False))  # 未解除的关系不进入增量
        r=self.client.post(self.base+'/relations/undo',json={'expected_revision':1},headers=self.auth())
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(len(r.json()['confirmed']),2)
        restore_events=self.trace_changed_events()[len(unlink_events):]
        self.assertEqual(sorted(e['subject_id'] for e in restore_events),['R1','R2'])  # 撤销恢复留痕
        added=[l for e in restore_events for l in e['after']['changes']['links']]
        self.assertEqual(len(added),2);self.assertTrue(all(l['id']=='L1' for l in added))
        self.assertTrue(all(l['actor_id']==coder for l in added))  # 恢复的是被解除的那一条

    def _insert_legacy_trace_events(self,rows):
        # 直接预置 2.1.3 形态的全量快照轨迹事件（绕过当前写入路径；载荷每侧须 <1MB 以内可存）。
        with closing(db._connect(self.ctx.owner_db)) as c:
            research_trace.ensure_schema(c)
            store_id=c.execute("SELECT store_id FROM trace_store WHERE singleton=1").fetchone()[0]
            for actor,subject,before,after in rows:
                # 门禁墙钟：before/after 允许直接传已序列化串（调用方复用上轮
                # after 串，免每行重复 dumps/深拷贝同一 0.4MB 快照）
                before_json = before if isinstance(before, str) else json.dumps(before, ensure_ascii=False)
                after_json = after if isinstance(after, str) else json.dumps(after, ensure_ascii=False)
                c.execute("INSERT INTO trace_events (event_id,store_id,task_id,actor_id,stage,action,subject_id,"
                          "before_json,after_json,field_json,source,recorded_at,schema_version,app_version) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (str(uuid.uuid4()),store_id,self.task,actor,'coding','report_evidence_changed',subject,
                           before_json,after_json,None,
                           'report_evidence','2026-01-01T00:00:00Z','reviewflow.research-trace/1','2.1.3'))
            c.commit()

    def _legacy_payload_bytes(self):
        with closing(db._connect(self.ctx.owner_db)) as c:
            return c.execute("SELECT COUNT(*),SUM(length(before_json)+length(after_json)) FROM trace_events "
                             "WHERE action IN ('report_evidence_changed','imported_report_evidence_changed')").fetchone()

    def _insert_single_subject_legacy_history(self,saves=90):
        # 构造 2.1.3 形态的单主体保存链（每次 before/after 互不相同，不坍缩），
        # 每份快照约 0.4MB，且每份 after 都含一条他人 evidence/locator 提案。
        exporter=str(self.users[0]['user_id']);coder=str(self.users[1]['user_id'])
        secret='LEGACY-SECRET coder evidence'
        rows=[];prev_json=json.dumps({'value':'Save evidence','workspace':{'links':[],'drafts':[]}},ensure_ascii=False)
        for i in range(saves):
            current={'links':[{'id':'L1','a':'R1','b':'R2','type':'study','same':False,'evidence':secret,
                               'locator':'p.4','status':'agreed','actor_id':coder}],
                     'drafts':[{'id':f'd{j}','key':'R1','finding':f'{i}-{j}','quote':'x'*19500} for j in range(20)]}
            # before 直接复用上一轮 after 串（同一快照内容；立即序列化无别名风险）
            cur_json=json.dumps({'value':'Save evidence','workspace':current},ensure_ascii=False)
            rows.append((exporter,'R1',prev_json,cur_json))
            prev_json=cur_json
        self._insert_legacy_trace_events(rows)
        return secret

    def test_legacy_single_subject_history_folds_and_exports(self):
        # 回归（H1）：90 次单主体约 0.4MB 快照的存量轨迹按保存批次折叠后体积显著缩小，
        # public/complete 导出均不再 413；迁移前建 .pre-fold-v1.bak 备份并保留折叠前原貌。
        secret=self._insert_single_subject_legacy_history(90)
        count,before_total=self._legacy_payload_bytes()
        self.assertEqual(count,90);self.assertGreater(before_total,32*1024*1024)  # 未折叠时导出必然超过 32MB 上限
        with closing(connect(self.ctx.owner_db)) as c:
            pass  # 触发按保存批次折叠
        backup=str(self.ctx.owner_db)+'.pre-fold-v1.bak'
        self.assertTrue(os.path.exists(backup))
        with closing(sqlite3.connect(backup)) as bc:  # 备份不经 db._connect（避免对备份执行自愈迁移）
            bak_count,bak_total=bc.execute("SELECT COUNT(*),SUM(length(before_json)+length(after_json)) FROM trace_events "
                                           "WHERE action='report_evidence_changed'").fetchone()
        self.assertEqual((bak_count,bak_total),(count,before_total))  # 备份保留折叠前原貌
        count,after_total=self._legacy_payload_bytes()
        self.assertEqual(count,90)  # 单主体每次保存各自成批，不互相坍缩
        self.assertLess(after_total,5_000_000)  # 显著缩小（预算外批次降级为摘要+哈希）
        with closing(db._connect(self.ctx.owner_db)) as c:
            blob="".join(str(r[0])+str(r[1]) for r in c.execute(
                "SELECT before_json,after_json FROM trace_events WHERE action='report_evidence_changed'"))
            meta=c.execute("SELECT folded_runs,deleted_rows,skipped_events FROM report_evidence_fold_meta WHERE singleton=1").fetchone()
        self.assertEqual(tuple(meta),(90,0,0))
        self.assertNotIn('"workspace":',blob);self.assertIn('"workspace_sha256"',blob)
        self.assertIn('"value"',blob)  # 折叠载荷保留操作标签（行内摘要不变）
        self.assertNotIn(secret,blob);self.assertIn(hashlib.sha256(secret.encode()).hexdigest(),blob)  # 他人 evidence 只留哈希
        for preset in ('public','complete'):
            package=self.client.post(f'/api/tasks/{self.task}/trace/exports',json={'preset':preset},headers=self.auth())
            self.assertEqual(package.status_code,200,f'{preset}: {package.text[:200]}')
            with zipfile.ZipFile(_io.BytesIO(package.content)) as z:
                exported=b''.join(z.read(name) for name in z.namelist())
                events_jsonl=z.read('events.jsonl')
            self.assertNotIn(secret.encode(),exported)
            self.assertNotIn(b'"workspace":',events_jsonl)
            self.assertLess(len(events_jsonl),32*1024*1024)
        # 迁移幂等：标记就位后再次连接与导出不再改写。
        with closing(connect(self.ctx.owner_db)) as c:
            pass
        self.assertEqual(self._legacy_payload_bytes(),(count,after_total))

    def test_export_hook_folds_without_prior_report_evidence_connect(self):
        # 回归（评审）：导出钩子传入 db._connect 连接（不设 Row 工厂），迁移不得依赖
        # 调用方 row_factory。全程不调用 report_evidence 的 connect，仅靠导出触发折叠。
        secret=self._insert_single_subject_legacy_history(90)
        count,before_total=self._legacy_payload_bytes()
        self.assertEqual(count,90);self.assertGreater(before_total,32*1024*1024)
        for preset in ('public','complete'):
            package=self.client.post(f'/api/tasks/{self.task}/trace/exports',json={'preset':preset},headers=self.auth())
            self.assertEqual(package.status_code,200,f'{preset}: {package.text[:200]}')
            with zipfile.ZipFile(_io.BytesIO(package.content)) as z:
                exported=b''.join(z.read(name) for name in z.namelist())
                events_jsonl=z.read('events.jsonl')
            self.assertNotIn(b'"workspace":',events_jsonl)  # 迁移确实经导出钩子执行
            self.assertNotIn(secret.encode(),exported)
            self.assertNotIn(b'"p.4"',exported)
        with closing(db._connect(self.ctx.owner_db)) as c:
            marker=c.execute("SELECT folded_runs,deleted_rows,skipped_events FROM report_evidence_fold_meta "
                             "WHERE singleton=1").fetchone()
        self.assertEqual(tuple(marker),(90,0,0))
        count2,after_total2=self._legacy_payload_bytes()
        self.assertEqual(count2,90);self.assertLess(after_total2,5_000_000)

    def test_public_export_redacts_links_even_when_fold_cannot_run(self):
        # 回归（契约⑧兜底）：导出前迁移失败/未执行时，public 导出仍不得带出关系条目中的
        # evidence/locator 原文——导出侧脱敏独立于存储折叠（fail-safe）。
        # 注：不用真实写锁模拟——导出路由自身在进入迁移钩子前就会对 owner 库取写锁
        #（main._screener_db→db.init_db，非本泳道文件），持锁会挡住整条路由而非仅迁移；
        # 故以注入 _fold_legacy_runs 异常模拟“迁移未执行/失败”这一等价路径。
        secret='LOCKED-SECRET coder evidence'
        actor=str(self.users[0]['user_id']);coder=str(self.users[1]['user_id'])
        before={'value':'Save evidence','workspace':{'links':[],'drafts':[]}}
        after={'value':'Save evidence','workspace':{'links':[
            {'id':'L1','a':'R1','b':'R2','type':'study','same':False,'evidence':secret,
             'locator':'p.4','status':'agreed','actor_id':coder}],
            'drafts':[{'id':'F1','key':'R1','finding':'UNFOLDED-SECRET finding','quote':'UNFOLDED-QUOTE',
                       'locator':'p.9','context':'UNFOLDED-CONTEXT','theme':'UNFOLDED-THEME',
                       'interpretation':'UNFOLDED-INTERPRETATION'}]}}
        self._insert_legacy_trace_events([(actor,'R1',before,after)])
        from custom_backend import report_evidence_api as api_module
        original=api_module._fold_legacy_runs
        def _simulated_failure(*args,**kwargs):
            raise RuntimeError('simulated fold failure')
        api_module._fold_legacy_runs=_simulated_failure
        try:
            package=self.client.post(f'/api/tasks/{self.task}/trace/exports',json={'preset':'public'},headers=self.auth())
            self.assertEqual(package.status_code,200,package.text[:200])  # 迁移失败不阻断导出
            with zipfile.ZipFile(_io.BytesIO(package.content)) as z:
                exported=b''.join(z.read(name) for name in z.namelist())
            self.assertNotIn(secret.encode(),exported)  # 他人 evidence 原文不外带
            self.assertNotIn(b'"p.4"',exported)  # locator 原文不外带
            for text in ('UNFOLDED-SECRET','UNFOLDED-QUOTE','UNFOLDED-CONTEXT',
                         'UNFOLDED-THEME','UNFOLDED-INTERPRETATION'):
                self.assertNotIn(text.encode(),exported)  # 未折叠的草稿文本同样不外带
            self.assertIn(hashlib.sha256(secret.encode()).hexdigest().encode(),exported)  # evidence 保留哈希摘要
        finally:
            api_module._fold_legacy_runs=original
        with closing(db._connect(self.ctx.owner_db)) as c:
            folded=c.execute("SELECT COUNT(*) FROM trace_events WHERE action='report_evidence_changed' "
                             "AND after_json LIKE '%\"workspace\"%'").fetchone()[0]
            marker=c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name="
                             "'report_evidence_fold_meta'").fetchone()[0]
        self.assertEqual(folded,1)  # 迁移确实未执行（载荷仍是全量快照形态）
        self.assertEqual(marker,0)  # 未置完成标记；隐私完全由导出侧脱敏承担

    def test_native_batch_with_origin_row_never_deletes_origin(self):
        # 回归（契约②）：同键连续批次中混入携带 origin 身份的行时，origin 行独立成批、
        # 只更新不删除；origin_* 列与 origin_sha256 原样保留。
        actor=str(self.users[0]['user_id'])
        before={'value':'Save evidence','workspace':{'links':[],'drafts':[]}}
        after={'value':'Save evidence','workspace':{'links':[
            {'id':'L1','a':'R1','b':'R2','type':'study','same':False,'evidence':'Mixed batch evidence',
             'locator':'p.9','status':'agreed','actor_id':str(self.users[1]['user_id'])}],'drafts':[]}}
        origin_event={'event_id':str(uuid.uuid4()),'store_id':'st-x','sequence':99,'task_id':self.task,
                      'actor_id':actor,'stage':'coding','action':'report_evidence_changed','subject_id':'R2',
                      'before':before,'after':after,'source':'report_evidence','recorded_at':'2026-01-01T00:00:00Z'}
        origin_sha=research_trace.origin_event_sha256(origin_event)
        origin_row_id=str(uuid.uuid4())
        with closing(db._connect(self.ctx.owner_db)) as c:
            research_trace.ensure_schema(c)
            store_id=c.execute("SELECT store_id FROM trace_store WHERE singleton=1").fetchone()[0]
            for subject,with_origin in (('R1',False),('R2',True),('R3',False)):
                if with_origin:
                    c.execute("INSERT INTO trace_events (event_id,store_id,task_id,actor_id,stage,action,subject_id,"
                              "before_json,after_json,field_json,source,recorded_at,schema_version,app_version,"
                              "origin_store_id,origin_event_id,origin_sequence,origin_actor_id,imported_at,"
                              "origin_alias,origin_json,origin_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (origin_row_id,store_id,self.task,actor,'coding','report_evidence_changed',subject,
                               json.dumps(before,ensure_ascii=False),json.dumps(after,ensure_ascii=False),None,
                               'report_evidence','2026-01-01T00:00:00Z','reviewflow.research-trace/1','2.1.3',
                               'st-x',origin_event['event_id'],99,actor,'2026-01-02T00:00:00Z','alias',
                               json.dumps(research_trace._origin_record(origin_event),ensure_ascii=False,sort_keys=True),
                               origin_sha))
                else:
                    c.execute("INSERT INTO trace_events (event_id,store_id,task_id,actor_id,stage,action,subject_id,"
                              "before_json,after_json,field_json,source,recorded_at,schema_version,app_version) "
                              "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (str(uuid.uuid4()),store_id,self.task,actor,'coding','report_evidence_changed',subject,
                               json.dumps(before,ensure_ascii=False),json.dumps(after,ensure_ascii=False),None,
                               'report_evidence','2026-01-01T00:00:00Z','reviewflow.research-trace/1','2.1.3'))
            c.commit()
        with closing(connect(self.ctx.owner_db)) as c:
            origin_row=c.execute("SELECT origin_json,origin_sha256,origin_event_id,origin_store_id,after_json "
                                 "FROM trace_events WHERE event_id=?",(origin_row_id,)).fetchone()
            remaining=c.execute("SELECT COUNT(*) FROM trace_events WHERE action='report_evidence_changed'").fetchone()[0]
            meta=c.execute("SELECT folded_runs,deleted_rows FROM report_evidence_fold_meta WHERE singleton=1").fetchone()
        self.assertIsNotNone(origin_row)  # origin 行绝不被删除
        self.assertEqual(origin_row[1],origin_sha)  # origin_sha256 原样
        self.assertEqual((origin_row[2],origin_row[3]),(origin_event['event_id'],'st-x'))
        self.assertIn('changes',json.loads(origin_row[4]))  # 载荷照常折叠
        self.assertEqual(remaining,3)  # 三行全部保留（origin 独立成批，批次内不删除任何行）
        self.assertEqual(tuple(meta),(3,0))

    def test_legacy_multi_subject_batch_keeps_per_subject_rows(self):
        # 回归（契约④）：同一次保存影响 3 个主体（相同 before/after、连续序号）时，
        # 批内逐行改写为各自主体的增量、不合并为单主体事件、不删除行——事件条数、
        # 唯一主体计数/修改主体计数与每日圆点与迁移前一致（唯一被允许的变化是载荷
        # 压缩）。trace_field_lifecycles 与 origin 列不受影响。
        actor=str(self.users[0]['user_id'])
        before={'value':'Batch save','workspace':{'links':[],'drafts':[{'id':'d1','key':'R1','finding':'Base'}]}}
        after={'value':'Batch save','workspace':{'links':[
                    {'id':'L1','a':'R2','b':'R3','type':'study','same':False,'evidence':'Own evidence',
                     'locator':'p.2','status':'mine'}],
                'drafts':[{'id':'d1','key':'R1','finding':'Base'},{'id':'d2','key':'R1','finding':'Added'}]}}
        self._insert_legacy_trace_events([(actor,s,before,after) for s in ('R1','R2','R3')])
        with closing(db._connect(self.ctx.owner_db)) as c:
            # 迁移不得走 clear_history 语义：预置一行字段生命周期并断言折叠后仍在。
            c.execute("INSERT INTO trace_field_lifecycles (task_id,actor_id,legacy_field_id,field_uuid,definition_json,created_at) "
                      "VALUES(?,?,?,?,?,?)",(self.task,actor,'f1','uuid-f1','{}','2026-01-01T00:00:00Z'))
            first_seq=c.execute("SELECT MIN(sequence) FROM trace_events WHERE action='report_evidence_changed'").fetchone()[0]
            before_summary=research_trace.summary(c,self.task,actor)
            c.commit()
        with closing(connect(self.ctx.owner_db)) as c:
            rows=c.execute("SELECT sequence,subject_id,after_json FROM trace_events WHERE action='report_evidence_changed' "
                           "ORDER BY sequence").fetchall()
            meta=c.execute("SELECT folded_runs,deleted_rows,skipped_events FROM report_evidence_fold_meta WHERE singleton=1").fetchone()
            lifecycle=c.execute("SELECT COUNT(*) FROM trace_field_lifecycles").fetchone()[0]
            origins=c.execute("SELECT COUNT(*) FROM trace_events WHERE origin_event_id IS NOT NULL").fetchone()[0]
            after_summary=research_trace.summary(c,self.task,actor)
        self.assertEqual(len(rows),3)  # 三条主体事件全部保留，不合并为单主体事件
        self.assertEqual([r[1] for r in rows],['R1','R2','R3'])  # 各行主体保持原位
        self.assertEqual(tuple(meta),(1,0,0))  # 折叠 1 批、不删除任何行
        self.assertEqual(rows[0][0],first_seq)  # 各行序号不变（外层字段不动）
        self.assertEqual(origins,0)  # 未产生任何 origin 身份行
        self.assertEqual(lifecycle,1)  # 无 clear_history 语义
        # 契约④核心断言：summary 主体计数与每日圆点迁移前后一致，只允许 event_count 不变或变小。
        self.assertEqual(after_summary['unique_subject_count'],before_summary['unique_subject_count'])
        self.assertEqual(after_summary['modified_subject_count'],before_summary['modified_subject_count'])
        self.assertEqual(after_summary['event_count'],before_summary['event_count'])
        self.assertEqual([(d['date'],d['unique_subject_count']) for d in after_summary['daily']],
                         [(d['date'],d['unique_subject_count']) for d in before_summary['daily']])
        by_subject={r[1]:json.loads(r[2]) for r in rows}
        for subject in ('R1','R2','R3'):
            self.assertNotIn('affected_subjects',by_subject[subject])  # 不再产出合并载荷字段
            self.assertIn('changes',by_subject[subject])
        self.assertEqual(by_subject['R1']['changes']['drafts'][0]['id'],'d2')  # R1 只带自己的新增草稿（d1 未变被抵消）
        self.assertEqual(by_subject['R1']['changes']['links'],[])
        for subject in ('R2','R3'):  # 关系的 a 与 b 各算一个受影响主体，增量同型分置两行
            self.assertEqual([l['id'] for l in by_subject[subject]['changes']['links']],['L1'])
            self.assertEqual(by_subject[subject]['changes']['drafts'],[])

    def test_legacy_mixed_digest_side_preserved(self):
        # 回归（M2）：一侧全量、一侧已是旧摘要的混合形态折叠时，摘要侧原有
        # workspace_sha256 与 links/findings 计数原样保留，不得重算为空摘要。
        actor=str(self.users[0]['user_id'])
        before={'value':'Save evidence','workspace':{'links':[],'drafts':[{'id':'F1','key':'R1','finding':'Meaning'}]}}
        after={'value':'Save evidence','workspace_sha256':'e'*64,'links':3,'findings':1,
               'note':'Large workspace: export JSON to inspect current details.'}
        self._insert_legacy_trace_events([(actor,'R1',before,after)])
        with closing(connect(self.ctx.owner_db)) as c:  # 不抛错（不得 500）
            row=c.execute("SELECT before_json,after_json FROM trace_events WHERE action='report_evidence_changed'").fetchone()
        folded_after=json.loads(row[1])
        self.assertEqual(folded_after['workspace_sha256'],'e'*64)  # 原摘要哈希保留
        self.assertEqual(folded_after['links'],3);self.assertEqual(folded_after['findings'],1)
        folded_before=json.loads(row[0])  # 全量侧脱敏后降级为计算摘要
        self.assertIn('workspace_sha256',folded_before)
        self.assertNotEqual(folded_before['workspace_sha256'],'e'*64)
        self.assertEqual(folded_before['findings'],1)
        self.assertIn('Legacy full workspace snapshot compacted',folded_before['note'])

    def test_legacy_malformed_payloads_skipped_with_marker(self):
        # 回归（M5）：畸形载荷（links 非列表、载荷非对象等）跳过并写标记事件，任何情况不得 500。
        actor=str(self.users[0]['user_id'])
        malformed_links={'value':'Save evidence','workspace':{'links':'oops','drafts':[]}}
        malformed_shape=['workspace']  # 载荷本身非对象，但含 "workspace" 文本可被扫描命中
        healthy_before={'value':'Save evidence','workspace':{'links':[],'drafts':[]}}
        healthy_after={'value':'Save evidence','workspace':{'links':[],'drafts':[{'id':'F1','key':'R1','finding':'Ok'}]}}
        self._insert_legacy_trace_events([
            (actor,'R1',malformed_links,malformed_links),
            (actor,'R1',malformed_shape,malformed_shape),
            (actor,'R1',healthy_before,healthy_after)])
        with closing(connect(self.ctx.owner_db)) as c:
            pass
        with closing(connect(self.ctx.owner_db)) as c:  # 重复连接同样成功
            rows=c.execute("SELECT before_json,after_json FROM trace_events WHERE action='report_evidence_changed' "
                           "ORDER BY sequence").fetchall()
        self.assertEqual(len(rows),4)  # 3 条旧事件（2 畸形 + 1 正常）+ 1 条跳过标记
        marker=json.loads(rows[3][1])
        self.assertEqual(marker['skipped_events'],2)
        self.assertEqual(json.loads(rows[0][0]),malformed_links)  # 畸形事件原样保留、绝不动 origin
        self.assertEqual(json.loads(rows[1][0]),malformed_shape)
        self.assertEqual(json.loads(rows[2][1])['changes']['drafts'][0]['id'],'F1')  # 正常事件照常折叠
        with closing(db._connect(self.ctx.owner_db)) as c:
            meta=c.execute("SELECT folded_runs,deleted_rows,skipped_events FROM report_evidence_fold_meta WHERE singleton=1").fetchone()
        self.assertEqual(tuple(meta),(1,0,2))

    def test_legacy_distinct_saves_do_not_collapse(self):
        # 回归（评审 fixture 缺陷）：不同保存（不同 before/after）的单主体事件不得互相坍缩，
        # 每次保存保留各自的事件与增量（fixture 显式构造互不相同的快照链）。
        actor=str(self.users[0]['user_id'])
        chain=[{'links':[],'drafts':[]}]+[
            {'links':[],'drafts':[{'id':f'F{j}','key':'R1','finding':f'Finding {j}'} for j in range(i+1)]}
            for i in range(3)]
        self._insert_legacy_trace_events([
            (actor,'R1',{'value':'Save evidence','workspace':chain[i]},
                    {'value':'Save evidence','workspace':chain[i+1]}) for i in range(3)])
        with closing(connect(self.ctx.owner_db)) as c:
            stored=c.execute("SELECT before_json,after_json FROM trace_events WHERE action='report_evidence_changed' "
                             "ORDER BY sequence").fetchall()
        self.assertEqual(len(stored),3)  # 三次保存三条事件，不坍缩为一条
        payloads=[json.loads(r[1]) for r in stored]
        self.assertNotEqual(payloads[0],payloads[1]);self.assertNotEqual(payloads[1],payloads[2])
        for payload in payloads:
            self.assertIn('value',payload);self.assertIn('changes',payload)
        self.assertEqual([p['changes']['drafts'][0]['id'] for p in payloads],['F0','F1','F2'])  # 增量=本次新增条目

    def test_fold_backup_created_once_and_not_overwritten(self):
        # 回归（③）：迁移前建 .pre-fold-v1.bak；已存在则跳过不覆盖，迁移照常执行。
        actor=str(self.users[0]['user_id'])
        before={'value':'Save evidence','workspace':{'links':[],'drafts':[]}}
        after={'value':'Save evidence','workspace':{'links':[],'drafts':[{'id':'F1','key':'R1','finding':'X'}]}}
        self._insert_legacy_trace_events([(actor,'R1',before,after)])
        backup_path=str(self.ctx.owner_db)+'.pre-fold-v1.bak'
        Path(backup_path).write_bytes(b'sentinel-not-a-db')
        try:
            with closing(connect(self.ctx.owner_db)) as c:
                folded=c.execute("SELECT after_json FROM trace_events WHERE action='report_evidence_changed'").fetchone()
            self.assertEqual(Path(backup_path).read_bytes(),b'sentinel-not-a-db')  # 已有备份不覆盖
            self.assertIn('changes',json.loads(folded[0]))  # 迁移照常折叠
        finally:
            os.remove(backup_path)

    def test_connect_read_path_takes_no_write_lock_after_fold(self):
        # 回归（③）：折叠完成标记就位后，connect() 读路径零写锁——
        # 另一连接持 BEGIN IMMEDIATE 写锁时，读连接仍即时成功（不等待、不抢写锁）。
        self.assertEqual(self.save(self.link(self.state())).status_code,200)  # 首连建表+写入轨迹
        with closing(connect(self.ctx.owner_db)) as c:
            pass  # 轨迹表就位后的下一次连接：空扫描并置折叠完成标记（一次性小写入）
        blocker=sqlite3.connect(self.ctx.owner_db,timeout=0.5)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            started=time.monotonic()
            c=connect(self.ctx.owner_db)
            try:
                self.assertGreaterEqual(c.execute("SELECT COUNT(*) FROM report_evidence_state").fetchone()[0],1)
                c.execute("SELECT COUNT(*) FROM trace_events").fetchone()
            finally:
                c.close()
            self.assertLess(time.monotonic()-started,5)  # 若再取写锁会阻塞至 busy_timeout
        finally:
            blocker.rollback();blocker.close()

    def test_legacy_imported_events_keep_origin_identity_for_reimport(self):
        # 回归（契约②）：折叠只改写导入行的 before/after 载荷，origin_* 九列与
        # origin_sha256 绝对不动、行不删除——旧导出包重导入仍判定 duplicate 而非 conflict。
        actor=str(self.users[0]['user_id'])
        original={'event_id':str(uuid.uuid4()),'store_id':'st-legacy','sequence':5,'task_id':self.task,
                  'actor_id':'77','stage':'coding','action':'report_evidence_changed','subject_id':'R1',
                  'before':{'value':'Save evidence','workspace':{'links':[],'drafts':[]}},
                  'after':{'value':'Save evidence','workspace':{'links':[],'drafts':[{'id':'F1','key':'R1','finding':'Imported finding'}]}},
                  'source':'report_evidence','recorded_at':'2026-01-01T00:00:00Z'}
        with closing(db._connect(self.ctx.owner_db)) as c:
            research_trace.ensure_schema(c)
            result=research_trace.import_event(c,task_id=self.task,importer_actor_id=actor,event=original)
            self.assertEqual(result['status'],'imported')
            row=c.execute("SELECT origin_json,origin_sha256,origin_event_id,origin_store_id FROM trace_events "
                          "WHERE event_id=?",(result['event_id'],)).fetchone()
            origin_before=tuple(row)
            c.commit()
        # 同库补一条自有旧事件，触发折叠扫描一并覆盖导入行。
        self._insert_legacy_trace_events([(actor,'R1',
            {'value':'Save evidence','workspace':{'links':[],'drafts':[]}},
            {'value':'Save evidence','workspace':{'links':[],'drafts':[{'id':'N1','key':'R1','finding':'Native'}]}})])
        with closing(connect(self.ctx.owner_db)) as c:
            row2=c.execute("SELECT origin_json,origin_sha256,origin_event_id,origin_store_id,after_json "
                           "FROM trace_events WHERE event_id=?",(result['event_id'],)).fetchone()
        self.assertIsNotNone(row2)  # 携带 origin 身份的导入行绝不删除
        self.assertEqual(tuple(row2[:4]),origin_before)  # origin_* 九列与 origin_sha256 原样保留
        self.assertIn('changes',json.loads(row2[4]))  # 载荷已按主体增量折叠
        with closing(db._connect(self.ctx.owner_db)) as c:
            again=research_trace.import_event(c,task_id=self.task,importer_actor_id=actor,event=original)
        self.assertEqual(again['status'],'duplicate')  # 旧包重导入保持 duplicate 语义而非 conflict

    def test_public_export_redacts_origin_column(self):
        # 回归（⑧/M3）：公开导出对 origin 原始副本做结构化脱敏——保留导入来源结构
        #（与事件本体走同一公开管线：私有键/身份字段移除、links/drafts 文本兜底脱敏，
        # 含 origin 副本内 workspace.drafts 的 finding/context/theme/interpretation）；
        # complete 预设 evidence 与草稿文本全部原样保留。
        from coscreen.research_trace_export import build_export_zip
        secret='IMPORTED-CODER-SECRET'
        original_after={'value':'Save','workspace':{'links':[{'id':'L1','a':'R1','b':'R2','type':'study',
                     'same':False,'evidence':secret,'locator':'p.4','status':'agreed','actor_id':'9'}],
                     'drafts':[{'id':'F1','key':'R1','finding':'SECRET-FINDING synthesis','quote':'SECRET-QUOTE body',
                                'locator':'p.7','context':'SECRET-CONTEXT','theme':'SECRET-THEME',
                                'interpretation':'SECRET-INTERPRETATION','credibility':'credible'}]}}
        # 折叠后的载荷形态（evidence 已替换为哈希摘要），origin 副本仍保留原文。
        folded_after={'value':'Save','changes':{
            'links':[{'id':'L1','a':'R1','b':'R2','type':'study','same':False,
                      'evidence':{'redacted':'other_member_private_text','length':len(secret),
                                  'sha256':hashlib.sha256(secret.encode()).hexdigest()},'actor_id':'9'}],
            'drafts':[{'id':'F1','key':'R1','finding':'OWN-FINDING synthesis text'}]}}
        base_event={'event_id':str(uuid.uuid4()),'store_id':'st-1','sequence':7,'task_id':'T1','actor_id':'42',
                    'stage':'coding','action':'imported_report_evidence_changed','subject_id':'R1',
                    'before':{'value':'Save','changes':{'links':[],'drafts':[]}},'after':folded_after,
                    'source':'import','recorded_at':'2026-01-01T00:00:00Z'}
        snapshot={'stores':[{'partition':'personal','store_id':'st-1','settings':{'status':'recording','stages':['coding']},
                             'summary':{},'events':[{**base_event,'origin':{**base_event,'after':original_after}}]}]}
        public=build_export_zip(snapshot,public=True)
        with zipfile.ZipFile(_io.BytesIO(public)) as z:
            pub_events=z.read('events.jsonl')
            pub_all=b''.join(z.read(n) for n in z.namelist())
        self.assertIn(b'"origin"',pub_events)  # origin 结构化保留（导入来源可追溯）
        self.assertNotIn(b'"actor_id"',pub_events)  # 事件与 origin 内的身份字段均已移除
        self.assertNotIn(b'"task_id"',pub_events)
        self.assertNotIn(secret.encode(),pub_all)  # origin 副本内的他人 evidence 原文不外带
        self.assertNotIn(b'"p.4"',pub_all)  # locator 原文不外带
        for text in ('SECRET-FINDING','SECRET-QUOTE','SECRET-CONTEXT','SECRET-THEME',
                     'SECRET-INTERPRETATION','OWN-FINDING'):
            self.assertNotIn(text.encode(),pub_all)  # origin 与事件本体两侧的草稿文本一律脱敏
        self.assertIn(hashlib.sha256(secret.encode()).hexdigest().encode(),pub_all)
        complete=build_export_zip(snapshot,public=False)
        with zipfile.ZipFile(_io.BytesIO(complete)) as z:
            comp_events=z.read('events.jsonl')
        self.assertIn(secret.encode(),comp_events)
        for text in ('SECRET-FINDING','OWN-FINDING synthesis text'):
            self.assertIn(text.encode(),comp_events)
        self.assertIn(b'"actor_id"',comp_events)  # complete 保持完整备份

    def test_large_event_lines_round_trip(self):
        # 回归（评审）：写路径真实产物（折叠增量 ~0.8MB、complete 包 origin 原始副本）
        # 的单行事件必须能完整导出→重导入 round-trip，不再被 256KB 每事件上限拒绝。
        from coscreen import research_trace_export as exporter
        big_after={'value':'Save','changes':{'drafts':[{'id':f'd{i}','key':'R1','finding':'x'*4000} for i in range(100)]}}
        big_origin_after={'value':'Save','workspace':{'drafts':[{'id':f'd{i}','key':'R1','finding':'y'*3000}
                                                             for i in range(100)]}}
        event={'event_id':str(uuid.uuid4()),'store_id':'st-1','sequence':1,'task_id':'T1','actor_id':'42',
               'stage':'coding','action':'report_evidence_changed','subject_id':'R1',
               'before':{'value':'Save','changes':{'drafts':[]}},'after':big_after,
               'source':'report_evidence','recorded_at':'2026-01-01T00:00:00Z',
               'origin':{'event_id':str(uuid.uuid4()),'store_id':'st-0','sequence':1,'task_id':'T0',
                         'actor_id':'42','stage':'coding','action':'report_evidence_changed','subject_id':'R1',
                         'before':{'value':'Save','workspace':{'drafts':[]}},'after':big_origin_after,
                         'source':'report_evidence','recorded_at':'2026-01-01T00:00:00Z'}}
        package=exporter.build_export_zip({'stores':[{'partition':'personal','store_id':'st-1',
            'settings':{'status':'recording','stages':['coding']},'summary':{},'events':[event]}]},public=False)
        with zipfile.ZipFile(_io.BytesIO(package)) as z:
            line=z.read('events.jsonl').splitlines()[0]
        self.assertGreater(len(line),256*1024)  # 旧 256KB 上限必然拒绝的行
        parsed=exporter.validate_import_package(package)  # 新上限下完整 round-trip
        self.assertEqual(len(parsed['events']),1)
        self.assertEqual(parsed['events'][0]['after'],big_after)
        self.assertEqual(parsed['events'][0]['origin']['after'],big_origin_after)

    def _volume_snapshot(self,count):
        def make_event(i):
            return {'event_id':str(uuid.uuid4()),'store_id':'st-1','sequence':i+1,'task_id':'T1',
                    'actor_id':'42','stage':'coding','action':'coding_value_changed','subject_id':f'S{i%7}',
                    'before':None,'after':{'field':f'f{i%3}','value':'v'*6000},
                    'source':'manual','recorded_at':'2026-01-01T00:00:00Z'}
        return {'stores':[{'partition':'personal','store_id':'st-1',
                           'settings':{'status':'recording','stages':['coding']},'summary':{},
                           'events':[make_event(i) for i in range(count)]}]}

    def test_event_volume_round_trip_and_legacy_single_file(self):
        # 回归（契约⑥）：仅 events.jsonl 按行切卷（零填充连续编号、行不撕裂、同输入
        # 切分确定），其余成员保持单文件；manifest 带 event_volumes 清单；导入校验
        # 接受分卷包，也继续接受旧单文件 events.jsonl 包（含无 event_volumes 键的旧 manifest）。
        from coscreen import research_trace_export as exporter
        snapshot=self._volume_snapshot(120)
        original=exporter.MAX_EVENT_VOLUME_BYTES
        exporter.MAX_EVENT_VOLUME_BYTES=200_000  # 每行约 6KB：切出多卷
        try:
            package=exporter.build_export_zip(snapshot,public=False)
            again=exporter.build_export_zip(snapshot,public=False)
        finally:
            exporter.MAX_EVENT_VOLUME_BYTES=original
        with zipfile.ZipFile(_io.BytesIO(package)) as z:
            names=z.namelist()
            volumes=sorted(n for n in names if n.startswith('events-part-'))
            volume_data={n:z.read(n) for n in volumes}
            manifest=json.loads(z.read('manifest.json'))
        self.assertGreaterEqual(len(volumes),3)
        self.assertNotIn('events.jsonl',names)  # 分卷时不再有单文件事件成员
        for member in ('events.csv','summary.csv','report.html','report.md','activity.csv','readable-events.csv'):
            self.assertIn(member,names)  # 其余全局成员保持单文件
        self.assertEqual(volumes,[f'events-part-{i:02d}.jsonl' for i in range(1,len(volumes)+1)])  # 零填充连续编号
        self.assertEqual(manifest['event_volumes'],volumes)
        self.assertEqual(manifest['event_count'],120)
        for data in volume_data.values():
            self.assertLessEqual(len(data),200_000)  # 每卷不超上限
            for line in data.splitlines():  # 按行切不撕裂：每行都是完整 JSON
                json.loads(line)
        with zipfile.ZipFile(_io.BytesIO(again)) as z2:  # 同输入切分确定
            self.assertEqual({n:z2.read(n) for n in volumes},volume_data)
        parsed=exporter.validate_import_package(package)  # 分卷包导入校验 round-trip
        self.assertEqual(len(parsed['events']),120)
        self.assertEqual([e['sequence'] for e in parsed['events']],list(range(1,121)))
        self.assertEqual(parsed['manifest']['event_volumes'],volumes)
        small=exporter.build_export_zip(self._volume_snapshot(1),public=False)  # 小导出保持旧单文件形态
        with zipfile.ZipFile(_io.BytesIO(small)) as z:
            self.assertIn('events.jsonl',z.namelist())
            self.assertFalse([n for n in z.namelist() if n.startswith('events-part-')])
            self.assertEqual(json.loads(z.read('manifest.json'))['event_volumes'],['events.jsonl'])
        self.assertEqual(len(exporter.validate_import_package(small)['events']),1)
        legacy=_io.BytesIO()  # 旧 manifest（无 event_volumes 键）的单文件包继续被接受
        with zipfile.ZipFile(_io.BytesIO(small),'r') as src:
            with zipfile.ZipFile(legacy,'w',compression=zipfile.ZIP_DEFLATED) as dst:
                for info in src.infolist():
                    data=src.read(info.filename)
                    if info.filename=='manifest.json':
                        stripped=json.loads(data);stripped.pop('event_volumes',None)
                        data=json.dumps(stripped,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()+b'\n'
                    dst.writestr(info.filename,data)
        self.assertEqual(len(exporter.validate_import_package(legacy.getvalue())['events']),1)

    def test_volume_packing_never_exceeds_volume_cap(self):
        # 回归（评审）：行序贪心装箱的最坏浪费（行略超单卷一半 → 一行一卷）不得让
        # “总事件字节 ≤ MAX_UNCOMPRESSED 且逐事件 ≤ MAX_EVENT_BYTES”的输入报
        # needs too many volumes——卷数上限按 2*(U/V)+2 推导后必然可导出。
        from coscreen import research_trace_export as exporter
        def sized_events(count,target_line_bytes):
            events=[]
            for i in range(count):
                low,high=0,target_line_bytes
                for _ in range(24):  # 二分填充，把单行字节调到目标附近
                    mid=(low+high)//2
                    candidate={'event_id':str(uuid.uuid4()),'store_id':'st-1','sequence':i+1,'task_id':'T1',
                               'actor_id':'42','stage':'coding','action':'coding_value_changed','subject_id':'R1',
                               'before':None,'after':{'value':'x'*mid},'source':'manual',
                               'recorded_at':'2026-01-01T00:00:00Z'}
                    if len(exporter._json_bytes(candidate))<target_line_bytes:low=mid+1
                    else:high=mid
                events.append(candidate)
            return events
        volume_limit=1000
        uncompressed=4_000_000  # 事件总量上限（其余成员留余量）
        volume_cap=2*(uncompressed//volume_limit)+2  # 与模块公式一致的推导值
        self.assertEqual(exporter.MAX_EVENT_VOLUMES,  # 模块常量本身按公式推导（防回退为固定小值）
                         2*(exporter.MAX_UNCOMPRESSED_BYTES//exporter.MAX_EVENT_VOLUME_BYTES)+2)
        self.assertEqual(exporter.MAX_MEMBERS,13+exporter.MAX_EVENT_VOLUMES)
        originals=(exporter.MAX_EVENT_VOLUME_BYTES,exporter.MAX_UNCOMPRESSED_BYTES,
                   exporter.MAX_EVENT_VOLUMES,exporter.MAX_MEMBERS)
        try:
            exporter.MAX_EVENT_VOLUME_BYTES=volume_limit
            exporter.MAX_UNCOMPRESSED_BYTES=uncompressed
            exporter.MAX_EVENT_VOLUMES=volume_cap
            exporter.MAX_MEMBERS=13+volume_cap
            # 评审探针复现：3×613B（两行装不进一卷）总量 ≤ 合法上限 → 必须可导出。
            probe={'stores':[{'partition':'personal','store_id':'st-1',
                              'settings':{'status':'recording','stages':['coding']},'summary':{},
                              'events':sized_events(3,613)}]}
            package=exporter.build_export_zip(probe,public=False)
            with zipfile.ZipFile(_io.BytesIO(package)) as z:
                volumes=[n for n in z.namelist() if n.startswith('events-part-')]
            self.assertEqual(len(volumes),3)  # 一行一卷且不报卷数超限
            self.assertEqual(len(exporter.validate_import_package(package)['events']),3)
            # 最坏浪费规模化：1000×~505B（一行一卷）→ 1000 卷 ≤ 推导上限，仍可导出并可回导。
            worst={'stores':[{'partition':'personal','store_id':'st-1',
                              'settings':{'status':'recording','stages':['coding']},'summary':{},
                              'events':sized_events(1000,505)}]}
            worst_package=exporter.build_export_zip(worst,public=False)
            with zipfile.ZipFile(_io.BytesIO(worst_package)) as z:
                worst_volumes=[n for n in z.namelist() if n.startswith('events-part-')]
                for name in worst_volumes:
                    self.assertLessEqual(len(z.read(name)),volume_limit)  # 每卷不超上限
            self.assertEqual(len(worst_volumes),1000)
            self.assertLessEqual(len(worst_volumes),volume_cap)
            parsed=exporter.validate_import_package(worst_package)  # 三位数卷名（100+）可回导
            self.assertEqual(len(parsed['events']),1000)
            # 总量超上限 → 报总量错误而非卷数错误。
            over={'stores':[{'partition':'personal','store_id':'st-1',
                             'settings':{'status':'recording','stages':['coding']},'summary':{},
                             'events':sized_events(5000,900)}]}  # ≈4.5MB > 4MB
            with self.assertRaises(exporter.TracePackageError) as raised:
                exporter.build_export_zip(over,public=False)
            self.assertIn('exceeds the export size limit',str(raised.exception))
            self.assertNotIn('too many volumes',str(raised.exception))
        finally:
            (exporter.MAX_EVENT_VOLUME_BYTES,exporter.MAX_UNCOMPRESSED_BYTES,
             exporter.MAX_EVENT_VOLUMES,exporter.MAX_MEMBERS)=originals

    def test_volume_package_requires_event_volumes_manifest(self):
        # 回归（评审）：分卷形态的包必须携带 manifest.event_volumes 且与实际成员一致
        #（缺键的手工拼卷包拒绝）；旧单文件 events.jsonl 包无该键仍接受（见
        # test_event_volume_round_trip_and_legacy_single_file 的旧 manifest 用例）。
        from coscreen import research_trace_export as exporter
        snapshot=self._volume_snapshot(3)
        original=exporter.MAX_EVENT_VOLUME_BYTES
        exporter.MAX_EVENT_VOLUME_BYTES=150  # 每行约 6KB → 三卷
        try:
            package=exporter.build_export_zip(snapshot,public=False)
        finally:
            exporter.MAX_EVENT_VOLUME_BYTES=original
        with zipfile.ZipFile(_io.BytesIO(package),'r') as src:
            files={n:src.read(n) for n in src.namelist()}
            manifest=json.loads(files['manifest.json'])
            manifest.pop('event_volumes')  # 手工去掉清单的分卷包
        stripped=_io.BytesIO()
        with zipfile.ZipFile(stripped,'w',compression=zipfile.ZIP_DEFLATED) as dst:
            for name,data in files.items():
                if name=='manifest.json':
                    data=json.dumps(manifest,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()+b'\n'
                dst.writestr(name,data)
        with self.assertRaises(exporter.TracePackageError) as raised:
            exporter.validate_import_package(stripped.getvalue())
        self.assertIn('event_volumes',str(raised.exception))
        self.assertEqual(len(exporter.validate_import_package(package)['events']),3)  # 带清单的原包仍接受

    def test_volume_package_imports_through_api(self):
        # 回归（契约⑥/⑦）：分卷包经真实导出→导入两步流（preview + commit）完整
        # round-trip，响应键不变；二次 commit 全量 duplicate；旧单文件 events.jsonl
        # 包（含无 event_volumes 键的旧 manifest）走同一 API 两步流同样成功。
        from coscreen import research_trace_export as exporter
        self.assertEqual(self.save(self.link(self.state())).status_code,200)  # 本任务已有轨迹事件
        original=exporter.MAX_EVENT_VOLUME_BYTES
        exporter.MAX_EVENT_VOLUME_BYTES=600  # 超小上限强制每行一卷
        try:
            package=self.client.post(f'/api/tasks/{self.task}/trace/exports',json={'preset':'complete'},headers=self.auth())
        finally:
            exporter.MAX_EVENT_VOLUME_BYTES=original
        self.assertEqual(package.status_code,200,package.text[:200])
        with zipfile.ZipFile(_io.BytesIO(package.content)) as z:
            volumes=[n for n in z.namelist() if n.startswith('events-part-')]
            manifest=json.loads(z.read('manifest.json'))
        self.assertGreaterEqual(len(volumes),2)
        event_count=manifest['event_count']
        def two_step(base,raw):
            preview=self.client.post(f'{base}/trace/imports/preview',
                                     files={'package':('trace.zip',raw,'application/zip')},headers=self.auth())
            self.assertEqual(preview.status_code,200,preview.text[:300])
            meta=preview.json()
            for key in ('package_sha256','source_aliases','subjects'):  # 两步流响应键不变
                self.assertIn(key,meta)
            commit=self.client.post(f'{base}/trace/imports/commit',
                                    files={'package':('trace.zip',raw,'application/zip')},
                                    data={'preview_sha256':meta['package_sha256'],
                                          'actor_aliases':json.dumps({a:'Owner' for a in meta['source_aliases']}),
                                          'subject_mappings':'{}','confirm_unmapped':'true'},
                                    headers=self.auth())
            self.assertEqual(commit.status_code,200,commit.text[:300])
            return commit.json()
        first=two_step(f'/api/tasks/{self.task}',package.content)
        self.assertEqual(first['status'],'committed')
        self.assertEqual(first['event_count'],event_count)
        self.assertEqual(first['imported'],event_count)  # 首次提交全量导入
        self.assertEqual(first['duplicates'],0)
        second=two_step(f'/api/tasks/{self.task}',package.content)  # 同包二次提交：幂等 duplicate
        self.assertEqual(second['status'],'committed')
        self.assertEqual(second['imported'],0)
        self.assertEqual(second['duplicates'],event_count)
        # 旧单文件包（manifest 无 event_volumes 键、events.jsonl 单成员、哈希重算）：
        # 在另一个全新任务上走同一 API 两步流，验证旧包形态的真实导入。
        task2=self.client.post('/api/tasks',json={'name':'legacy-single-file'},headers=self.auth()).json()['task_id']
        ctx2=app.state.review_workflow.context(task2,self.users[0])
        db.upsert_articles(ctx2.owner_db,[Article(zotero_key='R1',title='First'),Article(zotero_key='R2',title='Second')],[])
        self.assertEqual(self.client.put(f'/api/tasks/{task2}/report-evidence',json={
            'expected_revision':0,'operation_id':str(uuid.uuid4()),
            'state':self.link(self.state()),'label':'Save evidence'},headers=self.auth()).status_code,200)
        pkg2=self.client.post(f'/api/tasks/{task2}/trace/exports',json={'preset':'complete'},headers=self.auth())
        self.assertEqual(pkg2.status_code,200,pkg2.text[:200])
        with zipfile.ZipFile(_io.BytesIO(pkg2.content)) as z:
            self.assertIn('events.jsonl',z.namelist())  # 小导出天然是单文件形态
            manifest2=json.loads(z.read('manifest.json'))
            files2={n:z.read(n) for n in z.namelist()}
        legacy_count=manifest2['event_count']
        manifest2.pop('event_volumes',None)  # 改写为旧 manifest 形态（无此键）
        legacy=_io.BytesIO()
        with zipfile.ZipFile(legacy,'w',compression=zipfile.ZIP_DEFLATED) as dst:
            for name,data in files2.items():
                if name=='manifest.json':
                    data=json.dumps(manifest2,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()+b'\n'
                dst.writestr(name,data)
        legacy_bytes=legacy.getvalue()
        parsed_legacy=exporter.validate_import_package(legacy_bytes)
        self.assertEqual(len(parsed_legacy['events']),legacy_count)
        legacy_result=two_step(f'/api/tasks/{task2}',legacy_bytes)
        self.assertEqual(legacy_result['imported'],legacy_count)  # 旧单文件包经 API 真实导入
        self.assertEqual(legacy_result['duplicates'],0)


class ScreenerParamGuardPins(unittest.TestCase):
    """终审 #58：全局 ``?screener=`` 跨筛选员守卫的「daily-status 属主例外
    收窄」负向红钉。

    此前唯一断言带 screener 403 的用例（本文件 test_personal_ml… 的 PUT
    /al/settings）其 403 来自端点自身 _al_actor——去掉全局守卫该用例仍过
    （代偿）。本类钉：属主带 ``?screener=<成员>`` 访问 daily-status 之外的
    GET 端点（/decisions、/articles、/export/decisions、/progress）必须
    403；唯二例外（daily-status 与 /suggestion）对属主放行；变异钉以依赖
    覆盖剥掉请求中的 screener 参数（终审 u7-deepseek-c1 mutplugin 的运行
    期等价）证明 403 完全由全局守卫承重。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        from custom_backend import auth as auth_mod
        cls.users = []
        for i in range(2):
            user = auth_mod.create_user(f'guard58-{i}@example.test',
                                        'Guard58-Test-Pass')
            cls.users.append({'user_id': user['id'],
                              'username': user['username'],
                              'token': auth_mod.create_access_token(
                                  user['id'], user['username'])})

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def auth(self, i=0):
        return {'Authorization': 'Bearer ' + self.users[i]['token']}

    def setUp(self):
        self.task = self.client.post(
            '/api/tasks', json={'name': self._testMethodName},
            headers=self.auth()).json()['task_id']
        # 建立协作关系（属主 + 成员），让 daily-status 例外路径可走到放行。
        ctx = app.state.review_workflow.context(self.task, self.users[0])
        task_collaboration.grant_role(
            ctx.collaboration_db, actor_user_id=self.users[0]['user_id'],
            user_id=self.users[1]['user_id'], role='coder', task_id=self.task)
        db.upsert_articles(ctx.owner_db, [
            Article(zotero_key='G1', title='Guard study')], [])

    def _member_param(self):
        return '?screener=' + self.users[1]['username']

    def test_owner_cannot_read_member_data_via_screener_param(self):
        member = self._member_param()
        for path in (f'/api/tasks/{self.task}/decisions{member}',
                     f'/api/tasks/{self.task}/articles{member}',
                     f'/api/tasks/{self.task}/export/decisions{member}',
                     f'/api/tasks/{self.task}/progress{member}'):
            with self.subTest(path=path):
                response = self.client.get(path, headers=self.auth())
                self.assertEqual(response.status_code, 403,
                                 f'{path} -> {response.status_code}（双盲阶段'
                                 '属主不得经 screener 参数读成员数据）')

    def test_daily_status_exception_is_the_only_get_readthrough(self):
        """唯二例外：daily-status 与 /suggestion 对属主放行（非 403）。"""
        for suffix in ('/screening/daily-status',
                       '/screening/daily-status/suggestion'):
            with self.subTest(suffix=suffix):
                response = self.client.get(
                    f'/api/tasks/{self.task}{suffix}{self._member_param()}',
                    headers=self.auth())
                self.assertNotEqual(response.status_code, 403, response.text)

    def test_mutation_guard_bypassed_pin_goes_red(self):
        """破坏性变异：依赖覆盖剥掉请求中的 screener 参数（= 删守卫的运行期
        等价，终审探针同法）→ /decisions 等从 403 变 200——上一颗钉必红。"""
        from fastapi import Request, Header
        from custom_backend.main import require_user
        from unittest import mock

        def _stripped(request: Request,
                      authorization: str | None = Header(default=None)):
            real_getlist = request.query_params.getlist

            def _getlist(key):
                values = real_getlist(key)
                if key == 'screener':
                    return [value for value in values if not value]
                return values

            with mock.patch.object(request.query_params, 'getlist', _getlist):
                return require_user(request, authorization)

        app.dependency_overrides[require_user] = _stripped
        try:
            for path in (f'/api/tasks/{self.task}/decisions',
                         f'/api/tasks/{self.task}/articles'):
                with self.subTest(path=path):
                    response = self.client.get(
                        path + self._member_param(), headers=self.auth())
                    self.assertEqual(response.status_code, 200,
                                     f'无守卫时 {path} 真的放行（= 钉必红）')
        finally:
            app.dependency_overrides.pop(require_user, None)


if __name__=='__main__': unittest.main(verbosity=2)
