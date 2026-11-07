"""R15 浏览器回归的账号/任务/文献准备（隔离实例专用，自包含）。

对已启动的隔离实例（197xx 临时端口）执行：
1) HTTP 注册一次性测试账号并创建固定名称任务；
2) 直写 owner 库种入 2 篇未决策文献（K1/K2），让真实浏览器先看到第 1 轮
   的「暂不确定」按钮；
3) 清空该任务库里的决策与轮次快照（每次运行都必须使用全新数据目录）；
4) 写出 session.json（token/task/user_id/username）供
   build/tests/test_r15_round_maybe_browser.cjs 读取。

用法（由 test_r15_round_maybe_browser.py 门控调用）：

    python build/tests/test_r15_round_maybe_prep.py --url http://127.0.0.1:19701 \
        --data-dir E:/rf214tmp/r15/browser-data --session-out .../session.json

登录口令在运行时随机生成并只留在本进程内（session 文件只放 token），
不写死任何可用凭据；实例地址必须是本机回环 http，拒绝其他主机/协议。
"""
import argparse
import json
import os
import secrets
import sqlite3
import sys
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse

USERNAME = 'r15round@round.test'
TASK_NAME = 'r15-round-maybe'
LOOPBACK_HOSTS = {'127.0.0.1', 'localhost', '::1'}


def _loopback_base(raw: str) -> str:
    """只允许 http://<loopback>:port，阻断 SSRF（外部主机/协议/凭据段）。"""
    parsed = urlparse(raw)
    if parsed.scheme != 'http' or parsed.hostname not in LOOPBACK_HOSTS or parsed.username or parsed.password:
        raise SystemExit('refusing non-loopback instance URL: %r' % raw)
    return raw.rstrip('/')


def _request(base: str, path: str, token: str | None = None, body: dict | None = None) -> dict:
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode() if body is not None else None,
        headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--url', required=True)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--session-out', required=True)
    args = parser.parse_args()

    base = _loopback_base(args.url)
    data_dir = Path(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ['COBOOKSHELF_DATA_DIR'] = str(data_dir)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    # 一次性账号：口令随机生成，仅本进程持有；数据目录每次全新。
    password = secrets.token_urlsafe(24)
    try:
        reg = _request(base, '/api/auth/register', body={'username': USERNAME, 'password': password})
    except urllib.error.HTTPError as exc:
        raise SystemExit('prep requires a fresh data directory (account already exists): %s' % exc)
    token, user = reg['token'], reg['user']

    tasks = _request(base, '/api/tasks?include_archived=true', token)['tasks']
    existing = next((t for t in tasks if t['name'] == TASK_NAME), None)
    task = existing['task_id'] if existing else _request(
        base, '/api/tasks', token, body={'name': TASK_NAME})['task_id']

    from custom_backend.main import app
    from coscreen import db
    from coscreen.models import Article

    ctx = app.state.review_workflow.context(
        task, {'user_id': user['id'], 'username': user['username']})
    db.upsert_articles(ctx.owner_db, [
        Article(zotero_key='K1', title='Round maybe first article', authors='Ada',
                doi='10.1234/r15k1', raw={'source_file': '01-rounds.ris'}),
        Article(zotero_key='K2', title='Round maybe second article', authors='Bob',
                doi='10.1234/r15k2', raw={'source_file': '01-rounds.ris'}),
    ], [])
    with closing(sqlite3.connect(ctx.owner_db)) as conn, conn:
        conn.execute('DELETE FROM decisions')
        conn.execute('DELETE FROM stage2_decisions')
        conn.execute('DELETE FROM workflow_screen_rounds')

    Path(args.session_out).write_text(json.dumps({
        'token': token, 'task': task, 'user_id': user['id'], 'username': user['username'],
        'keys': ['K1', 'K2'],
    }), encoding='utf-8')
    print(json.dumps({'task': task, 'owner_db': str(ctx.owner_db),
                      'session': args.session_out}))


if __name__ == '__main__':
    main()
