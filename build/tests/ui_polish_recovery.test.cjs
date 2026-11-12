'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const {spawnSync} = require('node:child_process');
const repo = process.env.RF_CORE_REPO || path.join(__dirname,'../..');

test('desktop recovery CLI issues an actual one-use code in the active isolated data directory without starting the app',()=>{
  const script = String.raw`
import contextlib, io, json, os, pathlib, subprocess, sys, tempfile
repo=pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0,str(repo))
with tempfile.TemporaryDirectory(prefix='rf-recovery-') as folder:
    root=pathlib.Path(folder);active=root/'active';external=root/'external';active.mkdir();external.mkdir()
    config=root/'local-storage.json'
    config.write_text(json.dumps({'version':1,'current_path':str(active),'pending':None}),encoding='utf-8')
    os.environ['COBOOKSHELF_DATA_DIR']=str(active)
    os.environ['COBOOKSHELF_JWT_SECRET']='synthetic-recovery-test-secret'
    from custom_backend import auth, auth_recovery
    auth.create_user('synthetic-recovery-user','Synthetic-Recovery-Password-2026!')
    env=os.environ.copy()
    env.update(COBOOKSHELF_DATA_DIR=str(external),REVIEWFLOW_STORAGE_CONFIG=str(config),REVIEWFLOW_MODE='desktop',PYTHONIOENCODING='utf-8')
    for key in ('REVIEWFLOW_RUNTIME_OVERLAY','REVIEWFLOW_PORT','REVIEWFLOW_HOST'):
        env.pop(key,None)
    def cli(*args):
        return subprocess.run([sys.executable,str(repo/'build'/'launcher.py'),*args],env=env,cwd=root,capture_output=True,text=True,encoding='utf-8',timeout=30)
    missing=cli('--reset-user','no-such-synthetic-user')
    assert missing.returncode==1
    assert 'One-use recovery code' not in missing.stdout
    assert not (active/'auth_reset_codes.db').exists()
    result=cli('--reset-user','synthetic-recovery-user')
    assert result.returncode==0, 'reset command failed without a successful code'
    lines=result.stdout.strip().splitlines()
    assert lines[-2]=='One-use recovery code (valid for 15 minutes):'
    code=lines[-1]
    assert len(code)>=16
    assert auth_recovery.consume_reset_code('synthetic-recovery-user',code) is True
    assert auth_recovery.consume_reset_code('synthetic-recovery-user',code) is False
    assert not (external/'users.db').exists(), 'explicit config must select the same active path as normal startup'
    assert not (external/'auth_reset_codes.db').exists()
    assert not list(root.rglob('desktop-port.txt'))
    assert not list(root.rglob('*.log')), 'recovery must never redirect its code to application logs'
    config.write_text(json.dumps({'version':1,'current_path':str(active),'pending':{'destination':str(external)}}),encoding='utf-8')
    pending=cli('--reset-user','synthetic-recovery-user')
    assert pending.returncode==1
    assert 'pending data move' in pending.stderr+pending.stdout
    assert 'One-use recovery code' not in pending.stdout
    malformed=cli('--reset-user','synthetic-recovery-user','extra')
    assert malformed.returncode==2
    assert 'One-use recovery code' not in malformed.stdout
print('isolated recovery assertions passed')
`;
  const result=spawnSync(process.env.PYTHON || 'python',['-c',script,repo],{encoding:'utf8',timeout:90000,maxBuffer:1024*1024});
  // Never print a captured recovery code, including when an assertion fails.
  assert.equal(result.error,undefined,result.error?.message);
  assert.equal(result.status,0,'Isolated recovery subprocess failed: '+(result.stderr || '').replace(/[A-Za-z0-9_-]{16,}/g,'[redacted]'));
  assert.match(result.stdout,/isolated recovery assertions passed/);
});

test('both native desktop packages include the recovery module',()=>{
  for(const file of ['build/build_macos.sh','build/build_linux.sh']) assert.match(fs.readFileSync(path.join(repo,file),'utf8'),/--hidden-import custom_backend\.auth_recovery/);
});
