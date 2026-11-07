"""r13 build-1 nail: the Windows build bootstraps pip from an IMMUTABLE
artifact whose digest is pinned.

RED at baseline (1d23f22): ``build_windows.bat:50`` pinned
``GETPIP_SHA256=f4448a01...`` against ``https://bootstrap.pypa.io/get-pip.py``,
a ROLLING file that upstream rewrites on every pip release. Measured today
(``certutil -hashfile`` on the cached copy in
``E:/rf214tmp/dwave/confirm-build-1/get-pip.py``): ``356e0d0d...``. A build
machine without that exact cached byte therefore hard-stops at ``[1/6]`` with
``downloading get-pip.py failed - check the network or proxy`` - a *network*
message for a *checksum* failure, on the release path.

The fix pins a fixed-version pip wheel on ``files.pythonhosted.org`` (immutable
per release) and bootstraps from it: the wheel is a zip, so the runtime can put
it on ``sys.path`` and call pip's own CLI. ``setuptools``/``wheel``/
``packaging`` are pinned the same way (the old get-pip.py installed pip +
setuptools + wheel; ``wheel`` needs ``packaging`` at run time).

These nails are static + one behaviour check on the REAL ``:fetch_verified``
subroutine so a future edit cannot quietly reintroduce a rolling download or
re-merge "download failed" with "digest mismatch".
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BUILD = REPO / "build"
WINBAT = BUILD / "build_windows.bat"
CMD = os.environ.get("COMSPEC") if sys.platform == "win32" else None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _label_block(text: str, label: str) -> str:
    """Slice one batch label (``:label``) up to the next top-level label."""
    start = text.index(f"\n{label}\n") + 1
    rest = text[start + len(label) + 1:]
    offset = len(label) + 1
    for line in rest.splitlines(keepends=True):
        if re.match(r"^:[A-Za-z_][\w]*\s*$", line.rstrip("\r\n")):
            return text[start:start + offset]
        offset += len(line)
    return text[start:]


def _set_value(text: str, name: str) -> str:
    match = re.search(rf'set "{re.escape(name)}=([^"]*)"', text)
    assert match, f'{name} is not set in build_windows.bat'
    return match.group(1)


def _resolved(text: str, name: str) -> str:
    """The value of a batch variable after %VAR% expansion (the build's own
    indirection, e.g. PYEMBED_URL = .../%PYEMBED_VER%/%PYEMBED_ZIP%)."""
    value = _set_value(text, name)
    for _ in range(4):
        expanded = re.sub(
            r"%([A-Za-z0-9_]+)%",
            lambda m: _raw_value(text, m.group(1)) or m.group(0),
            value,
        )
        if expanded == value:
            break
        value = expanded
    return value


def _raw_value(text: str, name: str) -> str | None:
    match = re.search(rf'set "{re.escape(name)}=([^"]*)"', text)
    return match.group(1) if match else None


# --------------------------------------------------------------------------- #
# the source of the pip bootstrapper must be immutable
# --------------------------------------------------------------------------- #


def test_pip_bootstrapper_is_a_fixed_version_pypi_wheel():
    """RED at baseline: GETPIP_URL was bootstrap.pypa.io/get-pip.py."""
    text = _read(WINBAT)
    assert "bootstrap.pypa.io" not in text, (
        "bootstrap.pypa.io/get-pip.py is a rolling file: pinning a digest to it "
        "breaks the build on the next upstream release"
    )
    version = _set_value(text, "PIP_WHEEL_VER")
    url = _set_value(text, "GETPIP_URL")
    assert url.startswith("https://files.pythonhosted.org/packages/"), url
    assert url.endswith(f"/pip-{version}-py3-none-any.whl"), url
    assert "latest" not in url.lower()
    # the local file name is derived from the same version, not written twice
    assert _set_value(text, "GETPIP_FILE") == "pip-%PIP_WHEEL_VER%-py3-none-any.whl"


@pytest.mark.parametrize("stem", ["SETUPTOOLS", "WHEEL", "PACKAGING"])
def test_auxiliary_bootstrap_wheels_are_immutable_and_paired(stem: str):
    """setuptools/wheel/packaging replace the `get-pip.py pip setuptools wheel`
    install; each must come from files.pythonhosted.org with its own digest."""
    text = _read(WINBAT)
    url = _set_value(text, f"{stem}_URL")
    name = _set_value(text, f"{stem}_FILE")
    assert url.startswith("https://files.pythonhosted.org/packages/"), url
    assert url.endswith("/" + name), f"{stem}_URL does not serve {stem}_FILE"
    assert re.fullmatch(r"[0-9a-f]{64}", _set_value(text, f"{stem}_SHA256")), (
        f"{stem}_SHA256 must be a lowercase sha256 hex digest"
    )


def test_every_bootstrap_download_has_a_sha256_pin():
    text = _read(WINBAT)
    assert re.fullmatch(r"[0-9a-f]{64}", _set_value(text, "PYEMBED_SHA256"))
    assert re.fullmatch(r"[0-9a-f]{64}", _set_value(text, "GETPIP_SHA256"))
    for name in ("PYEMBED", "GETPIP", "SETUPTOOLS", "WHEEL", "PACKAGING"):
        assert f'call :fetch_verified "%{name}_URL%"' in text, (
            f"{name} must go through the digest-checking fetcher"
        )


def test_get_pip_script_is_no_longer_downloaded_or_run():
    """RED at baseline: the build ran `python.exe "%DOWNLOADS%\\get-pip.py"`."""
    text = _read(WINBAT)
    assert '"%DOWNLOADS%\\get-pip.py"' not in text
    for line in text.splitlines():
        lowered = line.strip().lower()
        if lowered.startswith("rem"):
            continue
        assert "get-pip.py" not in lowered, line


# --------------------------------------------------------------------------- #
# the wheel must be verified BEFORE it is executed, and bootstrapped from disk
# --------------------------------------------------------------------------- #


def test_pip_wheel_is_verified_before_the_runtime_executes_it():
    text = _read(WINBAT)
    fetch = text.index('call :fetch_verified "%GETPIP_URL%"')
    # the bootstrap driver is the only thing that imports pip out of the wheel
    run = text.index("pip._internal.cli.main")
    assert fetch < run, "the pip wheel is executed before its digest is checked"
    # every wheel handed to the installer is a verified cache entry
    install = text.index("--no-index")
    for name in ("GETPIP", "SETUPTOOLS", "WHEEL", "PACKAGING"):
        assert text.index(f'call :fetch_verified "%{name}_URL%"') < install


def test_bootstrap_uses_the_wheel_itself_and_offline_install():
    """The driver puts the verified wheel on sys.path and calls pip's CLI.

    `python pip.whl/pip` would trip pip's Windows guard
    (pip/_internal/utils/misc.py: protect_pip_from_modification_on_windows)
    because argv[0]'s basename is literally "pip"; running the same code from a
    driver file keeps argv[0] a normal script path (measured on pip 26.3).
    """
    text = _read(WINBAT)
    assert "sys.path.insert(0, sys.argv[1])" in text
    assert "from pip._internal.cli.main import main" in text
    assert "raise SystemExit(main(sys.argv[2:]))" in text
    assert "--no-index" in text, (
        "the bootstrap must install only the verified wheels, never reach the index"
    )
    assert 'del "%RF_PIPBOOT%"' in text, "the generated driver must not be left behind"


# --------------------------------------------------------------------------- #
# download failure and digest mismatch must not share one message
# --------------------------------------------------------------------------- #


def test_download_failure_and_digest_mismatch_have_distinct_messages():
    """RED at baseline: :fetch_verified returned 1 for both, so every caller
    printed "check the network or proxy" even when the network was fine."""
    text = _read(WINBAT)

    download = _label_block(text, ":fail_dlgetpip").lower()
    mismatch = _label_block(text, ":fail_dlgetpip_sha").lower()
    assert "network" in download or "proxy" in download, download
    assert "sha-256" not in download and "sha256" not in download, (
        "a download failure must not be reported as a digest problem"
    )
    assert ("sha-256" in mismatch or "sha256" in mismatch), mismatch
    assert "network" not in mismatch and "proxy" not in mismatch, mismatch
    assert "nothing was executed" in mismatch or "not run" in mismatch, mismatch

    # the embedded runtime's download has the same two-way split
    runtime_download = _label_block(text, ":fail_dlpy").lower()
    runtime_mismatch = _label_block(text, ":fail_dlpy_sha").lower()
    assert "network" in runtime_download or "proxy" in runtime_download
    assert "sha-256" in runtime_mismatch or "sha256" in runtime_mismatch

    # call sites branch on the specific code 2 BEFORE the generic failure code
    for name in ("PYEMBED", "GETPIP", "SETUPTOOLS", "WHEEL", "PACKAGING"):
        at = text.index(f'call :fetch_verified "%{name}_URL%"')
        window = text[at:at + 400]
        two = window.index("if errorlevel 2 goto")
        one = window.index("if errorlevel 1 goto")
        assert two < one, f"{name}: errorlevel 2 must be handled before errorlevel 1"


def test_fetch_verified_returns_two_distinct_failure_codes():
    text = _read(WINBAT)
    body = _label_block(text, ":fetch_verified")
    assert re.search(r"exit /b 2", body), (
        ":fetch_verified must report a digest mismatch with its own exit code"
    )
    assert re.search(r"exit /b 1", body), "download failure stays exit code 1"
    assert body.index('"%CURL_EXE%"') < body.rindex("exit /b 2"), (
        "the mismatch code must come from the post-download verification"
    )


# --------------------------------------------------------------------------- #
# behaviour: the REAL :fetch_verified against a wrong pin / a dead URL
# --------------------------------------------------------------------------- #


def _subroutines() -> str:
    text = _read(WINBAT)
    return text[text.index("\n:stagecopy\n"):].replace("\n", "\r\n")


def _write_bat(path: Path, lines: list[str]) -> Path:
    normalised: list[str] = []
    for entry in lines:
        normalised.extend(entry.replace("\r\n", "\n").split("\n"))
    path.write_bytes(("\r\n".join(normalised) + "\r\n").encode("utf-8"))
    return path


@pytest.mark.skipif(CMD is None, reason="cmd.exe not available")
def test_real_fetch_verified_separates_the_two_failure_modes(tmp_path):
    """Cache hit with a bad pin → 2 (mismatch); unreachable URL → 1 (download)."""
    payload = tmp_path / "wheel.bin"
    payload.write_bytes(b"pinned wheel payload " * 20)
    good = hashlib.sha256(payload.read_bytes()).hexdigest()

    # a "remote" file whose bytes are NOT the pinned ones
    remote = tmp_path / "remote.bin"
    remote.write_bytes(b"different bytes from the pin")
    cached = tmp_path / "cached.bin"
    cached.write_bytes(payload.read_bytes())

    harness = _write_bat(tmp_path / "fetch_codes.bat", [
        "@echo off", "setlocal EnableExtensions",
        f'set "LOG={tmp_path.as_posix()}/nail.log"',
        '> "%LOG%" echo fetch-codes',
        'set "LOGOK=1"',
        'call :resolve_where curl.exe CURL_EXE',
        'if not defined CURL_EXE (echo NO-CURL & exit /b 2)',
        # a cached file that mismatches the pin is discarded and re-downloaded;
        # the replacement bytes still mismatch → 2 (digest), not 1 (network)
        f'call :fetch_verified "{remote.as_uri()}" "{cached}" "{"0" * 64}"',
        'echo CACHED_RC=%ERRORLEVEL%',
        # unreachable URL, no cache: download failure
        f'call :fetch_verified "{(tmp_path / "missing" / "nope.bin").as_uri()}" '
        f'"{tmp_path / "fresh.bin"}" "{good}"',
        'echo FETCH_RC=%ERRORLEVEL%',
        # control: the real bytes with the right pin → 0
        f'call :fetch_verified "{payload.as_uri()}" "{tmp_path / "ok.bin"}" "{good}"',
        'echo OK_RC=%ERRORLEVEL%',
        "exit /b 0",
        "",
        _subroutines().rstrip("\r\n"),
        "",
    ])
    proc = subprocess.run([CMD, "/c", str(harness)], cwd=str(tmp_path),
                          capture_output=True, text=True, timeout=180)
    out = proc.stdout + proc.stderr
    assert "CACHED_RC=2" in out, out
    assert "FETCH_RC=1" in out, out
    assert "OK_RC=0" in out, out


@pytest.mark.skipif(CMD is None, reason="cmd.exe not available")
@pytest.mark.parametrize("label, expected, forbidden", [
    (":fail_dlgetpip", ["network"], ["sha-256", "sha256"]),
    (":fail_dlgetpip_sha", ["sha-256", "nothing was executed"], ["network", "proxy"]),
    (":fail_dlpy", ["network"], ["sha-256", "sha256"]),
    (":fail_dlpy_sha", ["sha-256"], ["network", "proxy"]),
])
def test_real_failure_labels_print_the_right_message(tmp_path, label, expected, forbidden):
    """The REAL labels, lifted from build_windows.bat, print the expected
    diagnosis - so an operator can tell a proxy problem from a bad pin."""
    text = _read(WINBAT)
    parts = []
    for name in (label, ":end_fail", ":err", ":log", ":release_lock"):
        block = _label_block(text, name).replace("\n", "\r\n").rstrip("\r\n")
        parts.append(block)
    log = tmp_path / "nail.log"
    harness = _write_bat(tmp_path / "labels.bat", [
        "@echo off", "setlocal EnableExtensions",
        'set "GITHUB_ACTIONS=1"',
        f'set "LOG={log.as_posix()}"',
        '> "%LOG%" echo labels',
        'set "LOGOK=1"',
        f"call {label}",
        "exit /b 0",
        "",
        *parts,
        "",
    ])
    proc = subprocess.run([CMD, "/c", str(harness)], cwd=str(tmp_path),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)
    out = (proc.stdout + proc.stderr).lower()
    for needle in expected:
        assert needle in out, f"{label}: missing {needle!r} in: {out}"
    for needle in forbidden:
        assert needle not in out, f"{label}: unexpected {needle!r} in: {out}"


@pytest.mark.skipif(
    os.environ.get("REVIEWFLOW_R13_PIP_BOOTSTRAP_E2E") != "1",
    reason="set REVIEWFLOW_R13_PIP_BOOTSTRAP_E2E=1 to download and prove the bootstrap",
)
def test_pinned_wheels_really_bootstrap_pip(tmp_path):
    """Opt-in end-to-end proof (network): download the pinned artifacts, verify
    the hashes exactly like the build does, extract the embedded runtime,
    bootstrap pip from the wheel and run `python -m pip --version`.

    Kept out of the default suite because it downloads ~15 MB; the default nails
    above are static + local behaviour. Run it after touching any pin.
    """
    import urllib.request
    import zipfile

    text = _read(WINBAT)
    artifacts = []
    for name, file_var in (
        ("PYEMBED", "PYEMBED_ZIP"), ("GETPIP", "GETPIP_FILE"),
        ("SETUPTOOLS", "SETUPTOOLS_FILE"), ("WHEEL", "WHEEL_FILE"),
        ("PACKAGING", "PACKAGING_FILE"),
    ):
        url = _resolved(text, f"{name}_URL")
        filename = _resolved(text, file_var)
        digest = _set_value(text, f"{name}_SHA256")
        target = tmp_path / filename
        urllib.request.urlretrieve(url, target)
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        assert actual == digest, f"{filename}: {actual} != pinned {digest}"
        artifacts.append(target)

    runtime = tmp_path / "runtime"
    with zipfile.ZipFile(tmp_path / _resolved(text, "PYEMBED_ZIP")) as archive:
        # The build uses Expand-Archive; extract member by member here so an
        # archive entry can never escape the destination directory.
        root = runtime.resolve()
        for member in archive.infolist():
            target = (runtime / member.filename).resolve()
            assert root == target or root in target.parents, member.filename
            archive.extract(member, runtime)
    (runtime / "python312._pth").write_text(
        "python312.zip\n.\n..\nLib\\site-packages\nimport site\n", encoding="ascii")

    driver = tmp_path / "bootstrap.py"
    driver.write_text(
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from pip._internal.cli.main import main\n"
        "raise SystemExit(main(sys.argv[2:]))\n", encoding="ascii")
    wheels = [str(p) for p in artifacts if p.suffix == ".whl"]
    pip_wheel = next(w for w in wheels if Path(w).name.startswith("pip-"))
    proc = subprocess.run(
        [str(runtime / "python.exe"), str(driver), pip_wheel,
         "install", "--no-index", "--disable-pip-version-check", *wheels],
        capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    version = subprocess.run([str(runtime / "python.exe"), "-m", "pip", "--version"],
                             capture_output=True, text=True, timeout=120)
    assert version.returncode == 0, version.stdout + version.stderr
    assert f"pip {_set_value(text, 'PIP_WHEEL_VER')}" in version.stdout, version.stdout
