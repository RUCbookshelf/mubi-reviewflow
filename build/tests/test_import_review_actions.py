"""Integration regressions using only disposable catalogues and authenticated APIs."""
from pathlib import Path
import sys, os, tempfile, unittest, json, time, gc
from contextlib import closing
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
_fixture=tempfile.TemporaryDirectory(prefix='rf_review_test_')
os.environ['COBOOKSHELF_DATA_DIR']=_fixture.name
from fastapi.testclient import TestClient
from custom_backend.main import app
from coscreen import db, review_imports as store, review_workflow as wf
from coscreen.models import Article

class ReviewActionsTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.client=TestClient(app);cls.client.__enter__()
  result=cls.client.post('/api/auth/register',json={'username':'fixture@example.test','password':'Review-Test-2026'}).json()
  cls.user={'user_id':result['user']['id'],'username':result['user']['username']}
  cls.client.headers['Authorization']='Bearer '+result['token']
 @classmethod
 def tearDownClass(cls):cls.client.__exit__(None,None,None);gc.collect();_fixture.cleanup()
 def setUp(self):
  self.task=self.client.post('/api/tasks',json={'name':self._testMethodName}).json()['task_id']
  self.base=f'/api/tasks/{self.task}/import-batches/b'
  ctx=app.state.review_workflow.context(self.task,self.user);self.db=ctx.owner_db
  db.init_db(self.db);store.migrate(self.db)
  with closing(wf.connect(self.db)) as c, c:
   c.execute('INSERT INTO workflow_sources VALUES(?,?,?,?,?)',('s','01-Wiley.ris','01-wiley.ris',1,'now'))
   c.execute('INSERT INTO workflow_import_batches VALUES(?,?,?,?,?,?,?)',('b',str(self.user['user_id']),'h','batch',1,'staged','now'))
   c.execute('INSERT INTO workflow_import_files VALUES(?,?,?,?,?)',('b',0,'01-Wiley.ris','d','s'))
 def record(self,c,key,doi='10.test/same',canonical=None):
  a=Article(zotero_key=key,title='Synthetic '+key,doi=doi).to_dict()
  c.execute('INSERT INTO workflow_source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(key,'s',key,1,'01-Wiley.ris',json.dumps(a),json.dumps(a),key,key,canonical,None,'new','staged','now'))
  c.execute('INSERT INTO workflow_import_members VALUES(?,?,?)',('b',key,0))
 def group(self,c,cid,kind='title',doi='10.test/same'):
  for key in (cid+'a',cid+'b'):self.record(c,key,doi)
  c.execute('INSERT INTO workflow_import_candidates(id,signature,kind,status,created_at,updated_at) VALUES(?,?,?,?,?,?)',(cid,cid,kind,'pending','now','now'))
  for key in (cid+'a',cid+'b'):c.execute('INSERT INTO workflow_import_candidate_members VALUES(?,?,?)',(cid,'record',key))
 def version(self):return self.client.get(self.base).json().get('policy_version',self.readversion())
 def readversion(self):
  with closing(wf.connect(self.db)) as c, c:return c.execute('SELECT version FROM workflow_state').fetchone()[0]
 def post(self,path,body):return self.client.post(self.base+path,json={'expected_policy_version':self.readversion(),**body})
 def seed(self,n=2):
  with closing(wf.connect(self.db)) as c, c:
   for i in range(n):self.group(c,str(i))
 def test_single_delta_and_undo(self):
  self.seed();v=self.readversion();r=self.post('/candidates/0',{'status':'duplicate','keeper_record_id':'0a'})
  self.assertEqual(r.status_code,200,r.text);d=r.json();self.assertEqual(d['policy_version'],v+1);self.assertTrue(d['undo']['available'])
  self.assertEqual(len(d['review_updates']['records']),2);self.assertEqual(len(d['review_updates']['candidates']),1)
  r=self.post('/undo',{});self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['review_updates']['candidates'][0]['status'],'pending')
 def test_atomic_bulk_and_one_undo(self):
  self.seed(3);r=self.post('/bulk-review',{'actions':[{'kind':'candidate','id':str(i),'keeper_record_id':str(i)+'a'} for i in range(3)]})
  self.assertEqual(r.status_code,200,r.text);self.assertEqual(len(r.json()['review_updates']['records']),6)
  self.assertEqual(r.json()['undo']['kind'],'bulk');self.assertEqual(self.post('/undo',{}).status_code,200)
  groups=self.client.get(self.base+'/candidates').json()['candidates'];self.assertTrue(all(g['status']=='pending' for g in groups))
 def test_bulk_rollback_if_second_keeper_invalid(self):
  self.seed();v=self.readversion();r=self.post('/bulk-review',{'actions':[{'kind':'candidate','id':'0','keeper_record_id':'0a'},{'kind':'candidate','id':'1','keeper_record_id':'wrong'}]})
  self.assertEqual(r.status_code,409,r.text);self.assertEqual(self.readversion(),v)
  with closing(wf.connect(self.db)) as c, c:self.assertEqual(c.execute("SELECT COUNT(*) FROM workflow_import_candidates WHERE status='duplicate'").fetchone()[0],0)
 def test_identity_conflict_excluded(self):
  with closing(wf.connect(self.db)) as c, c:self.group(c,'conflict','identity_conflict')
  r=self.post('/bulk-review',{'actions':[{'kind':'candidate','id':'conflict','keeper_record_id':'conflicta'}]});self.assertEqual(r.status_code,409)
 def test_title_only_not_bulk_eligible(self):
  with closing(wf.connect(self.db)) as c, c:self.group(c,'title',doi='')
  self.assertEqual(self.post('/bulk-review',{'actions':[{'kind':'candidate','id':'title','keeper_record_id':'titlea'}]}).status_code,409)
 def test_invalid_duplicate_actions_and_empty_rejected(self):
  self.seed();action={'kind':'candidate','id':'0','keeper_record_id':'0a'}
  self.assertEqual(self.post('/bulk-review',{'actions':[action,action]}).status_code,409)
  self.assertEqual(self.post('/bulk-review',{'actions':[]}).status_code,422)
 def test_stale_version_rejected(self):
  self.seed();v=self.readversion();self.post('/candidates/0',{'status':'not_duplicate'})
  r=self.client.post(self.base+'/candidates/1',json={'status':'not_duplicate','expected_policy_version':v});self.assertEqual(r.status_code,409)
 def test_activation_invalidates_undo(self):
  self.seed(1);self.post('/candidates/0',{'status':'not_duplicate'})
  r=self.post('/activate',{});self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()['undo']['available']);self.assertEqual(self.post('/undo',{}).status_code,409)
 def test_metadata_bulk_and_undo(self):
  db.upsert_articles(self.db,[Article(zotero_key='active',title='Existing',doi='10.test/existing')],[])
  with closing(wf.connect(self.db)) as c, c:
   for i in range(2):
    self.record(c,'m'+str(i),canonical='active');store.proposal(c,'m'+str(i),'active')
  r=self.post('/bulk-review',{'actions':[{'kind':'metadata','id':'m0'},{'kind':'metadata','id':'m1'}]});self.assertEqual(r.status_code,200,r.text)
  self.assertTrue(all(x['metadata']['action']=='keep_existing' for x in r.json()['review_updates']['records']))
  r=self.post('/undo',{});self.assertEqual(r.status_code,200,r.text);self.assertTrue(all(x['metadata']['action'] is None for x in r.json()['review_updates']['records']))
 def test_accepting_metadata_updates_related_stale_proposals(self):
  db.upsert_articles(self.db,[Article(zotero_key='active',title='Existing',doi='10.test/existing')],[])
  with closing(wf.connect(self.db)) as c, c:
   for i in range(2):
    self.record(c,'m'+str(i),canonical='active');store.proposal(c,'m'+str(i),'active')
  r=self.post('/metadata/m0',{'action':'accept_incoming'});self.assertEqual(r.status_code,200,r.text)
  rows=r.json()['review_updates']['records'];self.assertEqual(len(rows),2);self.assertTrue(next(x for x in rows if x['record_id']=='m1')['metadata']['stale'])
  self.assertEqual(self.post('/metadata/m1',{'action':'accept_incoming'}).status_code,409)
 def test_unauthenticated_write_rejected(self):
  self.seed();r=self.client.post(self.base+'/candidates/0',headers={'Authorization':''},json={'status':'not_duplicate'});self.assertEqual(r.status_code,401)
 def test_large_queue_single_save_benchmark(self):
  self.seed(600);timings=[]
  for i in range(10):
   start=time.perf_counter();r=self.post('/candidates/'+str(i),{'status':'not_duplicate'});timings.append((time.perf_counter()-start)*1000)
   self.assertEqual(r.status_code,200,r.text);self.assertEqual(len(r.json()['review_updates']['records']),2)
  print('BENCHMARK 1200 records / 600 groups, 10 saves ms:',[round(x,1) for x in timings])
  self.assertLess(max(timings),2000)
if __name__=='__main__':unittest.main(verbosity=2)
