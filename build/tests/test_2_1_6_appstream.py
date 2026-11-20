"""PKG-01 (2.1.6 maintenance): AppStream metainfo nails.

build/reviewflow.metainfo.xml ships inside the Linux AppImage payload
(build/build_linux.sh copies it to usr/share/metainfo/ in the AppDir). The
file is the product's public metadata face on Linux, so this nail keeps it
REAL and in step with everything it mirrors:

- the <id>/<name>/<summary> triple must stay consistent with the
  reviewflow.desktop entry that build_linux.sh generates inline;
- <project_license> must match the actual root LICENSE (MIT);
- <url type="homepage"> must be the link README.md actually publishes;
- no <screenshots> and no <releases>: metadata must not carry screenshots or
  release history upstream has not actually published (no fabrication). If
  real screenshots/release entries are ever introduced, update this policy
  nail deliberately.

MM7-02-style static discipline: the checks read repo files, parse XML with
the stdlib parser, and syntax-check build_linux.sh via `bash -n` - no
packaging run is required. The XML/consistency nails run on any host with a
Python; the bash syntax check REQUIRES Bash and fails explicitly (never
skips) when it is missing - Bash is part of the full-validation / release
development contract (SafetyR3 F7), present on all three CI runners, and is
NOT an end-user runtime dependency. appstreamcli-level validation is
delegated to the build script's conditional `appstreamcli validate`
(PKG-01 evidence, best effort) - this suite only covers structure and
consistency.
"""
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
METAINFO = REPO / "build" / "reviewflow.metainfo.xml"
LINUX_SCRIPT = REPO / "build" / "build_linux.sh"
LICENSE = REPO / "LICENSE"
README = REPO / "README.md"

HOMEPAGE = "https://github.com/RUCbookshelf/mubi-reviewflow"


def _parse_metainfo() -> ET.Element:
    assert METAINFO.is_file(), (
        "build/reviewflow.metainfo.xml is missing - the Linux payload would "
        "ship without AppStream metadata (build/build_linux.sh PKG-01 block)"
    )
    return ET.parse(METAINFO).getroot()


def _desktop_entry() -> dict:
    """Parse the reviewflow.desktop that build_linux.sh generates inline.

    The desktop file does not exist in the repo as a standalone file - it is
    a heredoc inside build_linux.sh - so the nail reads the same source of
    truth the packaging actually uses.
    """
    text = LINUX_SCRIPT.read_text(encoding="utf-8")
    match = re.search(
        r"cat > \"\$APPDIR/reviewflow\.desktop\" <<'EOF'\n(.*?)\nEOF\n",
        text, re.DOTALL,
    )
    assert match, (
        "build_linux.sh no longer generates reviewflow.desktop inline "
        "(PKG-01 consistency anchors lost)"
    )
    entry = {}
    for line in match.group(1).splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            entry[key.strip()] = value.strip()
    return entry


def test_metainfo_parses_as_desktop_application_component():
    root = _parse_metainfo()
    assert root.tag == "component"
    assert root.get("type") == "desktop-application"


def test_id_launchable_name_summary_match_the_desktop_entry():
    root = _parse_metainfo()
    entry = _desktop_entry()
    # AppStream convention: <id> is the desktop file id (reviewflow.desktop
    # -> reviewflow); <launchable type="desktop-id"> names the desktop file.
    assert root.findtext("id") == "reviewflow"
    launchable = root.find("launchable")
    assert launchable is not None and launchable.get("type") == "desktop-id"
    assert launchable.text == "reviewflow.desktop"
    assert root.findtext("name") == entry["Name"]
    assert root.findtext("summary") == entry["Comment"]


def test_project_license_matches_the_repo_license():
    root = _parse_metainfo()
    assert root.findtext("metadata_license") == "CC0-1.0", (
        "metadata_license describes THIS FILE, not the product "
        "(CC0-1.0 is the AppStream convention for metadata)"
    )
    project_license = root.findtext("project_license")
    license_text = LICENSE.read_text(encoding="utf-8")
    assert "MIT License" in license_text, "root LICENSE changed - keep the metainfo in step"
    assert project_license == "MIT", (
        f"project_license {project_license!r} no longer mirrors the root LICENSE (MIT)"
    )


def test_homepage_is_the_link_readme_actually_publishes():
    root = _parse_metainfo()
    url = root.find("url")
    assert url is not None and url.get("type") == "homepage"
    assert url.text == HOMEPAGE
    assert HOMEPAGE in README.read_text(encoding="utf-8"), (
        "README no longer publishes the homepage link - update the metainfo "
        "and this nail together"
    )


def test_no_fabricated_screenshots_or_release_history():
    """Policy nail: the metainfo must not invent visual or release evidence.

    If real screenshots or a real release history are ever added upstream,
    remove the corresponding assertion here DELIBERATELY together with the
    content change - an accidental <releases>/<screenshots> block is exactly
    the fabrication this file exists to prevent.
    """
    root = _parse_metainfo()
    assert root.find("screenshots") is None, "screenshots present: is the evidence real and published?"
    assert root.find("releases") is None, "releases present: is the history real and published?"


def test_content_rating_and_description_are_present_and_real():
    root = _parse_metainfo()
    rating = root.find("content_rating")
    assert rating is not None and rating.get("type") == "oars-1.1"
    description = root.find("description")
    assert description is not None
    paragraphs = [p for p in description.findall("p") if (p.text or "").strip()]
    assert paragraphs, "description needs at least one non-empty paragraph"


def test_build_linux_installs_the_metainfo_into_the_standard_path():
    script = LINUX_SCRIPT.read_text(encoding="utf-8")
    assert 'mkdir -p "$APPDIR/usr/share/metainfo"' in script
    assert (
        'cp "$BUILD/reviewflow.metainfo.xml" '
        '"$APPDIR/usr/share/metainfo/reviewflow.metainfo.xml"' in script
    ), "build_linux.sh must install the metainfo to usr/share/metainfo/ (PKG-01)"
    # Ruling 2026-11-14: the template is a REQUIRED build input - the script
    # must hard-fail on a missing template, never skip the install. The old
    # best-effort skip could silently drop metadata in production because a
    # test fixture never carried the template.
    assert 'if [ ! -f "$BUILD/reviewflow.metainfo.xml" ]' in script
    assert "refusing to build a payload without AppStream metadata" in script
    assert 'if [ -f "$BUILD/reviewflow.metainfo.xml" ]; then' not in script, (
        "the tolerated-missing-skip pattern must stay removed"
    )
    # Best-effort validation wiring must stay in place: validate when the
    # tool exists, print the limitation loudly when it does not.
    assert "appstreamcli validate" in script
    assert "command -v appstreamcli" in script


def test_build_linux_sh_syntax_is_valid_bash():
    # SafetyR3 F7（主控能力合同已定）：完整验证/发行的**开发与 CI 环境**要求
    # Bash（三平台 runner 均自带）——这是 dev 依赖，不是安装包终端用户的运行
    # 依赖。本语法检查缺 Bash 时**明确失败并指出依赖**，绝不 skip：否则缺
    # Bash 的环境会让完整门禁看似通过（review_safety_r2.md F7）。只对本机实
    # 际取得的那一个/几个 Bash flavour 报通过——未覆盖的 flavour 属证据限制，
    # 不以单一 flavour 成功冒充全量验证。
    bash = shutil.which("bash")
    assert bash is not None, (
        "bash not found on PATH: this syntax check is part of the "
        "full-validation / release development contract (SafetyR3 F7) and "
        "REQUIRES Bash, which all three CI runners provide. Install a Bash "
        "(Git-for-Windows / MSYS / WSL / cygwin) or run inside the release "
        "dev environment. NOTE: this is a DEV/CI-only dependency - NOT an "
        "end-user runtime requirement of the distributed packages."
    )
    # 路径可移植（Task8 箱内实红 + SafetyR2 复核）：绝对路径两 flavour 各翻一
    # 辆车——msys/bash 吞反斜杠（E:\x\y→E:xbuild…），cygwin/bash 不认 E:/…
    # 盘符正斜杠（rc=127）。cwd=REPO 下传**相对** POSIX 路径三 flavour 通吃
    # （Task8 手工验证 `bash -n build/build_linux.sh` rc=0 的正是该形态）。
    proc = subprocess.run(
        ["bash", "-n", LINUX_SCRIPT.relative_to(REPO).as_posix()],
        cwd=REPO, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"bash -n failed: {proc.stderr}"
