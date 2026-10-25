"""2.1.3 actor isolation, relationships, undo and export on disposable data."""
import os
import sys
import json
import uuid
import hashlib
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

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def auth(self, i=0):
        return {'Authorization':'Bearer '+self.users[i]['token']}

    def setUp(self):
        self.task = self.client.post('/api/tasks',json={'name':self._testMethodName},headers=self.auth()).json()['task_id']
        self.ctx = app.state.review_workflow.context(self.task,self.users[0])
        db.upsert_articles(self.ctx.owner_db,[Article(zotero_key='R1',title='First',doi='10.test/1',raw={'source_file':'01-Wiley.ris'}),Article(zotero_key='R2',title='Second',doi='10.test/2')],[])
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
        self.assertEqual(self.client.post(self.base+'/relations',json=body,headers=self.auth()).status_code,200)
        body.update(expected_revision=1,actor_id=owner)
        r=self.client.post(self.base+'/relations',json=body,headers=self.auth());self.assertEqual(r.status_code,200,r.text)
        confirmed=r.json()['confirmed']
        self.assertEqual(len(confirmed),2)
        self.assertEqual([l['id'] for l in confirmed],['L1','L1'])  # id 与提案一致，未注入提案人 user id
        dropped=next(l for l in confirmed if l['actor_id']==coder)
        kept=next(l for l in confirmed if l['actor_id']==owner)
        self.assertEqual({dropped['a'],dropped['b']},{'R1','R2'});self.assertEqual({kept['a'],kept['b']},{'R1','R3'})
        body.update(expected_revision=2,link_id=dropped['id'],actor_id=dropped['actor_id'],action='unlink')
        r=self.client.post(self.base+'/relations',json=body,headers=self.auth());self.assertEqual(r.status_code,200,r.text)
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
        # 回归#2 静默截断：一次保存影响超过100个主体时补显式截断标记事件，不再静默丢弃。
        keys=[f'T{i:03d}' for i in range(105)]
        db.upsert_articles(self.ctx.owner_db,[Article(zotero_key=k,title=f'Trunc {k}') for k in keys],[])
        state=self.state()
        state['drafts']=[{'id':f'd{i}','key':k,'finding':f'Finding {k}'} for i,k in enumerate(keys)]
        self.assertEqual(self.save(state).status_code,200)
        r=self.client.get(f'/api/tasks/{self.task}/trace/events?limit=200&action=report_evidence_changed',headers=self.auth())
        self.assertEqual(r.status_code,200,r.text);changed=r.json()['events']
        self.assertFalse(r.json()['has_more']);self.assertEqual(len(changed),100)
        r=self.client.get(f'/api/tasks/{self.task}/trace/events?limit=10&action=report_evidence_truncated',headers=self.auth())
        self.assertEqual(r.status_code,200,r.text)
        marker=next(e for e in r.json()['events'])
        self.assertEqual(marker['after']['total_subjects'],105)
        self.assertEqual(marker['after']['truncated_subjects'],5)
        self.assertEqual(marker['stage'],'coding')

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

    def _insert_legacy_trace_events(self,rows):
        # 直接预置 2.1.3 形态的全量快照轨迹事件（绕过当前写入路径；载荷每侧须 <1MB 以内可存）。
        with closing(db._connect(self.ctx.owner_db)) as c:
            research_trace.ensure_schema(c)
            store_id=c.execute("SELECT store_id FROM trace_store WHERE singleton=1").fetchone()[0]
            for actor,subject,before,after in rows:
                c.execute("INSERT INTO trace_events (event_id,store_id,task_id,actor_id,stage,action,subject_id,"
                          "before_json,after_json,source,recorded_at,schema_version,app_version) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (str(uuid.uuid4()),store_id,self.task,actor,'coding','report_evidence_changed',subject,
                           json.dumps(before,ensure_ascii=False),json.dumps(after,ensure_ascii=False),
                           'report_evidence','2026-01-01T00:00:00Z','reviewflow.research-trace/1','2.1.3'))
            c.commit()

    def test_legacy_full_snapshot_events_compacted_and_redacted_on_export(self):
        # 回归（F16/F19 存量）：2.1.3 全量快照轨迹事件在导出前被一次性压缩与脱敏，
        # 导出不再 413；公开导出不含他人 evidence/locator 原文，locator 也不留可枚举哈希。
        exporter=str(self.users[0]['user_id']);coder=str(self.users[1]['user_id'])
        secret='LEGACY-SECRET coder evidence'
        pad={'links':[],'drafts':[{'id':f'pad{i}','key':'R2','finding':'F','quote':'x'*19500} for i in range(40)]}
        rows=[]
        for _ in range(22):
            before={'value':'Save evidence','workspace':json.loads(json.dumps(pad))}
            after={'value':'Save evidence','workspace':{**json.loads(json.dumps(pad)),'links':[
                {'id':'L1','a':'R1','b':'R2','type':'study','same':False,'evidence':secret,
                 'locator':'p.4','status':'agreed','actor_id':coder}]}}
            rows.append((exporter,'R1',before,after))
        # 单主体增量超过单条预算的另一组旧事件：迁移应降级为摘要+哈希。
        big_before={'value':'Big','workspace':{'links':[],'drafts':[{'id':f'd{i}','key':'R1','finding':'F','quote':'a'*19500} for i in range(45)]}}
        big_after={'value':'Big','workspace':{'links':[],'drafts':[{'id':f'd{i}','key':'R1','finding':'F','quote':'b'*19500} for i in range(45)]}}
        rows.append((exporter,'R1',big_before,big_after))
        self._insert_legacy_trace_events(rows)
        with closing(db._connect(self.ctx.owner_db)) as c:
            stored=c.execute("SELECT SUM(length(before_json)+length(after_json)) FROM trace_events "
                             "WHERE action='report_evidence_changed'").fetchone()[0]
        self.assertGreater(stored,32*1024*1024)  # 未迁移时导出必然超过 32MB 上限
        package=self.client.post(f'/api/tasks/{self.task}/trace/exports',json={'preset':'public'},headers=self.auth())
        self.assertEqual(package.status_code,200,package.text[:200])
        with zipfile.ZipFile(_io.BytesIO(package.content)) as z:
            exported=b''.join(z.read(name) for name in z.namelist())
            events_jsonl=z.read('events.jsonl')
        self.assertLess(len(events_jsonl),1_000_000)  # 压缩后事件数据远小于上限
        self.assertNotIn(secret.encode(),exported)
        self.assertNotIn(b'"p.4"',exported)
        self.assertNotIn(hashlib.sha256(b'p.4').hexdigest().encode(),exported)
        self.assertIn(hashlib.sha256(secret.encode()).hexdigest().encode(),exported)  # 他人 evidence 保留哈希摘要（既有契约）
        self.assertNotIn(b'"workspace"',events_jsonl)  # 全量快照已改写为增量/摘要
        self.assertIn(b'"workspace_sha256"',events_jsonl)  # 超预算主体降级为摘要+哈希（note 键按公开导出私有键规则移除）
        self.assertIn(b'"findings":45',events_jsonl)
        with closing(db._connect(self.ctx.owner_db)) as c:
            remaining=c.execute("SELECT COUNT(*) FROM trace_events WHERE action='report_evidence_changed' "
                                "AND (before_json LIKE '%\"workspace\"%' OR after_json LIKE '%\"workspace\"%')").fetchone()[0]
            marker=c.execute("SELECT 1 FROM report_evidence_trace_meta WHERE singleton=1").fetchone()
            digest_note=c.execute("SELECT COUNT(*) FROM trace_events WHERE before_json LIKE '%Legacy full workspace snapshot compacted%'").fetchone()[0]
        self.assertEqual(remaining,0)
        self.assertTrue(marker)
        self.assertGreaterEqual(digest_note,1)  # 库内超预算主体保留迁移说明
        # 迁移幂等：第二次导出同样成功。
        self.assertEqual(self.client.post(f'/api/tasks/{self.task}/trace/exports',json={'preset':'public'},headers=self.auth()).status_code,200)


if __name__=='__main__': unittest.main(verbosity=2)
