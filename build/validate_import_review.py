"""Presentation API regression checks using only disposable acceptance data."""
from pathlib import Path
import runpy, sqlite3, json
scope=runpy.run_path(str(Path(__file__).with_name('validate_team_prisma.py')))
client,call,base,data=scope['client'],scope['call'],scope['base'],scope['DATA']
client.headers['Authorization']='Bearer '+scope['owner']['token']
# Warm existing read-context migrations before checking read-only behavior.
call('GET',base+'/workflow-settings')
def snapshot():
    result={}
    for path in data.rglob('*.db'):
        with sqlite3.connect(path) as conn:
            result[str(path)]=list(conn.iterdump())
    return result
from coscreen.models import Article
# A large fixture puts outstanding rows after a long completed prefix.
ctx=scope['ctx'];bid='presentation-large-fixture'
with sqlite3.connect(ctx.owner_db) as conn:
    conn.row_factory=sqlite3.Row
    source=conn.execute('SELECT id FROM workflow_sources LIMIT 1').fetchone()[0]
    conn.execute('INSERT INTO workflow_import_batches VALUES(?,?,?,?,?,?,?)',(bid,str(scope['team']['owner_user_id']),'fixture-history','batch',1,'partially_active','2026-10-12'))
    conn.execute('INSERT INTO workflow_import_files VALUES(?,?,?,?,?)',(bid,0,'Fictional-large.ris','fixture-file',source))
    for i in range(359):
        key='fixture-'+str(i);state='active' if i<321 else 'staged'
        article=Article(zotero_key=key,title='Fictional article '+str(i),authors='Example Reviewer',year=2026,source_format='ris').to_dict()
        conn.execute('INSERT INTO workflow_source_records(id,source_id,digest,row_index,filename,original_json,article_json,incoming_key,staged_key,canonical_key,target_record_id,outcome,state,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(key,source,'fixture-file',i,'Fictional-large.ris','{}',json.dumps(article),key,key,'one' if state=='active' or i==358 else None,None,'new',state,'2026-10-12'))
        conn.execute('INSERT INTO workflow_import_members VALUES(?,?,0)',(bid,key))
    current=dict(conn.execute("SELECT * FROM articles WHERE zotero_key='one'").fetchone())
    conn.execute('INSERT INTO workflow_import_metadata(record_id,canonical_key,before_json,incoming_json) VALUES(?,?,?,?)',('fixture-358','one',json.dumps(current),json.dumps({'title':'Incoming fictional title'})))
    for i in range(28):
        cid='fixture-group-'+str(i);status='not_duplicate' if i<25 else 'pending'
        conn.execute('INSERT INTO workflow_import_candidates(id,signature,kind,status,created_at,updated_at) VALUES(?,?,?,?,?,?)',(cid,cid,'title',status,'2026-10-12' if i<25 else '2026-10-13','2026-10-12'))
        conn.execute('INSERT INTO workflow_import_candidate_members VALUES(?,?,?)',(cid,'record','fixture-'+str(i if i<25 else 358)))
# Registering an outsider is allowed only in this temporary validation instance.
outsider=call('POST','/api/auth/register',json={'username':'presentation.outsider','password':'DisposableValidation2026!'})
before=snapshot()
assert str(ctx.owner_db) in before
assert Path(ctx.owner_db).resolve().is_relative_to(data.resolve())
summary=call('GET',base+'/import-review-summary')
assert summary['can_manage'] is True
batches=call('GET',base+'/import-batches?limit=200')['batches']
checked=0
for batch in batches:
    path=base+'/import-batches/'+batch['batch_id']
    all_rows=call('GET',path+'?view=all&limit=200')
    attention=call('GET',path+'?view=attention&limit=200')
    completed=call('GET',path+'?view=completed&limit=200')
    counts=all_rows['review_counts']
    assert counts['attention']+counts['completed']==all_rows['raw_source_records']
    assert attention['view_total']==counts['attention']
    assert completed['view_total']==counts['completed']
    assert [r['review_priority'] for r in attention['records']]==sorted(r['review_priority'] for r in attention['records'])
    assert all(r['attention_reason']=='completed' for r in completed['records'])
    assert all(r['attention_reason']!='completed' for r in attention['records'])
    assert counts['ready']+counts['needs_review']==counts['attention']
    assert client.get(path+'?view=invalid').status_code==422
    checked+=1
assert snapshot()==before, 'Presentation reads changed saved data'
# Default view remains compatible; attention pagination ignores completed prefix.
path=base+'/import-batches/'+bid
assert all(r['state']=='active' for r in call('GET',path+'?limit=20')['records'])
attention=call('GET',path+'?view=attention&limit=20')
assert attention['review_counts']=={'attention':38,'completed':321,'ready':37,'needs_review':1},attention['review_counts']
assert attention['records'][0]['record_id']=='fixture-358'
assert attention['records'][0]['metadata']['current']['title']==current['title']
assert attention['records'][0]['metadata']['stale'] is False
assert len(call('GET',path+'?view=attention&limit=20&offset=20')['records'])==18
assert len(call('GET',path+'?view=completed&limit=20&offset=320')['records'])==1
pending_groups=call('GET',path+'/candidates?view=attention&limit=20')
assert pending_groups['total']==3 and all(c['status']=='pending' for c in pending_groups['candidates'])
finished_groups=call('GET',path+'/candidates?view=completed&limit=20')
assert finished_groups['total']==25 and finished_groups['next_offset']==20
assert len(call('GET',path+'/candidates?view=completed&limit=20&offset=20')['candidates'])==5
from coscreen import review_imports as store
with sqlite3.connect(ctx.owner_db) as conn:
    conn.row_factory=sqlite3.Row
    assert call('GET',path+'/candidates?limit=20')['candidates']==store.candidate_rows(conn,bid,limit=20)
client.headers['Authorization']='Bearer '+scope['member']['token']
assert call('GET',path+'?view=attention&limit=1')['can_manage'] is False
client.headers['Authorization']='Bearer '+outsider['token']
assert client.get(path).status_code==403
assert client.get(base+'/import-review-summary').status_code==403
client.headers['Authorization']='Bearer '+scope['owner']['token']
assert snapshot()==before, 'Presentation reads changed saved data'
# Actual activation is exercised ONLY on fictional fixture records.
# Unreviewed metadata stays staged; the displayed ready count matches links.
with sqlite3.connect(ctx.owner_db) as conn:
    decisions_before=list(conn.execute('SELECT * FROM decisions'))
    coding_before=list(conn.execute('SELECT * FROM coding_values'))
version=call('GET',base+'/workflow-settings')['version']
activated=call('POST',path+'/activate',json={'expected_policy_version':version})
assert activated['activated_source_records']==attention['review_counts']['ready']==37,activated
assert activated['pending_records']==1
version=call('GET',base+'/workflow-settings')['version']
again=call('POST',path+'/activate',json={'expected_policy_version':version})
assert again['activated_new']==again['activated_source_records']==0,again
with sqlite3.connect(ctx.owner_db) as conn:
    assert list(conn.execute('SELECT * FROM decisions'))==decisions_before
    assert list(conn.execute('SELECT * FROM coding_values'))==coding_before
print(json.dumps({'presentation_batches_checked':checked,'large_batch_records':359,'read_only_snapshot':'unchanged','activation_ready_count':37,'repeat_activation':'zero additions','research_decisions_and_coding':'unchanged','data':str(data)}))
