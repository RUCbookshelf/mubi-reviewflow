"""report_evidence 浏览器回归的账号/任务/文献准备脚本（G21 配套，自包含于仓库）。

对已启动的隔离实例（build/tests/report_evidence_instance.py）执行：
1) HTTP 注册（或登录）固定测试账号并创建/复用固定名称任务；
2) 直写 owner 库种入 3 篇文献（R1/R2/R3，与 pytest 同一入口 coscreen.db.upsert_articles）；
3) 复位证据工作区（report_evidence_state 幂等日志与撤销栈清空），保证 cjs 每次运行
   前置一致、可重复执行；
4) 写出 session.json（{token, task, user_id, username}）供 report_evidence_browser.cjs 读取。

用法（可重复运行；每次跑 cjs 前重跑一次即可）：

    python build/tests/report_evidence_prep.py --url http://127.0.0.1:18772 \
        --data-dir E:/uxt-r4-frontend [--session-out E:/uxt-r4-frontend/session.json]
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

USERNAME = 'r4front@evidence.test'
PASSWORD = 'R4Frontend-2026!'
TASK_NAME = 'r4-frontend-evidence'


def _request(base: str, path: str, token: str | None = None, body: dict | None = None) -> dict:
    """对实例发 GET/POST 并返回 JSON（POST 当 body 非 None）。"""
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--url', default='http://127.0.0.1:18772', help='隔离实例地址（默认 18772）')
    parser.add_argument('--data-dir', required=True,
                        help='实例的独立数据目录（须与启动实例时一致，用于直写 owner 库）')
    parser.add_argument('--session-out', default=None, help='session.json 输出路径（默认 <数据目录>/session.json）')
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    session_path = Path(args.session_out).resolve() if args.session_out else data_dir / 'session.json'
    os.environ['COBOOKSHELF_DATA_DIR'] = str(data_dir)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 仓库根（本工作树代码）

    try:
        reg = _request(args.url, '/api/auth/register', body={'username': USERNAME, 'password': PASSWORD})
    except urllib.error.HTTPError:  # 账号已存在（重复运行）：改为登录
        reg = _request(args.url, '/api/auth/login', body={'username': USERNAME, 'password': PASSWORD})
    token, user = reg['token'], reg['user']

    tasks = _request(args.url, '/api/tasks?include_archived=true', token)['tasks']
    existing = next((t for t in tasks if t['name'] == TASK_NAME), None)
    task = existing['task_id'] if existing else _request(args.url, '/api/tasks', token,
                                                         body={'name': TASK_NAME})['task_id']

    from custom_backend.main import app
    from custom_backend.report_evidence_api import connect
    from coscreen import db
    from coscreen.models import Article

    ctx = app.state.review_workflow.context(task, {'user_id': user['id'], 'username': user['username']})
    # 复位证据工作区（revision→0、撤销栈清空、幂等日志清空），保证 cjs 每次运行的前置一致。
    with closing(connect(ctx.owner_db)) as conn:
        conn.execute('DELETE FROM report_evidence_state')
        conn.execute('DELETE FROM report_evidence_requests')
        conn.commit()

    db.upsert_articles(ctx.owner_db, [
        Article(zotero_key='R1', title='First Report', authors='Ada', doi='10.test/1',
                raw={'source_file': '01-Wiley.ris'}),
        Article(zotero_key='R2', title='Second Report', authors='Bob', doi='10.test/2'),
        Article(zotero_key='R3', title='Third Report', authors='Cal', doi='10.test/3'),
    ], [])

    session_path.write_text(json.dumps({'token': token, 'task': task,
                                        'user_id': user['id'], 'username': user['username']}),
                            encoding='utf-8')
    print(json.dumps({'task': task, 'user_id': user['id'], 'owner_db': str(ctx.owner_db),
                      'session': str(session_path)}))


if __name__ == '__main__':
    main()
