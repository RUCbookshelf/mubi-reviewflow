"""2.1.3 actor isolation, relationships, undo and export on disposable data."""
import os
import sys
import json
import uuid
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
from custom_backend.report_evidence_api import DEFAULT
from coscreen import db, task_collaboration, review_workflow
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


if __name__=='__main__': unittest.main(verbosity=2)
