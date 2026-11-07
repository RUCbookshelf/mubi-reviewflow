"""R9 L7b nails - build/packaging script fixes from the night scan (batch 02/07/08/09).

Every nail in this file is RED on the bfd07ae baseline and GREEN on the fixed
tree, except the deliberately-labelled ``test_already_...`` controls, which pin
behaviours that the baseline ALREADY had (the night scan's own lines had moved
or the r7 wave had fixed the item) - those exist so the "no longer reproduces"
entries carry probe evidence instead of a bare claim.

The lane's rule is: batch/shell/installer scripts are pinned with a static
assertion PLUS a runnable-subset probe (no full build is expected), and
encoding fixes must be proven at the byte level:

* F1-33 / F1-38 - the .iss/.nsi BOMs are asserted on the raw bytes, and (when a
  real compiler is on PATH via RF_ISCC / RF_ISCC_OLD / RF_MAKENSIS) the file is
  compiled to prove the BOM is what makes an ANSI-ACP machine work.
* F1-19 / F1-20 / F1-21 / F1-24 / F1-26 / F1-18 - the REAL subroutines are
  sliced out of build_windows.bat and executed through cmd.exe in a throwaway
  tree, exactly like test_r8_fl8_build.py does for the lock.
* F1-17 / F1-16 - robocopy and the certutil-verified fetch run against real
  binaries with prepared inputs.

Skip policy: cmd.exe-only nails skip on non-Windows; compiler nails skip when
the compiler is absent (CI installs Inno Setup for the Windows job, but the
pytest suite also runs on macOS/Linux runners where no ISCC/NSIS exists).
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
CMD = shutil.which("cmd.exe") if sys.platform == "win32" else None
ROBOCOPY = shutil.which("robocopy") if sys.platform == "win32" else None
NODE = (
    os.environ.get("RF_NODE")
    or shutil.which("node")
    or r"C:\Users\16073\AppData\Local\hermes\node\node.exe"
)

WINBAT = REPO / "build" / "build_windows.bat"
MACOS = REPO / "build" / "build_macos.sh"
LINUX = REPO / "build" / "build_linux.sh"
ISS = REPO / "build" / "installer.iss"
NSI = REPO / "build" / "installer.nsi"

ISCC = os.environ.get("RF_ISCC") or shutil.which("ISCC") or shutil.which("iscc")
ISCC_OLD = os.environ.get("RF_ISCC_OLD")
MAKENSIS = os.environ.get("RF_MAKENSIS") or shutil.which("makensis")

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _bat_tail_subroutines() -> str:
    """The :stagecopy/:resolve_where/:log/:err/:acquire_lock/:release_lock/
    :fetch_verified/:sha256_matches block, verbatim, out of the real bat."""
    text = _read(WINBAT)
    return text[text.index("\n:stagecopy\n"):]


def _bat_block(start: str, end: str | None = None) -> str:
    """Slice a build_windows.bat region starting at a top-level :label."""
    text = _read(WINBAT)
    begin = text.index(f"\n{start}\n") + 1
    if end is None:
        return text[begin:]
    return text[begin:text.index(f"\n{end}\n") + 1]


def _write_bat(path: Path, lines: list[str]) -> Path:
    """Write a CRLF .bat. Every entry is normalised first: the script regions
    lifted out of build_windows.bat are multi-line strings with LF endings
    (the tracked blob is LF), and a lone LF inside a cmd.exe batch file is not
    a line break - it silently glued the following command onto the previous
    one (measured: the F1-24 cleanup ran, then jumped straight to its failure
    label)."""
    normalised: list[str] = []
    for entry in lines:
        normalised.extend(entry.replace("\r\n", "\n").split("\n"))
    path.write_bytes(("\r\n".join(normalised) + "\r\n").encode("utf-8"))
    return path


def _run_bat(path: Path, cwd: Path, *, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run([CMD, "/c", str(path)], cwd=str(cwd),
                          capture_output=True, text=True, errors="replace",
                          timeout=timeout)


def _bat_harness(tmp_path: Path, name: str, lines: list[str], *,
                 extra_labels: dict[str, list[str]] | None = None) -> Path:
    """Build a .bat harness: the given lines, the fail-label stubs they jump to,
    and the REAL subroutine tail (so :log/:err/:resolve_where/... all exist)."""
    stub_lines: list[str] = []
    for label, body in (extra_labels or {}).items():
        stub_lines += ["", f":{label}"] + body + ["exit /b 1"]
    return _write_bat(tmp_path / name,
                      ["@echo off", "setlocal EnableExtensions", *lines,
                       *stub_lines, "", _bat_tail_subroutines().rstrip("\r\n"), ""])


def _bat_region(start_marker: str, end_marker: str, *, first_line: str | None = None) -> str:
    """Slice a contiguous region of build_windows.bat between two markers."""
    text = _read(WINBAT)
    begin = text.index(start_marker)
    if first_line:
        begin = text.index(first_line, begin)
    end = text.index(end_marker, begin)
    return text[begin:end].rstrip("\r\n")


def _staging_cleanup_block() -> str:
    """The REAL [2/6] cleanup from the script, ending at the Expand-Archive
    invocation. The end marker must be the command itself - the F1-24 comment
    above it also says "Expand-Archive", so a bare index() would cut the block
    off inside that comment."""
    text = _read(WINBAT)
    begin = text.index('call :log "[2/6]')
    end = text.index('"%POWERSHELL_EXE%" -NoProfile -NonInteractive -Command "Expand-Archive', begin)
    return text[begin:end].rstrip("\r\n")


def powershell_path() -> str:
    """The Windows PowerShell 5.1 path, as a plain string for a .bat harness."""
    sep = chr(92)
    root = os.environ.get("SYSTEMROOT", "C:" + sep + "Windows")
    return sep.join([root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"])


def _make_fake_staging(root: Path, marker: str = "STALE") -> Path:
    """Minimal staging tree whose runtime file can be pinned by a probe."""
    staging = root / "staging"
    (staging / "ReviewFlow" / "runtime").mkdir(parents=True)
    (staging / "ReviewFlow" / "runtime" / "marker.txt").write_text(marker, encoding="utf-8")
    return staging


def _pin_open_exclusive(path: Path):
    """Open `path` with a share mode that blocks deletion (F1-24 probe shape)."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = k32.CreateFileW(str(path.resolve()), 0x80000000, 0, None, 3, 0, None)
    assert handle != wintypes.HANDLE(-1).value, ctypes.get_last_error()
    return k32, handle


# --------------------------------------------------------------------------- #
# F1-33 - installer.iss must be UTF-8 WITH BOM (ISCC 6.0-6.2 decode it as ANSI)
# --------------------------------------------------------------------------- #


def test_f1_33_iss_is_utf8_with_bom():
    """RED at baseline: the first 16 bytes were ``3b 20 e6 9c a8 e7 ac 94 ...``
    (a bare '; ' then UTF-8 木) with no ``EF BB BF``; ISCC 6.0.5 on a CJK ACP
    then aborts with "Missing closing quote" at the first Chinese literal.
    The probe is the byte layer the finding is about, plus the decode fact that
    makes the BOM necessary."""
    raw = ISS.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), (
        "installer.iss must start with a UTF-8 BOM: ISCC before 6.3 decodes a "
        "BOM-less .iss with the machine's ANSI code page"
    )
    body = raw[3:]
    assert any(byte > 127 for byte in body), "the probe needs non-ASCII content to be meaningful"
    assert body.decode("utf-8")  # the BOM+UTF-8 pairing is valid
    with pytest.raises(UnicodeDecodeError):
        body.decode("gbk")


@pytest.mark.skipif(not (ISCC_OLD and CMD), reason="ISCC 6.0-6.2 (RF_ISCC_OLD) not available")
def test_f1_33_old_iscc_compiles_the_bom_file_and_rejects_the_bomless_one(tmp_path):
    """Behaviour on a REAL ISCC 6.0.5: the shipped file compiles, the same
    content without the BOM does not. RED at baseline (the shipped file had no
    BOM, so it hit the second branch)."""
    work = tmp_path / "iss"
    (work / "staging" / "ReviewFlow" / "runtime").mkdir(parents=True)
    (work / "VERSION").write_text("2.1.4\n", encoding="utf-8")
    shutil.copy2(REPO / "build" / "icon.ico", work / "icon.ico")
    (work / "staging" / "ReviewFlow" / "windows_start.py").write_text("x\n", encoding="utf-8")
    shutil.copy2(sys.executable, work / "staging" / "ReviewFlow" / "runtime" / "pythonw.exe")
    body = ISS.read_bytes()[3:]
    (work / "good.iss").write_bytes(b"\xef\xbb\xbf" + body)
    (work / "bad.iss").write_bytes(body)
    (work / "staging2").symlink_to(work / "staging") if False else None
    good = subprocess.run([ISCC_OLD, "/DAppVersion=2.1.4", "good.iss"], cwd=str(work),
                          capture_output=True, text=True, errors="replace", timeout=300)
    assert good.returncode == 0, good.stdout[-2000:] + good.stderr[-2000:]
    bad = subprocess.run([ISCC_OLD, "/DAppVersion=2.1.4", "bad.iss"], cwd=str(work),
                         capture_output=True, text=True, errors="replace", timeout=300)
    assert bad.returncode != 0, "ISCC 6.0.5 accepted the BOM-less file - probe is not biting"
    assert "Missing closing quote" in (bad.stdout + bad.stderr)


# --------------------------------------------------------------------------- #
# F1-38 - installer.nsi must be UTF-8 WITH BOM and makensis gets /INPUTCHARSET
# --------------------------------------------------------------------------- #


def test_f1_38_nsi_has_bom_and_the_build_passes_inputcharset():
    """RED at baseline: installer.nsi started with ``55 6e 69 63 6f 64 65 20``
    ("Unicode ") and no BOM, and build_windows.bat ran ``makensis /DAPP_VERSION=``
    with no /INPUTCHARSET - measured on this ACP-936 machine: "Bad text
    encoding: installer.nsi:32", build aborted."""
    raw = NSI.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "installer.nsi must start with a UTF-8 BOM"
    with pytest.raises(UnicodeDecodeError):
        raw[3:].decode("gbk")
    bat = _read(WINBAT)
    assert re.search(r'"%MAKENSIS%"\s+/INPUTCHARSET\s+UTF8', bat), (
        "build_windows.bat must pass /INPUTCHARSET UTF8 so a bare makensis run "
        "is correct even on a non-UTF-8 ACP"
    )


@pytest.mark.skipif(not (MAKENSIS and CMD), reason="makensis not available")
def test_f1_38_makensis_rejects_the_bomless_nsi_on_an_ansi_acp(tmp_path):
    """Behaviour on REAL makensis 3.10 under the machine's ACP: the BOM-less
    content aborts with a text-encoding error while the shipped file compiles.
    RED at baseline (the shipped file was the failing bytes)."""
    (tmp_path / "Output").mkdir()
    shutil.copy2(REPO / "build" / "icon.ico", tmp_path / "icon.ico")
    (tmp_path / "VERSION").write_text("2.1.4\n", encoding="utf-8")
    (tmp_path / "staging" / "ReviewFlow" / "runtime").mkdir(parents=True)
    (tmp_path / "staging" / "ReviewFlow" / "windows_start.py").write_text("x\n", encoding="utf-8")
    shutil.copy2(sys.executable, tmp_path / "staging" / "ReviewFlow" / "runtime" / "pythonw.exe")
    body = NSI.read_bytes()[3:]
    (tmp_path / "bad.nsi").write_bytes(body)
    (tmp_path / "good.nsi").write_bytes(b"\xef\xbb\xbf" + body)
    bad = subprocess.run([MAKENSIS, "/DAPP_VERSION=2.1.4", "bad.nsi"], cwd=str(tmp_path),
                         capture_output=True, text=True, errors="replace", timeout=300)
    assert bad.returncode != 0, "the BOM-less script compiled - the probe is not biting"
    assert "encoding" in (bad.stdout + bad.stderr).lower()
    good = subprocess.run([MAKENSIS, "/DAPP_VERSION=2.1.4", "good.nsi"], cwd=str(tmp_path),
                          capture_output=True, text=True, errors="replace", timeout=300)
    assert good.returncode == 0, good.stdout[-2000:] + good.stderr[-2000:]


# --------------------------------------------------------------------------- #
# F1-34 / F1-40 - AppVersion fallback must come from VERSION, never a stale pin
# --------------------------------------------------------------------------- #


def test_f1_34_f1_40_no_stale_version_fallback_anywhere():
    """RED at baseline: installer.iss:6 and installer.nsi:5 both fell back to
    ``"2.0.1"`` while VERSION says 2.1.4, and build_windows.bat:28 fell back to
    ``0.0.0`` - three different answers to "what am I building"."""
    iss = _read(ISS)
    nsi = _read(NSI)
    bat = _read(WINBAT)

    def directives(text: str) -> list[str]:
        # only real directives, not the explanatory comments around them
        return [ln for ln in text.splitlines()
                if ln.strip() and not ln.lstrip().startswith((";", "rem ", "rem"))]

    iss_lines = directives(iss.split("[Setup]")[0])
    nsi_lines = directives(nsi.split('Name "')[0])
    assert not any('"2.0.1"' in ln for ln in iss_lines), iss_lines
    assert not any('"2.0.1"' in ln for ln in nsi_lines), nsi_lines
    assert 'set "APP_VERSION=0.0.0"' not in bat, "build_windows.bat still falls back to 0.0.0"
    # both installers now read the repository VERSION file when /D is absent
    assert re.search(r"#define AppVersion\s+Trim\(FileRead\(", iss), (
        "installer.iss must read VERSION instead of pinning a literal"
    )
    assert re.search(r'!define /file APP_VERSION\s+"\$\{__FILEDIR__\}\\\.\.\\VERSION"', nsi), (
        "installer.nsi must read VERSION instead of pinning a literal"
    )
    # and the bat refuses to build without one
    assert "goto :fail_noversion" in bat


@pytest.mark.skipif(CMD is None, reason="cmd.exe not available")
def test_f1_34_bat_stops_when_version_is_missing(tmp_path):
    """Behaviour: the REAL head of build_windows.bat (path setup + VERSION read)
    must stop with :fail_noversion when the root VERSION file is absent.
    RED at baseline: it silently continued with 0.0.0."""
    bat = _read(WINBAT)
    head_block = bat[bat.index('set "SRCDIR=%~dp0"'):bat.index('set "LOG=')]
    bindir = tmp_path / "build"       # %~dp0 of the harness -> VERSION is its sibling
    bindir.mkdir()
    harness = _bat_harness(bindir, "version_nail.bat", [
        'set "BUILDROOT=%~dp0relocated"',
        head_block.rstrip("\r\n"),
        'echo REACHED-END',
        "exit /b 0",
    ], extra_labels={"fail_noversion": ["echo HIT-FAIL-NOVERSION"]})
    proc = _run_bat(harness, tmp_path)
    out = proc.stdout + proc.stderr
    assert "HIT-FAIL-NOVERSION" in out, out
    assert "REACHED-END" not in out, "the version guard did not stop the script"

    # control: with VERSION present the script continues and picks it up
    (tmp_path / "VERSION").write_text("2.1.4\n", encoding="utf-8")
    proc = _run_bat(harness, tmp_path)
    out = proc.stdout + proc.stderr
    assert "REACHED-END" in out, out
    assert "HIT-FAIL-NOVERSION" not in out, out


# --------------------------------------------------------------------------- #
# F1-29 / F1-32 - Inno needs [InstallDelete] (upgrade) and [UninstallDelete]
# --------------------------------------------------------------------------- #


def test_f1_29_iss_clears_the_previous_runtime_before_upgrading():
    """RED at baseline: installer.iss had only [Setup]/[Files]/[Icons]/[Tasks]/
    [Run] and `grep InstallDelete` returned nothing. Inno keeps files that are
    not in the new [Files] list, and windows_start.py:57 puts {app} first on
    sys.path, so stale modules stayed importable."""
    text = _read(ISS)
    section = text[text.index("[InstallDelete]"):text.index("[Icons]")]
    for owned in ('{app}\\runtime', '{app}\\custom_backend', '{app}\\custom_frontend', '{app}\\coscreen'):
        assert owned in section, f"{owned} must be cleared on upgrade"
    assert "{app}\\Uninstall.exe" in section, "the other engine's uninstaller must be retired"


def test_f1_32_iss_removes_the_bytecode_caches_it_never_installed():
    """RED at baseline: no [UninstallDelete] and no [Code]; the app writes
    __pycache__ into {app} at run time (cwd={app}), which Inno's uninstall log
    never saw, so it survived the uninstall forever."""
    text = _read(ISS)
    assert "[UninstallDelete]" in text
    section = text[text.index("[UninstallDelete]"):text.index("[Code]")]
    assert "{app}\\__pycache__" in section
    assert "{app}\\runtime" in section


@pytest.mark.skipif(not (ISCC and CMD), reason="ISCC not available")
def test_f1_29_f1_32_iss_with_delete_sections_still_compiles(tmp_path):
    """Behaviour: the REAL installer.iss (with the new sections) compiles on a
    supported ISCC. A [InstallDelete]/[UninstallDelete]/[Code] syntax mistake is
    not caught by any static assertion above, so this is the compile proof.
    Guarded to keep the parse cost on the one platform that has ISCC."""
    work = tmp_path / "iss"
    (work / "staging" / "ReviewFlow" / "runtime").mkdir(parents=True)
    (work / "VERSION").write_text("2.1.4\n", encoding="utf-8")
    shutil.copy2(REPO / "build" / "icon.ico", work / "icon.ico")
    (work / "staging" / "ReviewFlow" / "windows_start.py").write_text("x\n", encoding="utf-8")
    shutil.copy2(sys.executable, work / "staging" / "ReviewFlow" / "runtime" / "pythonw.exe")
    (work / "installer.iss").write_bytes(ISS.read_bytes())
    proc = subprocess.run([ISCC, "/DAppVersion=2.1.4", "installer.iss"], cwd=str(work),
                          capture_output=True, text=True, errors="replace", timeout=300)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]


# --------------------------------------------------------------------------- #
# F1-30 - the two engines install into the same directory: take over properly
# --------------------------------------------------------------------------- #


def test_f1_30_each_engine_retires_the_others_uninstall_identity():
    """RED at baseline: Inno's AppId is a GUID while the NSIS uninstaller wrote
    HKCU\\...\\Uninstall\\ReviewFlow, so neither engine recognised the other's
    install in the shared directory and the stale NSIS uninstaller's
    ``RMDir /r "$INSTDIR"`` wiped an Inno tree."""
    iss = _read(ISS)
    nsi = _read(NSI)
    # Inno takes the tree over: remove the NSIS uninstaller + its ARP key
    assert "{app}\\Uninstall.exe" in iss
    assert "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\ReviewFlow" in iss
    assert "RegDeleteKeyIncludingSubkeys" in iss
    # NSIS recognises an Inno-owned tree and refuses to delete it
    assert "unins000.exe" in nsi, "the NSIS uninstaller must detect an Inno install"
    # and both write a marker of who owns the directory
    assert "reviewflow-install.marker" in iss and "reviewflow-install.marker" in nsi


@pytest.mark.skipif(CMD is None, reason="cmd.exe not available")
def test_f1_36_uninstall_guard_ordering_is_pinned(tmp_path):
    """Static pin on the uninstaller's ordering contract (the same facts the
    F1-30 nail above needs): the recursive delete happens after the install
    location is re-read, and the ARP entry is removed after the files.

    A real silent install/uninstall was exercised by hand during the fix
    (build/installer.nsi compiled with makensis 3.10, installed to a
    throwaway directory with /D, then uninstalled); it is not automated here
    because an automated run would write HKCU and the Start-menu/desktop
    program groups of the machine running the tests."""
    text = _read(NSI)
    un_init = text[text.index("Function un.onInit"):text.index('Section "Uninstall"')]
    assert 'ReadRegStr' in un_init and "InstallLocation" in un_init
    assert "Abort" in un_init
    uninstall = text[text.index('Section "Uninstall"'):]
    assert uninstall.index("RMDir /r") < uninstall.index("DeleteRegKey HKCU"), (
        "the ARP entry must not be deleted before the files (that is what left "
        "an 'uninstalled' entry with an orphan directory)"
    )


# --------------------------------------------------------------------------- #
# F1-36 - the NSIS uninstaller must not trust its own location
# --------------------------------------------------------------------------- #


def test_f1_36_nsis_uninstaller_is_pinned_to_the_installed_tree():
    """RED at baseline: no ``un.onInit``, no ``InstallLocation`` read, no
    marker, and the uninstall section was a bare ``RMDir /r "$INSTDIR"`` whose
    ``$INSTDIR`` was whatever directory the (movable) Uninstall.exe ran from."""
    text = _read(NSI)
    assert "Function un.onInit" in text
    assert "AllowRootDirInstall" not in text
    un_init = text[text.index("Function un.onInit"):text.index('Section "Uninstall"')]
    assert "InstallLocation" in un_init
    assert re.search(r'\$\{If\}\s+\$1\s*!=\s*"ReviewFlow"', un_init), (
        "the uninstaller must refuse a location that is not the ReviewFlow tree"
    )
    assert 'StrCpy $INSTDIR "$0"' in un_init


# --------------------------------------------------------------------------- #
# F1-37 - installing/uninstalling over a running ReviewFlow
# --------------------------------------------------------------------------- #


PS_GUARD_MARKERS = ("Get-Process", "StartsWith", ".Kill()", "exit 3")


def test_f1_37_both_installers_stop_a_running_app_first():
    """RED at baseline: neither installer had any process detection (grep for
    FindWindow/nsProcess/taskkill was empty) while launcher.py keeps the backend
    alive after the browser closes, with {app} as its working directory."""
    iss = _read(ISS)
    nsi = _read(NSI)
    for marker in PS_GUARD_MARKERS:
        assert marker in iss, f"Inno guard is missing {marker!r}"
        assert marker in nsi, f"NSIS guard is missing {marker!r}"
    assert "PrepareToInstall" in iss, "Inno must run the guard before copying files"
    assert "CurUninstallStepChanged" in iss
    assert "RF_STOP_INSTALLED_PROCESSES" in nsi


def _iss_guard_script() -> str:
    """Reassemble the PowerShell guard body out of installer.iss' concatenated
    Delphi string literal, so the nail runs the SHIPPED text and not a copy."""
    iss = _read(ISS)
    begin = iss.index("  Script :=")
    end = iss.index(";", iss.index("'exit 3'", begin)) + 1
    literals = re.findall(r"'((?:[^']|'')*)'", iss[begin:end])
    assert literals, "no PowerShell body found in installer.iss"
    return "\r\n".join(piece.replace("''", "'") for piece in literals)


@pytest.mark.skipif(CMD is None, reason="cmd.exe not available")
def test_f1_37_process_guard_detects_and_clears_a_live_install(tmp_path):
    """Behaviour on the guard's REAL PowerShell body (parsed out of
    installer.iss, so the nail tracks the shipped text): an idle install tree
    is left alone, a process running from inside it is killed, and a process
    running from OUTSIDE the tree is not touched (the kill is scoped to the
    install directory, not a blanket python.exe kill). Exit 0 = the tree is
    free afterwards, which is the contract both installers rely on (3 = still
    busy, which they turn into an abort/confirm).
    RED at baseline - there is no body to extract."""
    script = _iss_guard_script()
    assert "exit 3" in script and "Get-Process" in script, script

    app = tmp_path / "ReviewFlow"
    (app / "runtime").mkdir(parents=True)
    victim = app / "runtime" / "python.exe"
    shutil.copy2(os.path.join(os.environ["SYSTEMROOT"], "System32", "cmd.exe"), victim)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    decoy = outside / "python.exe"
    shutil.copy2(victim, decoy)

    ps1 = tmp_path / "guard.ps1"
    ps1.write_text(script, encoding="utf-8")
    ps = os.path.join(os.environ["SYSTEMROOT"], "System32", "WindowsPowerShell",
                      "v1.0", "powershell.exe")
    args = [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(ps1), str(app)]

    idle = subprocess.run(args, capture_output=True, timeout=120)
    assert idle.returncode == 0, "an idle install tree must not be reported as busy"

    child = subprocess.Popen([str(victim), "/c", "ping -n 60 127.0.0.1 >nul"])
    neighbour = subprocess.Popen([str(decoy), "/c", "ping -n 60 127.0.0.1 >nul"])
    try:
        busy = subprocess.run(args, capture_output=True, timeout=180)
        assert busy.returncode == 0, "the guard could not free the install tree"
        child.wait(timeout=30)
        assert child.poll() is not None, "the process running from the install tree survived"
        assert neighbour.poll() is None, "the guard killed a process outside the install tree"
    finally:
        for proc in (child, neighbour):
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=30)


# --------------------------------------------------------------------------- #
# F1-18 - the previous installer must survive a failed compile
# --------------------------------------------------------------------------- #


def test_f1_18_old_installer_is_moved_aside_not_deleted_first():
    """RED at baseline: ``if exist "%INSTALLER%" del /q "%INSTALLER%"`` ran
    immediately before the compiler, and :fail_compiler had no restore path."""
    text = _read(WINBAT)
    compile_block = text[text.index(':log "[6/6]'):text.index(":done\n")]
    assert 'move /y "%INSTALLER%" "%INSTALLER%.prev"' in compile_block
    assert 'del /q "%INSTALLER%"' in compile_block
    assert compile_block.index("%INSTALLER%.prev") < compile_block.index('del /q "%INSTALLER%"'), (
        "the old installer must be moved aside BEFORE the new one is written"
    )
    fail_block = _bat_block(":fail_compiler", ":end_fail")
    assert 'move /y "%INSTALLER%.prev" "%INSTALLER%"' in fail_block, (
        ":fail_compiler must put the previous installer back"
    )


@pytest.mark.skipif(CMD is None, reason="cmd.exe not available")
def test_f1_18_failed_compile_keeps_the_previous_installer(tmp_path):
    """Behaviour: the REAL [6/6] block with a compiler stub that always fails.
    RED at baseline: the old file is gone after the run.

    The stub is a real .exe on purpose: the block invokes ``"%ISCC%"`` without
    ``call``, exactly like the shipped script does with the resolved .exe - a
    .bat stub would take control and never return, which is the F1-19 mechanism
    rather than the F1-18 one this nail is about. cmd.exe honours
    ``/DAppVersion=...`` as an invalid switch, so it exits non-zero."""
    staging = _make_fake_staging(tmp_path)
    (staging / "ReviewFlow" / "windows_start.py").write_text("x\n", encoding="utf-8")
    outdir = tmp_path / "Output"
    outdir.mkdir()
    old_installer = outdir / "ReviewFlow-Setup-x64.exe"
    old_installer.write_bytes(b"OLD-INSTALLER-CONTENT")
    compiler = tmp_path / "fake_iscc.exe"
    shutil.copy2(os.path.join(os.environ["SYSTEMROOT"], "System32", "cmd.exe"), compiler)

    body = _read(WINBAT)
    block = body[body.index('call :log "[6/6]'):body.index("\n:done\n")]
    harness = _bat_harness(tmp_path, "compile_nail.bat", [
        f'set "SRCDIR={tmp_path}"',
        f'set "INSTALLER={old_installer}"',
        f'set "LOG={tmp_path / "compile.log"}"',
        'set "INSTALLER_ENGINE=Inno Setup"',
        f'set "ISCC={compiler}"',
        block.rstrip("\r\n"),
    ], extra_labels={
        "fail_compiler": [
            # the REAL :fail_compiler body, so the restore itself is under test
            *[ln for ln in _bat_block(":fail_compiler", ":end_fail").splitlines()
              if not ln.lstrip().startswith(("rem ", "rem"))
              and ln.strip() != "goto :end_fail"],
            "echo HIT-FAIL-COMPILER",
        ],
        "end_fail": ["echo HIT-END-FAIL"],
    })
    proc = _run_bat(harness, tmp_path)
    out = proc.stdout + proc.stderr
    assert "HIT-FAIL-COMPILER" in out, out
    assert old_installer.exists(), "a failed compile destroyed the previous installer"
    assert old_installer.read_bytes() == b"OLD-INSTALLER-CONTENT"
    assert not (outdir / "ReviewFlow-Setup-x64.exe.prev").exists(), "the backup was not restored"


# --------------------------------------------------------------------------- #
# F1-19 - `where` a checked exe, then run the checked exe
# --------------------------------------------------------------------------- #


def test_f1_19_no_bare_tool_invocations_remain():
    """RED at baseline: ``where curl.exe``/``where powershell.exe`` approved the
    real exe, then ``curl``/``powershell``/``node`` were called by bare name,
    which cmd resolves through PATH+PATHEXT - a curl.bat earlier in PATH ran
    instead and, being an inter-batch call, never returned."""
    text = _read(WINBAT)
    assert ":resolve_where" in text
    for bare in (r"(?m)^curl\s", r"(?m)^powershell\s", r"(?m)^node\s"):
        assert not re.search(bare, text), f"bare tool invocation still present: {bare}"
    for bound in ('"%CURL_EXE%"', '"%POWERSHELL_EXE%"', '"%NODE_EXE%"'):
        assert bound in text, f"{bound} must be the invocation form"


@pytest.mark.skipif(CMD is None, reason="cmd.exe not available")
def test_f1_19_where_shadow_does_not_hijack_the_bound_path(tmp_path):
    """Behaviour: a PATH-shadowing curl.bat is FIRST in PATH while a real
    curl.exe sits later. ``where curl.exe`` approves the exe; :resolve_where
    returns that exe; the BARE name (what the baseline called) runs the shadow
    instead. RED at baseline: only the bare-name invocation existed."""
    shadow = tmp_path / "shadow"
    real = tmp_path / "real"
    shadow.mkdir()
    real.mkdir()
    _write_bat(shadow / "curl.bat", ["@echo off", "echo SHADOW-CURL-BAT-RAN", "exit /b 7"])
    # any real .exe will do; cmd.exe stands in for curl.exe
    shutil.copy2(os.path.join(os.environ["SYSTEMROOT"], "System32", "cmd.exe"),
                 real / "curl.exe")
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(shadow), str(real), env["PATH"]])

    body = _read(WINBAT)
    resolver = body[body.index(":resolve_where"):body.index("\n:log\n")]
    harness = _write_bat(tmp_path / "resolve_nail.bat", [
        "@echo off", "setlocal EnableExtensions",
        'set "CURL_EXE="',
        "call :resolve_where curl.exe CURL_EXE",
        'echo RESOLVED=%CURL_EXE%',
        # the baseline form: bare name, resolved by cmd through PATHEXT
        "call curl --version",
        'echo BARE-EXIT=%ERRORLEVEL%',
        # the fixed form: the path `where` reported
        '"%CURL_EXE%" /c echo BOUND-EXE-RAN',
        'echo BOUND-EXIT=%ERRORLEVEL%',
        "exit /b 0",
        "",
        resolver.rstrip("\r\n"),
        "",
    ])
    proc = subprocess.run([CMD, "/c", str(harness)], cwd=str(tmp_path), env=env,
                          capture_output=True, text=True, errors="replace", timeout=120)
    out = proc.stdout + proc.stderr
    assert f"RESOLVED={real / 'curl.exe'}" in out, out
    # the finding reproduces: the bare name ran the shim
    assert "SHADOW-CURL-BAT-RAN" in out, out
    assert "BARE-EXIT=7" in out, out
    # the fix: the bound full path runs the approved exe
    assert "BOUND-EXE-RAN" in out, out
    assert "BOUND-EXIT=0" in out, out


# --------------------------------------------------------------------------- #
# F1-20 - Expand-Archive must survive an apostrophe in the repository path
# --------------------------------------------------------------------------- #


def test_f1_20_expand_archive_uses_literal_path():
    """RED at baseline: ``Expand-Archive -Force '%DOWNLOADS%\\%PYEMBED_ZIP%'
    '%STAGING%\\runtime'`` used single-quoted arguments, so a folder named
    ``Kang's docs`` cut the string at the apostrophe and the build failed with
    the misleading "unpacking failed"."""
    text = _read(WINBAT)
    line = next((ln for ln in text.splitlines()
                 if "Expand-Archive" in ln and not ln.lstrip().startswith(("rem ", "rem"))), "")
    assert line, "Expand-Archive invocation disappeared"
    assert "-LiteralPath" in line, "a path with an apostrophe must not be treated as a quoted string"
    assert "'%DOWNLOADS%" not in line and "'%STAGING%" not in line


@pytest.mark.skipif(CMD is None, reason="cmd.exe not available")
def test_f1_20_apostrophe_path_extracts(tmp_path):
    """Behaviour against a real PowerShell: a zip under ``Kang's docs`` extracts
    with -LiteralPath and fails with the old single-quoted form.
    RED at baseline: only the failing form existed."""
    import zipfile

    base = tmp_path / "Kang's docs"
    base.mkdir()
    archive = base / "src.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("python.exe", "payload")
    ps = os.path.join(os.environ["SYSTEMROOT"], "System32", "WindowsPowerShell",
                      "v1.0", "powershell.exe")

    good = subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-Command",
         f'Expand-Archive -Force -LiteralPath "{archive}" -DestinationPath "{base / "out"}"'],
        capture_output=True, text=True, errors="replace", timeout=180)
    assert good.returncode == 0, good.stderr[-1500:]
    assert (base / "out" / "python.exe").exists()

    bad = subprocess.run(
        [ps, "-NoProfile", "-Command",
         f"Expand-Archive -Force '{archive}' '{base / 'out2'}'"],
        capture_output=True, text=True, errors="replace", timeout=180)
    assert bad.returncode != 0, "the single-quoted form unexpectedly worked - probe stale"
    assert not (base / "out2" / "python.exe").exists()


# --------------------------------------------------------------------------- #
# F1-21 - log/err must not re-parse the message
# --------------------------------------------------------------------------- #


def test_f1_21_log_and_err_do_not_interpolate_the_message():
    """RED at baseline: ``echo [%TIME%] %~1`` cut the line at a ``&`` in the
    path and ran the remainder as a command (measured: "系统找不到指定的路径"
    in the log)."""
    text = _read(WINBAT)

    def body(label: str, end: str) -> list[str]:
        block = _bat_block(label, end)
        return [ln for ln in block.splitlines()
                if not ln.lstrip().startswith(("rem ", "rem"))]

    log_block = body(":log", ":acquire_lock")
    assert not any(ln.strip() == "echo [%TIME%] %~1" for ln in log_block), log_block
    assert any('set "RFLINE=' in ln for ln in log_block)
    assert any("echo(!RFLINE!" in ln for ln in log_block)
    err_block = body(":err", ":acquire_lock")
    assert not any(ln.strip() == "echo [ERROR] %~1" for ln in err_block), err_block
    assert any("echo(!RFLINE!" in ln for ln in err_block)


@pytest.mark.skipif(CMD is None, reason="cmd.exe not available")
def test_f1_21_ampersand_path_stays_on_one_log_line(tmp_path):
    """Behaviour on the REAL :log/:err subroutines: a message containing ``&``
    is echoed whole and appended whole. RED at baseline: the line was truncated
    at the ampersand and the tail ran as a command."""
    log = tmp_path / "nail.log"
    harness = _write_bat(tmp_path / "log_nail.bat", [
        "@echo off", "setlocal EnableExtensions",
        'set "LOGOK=1"',
        f'set "LOG={log}"',
        '> "%LOG%" echo header',
        'call :log "C:\\Users\\Kang & Smith\\build"',
        'call :err "failed at C:\\A & B"',
        "exit /b 0",
        "",
        _bat_tail_subroutines().rstrip("\r\n"),
        "",
    ])
    proc = _run_bat(harness, tmp_path)
    written = log.read_text(encoding="utf-8", errors="replace")
    assert "[ERROR] failed at C:\\A & B" in written, written
    assert "Kang & Smith\\build" in written, written
    assert "系统找不到指定的路径" not in written
    assert "系统找不到指定的路径" not in (proc.stdout + proc.stderr)


# --------------------------------------------------------------------------- #
# F1-24 - a staging tree that cannot be removed must stop the build
# --------------------------------------------------------------------------- #


def test_f1_24_staging_cleanup_result_is_checked():
    """RED at baseline: ``rmdir /s /q "%SRCDIR%\\staging" 2>nul`` with no
    errorlevel check, then ``if not exist "%STAGING%\\runtime"`` read the
    surviving old tree as "present" and Expand-Archive -Force stacked a new
    runtime on top of it."""
    text = _read(WINBAT)
    assert ":fail_stageclean" in text
    start = text.index('if not exist "%SRCDIR%\\staging" goto :staging_clean')
    section = text[start:text.index('Expand-Archive -Force -LiteralPath', start)]
    assert section.index("rmdir /s /q") < section.index("goto :fail_stageclean"), (
        "the cleanup must be verified right after it is attempted"
    )
    assert "call :path_exists" in section, (
        "the verification must run in a fresh process: cmd.exe's own if exist "
        "reported NO for a tree that was still on disk after a partial rmdir"
    )
    assert section.index("goto :fail_stageclean") < section.index("mkdir"), (
        "the stale tree must be refused before anything is written into it"
    )


@pytest.mark.skipif(CMD is None, reason="cmd.exe not available")
def test_f1_24_locked_staging_tree_is_reported_not_reused(tmp_path):
    """Behaviour: the REAL cleanup block against a staging tree pinned by an
    open exclusive handle. RED at baseline: the guard printed TRUSTED and the
    stale runtime stayed in place for Expand-Archive to overwrite."""
    staging = _make_fake_staging(tmp_path)
    k32, handle = _pin_open_exclusive(staging / "ReviewFlow" / "runtime" / "marker.txt")
    try:
        harness = _bat_harness(tmp_path, "stage_nail.bat", [
            # the REAL path setup out of the script head (SRCDIR without its
            # trailing backslash is what the block's "%SRCDIR%\staging" needs)
            'set "SRCDIR=%~dp0"',
            'if "%SRCDIR:~-1%"=="\\" set "SRCDIR=%SRCDIR:~0,-1%"',
            'set "STAGING=%~dp0staging\\ReviewFlow"',
            rf'set "POWERSHELL_EXE={powershell_path()}"',
            _staging_cleanup_block(),
            "echo REACHED-EXPAND",
            "exit /b 0",
        ], extra_labels={"fail_stageclean": ["echo HIT-FAIL-STAGECLEAN"],
                         "fail_staging": ["echo HIT-FAIL-STAGING"]})
        proc = _run_bat(harness, tmp_path)
        out = proc.stdout + proc.stderr
        assert "HIT-FAIL-STAGECLEAN" in out, out
        assert "REACHED-EXPAND" not in out, "the build continued onto a stale staging tree"
        assert (staging / "ReviewFlow" / "runtime" / "marker.txt").exists()
    finally:
        k32.CloseHandle(handle)


# --------------------------------------------------------------------------- #
# F1-26 - every staged entry point is checked, not just three
# --------------------------------------------------------------------------- #


def test_f1_26_all_staged_entry_points_are_checked():
    """RED at baseline: nine unchecked ``copy /y ... >nul`` lines followed by
    three ``if not exist`` checks, so a missing THIRD_PARTY_NOTICES.md shipped
    without a licence notice and the installers (whole-tree packaging) never
    looked at the name."""
    text = _read(WINBAT)
    stage = text[text.index('call :log "[4/6]'):text.index("Embedded Python ignores")]
    assert ":stagecopy" in stage
    assert "STAGECOPYFAIL" in stage
    assert "THIRD_PARTY_NOTICES.md" in stage
    # no bare unchecked copy of the notices file remains
    assert 'copy /y "%SRCDIR%\\..\\THIRD_PARTY_NOTICES.md" "%STAGING%\\THIRD_PARTY_NOTICES.md" >nul\n' not in stage
    subroutine = _bat_block(":stagecopy", ":resolve_where")
    assert "if not exist" in subroutine and "STAGECOPYFAIL" in subroutine


@pytest.mark.skipif(CMD is None, reason="cmd.exe not available")
def test_f1_26_missing_notices_is_reported(tmp_path):
    """Behaviour on the REAL :stagecopy subroutine: a missing source sets
    STAGECOPYFAIL and names the file; a present source leaves it unset.
    RED at baseline: the subroutine does not exist."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (tmp_path / "present.md").write_text("notices\n", encoding="utf-8")
    subroutine = _bat_block(":stagecopy", ":resolve_where")
    harness = _bat_harness(tmp_path, "copy_nail.bat", [
        'set "STAGECOPYFAIL="',
        f'call :stagecopy "{tmp_path / "present.md"}" "{staging / "present.md"}"',
        'if defined STAGECOPYFAIL (echo PRESENT=FAILED) else (echo PRESENT=OK)',
        f'call :stagecopy "{tmp_path / "missing.md"}" "{staging / "missing.md"}"',
        'if defined STAGECOPYFAIL (echo MISSING=REPORTED) else (echo MISSING=SWALLOWED)',
        "exit /b 0",
    ])
    proc = _run_bat(harness, tmp_path)
    out = proc.stdout + proc.stderr
    assert "PRESENT=OK" in out, out
    assert "MISSING=REPORTED" in out, out
    assert "missing.md" in out, "the failing file must be named in the message"
    assert (staging / "present.md").read_text(encoding="utf-8") == "notices\n"


# --------------------------------------------------------------------------- #
# F1-17 - the UNC relocation must purge files deleted upstream
# --------------------------------------------------------------------------- #


def test_f1_17_relocation_robocopy_purges():
    """RED at baseline: all three robocopy calls used ``/E`` without
    ``/PURGE``, so a file deleted/renamed upstream survived in
    %BUILDROOT% and was packed into the installer by :copyapp."""
    text = _read(WINBAT)
    calls = [ln for ln in text.splitlines() if ln.strip().lower().startswith("robocopy")]
    assert calls, "no robocopy call found"
    for call in calls:
        assert "/PURGE" in call, f"robocopy without /PURGE: {call}"
        assert "/E " in call


@pytest.mark.skipif(ROBOCOPY is None, reason="robocopy not available")
def test_f1_17_purge_removes_a_deleted_upstream_file(tmp_path):
    """Behaviour against real robocopy: a destination file whose source is gone
    survives /E and disappears with /E /PURGE (the finding's exact probe).
    RED at baseline: the script only every used /E."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "__pycache__").mkdir(parents=True)
    (dst / "__pycache__").mkdir(parents=True)
    (src / "keep.txt").write_text("keep", encoding="utf-8")
    (src / "SPEC.md").write_text("spec", encoding="utf-8")
    (dst / "deleted-upstream.txt").write_text("gone upstream", encoding="utf-8")
    (dst / "old.pyc").write_text("stale", encoding="utf-8")
    (dst / "__pycache__" / "cached.pyc").write_text("cache", encoding="utf-8")

    base = ["/NFL", "/NDL", "/NJH", "/NJS", "/XD", "__pycache__", ".pytest_cache", "/XF", "SPEC.md"]
    subprocess.run([ROBOCOPY, str(src), str(dst), "/E", *base],
                   capture_output=True, timeout=120)
    assert (dst / "deleted-upstream.txt").exists(), "/E alone must keep the stale file (probe) "

    subprocess.run([ROBOCOPY, str(src), str(dst), "/E", "/PURGE", *base],
                   capture_output=True, timeout=120)
    assert not (dst / "deleted-upstream.txt").exists(), "/PURGE must remove it"
    assert not (dst / "old.pyc").exists()
    assert (dst / "keep.txt").exists()
    # the excluded trees are still excluded from the purge, not deleted
    assert (dst / "__pycache__" / "cached.pyc").exists()


# --------------------------------------------------------------------------- #
# F1-16 - a truncated download must not be trusted from the cache
# --------------------------------------------------------------------------- #


def test_f1_16_cached_downloads_are_reverified_before_use():
    """RED at baseline (batch 02's finding): the cache was trusted through
    ``if exist "%DOWNLOADS%\\%PYEMBED_ZIP%" goto :zip_ready`` and curl wrote the
    truncated body straight to the final path. The tree now routes both
    bootstrap downloads through :fetch_verified, which certutil-checks the
    cache on every run and deletes+refetches on a mismatch."""
    text = _read(WINBAT)
    assert "goto :zip_ready" not in text, "the cache-trust shortcut is back"
    assert 'call :fetch_verified "%PYEMBED_URL%"' in text
    assert 'call :fetch_verified "%GETPIP_URL%"' in text
    body = _bat_block(":fetch_verified", ":sha256_matches")
    assert body.index(":sha256_matches") < body.index('"%CURL_EXE%"'), (
        "the cached file must be verified BEFORE any use or refetch"
    )
    assert 'del /q "%~2"' in body, "a mismatching cache entry must be discarded"
    assert 'if exist "%~2" del /q "%~2"' in body


@pytest.mark.skipif(CMD is None, reason="cmd.exe not available")
def test_f1_16_truncated_cache_is_rejected_and_refetched(tmp_path):
    """Behaviour on the REAL :fetch_verified/:sha256_matches subroutines with a
    truncated cache file and a file:// URL serving the complete bytes: the
    truncated copy is rejected (exit 1 on a truncated+pinned-mismatch run) and a
    correct one passes. RED at baseline: nothing verified the cache at all."""
    whole = tmp_path / "whole.bin"
    whole.write_bytes(b"complete payload " * 100)
    digest = hashlib.sha256(whole.read_bytes()).hexdigest()
    cache = tmp_path / "cache.bin"
    cache.write_bytes(whole.read_bytes()[:300])  # the truncated transfer

    subroutines = _bat_tail_subroutines()
    log = tmp_path / "fetch.log"
    harness = _write_bat(tmp_path / "fetch_nail.bat", [
        "@echo off", "setlocal EnableExtensions",
        f'set "LOG={log}"',
        '> "%LOG%" echo fetch-nail',
        'set "LOGOK=1"',
        'call :resolve_where curl.exe CURL_EXE',
        'if not defined CURL_EXE (echo NO-CURL & exit /b 2)',
        f'call :sha256_matches "{cache}" "{digest}"',
        'if errorlevel 1 (echo TRUNCATED=REJECTED) else (echo TRUNCATED=TRUSTED)',
        f'call :fetch_verified "{whole.as_uri()}" "{cache}" "{digest}"',
        'if errorlevel 1 (echo FETCH=FAILED) else (echo FETCH=OK)',
        f'call :sha256_matches "{cache}" "{digest}"',
        'if errorlevel 1 (echo AFTER=BAD) else (echo AFTER=GOOD)',
        "exit /b 0",
        "",
        subroutines.rstrip("\r\n"),
        "",
    ])
    proc = _run_bat(harness, tmp_path, timeout=180)
    out = proc.stdout + proc.stderr
    assert "TRUNCATED=REJECTED" in out, out
    assert "FETCH=OK" in out, out
    assert "AFTER=GOOD" in out, out
    assert cache.read_bytes() == whole.read_bytes(), "the cache was not replaced with the real bytes"


# --------------------------------------------------------------------------- #
# F1-7 - the macOS artifact's architecture must be chosen, not inherited
# --------------------------------------------------------------------------- #


def test_f1_7_macos_pins_and_asserts_the_target_architecture():
    """RED at baseline: PyInstaller got no ``--target-architecture``, release.yml
    labelled the job macos-x64 while macos-14 runners are arm64, and nothing
    downstream inspected the Mach-O."""
    text = _read(MACOS)
    assert "--target-architecture" in text
    assert "resolve_target_arch" in text
    assert "lipo -archs" in text, "the built app's architecture must be asserted"
    assert "REVIEWFLOW_MACOS_ARCH" in text, "an override must exist for a mislabelled runner"
    # the assertion must run on the frozen executable of the built app
    assert "Contents/MacOS/$APP_NAME" in text


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_f1_7_arch_resolution_maps_each_machine(tmp_path):
    """Behaviour on the REAL resolve_target_arch helper: arm64/x86_64 map, an
    unknown machine is rejected with a readable message. RED at baseline: the
    helper did not exist."""
    text = _read(MACOS)
    begin = text.index("resolve_target_arch() {")
    end = text.index("\n}\n", begin) + 3
    script = tmp_path / "arch.sh"
    script.write_text(
        "set -uo pipefail\n" + text[begin:end] + '\n'
        'for m in arm64 aarch64 x86_64 amd64 x64 riscv; do\n'
        '  if out="$(resolve_target_arch "$m")"; then echo "OK:$m:$out"; '
        'else echo "REJ:$m"; fi\n'
        "done\n", encoding="utf-8", newline="\n")
    proc = subprocess.run([BASH, str(script)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    lines = set(proc.stdout.split())
    assert lines == {"OK:arm64:arm64", "OK:aarch64:arm64", "OK:x86_64:x86_64",
                     "OK:amd64:x86_64", "OK:x64:x86_64", "REJ:riscv"}, proc.stdout


# --------------------------------------------------------------------------- #
# F1-13 - CFBundleVersion must not collide for a >=100 hotfix
# --------------------------------------------------------------------------- #


@staticmethod
def _version_helpers() -> str:
    text = _read(MACOS)
    return text[text.index("# F1-9:begin version helpers"):text.index("# F1-9:end version helpers")]


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_f1_13_hotfix_beyond_the_encoding_is_rejected(tmp_path):
    """RED at baseline: 2.1.4.100 -> 2.1.500 == 2.1.5.0 and 2.1.4.1000 ->
    2.1.1400 == 2.1.14.0, i.e. one CFBundleVersion for two releases and a
    non-monotonic release order. The guard now rejects the colliding input and
    leaves every already-published tag's mapping untouched."""
    script = tmp_path / "version.sh"
    script.write_text(
        "set -uo pipefail\n" + _version_helpers() + '\n'
        'check() { if out="$(compute_bundle_versions "$1" 2>&1)"; then '
        'short="${out%%$\'\\n\'*}"; build="${out##*$\'\\n\'}"; '
        'echo "OK:$1:$short:$build"; else echo "REJ:$1"; fi; }\n'
        "for v in 2.1.4 2.1.4.1 2.1.4.99 2.1.4.100 2.1.5.0 2.1.4.1000 2.1.14.0 2.0.5.3 2.1; do check \"$v\"; done\n",
        encoding="utf-8", newline="\n")
    proc = subprocess.run([BASH, str(script)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    lines = set(proc.stdout.split())
    # legacy + normal inputs keep the exact bundle versions Apple already saw
    assert lines == {
        "OK:2.1.4:2.1.4:2.1.400",
        "OK:2.1.4.1:2.1.4:2.1.401",
        "OK:2.1.4.99:2.1.4:2.1.499",
        "REJ:2.1.4.100",
        "OK:2.1.5.0:2.1.5:2.1.500",
        "REJ:2.1.4.1000",
        "OK:2.1.14.0:2.1.14:2.1.1400",
        "OK:2.0.5.3:2.0.5:2.0.503",
        "OK:2.1:2.1:2.1.0",
    }, proc.stdout


# --------------------------------------------------------------------------- #
# 销账 controls - the night scan's lines for these items no longer reproduce
# --------------------------------------------------------------------------- #


def test_already_fixed_f1_11_f1_4_shared_work_dirs_are_locked():
    """F1-11 (build_macos.sh:19-20) and F1-4 (build_linux.sh:6-11) claimed both
    scripts ``rm -rf`` a fixed work/dist with no mutex. The current tree already
    carries the F1-3 mkdir lock in BOTH scripts (r7 wave, present at bfd07ae),
    so the finding does not reproduce. Control nail: it must stay that way."""
    for script in (LINUX, MACOS):
        text = _read(script)
        assert ".build-dist.lock" in text, f"{script.name} lost its lock"
        assert 'if ! mkdir "$LOCK' in text
        assert "trap 'rm -rf \"$LOCK\"' EXIT" in text
        # the lock precedes the work; the only rm -rf before it is the
        # documented FORCE override of the lock itself
        lock_at = text.index('mkdir "$LOCK"')
        for match in re.finditer(r'rm -rf "([^"]+)"', text):
            if match.group(1) in ("$LOCK", "$WORK", "$DIST", "$BUILD/work-macos"):
                continue
            assert match.start() > lock_at, f"{script.name}: rm -rf before the lock"


def test_already_fixed_f1_12_iconset_slots_carry_their_pixel_size():
    """F1-12 claimed the 512 iconset slots were filled with 256px content.
    Already fixed in the baseline (F1-8/F1-11 of r7): the 512 slot is copied
    from the 512 source and 512@2x is produced by an explicit 1024 upscale.
    Control nail: the exact pre-fix ``cp`` lines must not come back."""
    text = _read(MACOS)
    assert 'cp "$ICONSET/icon_256x256.png" "$ICONSET/icon_512x512.png"' not in text
    assert 'cp "$ICONSET/icon_256x256.png" "$ICONSET/icon_256x256@2x.png"' not in text
    assert 'cp "$ICONSET/icon_512x512.png" "$ICONSET/icon_256x256@2x.png"' in text
    assert re.search(r'sips -z 1024 1024 .*--out "\$ICONSET/icon_512x512@2x\.png"', text)


def test_already_fixed_f1_14_f1_5_sumfiles_are_portable():
    """F1-14 (build_macos.sh:64) and F1-5 (build_linux.sh:70) claimed the
    SHA256SUMS.txt files record the build machine's absolute path. Already
    fixed in the baseline (r7 F1-5/F1-12): both are written from inside $DIST
    with the bare artifact name. Control nail + probe on the record format."""
    linux = _read(LINUX)
    macos = _read(MACOS)
    assert '( cd "$DIST" && sha256sum "ReviewFlow-${APPIMAGE_ARCH}.AppImage" > SHA256SUMS.txt )' in linux
    assert '( cd "$DIST" && shasum -a 256 "$APP_NAME.dmg" > SHA256SUMS.txt )' in macos
    assert 'sha256sum "$DIST/ReviewFlow' not in linux
    assert 'shasum -a 256 "$DIST/' not in macos


def test_already_fixed_f1_6_appimagetool_runs_without_fuse():
    """F1-6 claimed appimagetool was exec'd directly with no FUSE fallback.
    Already fixed in the baseline (r7 F1-4/F1-6): APPIMAGE_EXTRACT_AND_RUN=1
    makes it self-extract. Control nail: the env var must stay on the
    invocation, and the pinned 1.9.1 digest must stay in front of it."""
    text = _read(LINUX)
    assert 'APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGETOOL"' in text
    assert 'sha256sum --check --strict' in text
    assert text.index("sha256sum --check --strict") < text.index('APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGETOOL"')


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_already_fixed_f1_9_version_guard_rejects_malformed_version(tmp_path):
    """F1-9 claimed build_macos.sh had zero format validation for VERSION. The
    baseline already carries the r7 F1-9 guard (test_r8_fl8_build pins it), so
    the finding does not reproduce: the exact probe inputs from the batch
    (``2.1.4-beta``, empty, ``2.1.4.``) are all rejected before any arithmetic.
    Control nail, run against the REAL helper block."""
    script = tmp_path / "v.sh"
    script.write_text(
        "set -uo pipefail\n" + _version_helpers() + '\n'
        'for v in "2.1.4-beta" "" "2.1.4." "1.2.3.4.5" "v2.1.4"; do\n'
        '  if validate_app_version "$v" 2>/dev/null; then echo "ACCEPTED:$v"; else echo "REJECTED:$v"; fi\n'
        "done\n", encoding="utf-8", newline="\n")
    proc = subprocess.run([BASH, str(script)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "ACCEPTED" not in proc.stdout, proc.stdout
    assert proc.stdout.count("REJECTED") == 5
