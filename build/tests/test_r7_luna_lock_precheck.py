"""r7-lunafix L-01 钉子：任务锁的参与者/可见性前置检查（双进程实测）。

复刻 deepseek 复审的双进程探针（out/external_reviews/gpt_luna_findings_verification.md
L-01 节）为自动化测试：两个 uvicorn 进程共享同一临时 DATA_DIR 时——

- 非参与者持有效 JWT 的**慢速小请求体**期间：锁文件不得出现、不得持有；
  属主经另一进程并发 GET 必须 200（修前：中间件在 require_user 之前按
  URL task_id 取锁 → 锁被非参与者真实持有、属主 409 → 本测试红）；
- 合法参与者（属主）同样的慢体请求期间：跨进程锁语义逐字不变——锁文件
  出现、属主经另一进程并发 GET 409、请求结束后释放（回归钉子：修复不得
  弱化合法参与者的锁）；
- 无效 token 慢体：不取锁（U5-P4 既有语义对照），最终 401。

测试完全不 import custom_backend（子进程独立 DATA_DIR），避免与其它测试
模块的 COBOOKSHELF_DATA_DIR 环境相互干扰。
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
PORT_RANGE = range(18810, 18821)  # 任务书指定的空闲端口段
READY_TIMEOUT = 45.0

#: 属主有效 POST /decisions 体（DecisionIn 最小合法形状）
OWNER_BODY = json.dumps({"zotero_key": "L01KEY", "decision": "include",
                         "reason": "", "notes": "", "tags": None}).encode()
#: 非参与者体：必须是完整合法 JSON——体读完后 403/401 才由依赖鉴权发出
#: （体本身非法会先被 422 挡住，掩盖鉴权口径）
OUTSIDER_BODY = json.dumps({"zotero_key": "L01KEY",
                            "decision": "include"}).encode()


def _free_port() -> int:
    for port in PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise unittest.SkipTest("18810-18820 无空闲端口（双进程探针不可启动）")


class _SlowBody:
    """向指定进程发送只带前缀体的 POST，挂起期间供调用方断言，再补完读响应。"""

    def __init__(self, port: int, token: str, task_id: str, body: bytes,
                 prefix: int = 12):
        self.path = f"/api/tasks/{task_id}/decisions"
        head = (
            f"POST {self.path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Authorization: Bearer {token}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("ascii")
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.sock.sendall(head + body[:prefix])
        self._rest = body[prefix:]

    def no_early_response(self, wait: float = 0.5) -> bool:
        """挂起期间服务器不得提前回包（体未读完前 403/401 不应发出）。"""
        self.sock.settimeout(wait)
        try:
            return self.sock.recv(1) == b""
        except socket.timeout:
            return True
        finally:
            self.sock.settimeout(10)

    def finish(self) -> tuple[int, str]:
        """补完请求体并读取完整响应，返回 (status, body)。"""
        self.sock.settimeout(15)
        self.sock.sendall(self._rest)
        chunks: list[bytes] = []
        try:
            while True:
                data = self.sock.recv(65536)
                if not data:
                    break
                chunks.append(data)
        except socket.timeout:
            pass
        finally:
            self.sock.close()
        raw = b"".join(chunks)
        head, _, body = raw.partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n", 1)[0].decode("ascii", "replace")
        status = int(status_line.split()[1])
        return status, body.decode("utf-8", "replace")


class LunaLockPrecheckDualProcessTests(unittest.TestCase):
    """L-01：取锁前必须完成『JWT 有效 + 任务可见性/参与者』检查。"""

    @classmethod
    def _start_uvicorn(cls, env) -> tuple[subprocess.Popen, int, httpx.Client]:
        """在 18810-18820 段选空闲端口起一个 uvicorn；进程早死/端口被抢则换口重试。"""
        last_err = ""
        for _attempt in range(len(list(PORT_RANGE))):
            port = _free_port()
            errfile = tempfile.NamedTemporaryFile(
                prefix="rf_luna_l01_uv_", suffix=".log", delete=False)
            proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "custom_backend.main:app",
                 "--host", "127.0.0.1", "--port", str(port),
                 "--log-level", "warning"],
                cwd=str(REPO), env=env,
                stdout=subprocess.DEVNULL, stderr=errfile)
            client = httpx.Client(base_url=f"http://127.0.0.1:{port}",
                                  timeout=10)
            deadline = time.monotonic() + READY_TIMEOUT
            while True:
                if proc.poll() is not None:
                    errfile.close()
                    last_err = Path(errfile.name).read_text(
                        encoding="utf-8", errors="replace")[-1500:]
                    break  # 进程早死：换下一个端口重试
                if time.monotonic() > deadline:
                    proc.terminate()
                    proc.wait(timeout=10)
                    errfile.close()
                    last_err = f"uvicorn 端口 {port} {READY_TIMEOUT}s 未就绪"
                    break
                try:
                    if client.get("/api/health").status_code == 200:
                        return proc, port, client
                except httpx.TransportError:
                    time.sleep(0.2)
            client.close()
        raise AssertionError(f"uvicorn 双进程探针启动失败，末次诊断：{last_err}")

    @classmethod
    def setUpClass(cls):
        cls.data_dir = tempfile.mkdtemp(prefix="rf_luna_l01_")
        cls.procs: list[subprocess.Popen] = []
        cls.ports: list[int] = []
        cls.clients: list[httpx.Client] = []
        env = {**os.environ,
               "COBOOKSHELF_DATA_DIR": cls.data_dir,
               "PYTHONDONTWRITEBYTECODE": "1"}
        for _ in range(2):
            proc, port, client = cls._start_uvicorn(env)
            cls.procs.append(proc)
            cls.ports.append(port)
            cls.clients.append(client)
        # 注册属主与非参与者（开放注册即可取得有效 JWT——攻击者模型同复审）
        cls.tokens = {}
        for name in ("lunaowner1", "lunaoutsider1"):
            resp = cls.clients[0].post("/api/auth/register", json={
                "username": name, "password": "LunaProbe-Passw0rd"})
            assert resp.status_code == 200, resp.text
            cls.tokens[name] = resp.json()["token"]
        created = cls.clients[0].post("/api/tasks", headers={
            "Authorization": f"Bearer {cls.tokens['lunaowner1']}"},
            json={"name": "LunaLockProbe", "description": ""})
        assert created.status_code == 201, created.text
        cls.task_id = created.json()["task_id"]
        cls.lock_path = (Path(cls.data_dir) / "tasks" / cls.task_id
                         / f"{cls.task_id}.lock")

    @classmethod
    def tearDownClass(cls):
        for client in getattr(cls, "clients", []):
            client.close()
        for proc in getattr(cls, "procs", []):
            if proc.poll() is None:
                proc.terminate()
        for proc in getattr(cls, "procs", []):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        import shutil
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def _auth(self, name: str) -> dict:
        return {"Authorization": f"Bearer {self.tokens[name]}"}

    def _owner_get(self, via: int):
        return self.clients[via].get(
            f"/api/tasks/{self.task_id}/decisions",
            headers=self._auth("lunaowner1"))

    def test_l01_outsider_slow_body_takes_no_lock_owner_gets_200(self):
        # 基线：非参与者 403、属主 200（语义与 404/403 口径保持不变）
        self.assertEqual(
            self.clients[1].get(f"/api/tasks/{self.task_id}/decisions",
                                headers=self._auth("lunaoutsider1")).status_code,
            403)
        self.assertEqual(self._owner_get(1).status_code, 200)
        # 非参与者慢体（12 字节前缀，复审同款）：期间不得出现锁文件
        slow = _SlowBody(self.ports[0], self.tokens["lunaoutsider1"],
                         self.task_id, OUTSIDER_BODY)
        try:
            self.assertTrue(slow.no_early_response())
            appeared = False
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if self.lock_path.exists():
                    appeared = True
                    break
                time.sleep(0.05)
            # 修前红点：中间件按 URL task_id 先取锁 → 锁文件出现
            self.assertFalse(appeared,
                             f"非参与者慢体期间出现任务锁：{self.lock_path}")
            # 修前红点：属主经另一进程并发 GET 得 409（锁被非参与者持有）
            self.assertEqual(self._owner_get(1).status_code, 200)
        finally:
            status, body = slow.finish()
        self.assertEqual(status, 403)  # 下游 require_user 统一口径
        self.assertIn("参与者", body)
        self.assertFalse(self.lock_path.exists())
        self.assertEqual(self._owner_get(1).status_code, 200)

    def test_l01_participant_slow_body_still_locks_cross_process(self):
        """合法参与者锁语义回归：慢体期间跨进程互斥（409）逐字不变。"""
        self.assertFalse(self.lock_path.exists())
        slow = _SlowBody(self.ports[0], self.tokens["lunaowner1"],
                         self.task_id, OWNER_BODY)
        try:
            appeared = False
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if self.lock_path.exists():
                    appeared = True
                    break
                time.sleep(0.05)
            # 属主（参与者）必须照旧取到跨进程锁
            self.assertTrue(appeared, "属主慢体期间锁文件未出现（锁语义被弱化）")
            # 另一进程的属主并发 GET：409（跨进程互斥，复审同款断言）
            concurrent = self._owner_get(1)
            self.assertEqual(concurrent.status_code, 409)
            self.assertIn("另一个后端进程", concurrent.text)
        finally:
            status, _body = slow.finish()
        self.assertLess(status, 500)  # 合法体完成：正常业务响应（非 5xx）
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and self.lock_path.exists():
            time.sleep(0.05)
        self.assertFalse(self.lock_path.exists(), "请求结束后锁未释放")
        self.assertEqual(self._owner_get(1).status_code, 200)

    def test_l01_invalid_token_slow_body_never_takes_lock(self):
        """U5-P4 对照：无效 token 慢体不取锁，最终 401。"""
        slow = _SlowBody(self.ports[0], "not-a-real-jwt",
                         self.task_id, OUTSIDER_BODY)
        try:
            time.sleep(0.5)
            self.assertFalse(self.lock_path.exists())
            self.assertEqual(self._owner_get(1).status_code, 200)
        finally:
            status, body = slow.finish()
        self.assertEqual(status, 401)
        self.assertIn("令牌", body)


if __name__ == "__main__":
    unittest.main()
