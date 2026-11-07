"""r13 tailpiece ① nail: the desktop launcher must REFUSE
``REVIEWFLOW_MODE=server`` instead of silently becoming the desktop app.

RED at baseline (1d23f22, ``build/launcher.py:461-463``)::

    if os.environ.get("REVIEWFLOW_MODE") == "server":
        os.environ.pop("COBOOKSHELF_DATA_DIR", None)
    os.environ["REVIEWFLOW_MODE"] = "desktop"

A caller that asked for the team server mode - and named its data directory -
was rewritten in place: the desktop app started on the *default* directory with
no warning, exit code 0. A service script pointed at ``launcher.py`` therefore
looked healthy while the team UI was gone and the data "looked lost"
(evaluation: ``out/release_plan/drafts/tailpieces_2.1.4.md`` §①).

Fix: refuse with an actionable message, return 2, release a handed-down process
lock, and leave the caller's environment untouched. ``REVIEWFLOW_MODE`` is
still set to ``desktop`` on the normal path.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BUILD = REPO / "build"

sys.path.insert(0, str(BUILD))
sys.path.insert(0, str(REPO))


def _launcher():
    """A fresh module object: other nails mutate module-level state."""
    return importlib.reload(importlib.import_module("launcher"))


class _FakeChild:
    def __init__(self, returncode: int = 0):
        self.returncode = returncode

    def wait(self):
        return self.returncode


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point every user-scoped path at the pytest temp dir."""
    local = tmp_path / "localappdata"
    local.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("REVIEWFLOW_STORAGE_CONFIG",
                       str(tmp_path / "config" / "local-storage.json"))
    monkeypatch.delenv("REVIEWFLOW_PORT", raising=False)
    monkeypatch.delenv("REVIEWFLOW_HOST", raising=False)
    monkeypatch.delenv("REVIEWFLOW_PORT_FILE", raising=False)
    monkeypatch.delenv("REVIEWFLOW_RUNTIME_OVERLAY", raising=False)
    (tmp_path / "config").mkdir()
    return tmp_path


def _never_spawn(*args, **kwargs):
    pytest.fail("the desktop backend must not start when server mode is refused")


# --------------------------------------------------------------------------- #
# the refusal
# --------------------------------------------------------------------------- #


def test_server_mode_is_refused_instead_of_downgraded(sandbox, monkeypatch):
    """RED at baseline: rc 0, mode rewritten to desktop, data dir popped."""
    explicit = sandbox / "explicit-team-data"
    explicit.mkdir()
    monkeypatch.setenv("REVIEWFLOW_MODE", "server")
    monkeypatch.setenv("COBOOKSHELF_DATA_DIR", str(explicit))
    launcher = _launcher()
    notified: list[str] = []
    monkeypatch.setattr(launcher, "_notify_error", notified.append)
    monkeypatch.setattr(launcher, "_say", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "_spawn_server", _never_spawn)

    rc = launcher.main()

    assert rc == 2, "a refused server-mode start must not report success"
    assert notified, "the refusal must be visible to the user"
    message = notified[0]
    assert "uvicorn" in message and "UBUNTU_SERVER.md" in message, message
    assert "REVIEWFLOW_MODE" in message, message


def test_refusal_leaves_the_callers_environment_untouched(sandbox, monkeypatch):
    """The old code popped COBOOKSHELF_DATA_DIR and rewrote REVIEWFLOW_MODE."""
    explicit = sandbox / "explicit-team-data"
    explicit.mkdir()
    monkeypatch.setenv("REVIEWFLOW_MODE", "server")
    monkeypatch.setenv("COBOOKSHELF_DATA_DIR", str(explicit))
    launcher = _launcher()
    monkeypatch.setattr(launcher, "_notify_error", lambda text: None)
    monkeypatch.setattr(launcher, "_say", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "_spawn_server", _never_spawn)

    assert launcher.main() == 2

    assert os.environ.get("REVIEWFLOW_MODE") == "server", (
        "the caller's server intent was rewritten"
    )
    assert os.environ.get("COBOOKSHELF_DATA_DIR") == str(explicit), (
        "the caller's explicit data directory was discarded"
    )
    assert not (sandbox / "config" / "desktop-port.txt").exists(), (
        "the refused start must not publish a desktop port"
    )


def test_refusal_releases_a_handed_down_process_lock(sandbox, monkeypatch):
    """windows_start.py acquires the single-instance lock and hands it to
    main(); an early return that did not release it would leak the lock for the
    lifetime of the process."""
    explicit = sandbox / "explicit-team-data"
    explicit.mkdir()
    monkeypatch.setenv("REVIEWFLOW_MODE", "server")
    monkeypatch.setenv("COBOOKSHELF_DATA_DIR", str(explicit))
    launcher = _launcher()
    released: list[object] = []
    monkeypatch.setattr(launcher, "_notify_error", lambda text: None)
    monkeypatch.setattr(launcher, "_say", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "_spawn_server", _never_spawn)
    monkeypatch.setattr(launcher, "release_process_lock", released.append)
    handle = object()

    assert launcher.main(process_lock=handle) == 2
    assert released == [handle], "the handed-down process lock was not released"


# --------------------------------------------------------------------------- #
# control: an ordinary desktop start is unchanged
# --------------------------------------------------------------------------- #


def test_default_mode_still_starts_the_desktop_app(sandbox, monkeypatch):
    """R2 control: without REVIEWFLOW_MODE the launcher behaves as before."""
    monkeypatch.delenv("REVIEWFLOW_MODE", raising=False)
    data = sandbox / "desktop-data"
    data.mkdir()
    monkeypatch.setenv("COBOOKSHELF_DATA_DIR", str(data))
    launcher = _launcher()
    notified: list[str] = []
    monkeypatch.setattr(launcher, "_notify_error", notified.append)
    monkeypatch.setattr(launcher, "_say", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "find_free_port", lambda *a, **k: 19555)
    monkeypatch.setattr(launcher, "wait_and_open", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "open_browser", lambda url: None)
    spawned: list[tuple] = []
    monkeypatch.setattr(launcher, "_spawn_server",
                        lambda *a, **k: spawned.append(a) or _FakeChild(0))

    assert launcher.main() == 0
    assert spawned, "the desktop backend was not started"
    assert os.environ.get("REVIEWFLOW_MODE") == "desktop"
    assert notified == []
