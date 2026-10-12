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

duplicate_task=call('POST','/api/tasks',json={'name':'Duplicate count validation'})
dupbase='/api/tasks/'+duplicate_task['task_id']
dupris=ris('d1','Same record from two searches','WOS','10.1234/same')+ris('d2','Same record from two searches','Scopus','10.1234/same')+ris('d3','Completely different research topic','Scopus','10.1234/unique')
call('POST',dupbase+'/import',files={'file':('duplicates.ris',dupris.encode(),'application/x-research-info-systems')})
ds=call('GET',dupbase+'/workflow-settings');
for source in ds['sources']:
    call('PUT',dupbase+'/sources/'+source['id'],json={'name':source['name'],'source_type':'database','expected_version':ds['version']});ds=call('GET',dupbase+'/workflow-settings')
dupmetrics=call('GET',dupbase+'/prisma-metrics')
assert dupmetrics['fields']['duplicates_removed']==1,dupmetrics
assert sum(r['n'] for r in dupmetrics['fields']['identified_rows_db'])==3
assert sum(s['linked_count'] for s in dupmetrics['sources'])==2 # one file source contains two canonical articles

first=ris('one','Language acquisition in context','WOS','10.1234/one')+ris('two','Clinical trial reporting outcomes','Scopus','10.1234/two')
result=call('POST',base+'/import',files={'file':('mixed.ris',first.encode(),'application/x-research-info-systems')})
settings=call('GET',base+'/workflow-settings')
names={s['name'] for s in settings['sources']}
assert names=={'mixed.ris'},names
for source in settings['sources']:
    call('PUT',base+'/sources/'+source['id'],json={'name':source['name'],'source_type':'database','expected_version':settings['version']});settings=call('GET',base+'/workflow-settings')
metrics=call('GET',base+'/prisma-metrics')
assert sum(r['n'] for r in metrics['fields']['identified_rows_db'])==2
assert metrics['fields']['duplicates_removed']==0
assert metrics['fields']['reports_not_retrieved_db'] is None
call('POST',base+'/import',files={'file':('mixed.ris',first.encode(),'application/x-research-info-systems')})
assert sum(r['n'] for r in call('GET',base+'/prisma-metrics')['fields']['identified_rows_db'])==2
call('POST',base+'/decisions',json={'zotero_key':'one','decision':'maybe'})
queue=call('GET',base+'/work-queue/fulltext')
assert 'one' in [a['zotero_key'] for a in queue['queue']],queue
call('POST',base+'/import',files={'file':('unknown.ris',ris('three','Forest biodiversity restoration').encode(),'application/x-research-info-systems')})
assert call('GET',base+'/prisma-metrics')['pending']['staged_records']>=1
settings=call('GET',base+'/workflow-settings')
assert any(s['identification']=='file' for s in settings['sources'])
assert not any(s['reserved'] for s in settings['sources'])
unknown=next(s for s in settings['sources'] if s['identification']=='file')
call('PUT',base+'/sources/'+unknown['id'],json={'name':unknown['name'],'source_type':'website','active':True,'expected_version':settings['version']})
call('POST',base+'/import',files={'file':('unknown.ris',ris('three','Forest biodiversity restoration').encode(),'application/x-research-info-systems')})
confirmed=next(s for s in call('GET',base+'/workflow-settings')['sources'] if s['id']==unknown['id'])
assert confirmed['identification']=='confirmed' and confirmed['source_type']=='website' and confirmed['record_count']==1
member=call('POST','/api/auth/register',json={'username':'preview.member','password':'DisposableValidation2026!'})
call('POST',base+'/collaboration/invite',json={'username':'preview.member','role':'coder'})
team=call('GET',base+'/team-progress')
assert any(m['roles']==['coder'] for m in team['members'])
client.headers['Authorization']='Bearer '+member['token']
team=call('GET',base+'/team-progress');assert team['grants']==[]
assert any(m['roles']==['coder'] for m in team['members'])
assert client.post(base+'/sources',json={'name':'Forbidden'}).status_code==403
assert client.get(base+'/prisma-metrics?mode=team').status_code==403
client.headers['Authorization']='Bearer '+owner['token']
assert call('GET',base+'/prisma-metrics?mode=team')['pending']['team_unresolved']>=1
export=client.get(base+'/prisma-metrics/records');assert export.status_code==200 and 'mixed.ris' in export.text

# Adjudication is imported only when reviewer identities and live values match.
client.headers['Authorization']='Bearer '+member['token']
call('POST',base+'/decisions',json={'zotero_key':'one','decision':'exclude','reason':'Outside scope'})
client.headers['Authorization']='Bearer '+owner['token']
runtime=client.app.state.review_workflow;ctx=runtime.context(tid,{'user_id':team['owner_user_id'],'username':'preview.owner'})
import pandas as pd
from coscreen.screening_arbitration import append_decision_arbitration, merge_decision_packages
frames={
    'preview.owner':pd.DataFrame([{'zotero_key':'one','decision':'maybe','exclusion_reason':'','notes':''}]),
    'preview.member':pd.DataFrame([{'zotero_key':'one','decision':'exclude','exclusion_reason':'Outside scope','notes':''}])}
append_decision_arbitration(ctx.collaboration_db,stage='stage1',merged=merge_decision_packages(frames),resolutions=[{'zotero_key':'one','decision':'include'}],arbitrator_id=team['owner_user_id'])
team_metrics=call('GET',base+'/prisma-metrics?mode=team')
assert team_metrics['fields']['reports_sought_db']==1,team_metrics
call('POST',base+'/decisions',json={'zotero_key':'one','decision':'exclude','reason':'Outside scope'})
client.headers['Authorization']='Bearer '+member['token']
call('POST',base+'/decisions',json={'zotero_key':'one','decision':'maybe'})
client.headers['Authorization']='Bearer '+owner['token']
assert call('GET',base+'/prisma-metrics?mode=team')['fields']['reports_sought_db']==0

# Legacy recovery is additive and idempotent, retaining decisions and article bytes.
from coscreen import db
from coscreen.models import Article
legacy=DATA/'legacy.db';db.init_db(legacy)
db.upsert_articles(legacy,[Article(zotero_key='legacy',title='Legacy study',raw={'name_of_database':['WOS'],'source_file':'old.ris'})],[])
db.save_decision(legacy,'legacy','include')
review_workflow.migrate(legacy)
with review_workflow.connect(legacy) as conn:
    conn.execute("INSERT INTO coding_dimensions VALUES(1,'Sample size','numeric','[]',0,'','','',1,'2026-01-01')")
    conn.execute("INSERT INTO coding_values VALUES(1,'legacy','42','2026-01-01')")
    conn.execute("INSERT INTO coding_notes VALUES(1,1,'legacy','Evidence on page 2','2026-01-01','2026-01-01')")
    conn.execute("INSERT INTO coding_note_images VALUES(1,1,'fake-evidence.png',?,'','pending','2026-01-01')",('a'*64,))
    before={t:[tuple(r) for r in conn.execute('SELECT * FROM '+t)] for t in ['articles','decisions','coding_values','coding_notes','coding_note_images']}
    policy=review_workflow.defaults();policy['screen'].update(mode='selected',source_ids=[review_workflow.UNASSIGNED])
    conn.execute('UPDATE workflow_state SET stages_json=? WHERE singleton=1',(json.dumps(policy),))
import_sources.migrate(legacy);version=review_workflow.settings(legacy)['version'];import_sources.migrate(legacy)
assert review_workflow.settings(legacy)['version']==version
assert legacy.with_name('legacy.db.pre-source-v4.bak').is_file()
assert {s['name'] for s in review_workflow.sources(legacy)}=={'old.ris'}
assert review_workflow.UNASSIGNED not in review_workflow.settings(legacy)['stages']['screen']['source_ids']
with review_workflow.connect(legacy) as conn:
    assert before=={t:[tuple(r) for r in conn.execute('SELECT * FROM '+t)] for t in before}
assert import_sources.detect({'raw':{'database_provider':'Elsevier'}},'export.ris','x')['source_type'] is None
assert import_sources.detect({'raw':{}},'2026_WOS.ris','x')['identification']=='file'
manual_legacy=DATA/'manual-legacy.db';db.init_db(manual_legacy)
db.upsert_articles(manual_legacy,[Article(zotero_key='old',title='Old source record',raw={'name_of_database':['WOS']})],[])
review_workflow.migrate(manual_legacy)
with review_workflow.connect(manual_legacy) as conn:
    conn.execute("INSERT INTO workflow_sources VALUES('legacy-source','WOS','wos',1,'2026-01-01')")
    review_workflow.associate_sources(manual_legacy,['old'],['legacy-source'],conn=conn)
import_sources.migrate(manual_legacy)
assert review_workflow.sources(manual_legacy)[0]['identification']=='legacy'
with review_workflow.connect(manual_legacy) as conn:
    assert import_sources.ensure(conn,import_sources.detect({'raw':{'name_of_database':'WOS'}},'new.ris','new'))!='legacy-source'
    empty_file={'name':'empty.ris','digest':'empty','articles':[],'originals':[],'source_id':None}
    import_sources.prepare_files(conn,[empty_file])
    assert empty_file['source_ids']==[] and empty_file['source_id']
    assert {s['name'] for s in import_sources.decorate(conn,[dict(r)|{'reserved':False,'active':True} for r in conn.execute('SELECT id,name FROM workflow_sources WHERE id != ?',(review_workflow.UNASSIGNED,))])}=={'WOS'}

# The generation endpoint accepts manually entered values without a task ID.
form={'identified_rows_db':[{'source_type':'database','name':'Manual database','n':100}],
      'duplicates_removed':20,'records_screened':80,'records_excluded_screening':60,
      'screening_exclusion_reasons':[{'reason':'Outside scope','n':60}],
      'reports_sought_db':20,'reports_not_retrieved_db':2,'reports_assessed_db':18,
      'exclusion_reasons_db':[{'reason':'Wrong design','n':10}],
      'studies_included':6,'reports_of_included_studies':8}
generated=call('POST','/api/prisma/generate',json={'form':form});assert generated['ok'],generated
assert generated['form']['studies_included']==6 and generated['form']['reports_of_included_studies']==8
updated={**form,'review_mode':'updated','previous_studies':4,'previous_reports':5,'new_studies':6,'new_reports':8,
         'total_studies':10,'total_reports':13,'studies_included':10,'reports_of_included_studies':13}
assert call('POST','/api/prisma/generate',json={'form':updated})['ok']
other={**form,'sources_scope':'db_reg_other','identified_rows_other':[{'source_type':'website','name':'Manual website','n':3}],
       'reports_sought_other':3,'reports_not_retrieved_other':1,'reports_assessed_other':2,
       'exclusion_reasons_other':[{'reason':'Wrong setting','n':1}],'studies_included':7,'reports_of_included_studies':9}
assert call('POST','/api/prisma/generate',json={'form':other})['ok']
dta={**form,'review_mode':'dta','studies_included_meta_analysis':4}
assert call('POST','/api/prisma/generate',json={'form':dta})['ok']
assert expected_auto(PrismaFormData(), 'studies_included') is None
assert expected_auto(PrismaFormData(review_mode='updated'), 'new_studies') is None
response=client.get('/');assert response.headers['cache-control']=='no-cache'
response=client.get('/prisma_task_ui.js');assert response.headers['cache-control']=='public, max-age=86400'
print(json.dumps({'status':'passed','data_dir':str(DATA),'checks':['mixed RIS sources','empty fallback hidden','repeat import idempotent',
    'Maybe progression retained','additional import staged','member roles and grant privacy','owner permissions',
    'team unresolved not included','CSV metric provenance','current adjudication recognised','swapped reviewer votes reject old adjudication',
    'legacy migration idempotence and backup','research and evidence unchanged','selected legacy scope retained',
    'provider is not database','filename inference labelled','source correction retained on repeat import','legacy named source identity retained',
    'exact duplicate excess and source overlap','empty import source preparation','manual generation','updated review','other-source branch','DTA','study count independent','static cache']},indent=2))
