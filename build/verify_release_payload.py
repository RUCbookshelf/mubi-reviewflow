"""Check the WHOLE release payload tree against the exact checked-out sources.

MM7-01: the previous version compared only 11 hardcoded files while the Windows
payload ships 354 application source files (coverage 3.1%). This version walks
the payload tree and compares every file of custom_backend/, custom_frontend/
and coscreen/ by sha256, in both directions (missing, extra and tampered files
all fail).

MM9-02: the Windows staging root additionally receives the application entry
points verbatim from the repo (launcher.py, windows_start.py,
requirements-app.txt, repair_reviewflow.bat, windows_smoke.py, icon.ico,
icon_256.png from build/ and THIRD_PARTY_NOTICES.md from the root -
build_windows.bat copies each of them to the staging root). They are now
compared file-by-file like the trees; tampering with launcher.py previously
left this gate green. Still OUTSIDE the comparison set, deliberately:
runtime/ (the downloaded embedded Python + pip-installed site-packages),
runtime-lock.txt, wheelhouse/ and third_party_sources/ are build-time
generated/downloaded content, not checkout sources - there is no source tree
to compare them against. The staging windows_smoke.py run in build_windows.bat
[5/6] remains the functional backstop for a broken runtime.

Platform notes / trade-offs:
- windows: the staging tree copies all three application directories as loose
  files (robocopy), so a full three-directory comparison is possible.
- macos/linux: PyInstaller freezes the 149 coscreen/*.py sources into the
  executable's embedded archive and only copies coscreen/assets as loose data.
  A per-file comparison of frozen sources is not possible from the payload, so
  this gate does what mm9_verify_g1 §2.3 prescribes instead of pretending:
  * the loose coscreen/assets subtree is compared in both directions;
  * ANY other loose file under a payload coscreen/ directory is compared
    against the source tree when present and rejected when absent from the
    source (a source file missing from the payload is NOT a problem - frozen
    modules legitimately have no loose copy);
  * the frozen entry-point executable itself must exist and its WHOLE sha256
    is recorded in the emitted manifest ('frozen' section), pinning the one
    artifact that carries the frozen sources;
  * the frozen sources' digests are likewise recorded in the 'frozen' section
    from the source tree, so the published manifest covers them for audit
    while 'files' stays exactly what was actually compared.
- When no payload tree exists (local runs on a clean checkout) the verifier
  degrades to a full self-consistency pass over the source tree and emits the
  complete sha256 manifest as an auditable baseline. In CI (CI=true) a missing
  payload tree is a hard failure instead - the gate must never silently degrade.

MM9-12: digests are computed over CRLF→LF-normalized bytes for text files (no
NUL byte) so the same blob yields the same digest on every platform regardless
of the checkout's autocrlf setting; binaries are hashed raw.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]

PAYLOAD_ROOTS = {
    'windows': 'build/staging/ReviewFlow',
    'macos': 'build/dist/ReviewFlow.app',
    'linux': 'build/work-linux/AppDir',
}
# (source subtree, payload directory name, payload prefix inside that directory,
#  required, skip_prefixes) - with required=False the mapping only rejects
# payload files that differ from or are absent in the source tree; source files
# missing from the payload are NOT problems (the frozen platforms compile them
# away) but are recorded in the 'frozen' manifest section instead. skip_prefixes
# keeps a mapping off a subtree that another mapping already owns (without it a
# tampered coscreen/assets file was reported once per mapping).
PAYLOAD_TREES = {
    'windows': (
        ('custom_backend', 'custom_backend', None, True, ()),
        ('custom_frontend', 'custom_frontend', None, True, ()),
        ('coscreen', 'coscreen', None, True, ()),
    ),
    'macos': (
        ('custom_backend', 'custom_backend', None, True, ()),
        ('custom_frontend', 'custom_frontend', None, True, ()),
        ('coscreen/assets', 'coscreen', 'assets', True, ()),
        ('coscreen', 'coscreen', None, False, ('assets/',)),
    ),
    'linux': (
        ('custom_backend', 'custom_backend', None, True, ()),
        ('custom_frontend', 'custom_frontend', None, True, ()),
        ('coscreen/assets', 'coscreen', 'assets', True, ()),
        ('coscreen', 'coscreen', None, False, ('assets/',)),
    ),
}
# MM9-02: (source file, staging-root relative payload path) shipping entries
# build_windows.bat copies verbatim to the Windows staging root (lines with
# `copy /y ... %STAGING%\<name>`). Empty on the frozen platforms, whose entry
# points live inside the PyInstaller executable.
PAYLOAD_FILES = {
    'windows': (
        ('build/launcher.py', 'launcher.py'),
        ('build/windows_start.py', 'windows_start.py'),
        ('build/requirements-app.txt', 'requirements-app.txt'),
        ('build/repair_reviewflow.bat', 'repair_reviewflow.bat'),
        ('build/windows_smoke.py', 'windows_smoke.py'),
        ('build/icon.ico', 'icon.ico'),
        ('build/icon_256.png', 'icon_256.png'),
        ('THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_NOTICES.md'),
    ),
    'macos': (),
    'linux': (),
}
# MM9-02 (macos/linux): the frozen PyInstaller entry-point executable that
# carries the frozen coscreen/*.py sources. It must exist in the payload and
# its whole sha256 is recorded in the 'frozen' manifest section.
FROZEN_ENTRY_NAMES = {'macos': 'ReviewFlow', 'linux': 'ReviewFlow'}
# Mirror the build scripts' copy exclusions (robocopy /XD /XF) on BOTH sides so
# the comparison stays symmetric on every platform.
EXCLUDE_DIRS = {'__pycache__', '.pytest_cache'}
EXCLUDE_FILES = {'SPEC.md'}
EXCLUDE_SUFFIXES = ('Zone.Identifier',)


def sha256_file(path: Path) -> str:
    """sha256 over CRLF→LF-normalized bytes for text files (MM9-12).

    Worktree bytes of a text file depend on the checkout configuration
    (core.autocrlf=true on Windows, LF elsewhere), so raw-byte digests of the
    same blob differed across the three CI platforms and the emitted manifests
    were mutually incomparable. Text files (no NUL byte) are hashed with CRLF
    folded to LF - a local normalization that leaves the repo-wide line-ending
    policy untouched; files containing NUL bytes (icons, images) are hashed
    raw, because there \r\n is content, not a line ending."""
    data = path.read_bytes()
    if b'\x00' in data:
        return hashlib.sha256(data).hexdigest()
    return hashlib.sha256(data.replace(b'\r\n', b'\n')).hexdigest()


def collect_tree(base: Path) -> dict:
    """Map every non-excluded file under base to its sha256, keyed by posix relpath."""
    files = {}
    for path in sorted(base.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(base)
        parts = relative.parts
        if any(part in EXCLUDE_DIRS for part in parts[:-1]):
            continue
        name = parts[-1]
        if name in EXCLUDE_FILES or name.endswith(EXCLUDE_SUFFIXES):
            continue
        files[relative.as_posix()] = sha256_file(path)
    return files


def find_payload_dirs(payload_root: Path, name: str) -> list:
    return sorted(path for path in payload_root.rglob(name) if path.is_dir())


def _compare_mapping(source_root, payload_root, source_rel, payload_name,
                     payload_prefix, problems, files, required=True,
                     skip_prefixes=(), frozen=None):
    source_dir = source_root / source_rel
    if not source_dir.is_dir():
        problems.append(f'source tree is missing directory: {source_rel}/')
        return

    def owned(tree):
        """Drop subtrees another mapping already owns (duplicate-report guard)."""
        if not skip_prefixes:
            return tree
        return {rel: digest for rel, digest in tree.items()
                if not rel.startswith(skip_prefixes)}

    source_files = owned(collect_tree(source_dir))
    payload_dirs = find_payload_dirs(payload_root, payload_name)
    if not payload_dirs:
        if required:
            problems.append(f'payload tree is missing directory {payload_name}/ (from {source_rel}/)')
    payload_files = {}
    for directory in payload_dirs:
        walk_root = directory / payload_prefix if payload_prefix else directory
        if not walk_root.is_dir():
            if required:
                problems.append(f'payload tree is missing {payload_name}/{payload_prefix}/')
            continue
        # Keys are relative to walk_root, which already corresponds to source_dir,
        # so payload keys line up with source keys without any further prefixing.
        for rel, digest in owned(collect_tree(walk_root)).items():
            if rel in payload_files and payload_files[rel] != digest:
                problems.append(f'payload has conflicting copies of {source_rel}/{rel}')
            payload_files[rel] = digest
    if required:
        for rel in sorted(set(source_files) - set(payload_files)):
            problems.append(f'payload is missing file: {source_rel}/{rel}')
    elif frozen is not None:
        # Non-required mapping (frozen platforms): sources with no loose payload
        # copy are recorded for audit instead of being silently forgotten.
        for rel, digest in source_files.items():
            if rel not in payload_files:
                frozen[f'{source_rel}/{rel}'] = digest
    for rel in sorted(set(payload_files) - set(source_files)):
        problems.append(f'payload has a file absent from the source tree: {source_rel}/{rel}')
    for rel in sorted(set(source_files) & set(payload_files)):
        if source_files[rel] != payload_files[rel]:
            problems.append(f'payload differs from source: {source_rel}/{rel}')
    compared = set(source_files) if required else set(payload_files)
    for rel, digest in source_files.items():
        if rel in compared:
            files[f'{source_rel}/{rel}'] = digest


def _verify_frozen_entries(payload_root, platform, problems, frozen):
    """MM9-02 (macos/linux): require the frozen PyInstaller entry-point
    executable and record its whole sha256 (mm9_verify_g1 §2.3) - it is the one
    artifact that carries the frozen coscreen/*.py sources, and a payload
    without it previously passed every check."""
    name = FROZEN_ENTRY_NAMES[platform]
    executables = sorted(p for p in Path(payload_root).rglob(name) if p.is_file())
    if not executables:
        problems.append(f'payload tree is missing the frozen entry point: {name}')
    for executable in executables:
        relative = executable.relative_to(payload_root).as_posix()
        frozen[relative] = sha256_file(executable)


def _compare_root_files(source_root, payload_root, platform, problems, files):
    """MM9-02: compare the staging-root shipping entries (windows) file-by-file."""
    for source_rel, payload_rel in PAYLOAD_FILES[platform]:
        source_path = source_root / source_rel
        if not source_path.is_file():
            problems.append(f'source tree is missing shipping entry: {source_rel}')
            continue
        digest = sha256_file(source_path)
        files[source_rel] = digest
        if payload_root is None:
            continue
        payload_path = Path(payload_root) / payload_rel
        if not payload_path.is_file():
            problems.append(f'payload is missing file: {payload_rel} (from {source_rel})')
        elif sha256_file(payload_path) != digest:
            problems.append(f'payload differs from source: {source_rel} ({payload_rel})')


def _verify_version(source_root, payload_root, problems, files):
    source_version = source_root / 'VERSION'
    if not source_version.is_file():
        problems.append('source tree is missing VERSION')
        return
    expected = sha256_file(source_version)
    files['VERSION'] = expected
    if payload_root is None:
        return
    candidates = [path for path in payload_root.rglob('VERSION') if path.is_file()]
    if not any(sha256_file(path) == expected for path in candidates):
        problems.append('payload VERSION is missing or differs from the source VERSION')


def verify_payload(source_root, payload_root, platform, require_payload=False):
    """Compare the payload tree against the source tree.

    Returns {'ok', 'mode', 'problems', 'files', 'frozen', 'platform',
    'payload'}. With payload_root=None (or a missing tree) and
    require_payload=False this degrades to a self-consistency check over the
    source tree and still returns the complete manifest for auditing. 'files'
    holds exactly what was compared against the payload; 'frozen' holds the
    frozen-platform records (entry-point executable whole digest + source
    digests of the coscreen modules frozen into it) that a per-file comparison
    cannot reach.
    """
    problems, files, frozen = [], {}, {}
    if payload_root is not None and Path(payload_root).is_dir():
        mode = 'payload-compare'
        for source_rel, payload_name, payload_prefix, required, skip in PAYLOAD_TREES[platform]:
            _compare_mapping(source_root, Path(payload_root), source_rel, payload_name,
                             payload_prefix, problems, files, required=required,
                             skip_prefixes=skip, frozen=frozen)
        if platform in FROZEN_ENTRY_NAMES:
            _verify_frozen_entries(Path(payload_root), platform, problems, frozen)
        _compare_root_files(source_root, Path(payload_root), platform, problems, files)
        _verify_version(source_root, Path(payload_root), problems, files)
    elif require_payload:
        mode = 'payload-compare'
        problems.append(f'payload tree not found: {payload_root} (CI must not degrade)')
    else:
        mode = 'source-manifest'
        for source_rel, _payload_name, _prefix, _required, _skip in PAYLOAD_TREES[platform]:
            source_dir = source_root / source_rel
            if not source_dir.is_dir():
                problems.append(f'source tree is missing directory: {source_rel}/')
                continue
            for rel, digest in collect_tree(source_dir).items():
                files[f'{source_rel}/{rel}'] = digest
        _compare_root_files(source_root, None, platform, problems, files)
        _verify_version(source_root, None, problems, files)
    return {'ok': not problems, 'mode': mode, 'problems': problems, 'files': files,
            'frozen': frozen, 'platform': platform,
            'payload': str(payload_root) if payload_root else None}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in PAYLOAD_ROOTS:
        raise SystemExit(f'usage: {sys.argv[0]} <{"|".join(PAYLOAD_ROOTS)}>')
    platform = sys.argv[1]
    payload = root / PAYLOAD_ROOTS[platform]
    # CI must never silently degrade to the source-manifest mode.
    require_payload = os.environ.get('CI') == 'true'
    result = verify_payload(root, payload, platform, require_payload=require_payload)
    output = root / 'release-assets'
    output.mkdir(exist_ok=True)
    (output / f'build-manifest-{platform}.json').write_text(json.dumps({
        'version': (root / 'VERSION').read_text().strip(), 'platform': platform,
        'commit': os.environ.get('GITHUB_SHA'), 'mode': result['mode'],
        'file_count': len(result['files']), 'files': result['files'],
        # MM9-02: entries the per-file comparison cannot reach on the frozen
        # platforms - the PyInstaller entry point's whole digest and the source
        # digests of the modules frozen inside it ('files' stays compared-only).
        'frozen': result['frozen'],
    }, indent=2) + '\n', encoding='utf-8')
    if result['problems']:
        for problem in result['problems']:
            print(f'payload mismatch: {problem}', file=sys.stderr)
        raise SystemExit(f'{len(result["problems"])} payload problem(s) for {platform}; '
                         f'see stderr and release-assets/build-manifest-{platform}.json')
    if result['mode'] == 'payload-compare':
        print(f'Verified all {len(result["files"])} application payload files '
              f'for {platform} (full tree comparison).')
        if result['frozen']:
            print(f'Recorded {len(result["frozen"])} frozen entries for {platform} '
                  f'(entry-point whole digest + frozen source digests, not file-compared).')
    else:
        print(f'No payload tree for {platform}; emitted a self-consistent source '
              f'manifest of {len(result["files"])} files '
              f'(release-assets/build-manifest-{platform}.json).')


if __name__ == '__main__':
    main()
