"""Disposable acceptance checks; never uses an installed or research data directory."""
from __future__ import annotations
import os
import sys
import tempfile
import json
import asyncio
import time
from pathlib import Path

if not __debug__:
    # F1-68: all 21 checks in this gate are bare `assert`s and there is no
    # non-assert enforcement layer, so `python -O` compiled them away and the
    # gate printed "passed" with exit 0. Refuse the optimized interpreter; a
    # gate that cannot check anything must not report a pass.
    raise SystemExit('run this gate without -O: assertions are the gate')

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
# 第 2～5 轮各用一条「只在当轮才变成暂不确定」的文献推进轮次（从未进过
# 轮次快照 → 仍算第一轮，可存 maybe）。每条用独立来源文件：同一来源的
# 后续导入会因元数据核验暂存而不进入筛选队列。
fresh_titles={2:'Ocean acidification trial',3:'Urban transit cohort',4:'Vaccine hesitancy survey',5:'Soil carbon experiment'}
fresh_sources={2:'02-fresh2.ris',3:'03-fresh3.ris',4:'04-fresh4.ris',5:'05-fresh5.ris'}
for n in (2,3,4,5):
    call('POST',base+'/import',files={'file':(fresh_sources[n],ris(f'fresh{n}',fresh_titles[n],'',f'10.1234/fresh{n}').encode(),'application/x-research-info-systems')})
assert {s['name'] for s in call('GET',base+'/workflow-settings')['sources']}=={'01-Wiley.ris',*fresh_sources.values()}
for key in ('one','two'):call('POST',base+'/decisions',json={'zotero_key':key,'decision':'maybe'})
assert len(call('GET',base+'/work-queue/fulltext')['queue'])==2
assert call('GET',base+'/work-items/screen/one')['maybe_allowed'] is True
version=call('GET',base+'/workflow-settings')['version']
# 第 1 轮可存 maybe；把 two 与后续轮次候选先定为 include，使每个复筛轮次
# 恰好从「原 maybe 文献 one + 当轮候选」开始；候选在进入轮次前从未出现在
# 任何快照里，因此仍可存 maybe（第一轮语义）。
call('POST',base+'/decisions',json={'zotero_key':'two','decision':'include'})
for n in (2,3,4,5):
    call('POST',base+'/decisions',json={'zotero_key':f'fresh{n}','decision':'include'})
for number in (2,3,4,5):
    fresh=f'fresh{number}'
    call('POST',base+'/decisions',json={'zotero_key':fresh,'decision':'maybe'})
    round=call('POST',base+'/screening-rounds',json={'action':'start','expected_policy_version':version})
    assert round['screening_round']['number']==number,round
    assert set(round['screening_round']['pending_keys'])=={'one',fresh},round
    held={row['zotero_key'] for row in call('GET',base+'/work-queue/fulltext')['queue']}
    assert 'one' not in held and fresh not in held,held
    assert call('GET',base+'/work-items/screen/'+fresh)['maybe_allowed'] is False
    # 新裁定：第 2～5 轮 maybe 被 409 拒绝，决定保持原样（旧钉：可重存 maybe）。
    refused=client.post(base+'/decisions',json={'zotero_key':'one','decision':'maybe'})
    assert refused.status_code==409,refused.text
    detail=refused.json()['detail']
    assert detail['code']=='maybe_first_round_only',detail
    assert detail['version']==version,detail
    assert detail['workflow_settings']['version']==version,detail
    assert {r['zotero_key']:r['decision'] for r in call('GET',base+'/decisions')['decisions']}['one']=='maybe'
    # 轮次把 maybe 文献扣留在全文筛选之外（后端直写同样被拒）。
    refused=client.post(base+'/stage2/decisions',json={'zotero_key':'one','decision':'include'})
    assert refused.status_code==409,refused.text
    # 轮次进度仍用 include/exclude 推进（不再是重存 maybe）。
    # 指纹更新有秒粒度：与轮次开始同一秒的重复决定不算进度，按真实用户节奏留间隔。
    time.sleep(1.1)
    call('POST',base+'/decisions',json={'zotero_key':fresh,'decision':'include' if number%2==0 else 'exclude'})
    queue=call('GET',base+'/work-queue/screen?order=random')
    assert queue['screening_round']['pending_keys']==['one'],queue
    assert queue['scope_progress']=={'total':2,'completed':1,'pending':1},queue['scope_progress']
    call('POST',base+'/screening-rounds',json={'action':'finish','expected_policy_version':version})
    # finish 后原 maybe 决定未被改写，仍可进入全文筛选。
    assert 'one' in {row['zotero_key'] for row in call('GET',base+'/work-queue/fulltext')['queue']}
saved={row['zotero_key']:row['decision'] for row in call('GET',base+'/decisions')['decisions']}
assert saved['one']=='maybe',saved
assert saved['two']=='include',saved
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
# 成员库经共享目录同步也包含后续导入的文献：先把非 maybe 的行定完，
# 使成员自己的轮次只快照 one/two。
for key in ('fresh2','fresh3','fresh4','fresh5'):
    call('POST',base+'/decisions',json={'zotero_key':key,'decision':'include'})
for key in ('one','two'):call('POST',base+'/decisions',json={'zotero_key':key,'decision':'maybe'})
assert call('GET',base+'/work-items/screen/one')['maybe_allowed'] is True
mversion=call('GET',base+'/workflow-settings')['version']
call('POST',base+'/screening-rounds',json={'action':'start','expected_policy_version':mversion})
assert call('GET',base+'/work-items/screen/one')['maybe_allowed'] is False
assert not ({'one','two'} & {row['zotero_key'] for row in call('GET',base+'/work-queue/fulltext')['queue']})
client.headers['Authorization']='Bearer '+owner['token']
assert {'one','two'} <= {row['zotero_key'] for row in call('GET',base+'/work-queue/fulltext')['queue']}
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
    # 文件名来源：旧元数据名不得回潮，且不存在 Unassigned/元数据推断名。
    assert set(names)=={'01-Wiley.ris',*fresh_sources.values()},names
    new=conn.execute("SELECT id,active FROM workflow_sources WHERE name='01-Wiley.ris'").fetchone()
    assert new['active']==0
with closing(wf.connect(db)) as conn,conn:
    conn.execute('UPDATE workflow_sources SET active=1 WHERE id=?',(new['id'],))
    conn.execute('DELETE FROM workflow_screen_rounds')
print('Undo and migration preservation/idempotence/source pause: passed')
print('Filename recognition/reimport; rounds 2–5; hold/release; backend bypass rejection; policy conflict: passed')
print('PREVIEW_DATA='+str(DATA))
print('PREVIEW_TASK='+tid)
Path(os.environ.get('TEMP') or tempfile.gettempdir()).joinpath('reviewflow-rounds-preview.json').write_text(json.dumps({'data':str(DATA),'task':tid,'root':str(ROOT)}),encoding='utf-8')
