# -*- coding: utf-8 -*-
"""sec29 回归钉（deepseek 异构安全复审 2026-11-11·出站 HTTP·发现18）。

SMTP STARTTLS 此前不带 SSLContext——stdlib 回落 ssl._create_stdlib_context()
（实测 verify_mode=CERT_NONE、check_hostname=False），伪造证书的中间人可截获
client.login 的 SMTP 凭据。修复=starttls(context=ssl.create_default_context())；
use_tls=False 的明文路径行为不变（属主显式选择）。
"""
import os
import ssl
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import notify  # noqa: E402


class _FakeSMTP:
    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.tls_context = "never-called"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        return None

    def starttls(self, context=None):
        self.tls_context = context

    def login(self, username, password):
        self.credentials = (username, password)

    def send_message(self, message):
        self.sent = message


class SmtpStarttlsPins(unittest.TestCase):
    def _config(self):
        pw_env = notify.ENV_NAME_PREFIX + "SEC29_SMTP_PW"
        os.environ[pw_env] = "test-password"
        return {"host": "smtp.example.test", "port": 587,
                "from_addr": "a@example.test", "to_addr": "b@example.test",
                "username": "user", "password_env": pw_env,
                "use_tls": True}

    def _send(self, config):
        holder = {}

        class _Cap(_FakeSMTP):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                holder["client"] = self

        with mock.patch.object(notify.smtplib, "SMTP", _Cap), \
                mock.patch.object(notify, "validate_public_hostname",
                                  lambda *a, **k: None):
            notify._send_smtp(config, "text", timeout=5.0)
        return holder["client"]

    def test_starttls_uses_verifying_context(self):
        client = self._send(self._config())
        self.assertIsInstance(client.tls_context, ssl.SSLContext)
        self.assertEqual(client.tls_context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(client.tls_context.check_hostname)
        self.assertEqual(client.credentials, ("user", "test-password"))

    def test_tls_disabled_starttls_not_called(self):
        config = self._config()
        config["use_tls"] = False
        client = self._send(config)
        self.assertEqual(client.tls_context, "never-called")


if __name__ == "__main__":
    unittest.main()
