"""Disposable acceptance checks; never uses an installed or research data directory."""
from __future__ import annotations
import os
import sys
import tempfile
import json
import asyncio
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'build'))
DATA=Path(tempfile.mkdtemp(prefix='reviewflow-team-prisma-validation-'))
os.environ['COBOOKSHELF_DATA_DIR']=str(DATA)
os.environ['REVIEWFLOW_MODE']='server'
os.environ.pop('REVIEWFLOW_RUNTIME_OVERLAY',None)
from launcher import build_app
if os.environ.get('REVIEWFLOW_VALIDATION_DEPENDENCIES'):
    sys.path.append(os.environ['REVIEWFLOW_VALIDATION_DEPENDENCIES'])
import httpx
from coscreen import review_workflow, import_sources
from coscreen.stats.prisma_form import PrismaFormData, IdentifiedRow, ReasonRow, validate, expected_auto

class Client:
    def __init__(self, app):
        self.app,self.headers=app,{}
    def request(self,method,path,**kwargs):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://localhost') as client:
                return await client.request(method,path,headers=self.headers,**kwargs)
        return asyncio.run(run())
    def get(self,path,**kwargs): return self.request('GET',path,**kwargs)
    def post(self,path,**kwargs): return self.request('POST',path,**kwargs)
client=Client(build_app())
def call(method,path,**kwargs):
    response=client.request(method,path,**kwargs)
    assert response.status_code<400,(method,path,response.status_code,response.text)
    return response.json()
owner=call('POST','/api/auth/register',json={'username':'preview.owner','password':'DisposableValidation2026!'})
client.headers['Authorization']='Bearer '+owner['token']
task=call('POST','/api/tasks',json={'name':'Source validation'})
tid=task['task_id'];base='/api/tasks/'+tid

def ris(key,title,database='',doi=''):
    return f'TY  - JOUR\nID  - {key}\nTI  - {title}\nAU  - Reviewer, Example\nPY  - 2024\n'+(f'DB  - {database}\n' if database else '')+(f'DO  - {doi}\n' if doi else '')+'ER  - \n'


first=ris('one','Language acquisition in context','WRONG DATABASE','10.1234/one')+ris('two','Clinical trial reporting outcomes','Another wrong database','10.1234/two')
call('POST',base+'/import',files={'file':('01-Wiley.ris',first.encode(),'application/x-research-info-systems')})
settings=call('GET',base+'/workflow-settings')
assert {s['name'] for s in settings['sources']}=={'01-Wiley.ris'},settings
call('POST',base+'/import',files={'file':('01-Wiley.ris',first.encode(),'application/x-research-info-systems')})
assert len(call('GET',base+'/workflow-settings')['sources'])==1
for key in ('one','two'):call('POST',base+'/decisions',json={'zotero_key':key,'decision':'maybe'})
assert len(call('GET',base+'/work-queue/fulltext')['queue'])==2
version=call('GET',base+'/workflow-settings')['version']
for number in (2,3,4,5):
    round=call('POST',base+'/screening-rounds',json={'action':'start','expected_policy_version':version})
    assert round['screening_round']['number']==number,round
    assert set(round['screening_round']['pending_keys'])=={'one','two'}
    assert not call('GET',base+'/work-queue/fulltext')['queue']
    refused=client.post(base+'/stage2/decisions',json={'zotero_key':'one','decision':'include'})
    assert refused.status_code==409,refused.text
    for key in ('one','two'):call('POST',base+'/decisions',json={'zotero_key':key,'decision':'maybe'})
    queue=call('GET',base+'/work-queue/screen?order=random')
    assert queue['screening_round']['pending_keys']==[],queue
    assert queue['scope_progress']['completed']==2
call('POST',base+'/screening-rounds',json={'action':'finish','expected_policy_version':version})
assert len(call('GET',base+'/work-queue/fulltext')['queue'])==2
saved=call('GET',base+'/decisions')['decisions']
assert all(row['decision']=='maybe' for row in saved)
assert client.post(base+'/screening-rounds',json={'action':'start','expected_policy_version':version-1}).status_code==409

# Undo restores the round's pending item, rather than treating it as reviewed.
call('POST',base+'/screening-rounds',json={'action':'start','expected_policy_version':version})
call('POST',base+'/decisions',json={'zotero_key':'one','decision':'include'})
undo=call('GET',base+'/undo/screen')
entry=undo.get('entry',undo.get('latest'))
assert entry,undo
call('POST',base+'/undo/screen',json={'entry_id':entry['entry_id'],'expected_policy_version':version})
assert 'one' in call('GET',base+'/work-queue/screen')['screening_round']['pending_keys']
call('POST',base+'/screening-rounds',json={'action':'finish','expected_policy_version':version})

member=call('POST','/api/auth/register',json={'username':'preview.member','password':'DisposableValidation2026!'})
call('POST',base+'/collaboration/invite',json={'username':'preview.member','role':'coder'})
client.headers['Authorization']='Bearer '+member['token']
for key in ('one','two'):call('POST',base+'/decisions',json={'zotero_key':key,'decision':'maybe'})
mversion=call('GET',base+'/workflow-settings')['version']
call('POST',base+'/screening-rounds',json={'action':'start','expected_policy_version':mversion})
assert not call('GET',base+'/work-queue/fulltext')['queue']
client.headers['Authorization']='Bearer '+owner['token']
assert len(call('GET',base+'/work-queue/fulltext')['queue'])==2
assert call('GET',base+'/work-queue/screen')['screening_round'] is None
client.headers['Authorization']='Bearer '+member['token']
call('POST',base+'/screening-rounds',json={'action':'finish','expected_policy_version':mversion})
client.headers['Authorization']='Bearer '+owner['token']
print('Team reviewer independence: passed')
# Re-run filename migration from a simulated old metadata-derived source.
from coscreen import tasks, review_workflow as wf
from contextlib import closing
import sqlite3
info=tasks.find_task(tid,DATA)
db=info.dir/(tasks.slugify_screener('preview.owner')+'.db')
with closing(wf.connect(db)) as conn,conn:
    before=[tuple(r) for r in conn.execute('SELECT * FROM decisions ORDER BY zotero_key')]
    sid=conn.execute("SELECT id FROM workflow_sources WHERE name='01-Wiley.ris'").fetchone()[0]
    conn.execute("UPDATE workflow_sources SET name='Wrong metadata source',normalized_name='wrong metadata source',active=0 WHERE id=?",(sid,))
    conn.execute("UPDATE workflow_source_details SET identity='database:wrong',identification='metadata' WHERE source_id=?",(sid,))
    conn.execute('DELETE FROM workflow_migrations WHERE version=5')
import_sources.migrate(db)
import_sources.migrate(db)
with closing(wf.connect(db)) as conn:
    assert before==[tuple(r) for r in conn.execute('SELECT * FROM decisions ORDER BY zotero_key')]
    names=[r[0] for r in conn.execute("SELECT name FROM workflow_sources WHERE id IN (SELECT source_id FROM workflow_source_records)")]
    assert names==['01-Wiley.ris'],names
    new=conn.execute("SELECT id,active FROM workflow_sources WHERE name='01-Wiley.ris'").fetchone()
    assert new['active']==0
with closing(wf.connect(db)) as conn,conn:
    conn.execute('UPDATE workflow_sources SET active=1 WHERE id=?',(new['id'],))
    conn.execute('DELETE FROM workflow_screen_rounds')
print('Undo and migration preservation/idempotence/source pause: passed')
print('Filename recognition/reimport; rounds 2–5; hold/release; backend bypass rejection; policy conflict: passed')
print('PREVIEW_DATA='+str(DATA))
print('PREVIEW_TASK='+tid)
Path(os.environ['TEMP']).joinpath('reviewflow-rounds-preview.json').write_text(json.dumps({'data':str(DATA),'task':tid,'root':str(ROOT)}),encoding='utf-8')
