"""Read-only projection tests; all records live in an in-memory SQLite fixture."""
from pathlib import Path
import sys, sqlite3, json, unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from coscreen import review_imports as store
from coscreen.models import Article
from custom_backend.import_review_presentation import review_projection
class ProjectionTests(unittest.TestCase):
 def setUp(self):
  self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
  self.conn.executescript(store._SCHEMA)
 def tearDown(self):self.conn.close()
 def record(self,key,*,batch='b',state='staged',target=None,title='Example'):
  article=json.dumps(Article(zotero_key=key,title=title).to_dict())
  self.conn.execute('INSERT INTO workflow_source_records(id,source_id,digest,row_index,filename,original_json,article_json,incoming_key,staged_key,canonical_key,target_record_id,outcome,state,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(key,'s',key,1,'fixture.ris','{}',article,key,key,key if state=='active' else None,target,'new',state,'2026-10-12'))
  self.conn.execute('INSERT INTO workflow_import_members VALUES(?,?,0)',(batch,key))
 def test_attention_after_large_completed_prefix(self):
  for i in range(321):self.record('active'+str(i),state='active')
  for i in range(37):self.record('pending'+str(i))
  before=self.conn.total_changes;result=review_projection(self.conn,'b')
  self.assertEqual(result['review_counts'],{'attention':37,'completed':321,'ready':37,'needs_review':0})
  self.assertTrue(all(r['state']=='staged' for r in result['attention'][:20]))
  self.assertEqual(before,self.conn.total_changes)
 def test_priority_duplicate_metadata_ready(self):
  for k in ['ready','metadata','duplicate']:self.record(k)
  self.conn.execute('INSERT INTO workflow_import_metadata(record_id,canonical_key,before_json,incoming_json) VALUES(?,?,?,?)',('metadata','a','{}','{}'))
  self.conn.execute('INSERT INTO workflow_import_candidates(id,signature,kind,status,created_at,updated_at) VALUES(?,?,?,?,?,?)',('c','sig','title','pending','now','now'))
  self.conn.execute('INSERT INTO workflow_import_candidate_members VALUES(?,?,?)',('c','record','duplicate'))
  r=review_projection(self.conn,'b');self.assertEqual([x['id'] for x in r['attention']],['duplicate','metadata','ready']);self.assertEqual(r['review_counts']['ready'],1)
 def test_external_staged_dependency_not_ready(self):
  self.record('root',batch='other');self.record('alias',target='root')
  r=review_projection(self.conn,'b');self.assertEqual(r['classification']['alias']['attention_reason'],'dependency');self.assertEqual(r['classification']['alias']['related_batch_id'],'other');self.assertEqual(r['review_counts']['ready'],0)
 def test_incoming_alias_metadata_difference_not_promised_ready(self):
  self.record('root');self.record('alias',target='root',title='Different incoming title')
  r=review_projection(self.conn,'b');self.assertEqual(r['review_counts']['ready'],1);self.assertEqual(r['classification']['alias']['attention_reason'],'dependency')
 def test_active_metadata_proposal_still_needs_review(self):
  self.record('active',state='active')
  self.conn.execute('INSERT INTO workflow_import_metadata(record_id,canonical_key,before_json,incoming_json) VALUES(?,?,?,?)',('active','active','{}','{}'))
  r=review_projection(self.conn,'b');self.assertEqual(r['review_counts']['completed'],0);self.assertEqual(r['review_counts']['needs_review'],1)
 def test_cycle_is_not_reported_ready(self):
  self.record('one',target='two');self.record('two',target='one')
  self.assertEqual(review_projection(self.conn,'b')['review_counts']['ready'],0)
if __name__=='__main__':unittest.main(verbosity=2)
