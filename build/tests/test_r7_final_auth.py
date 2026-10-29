"""r7-finalfix 泳道 R3-03：密钥首次建库（mkdir/os.open）存储失败纳入应急回退。

复审实测（out/external_reviews/final_verify_v2.md §R3-03）：三种存储失败
形态均绕过既有应急回退 → ``_secret()`` 抛裸 ``PermissionError`` /
``FileExistsError`` → 真实 ``POST /api/auth/login`` 500，与 ``_secret``
docstring「只读/权限 → 进程内应急密钥」的承诺相反：

1. ``.jwt_secret`` 被占为目录 → 首开 ``os.open`` ``PermissionError``；
2. 密钥父目录被普通文件占位 → ``mkdir`` ``FileExistsError``；
3. 只读数据目录且无密钥文件 → 首开 ``os.open`` ``PermissionError``。

钉子（撤修复必红）：真实 app + TestClient——三种形态下 login 均 200，且应
急密钥跨请求稳定（首个令牌仍能通过 ``/api/auth/me`` 校验）；空密钥文件自
愈（MM-03 既有行为）与密钥文件存在且可读的正常路径逐位不变。

形态 2/3 的注入说明：密钥父目录即 ``users.db`` 所在目录，真实文件系统层面
无法「只坏密钥存储而保住 users.db」——本机实测只读目录（icacls 拒绝写）
下 WAL 连 users.db 都打不开（``OperationalError``）。故这两处只在存储边界
注入与真实文件系统完全一致的异常类/errno（``FileExistsError(EEXIST)`` /
``PermissionError(EACCES)``），经真实 ``_secret()`` 代码路径触发；形态 1
用真实目录占位，零注入。另附真实 ACL/chmod 只读目录下 ``_secret()`` 直调
的应急锚点（与复审探针同口径）。
"""
import errno
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
_fixture = tempfile.TemporaryDirectory(prefix="rf_r7_final_auth_")
os.environ["COBOOKSHELF_DATA_DIR"] = _fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from custom_backend import auth as auth_mod
from custom_backend.main import app


class R303SecretBootstrapFallbackTests(unittest.TestCase):
    """R3-03：首次建库的存储失败必须与空密钥写回失败同层回退应急密钥。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        cls.username = "r303-final@example.test"
        cls.password = "R303-Final-Pass-9"
        os.environ["COBOOKSHELF_DATA_DIR"] = _fixture.name
        auth_mod._EMERGENCY_SECRET = None
        auth_mod._EMERGENCY_WRITE_LOGGED = False
        # 直建账号（不走 /api/auth/register，避免与其他模块共享限流桶）
        auth_mod.create_user(cls.username, cls.password)
        # 健康自举：首次 login 经 O_EXCL 正常路径建立 .jwt_secret
        r = cls.client.post("/api/auth/login",
                            json={"username": cls.username,
                                  "password": cls.password})
        assert r.status_code == 200, f"健康路径自举失败：{r.status_code} {r.text}"

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    # ---- 基础设施 -------------------------------------------------------

    def _secret_path(self) -> Path:
        return auth_mod.data_dir() / ".jwt_secret"

    def _login(self):
        return self.client.post(
            "/api/auth/login",
            json={"username": self.username, "password": self.password})

    def _me(self, token: str):
        return self.client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    def _reset_secret_storage(self) -> None:
        p = self._secret_path()
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists() or p.is_symlink():
            p.unlink()

    def setUp(self):
        # 防同进程其他测试模块改写 env：本组用例一律指向本模块 fixture，
        # 用毕还原（见 tearDown）
        self._prev_env = os.environ.get("COBOOKSHELF_DATA_DIR")
        os.environ["COBOOKSHELF_DATA_DIR"] = _fixture.name
        auth_mod._EMERGENCY_SECRET = None
        auth_mod._EMERGENCY_WRITE_LOGGED = False
        self._reset_secret_storage()

    def tearDown(self):
        self._reset_secret_storage()
        auth_mod._EMERGENCY_SECRET = None
        auth_mod._EMERGENCY_WRITE_LOGGED = False
        if self._prev_env is None:
            os.environ.pop("COBOOKSHELF_DATA_DIR", None)
        else:
            os.environ["COBOOKSHELF_DATA_DIR"] = self._prev_env

    @staticmethod
    def _deny_dir_writes(directory: Path) -> bool:
        """真实只读目录：Windows icacls 拒写 / POSIX chmod 0555。

        返回是否**确实生效**（目录内已无法新建文件）；root/提权环境会绕过
        权限位，此时返回 False 供调用方跳过真实锚点。
        """
        if os.name == "nt":
            user = os.environ.get("USERNAME") or "Everyone"
            r = subprocess.run(
                ["icacls", str(directory), "/deny", f"{user}:(W)"],
                capture_output=True)
            if r.returncode != 0:
                return False
        else:
            try:
                os.chmod(directory, 0o555)
            except OSError:
                return False
        probe = directory / ".deny_probe"
        try:
            fd = os.open(str(probe), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            probe.unlink()
            return False  # 仍可新建文件 → 只读未生效
        except OSError:
            return True

    @staticmethod
    def _restore_dir_writes(directory: Path) -> None:
        if os.name == "nt":
            user = os.environ.get("USERNAME") or "Everyone"
            subprocess.run(
                ["icacls", str(directory), "/remove:d", user],
                capture_output=True)
        else:
            try:
                os.chmod(directory, 0o755)
            except OSError:
                pass

    # ---- 三种存储失败形态：login 均 200 ＋ 应急密钥跨请求稳定 ------------

    def test_r303_form1_secret_path_occupied_by_directory(self):
        """形态1（真实文件系统，零注入）：.jwt_secret 被目录占用。

        撤修复：os.open 的 PermissionError 冒泡 → login 500 → 本用例红。
        """
        secret = self._secret_path()
        secret.mkdir()  # setUp 已清空密钥文件；用目录占位
        with self.assertLogs("custom_backend.auth", level="ERROR") as logs:
            r1 = self._login()
        self.assertEqual(r1.status_code, 200, r1.text)
        token1 = r1.json()["token"]
        self.assertTrue(any("persist" in line for line in logs.output),
                        f"应急回退必须留 ERROR 诊断日志：{logs.output}")
        # 应急密钥进程内稳定：第二发 login 200，且**首个**令牌仍校验通过
        r2 = self._login()
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(
            self._me(token1).status_code, 200,
            "应急钥必须跨请求稳定：占位期间的令牌仍可鉴权")
        # 占位目录原样保留（应急口径不做任何破坏性清理）
        self.assertTrue(secret.is_dir())
        # 阻塞解除后下一次调用即写回自愈（应急钥退役，换新钥）
        shutil.rmtree(secret)
        r3 = self._login()
        self.assertEqual(r3.status_code, 200, r3.text)
        self.assertEqual(len(secret.read_text(encoding="utf-8").strip()), 64,
                         "自愈必须写回 64 位十六进制密钥")
        self.assertEqual(self._me(r3.json()["token"]).status_code, 200)

    def test_r303_form2_uncreatable_parent_dir(self):
        """形态2：密钥父目录不可创建（被普通文件占位 → mkdir
        FileExistsError）。

        真实 fs 无法只坏密钥父目录而保住同目录的 users.db（父目录即数据
        目录），故在 mkdir 存储边界注入与真实占位一致的
        FileExistsError(EEXIST)，经真实 _secret() 代码路径触发。撤修复：
        异常冒泡 → login 500 → 红。
        """
        secret = self._secret_path()
        parent = secret.parent
        real_mkdir = Path.mkdir

        def occupied_mkdir(self, *args, **kwargs):
            # 仅对 ``_secret()`` 内的密钥父目录 mkdir 注入（auth._connect 对
            # 同一父目录也有 mkdir——真实形态里父目录被文件占位时 users.db
            # 同样不可用，但 login 级钉子要求库可读，故按调用方收窄）。
            if (self == parent
                    and sys._getframe(1).f_code.co_name == "_secret"):
                raise FileExistsError(errno.EEXIST, "File exists", str(self))
            return real_mkdir(self, *args, **kwargs)

        with mock.patch.object(Path, "mkdir", occupied_mkdir):
            with self.assertLogs("custom_backend.auth", level="ERROR") as logs:
                r1 = self._login()
            self.assertEqual(r1.status_code, 200, r1.text)
            token1 = r1.json()["token"]
            r2 = self._login()
            self.assertEqual(r2.status_code, 200, r2.text)
            self.assertEqual(self._me(token1).status_code, 200,
                             "应急钥必须跨请求稳定")
        self.assertTrue(any("persist" in line for line in logs.output),
                        f"应急回退必须留 ERROR 诊断日志：{logs.output}")
        self.assertFalse(secret.exists(), "占位期间密钥文件不得被建出")
        # 解除占位后自愈
        r3 = self._login()
        self.assertEqual(r3.status_code, 200, r3.text)
        self.assertEqual(self._me(r3.json()["token"]).status_code, 200)

    def test_r303_form3_first_open_permission_error(self):
        """形态3：只读数据目录且无密钥文件 → 首开 os.open PermissionError。

        真实只读目录连 users.db 的 WAL 打开都会失败（本机实测
        OperationalError），无法以真实 fs 呈现「库可读＋密钥不可建」，故在
        os.open 存储边界注入与真实只读目录一致的 PermissionError(EACCES)
        （真实 ACL 锚点见 test_r303_form3_real_readonly_dir_direct）。
        撤修复：异常冒泡 → login 500 → 红。
        """
        secret = self._secret_path()
        real_open = os.open

        def readonly_open(path, flags, *args, **kwargs):
            if os.fspath(path) == str(secret):
                raise PermissionError(errno.EACCES, "Permission denied",
                                      os.fspath(path))
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(auth_mod.os, "open", readonly_open):
            with self.assertLogs("custom_backend.auth", level="ERROR") as logs:
                r1 = self._login()
            self.assertEqual(r1.status_code, 200, r1.text)
            token1 = r1.json()["token"]
            r2 = self._login()
            self.assertEqual(r2.status_code, 200, r2.text)
            self.assertEqual(self._me(token1).status_code, 200,
                             "应急钥必须跨请求稳定")
        self.assertTrue(any("persist" in line for line in logs.output),
                        f"应急回退必须留 ERROR 诊断日志：{logs.output}")
        self.assertFalse(secret.exists(), "只读期间密钥文件不得被建出")
        # 目录恢复可写后自愈
        r3 = self._login()
        self.assertEqual(r3.status_code, 200, r3.text)
        self.assertEqual(len(secret.read_text(encoding="utf-8").strip()), 64)

    def test_r303_form3_real_readonly_dir_secret_direct(self):
        """真实文件系统锚点（与复审探针同口径）：真实只读目录（Windows
        ACL 拒写 / POSIX chmod 0555）无密钥文件 → _secret() 直调必须返回
        进程内稳定应急密钥（曾抛裸 PermissionError）。权限注入未生效的环
        境（root 等）跳过——代码路径已由 login 用例钉住。
        """
        anchor = Path(_fixture.name) / "readonly_anchor"
        anchor.mkdir(exist_ok=True)
        prev = os.environ.get("COBOOKSHELF_DATA_DIR")
        os.environ["COBOOKSHELF_DATA_DIR"] = str(anchor)
        auth_mod._EMERGENCY_SECRET = None
        auth_mod._EMERGENCY_WRITE_LOGGED = False
        try:
            if not self._deny_dir_writes(anchor):
                self.skipTest(
                    "真实只读注入未生效（root/受限环境），跳过真实锚点；"
                    "login 级用例已钉住该代码路径")
            try:
                first = auth_mod._secret()
                second = auth_mod._secret()
                self.assertEqual(first, second, "应急钥必须进程内稳定")
                self.assertEqual(len(first), 64)
                self.assertFalse((anchor / ".jwt_secret").exists(),
                                 "只读期间密钥文件不得被建出")
            finally:
                self._restore_dir_writes(anchor)
        finally:
            if prev is None:
                os.environ.pop("COBOOKSHELF_DATA_DIR", None)
            else:
                os.environ["COBOOKSHELF_DATA_DIR"] = prev

    # ---- 既有行为不回归（对照组） ---------------------------------------

    def test_r303_empty_secret_file_self_heal_unchanged(self):
        """MM-03 既有行为（复审对照组成立口径）：空 .jwt_secret → login
        200 且原子重写 64 位密钥——逐位不变。"""
        secret = self._secret_path()
        secret.write_text("", encoding="utf-8")
        with self.assertLogs("custom_backend.auth", level="WARNING") as logs:
            r = self._login()
        self.assertEqual(r.status_code, 200, r.text)
        healed = secret.read_text(encoding="utf-8").strip()
        self.assertEqual(len(healed), 64, "空文件必须自愈写回 64 位密钥")
        self.assertEqual(auth_mod._secret(), healed, "自愈后读取须与磁盘同钥")
        self.assertEqual(self._me(r.json()["token"]).status_code, 200)
        self.assertTrue(any("empty .jwt_secret" in line for line in logs.output),
                        f"空文件事件须留 WARNING 定位日志：{logs.output}")

    def test_r303_healthy_secret_path_unchanged(self):
        """正常路径（密钥文件存在且可读）逐位不变：不重生成、不换钥。"""
        r0 = self._login()  # setUp 清空后自举健康密钥
        self.assertEqual(r0.status_code, 200, r0.text)
        secret = self._secret_path()
        before = secret.read_text(encoding="utf-8")
        r1 = self._login()
        self.assertEqual(r1.status_code, 200, r1.text)
        self.assertEqual(secret.read_text(encoding="utf-8"), before,
                         "正常路径不得重写/换钥")
        self.assertEqual(self._me(r0.json()["token"]).status_code, 200)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
