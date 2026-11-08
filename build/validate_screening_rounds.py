"""2.1.5 W1 一次性验收门：二选一 + 轮次移除 + 存量重置。

旧版（r15）检查「再开一轮」的 2–5 轮、轮次快照 hold/release、maybe 第一轮
语义。2.1.5 W1 产品移除轮次、两阶段全程二选一，本门禁等强度改为：

1. 两阶段保存 maybe → 结构化 409（code=maybe_disabled /
   message_key=workflow.maybe_disabled），决定不落盘；
2. 复筛队列仅 include（maybe 不进队）；全文 maybe 同样 409；
3. 轮次路由未注册（404）；queue/workflow-settings 载荷无轮次字段、
   show_maybe 退役（GET 无 show_maybe，PUT 携带被忽略）；
4. 存量重置迁移：maybe→未筛、复筛级联、非 maybe 零触碰、下游编码保留、
   .bak 可校验（integrity_check + sha256）、幂等二跑零操作；
5. filename sources 迁移段保留（重跑不改决策、不复活旧元数据名）。

Disposable acceptance checks; never uses an installed or research data directory.
"""
from __future__ import annotations
import os
import sys
import tempfile
import json
import asyncio
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

if not __debug__:
    # F1-68: all checks in this gate are bare `assert`s and there is no
    # non-assert enforcement layer, so `python -O` compiled them away and the
    # gate printed "passed" with exit 0. Refuse the optimized interpreter; a
    # gate that cannot check anything must not report a pass.
    raise SystemExit('run this gate without -O: assertions are the gate')

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'build'))
DATA=Path(tempfile.mkdtemp(prefix='reviewflow-binary-screening-validation-'))
os.environ['COBOOKSHELF_DATA_DIR']=str(DATA)
os.environ['REVIEWFLOW_MODE']='server'
os.environ.pop('REVIEWFLOW_RUNTIME_OVERLAY',None)
from launcher import build_app
if os.environ.get('REVIEWFLOW_VALIDATION_DEPENDENCIES'):
    sys.path.append(os.environ['REVIEWFLOW_VALIDATION_DEPENDENCIES'])
import httpx


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
    def put(self,path,**kwargs): return self.request('PUT',path,**kwargs)
client=Client(build_app())
def call(method,path,**kwargs):
    response=client.request(method,path,**kwargs)
    assert response.status_code<400,(method,path,response.status_code,response.text)
    return response.json()
owner=call('POST','/api/auth/register',json={'username':'preview.owner','password':'DisposableValidation2026!'})
client.headers['Authorization']='Bearer '+owner['token']
task=call('POST','/api/tasks',json={'name':'Binary screening validation'})
tid=task['task_id'];base='/api/tasks/'+tid

def ris(key,title,database='',doi=''):
    return f'TY  - JOUR\nID  - {key}\nTI  - {title}\nAU  - Reviewer, Example\nPY  - 2024\n'+(f'DB  - {database}\n' if database else '')+(f'DO  - {doi}\n' if doi else '')+'ER  - \n'


first=ris('one','Language acquisition in context','WRONG DATABASE','10.1234/one')+ris('two','Clinical trial reporting outcomes','Another wrong database','10.1234/two')
call('POST',base+'/import',files={'file':('01-Wiley.ris',first.encode(),'application/x-research-info-systems')})
settings=call('GET',base+'/workflow-settings')
assert {s['name'] for s in settings['sources']}=={'01-Wiley.ris'},settings
call('POST',base+'/import',files={'file':('01-Wiley.ris',first.encode(),'application/x-research-info-systems')})
assert len(call('GET',base+'/workflow-settings')['sources'])==1

# ---- 1/2. 两阶段 maybe 全拒、复筛仅 include -----------------------------
version=call('GET',base+'/workflow-settings')['version']
for key in ('one','two'):
    refused=client.post(base+'/decisions',json={'zotero_key':key,'decision':'maybe'})
    assert refused.status_code==409,refused.text
    detail=refused.json()['detail']
    assert detail['code']=='maybe_disabled',detail
    assert detail['message_key']=='workflow.maybe_disabled',detail
    assert detail['version']==version,detail
    assert detail['workflow_settings']['version']==version,detail
    assert 'show_maybe' not in detail['workflow_settings']['stages']['screen'],detail
assert call('GET',base+'/decisions')['decisions']==[]

call('POST',base+'/decisions',json={'zotero_key':'one','decision':'include'})
call('POST',base+'/decisions',json={'zotero_key':'two','decision':'exclude'})
queue=call('GET',base+'/work-queue/fulltext')['queue']
assert {row['zotero_key'] for row in queue}=={'one'},queue
refused=client.post(base+'/stage2/decisions',json={'zotero_key':'one','decision':'maybe'})
assert refused.status_code==409,refused.text
assert refused.json()['detail']['message_key']=='workflow.maybe_disabled',refused.text
assert call('GET',base+'/stage2/decisions')['decisions']==[]
assert call('GET',base+'/work-items/screen/one')['maybe_allowed'] is False
assert call('GET',base+'/work-items/fulltext/one')['maybe_allowed'] is False

# ---- 3. 轮次入口/载荷下线 ------------------------------------------------
for path in ('/screening-rounds',):
    removed=client.post(base+path,json={'action':'start','expected_policy_version':version})
    assert removed.status_code==404,(path,removed.status_code,removed.text)
queue=call('GET',base+'/work-queue/screen')
assert 'screening_round' not in queue and 'remaining_uncertain' not in queue,queue
live=call('GET',base+'/workflow-settings')
assert 'screening_round_marker' not in live,live
assert 'show_maybe' not in live['stages']['screen'],live
assert 'show_maybe' not in live['stages']['fulltext'],live
stages={name:{'mode':value['mode'],'source_ids':value['source_ids']} for name,value in live['stages'].items()}
stages['screen']['show_maybe']=False
put=client.put(base+'/workflow-settings',json={'expected_version':live['version'],'stages':stages,
    'evidence_required_field_ids':live['evidence_required_field_ids']})
assert put.status_code==200,put.text
assert 'show_maybe' not in put.json()['stages']['screen'],put.text
with closing(sqlite3.connect(DATA/'tasks'/tid/('preview.owner.db'))) as conn:
    stored=json.loads(conn.execute('SELECT stages_json FROM workflow_state WHERE singleton=1').fetchone()[0])
assert 'show_maybe' not in stored['screen'],stored

# ---- 4. 存量重置迁移（独立 scratch 库） ----------------------------------
from coscreen import db as db_mod, review_workflow as wf, fulltext, binary_reset
from coscreen.models import Article

scratch=DATA/'binary-reset-scratch.db'
db_mod.init_db(scratch); wf.migrate(scratch)
db_mod.upsert_articles(scratch,[Article(zotero_key=f'R{i}',title=f'Reset {i}') for i in range(1,5)],[])
db_mod.save_decision(scratch,'R1','maybe'); fulltext.save_stage2(scratch,'R1','include')  # 级联
db_mod.save_decision(scratch,'R2','maybe'); fulltext.save_stage2(scratch,'R2','maybe')    # 双阶段
db_mod.save_decision(scratch,'R3','include'); fulltext.save_stage2(scratch,'R3','include')  # 零触碰
db_mod.save_decision(scratch,'R4','exclude')                                              # 零触碰
with closing(sqlite3.connect(scratch)) as conn, conn:
    conn.execute("INSERT INTO coding_dimensions(name,dtype,options_json,multi_select,unit,description,section,position,created_at) VALUES('dose','text','[]',0,'','','',1,'2026-01-01T00:00:00Z')")
    conn.execute("INSERT INTO coding_values(dimension_id,zotero_key,value,updated_at) VALUES(1,'R1','12 mg','2026-01-01T00:00:00Z')")
with closing(sqlite3.connect(scratch)) as conn:
    before=( {r[0]:tuple(r) for r in conn.execute("SELECT * FROM decisions WHERE decision!='maybe'")},
             {r[0]:tuple(r) for r in conn.execute("SELECT * FROM stage2_decisions WHERE decision!='maybe' AND zotero_key NOT IN (SELECT zotero_key FROM decisions WHERE decision='maybe')")} )
first_reset=binary_reset.migrate(scratch,task_id=tid,actor_id=owner['user']['id'])
assert (first_reset['stage1_reset'],first_reset['stage2_reset'],first_reset['stage2_cascade_reset'])==(2,1,1),first_reset
with closing(sqlite3.connect(scratch)) as conn:
    assert conn.execute("SELECT COUNT(*) FROM decisions WHERE decision='maybe'").fetchone()[0]==0
    assert {r[0] for r in conn.execute('SELECT zotero_key FROM decisions')}=={'R3','R4'}
    assert {r[0] for r in conn.execute('SELECT zotero_key FROM stage2_decisions')}=={'R3'}
    assert conn.execute("SELECT value FROM coding_values WHERE zotero_key='R1'").fetchone()[0]=='12 mg'
    summary=conn.execute('SELECT * FROM workflow_binary_reset').fetchone()
    assert summary is not None
    assert conn.execute('SELECT COUNT(*) FROM workflow_migrations WHERE version=8').fetchone()[0]==1
backup=scratch.with_name(scratch.name+'.pre-binary-reset.bak')
assert backup.is_file(),backup
digest=hashlib.sha256(backup.read_bytes()).hexdigest()
with closing(sqlite3.connect(backup)) as conn:
    assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert conn.execute("SELECT COUNT(*) FROM decisions WHERE decision='maybe'").fetchone()[0]==2
    assert conn.execute("SELECT COUNT(*) FROM stage2_decisions WHERE decision='maybe'").fetchone()[0]==1
stamp=backup.stat().st_mtime_ns
second_reset=binary_reset.migrate(scratch,task_id=tid,actor_id=owner['user']['id'])
assert second_reset['applied_at']==first_reset['applied_at'],second_reset
assert backup.stat().st_mtime_ns==stamp
with closing(sqlite3.connect(scratch)) as conn:
    assert {r[0]:tuple(r) for r in conn.execute("SELECT * FROM decisions WHERE decision!='maybe'")}==before[0]
    assert {r[0]:tuple(r) for r in conn.execute("SELECT * FROM stage2_decisions WHERE decision!='maybe'")}==before[1]

# 摘要端点：本人可见、计数与备份存在性
summary_api=call('GET',base+'/binary-reset-summary')
assert summary_api['migration_version']==8,summary_api
assert len(summary_api['members'])==1,summary_api
row=summary_api['members'][0]
assert row['username']=='preview.owner' and row['migrated'] is True,row
assert row['stage1_reset']==0 and row['stage2_reset']==0,row  # 属主库无 maybe（API 已拒）
assert row['backup']['exists'] is False,row

# ---- 5. filename sources 迁移段保留 -------------------------------------
from coscreen import tasks, import_sources
info=tasks.find_task(tid,DATA)
db=info.dir/(tasks.slugify_screener('preview.owner')+'.db')
with closing(wf.connect(db)) as conn,conn:
    decisions_before=[tuple(r) for r in conn.execute('SELECT * FROM decisions ORDER BY zotero_key')]
    sid=conn.execute("SELECT id FROM workflow_sources WHERE name='01-Wiley.ris'").fetchone()[0]
    conn.execute("UPDATE workflow_sources SET name='Wrong metadata source',normalized_name='wrong metadata source',active=0 WHERE id=?",(sid,))
    conn.execute("UPDATE workflow_source_details SET identity='database:wrong',identification='metadata' WHERE source_id=?",(sid,))
    conn.execute('DELETE FROM workflow_migrations WHERE version=5')
import_sources.migrate(db)
import_sources.migrate(db)
with closing(wf.connect(db)) as conn:
    assert decisions_before==[tuple(r) for r in conn.execute('SELECT * FROM decisions ORDER BY zotero_key')]
    names=[r[0] for r in conn.execute("SELECT name FROM workflow_sources WHERE id IN (SELECT source_id FROM workflow_source_records)")]
    assert set(names)=={'01-Wiley.ris'},names
    new=conn.execute("SELECT id,active FROM workflow_sources WHERE name='01-Wiley.ris'").fetchone()
    assert new['active']==0
with closing(wf.connect(db)) as conn,conn:
    conn.execute('UPDATE workflow_sources SET active=1 WHERE id=?',(new['id'],))

print('Binary screening: two-stage maybe 409 shape / include-only fulltext queue: passed')
print('Rounds: route 404, queue/settings payload fields removed, show_maybe retired: passed')
print('Reset migration: counts/cascade/audit/backup/integrity/idempotence/coding preservation: passed')
print('Reset summary endpoint + filename migration preservation: passed')
print('PREVIEW_DATA='+str(DATA))
print('PREVIEW_TASK='+tid)
Path(os.environ.get('TEMP') or tempfile.gettempdir()).joinpath('reviewflow-binary-preview.json').write_text(json.dumps({'data':str(DATA),'task':tid,'root':str(ROOT)}),encoding='utf-8')
