"""Check the WHOLE release payload tree against the exact checked-out sources.

MM7-01: the previous version compared only 11 hardcoded files while the Windows
payload ships 354 application source files (coverage 3.1%). This version walks
the payload tree and compares every file of custom_backend/, custom_frontend/
and coscreen/ by sha256, in both directions (missing, extra and tampered files
all fail).

Platform notes / trade-offs:
- windows: the staging tree copies all three application directories as loose
  files (robocopy), so a full three-directory comparison is possible.
- macos/linux: PyInstaller freezes coscreen/*.py into the executable archive and
  only copies coscreen/assets as data, so only the data subtree is comparable
  file-by-file there; the frozen modules themselves are out of reach of a plain
  file comparison (documented limitation, same as the audited finding).
- When no payload tree exists (local runs on a clean checkout) the verifier
  degrades to a full self-consistency pass over the source tree and emits the
  complete sha256 manifest as an auditable baseline. In CI (CI=true) a missing
  payload tree is a hard failure instead - the gate must never silently degrade.
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
# (source subtree, payload directory name, payload prefix inside that directory)
# windows robocopies the three directories whole; the frozen builds only carry
# coscreen/assets as loose data files.
PAYLOAD_TREES = {
    'windows': (
        ('custom_backend', 'custom_backend', None),
        ('custom_frontend', 'custom_frontend', None),
        ('coscreen', 'coscreen', None),
    ),
    'macos': (
        ('custom_backend', 'custom_backend', None),
        ('custom_frontend', 'custom_frontend', None),
        ('coscreen/assets', 'coscreen', 'assets'),
    ),
    'linux': (
        ('custom_backend', 'custom_backend', None),
        ('custom_frontend', 'custom_frontend', None),
        ('coscreen/assets', 'coscreen', 'assets'),
    ),
}
# Mirror the build scripts' copy exclusions (robocopy /XD /XF) on BOTH sides so
# the comparison stays symmetric on every platform.
EXCLUDE_DIRS = {'__pycache__', '.pytest_cache'}
EXCLUDE_FILES = {'SPEC.md'}
EXCLUDE_SUFFIXES = ('Zone.Identifier',)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


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


def _compare_mapping(source_root, payload_root, source_rel, payload_name, payload_prefix, problems, files):
    source_dir = source_root / source_rel
    if not source_dir.is_dir():
        problems.append(f'source tree is missing directory: {source_rel}/')
        return
    source_files = collect_tree(source_dir)
    payload_dirs = find_payload_dirs(payload_root, payload_name)
    if not payload_dirs:
        problems.append(f'payload tree is missing directory {payload_name}/ (from {source_rel}/)')
    payload_files = {}
    for directory in payload_dirs:
        walk_root = directory / payload_prefix if payload_prefix else directory
        if not walk_root.is_dir():
            problems.append(f'payload tree is missing {payload_name}/{payload_prefix}/')
            continue
        # Keys are relative to walk_root, which already corresponds to source_dir,
        # so payload keys line up with source keys without any further prefixing.
        for rel, digest in collect_tree(walk_root).items():
            if rel in payload_files and payload_files[rel] != digest:
                problems.append(f'payload has conflicting copies of {source_rel}/{rel}')
            payload_files[rel] = digest
    for rel in sorted(set(source_files) - set(payload_files)):
        problems.append(f'payload is missing file: {source_rel}/{rel}')
    for rel in sorted(set(payload_files) - set(source_files)):
        problems.append(f'payload has a file absent from the source tree: {source_rel}/{rel}')
    for rel in sorted(set(source_files) & set(payload_files)):
        if source_files[rel] != payload_files[rel]:
            problems.append(f'payload differs from source: {source_rel}/{rel}')
    for rel, digest in source_files.items():
        files[f'{source_rel}/{rel}'] = digest


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

    Returns {'ok', 'mode', 'problems', 'files', 'platform', 'payload'}. With
    payload_root=None (or a missing tree) and require_payload=False this
    degrades to a self-consistency check over the source tree and still returns
    the complete manifest for auditing.
    """
    problems, files = [], {}
    if payload_root is not None and Path(payload_root).is_dir():
        mode = 'payload-compare'
        for source_rel, payload_name, payload_prefix in PAYLOAD_TREES[platform]:
            _compare_mapping(source_root, Path(payload_root), source_rel, payload_name,
                             payload_prefix, problems, files)
        _verify_version(source_root, Path(payload_root), problems, files)
    elif require_payload:
        mode = 'payload-compare'
        problems.append(f'payload tree not found: {payload_root} (CI must not degrade)')
    else:
        mode = 'source-manifest'
        for source_rel, _payload_name, _prefix in PAYLOAD_TREES[platform]:
            source_dir = source_root / source_rel
            if not source_dir.is_dir():
                problems.append(f'source tree is missing directory: {source_rel}/')
                continue
            for rel, digest in collect_tree(source_dir).items():
                files[f'{source_rel}/{rel}'] = digest
        _verify_version(source_root, None, problems, files)
    return {'ok': not problems, 'mode': mode, 'problems': problems, 'files': files,
            'platform': platform, 'payload': str(payload_root) if payload_root else None}


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
    }, indent=2) + '\n', encoding='utf-8')
    if result['problems']:
        for problem in result['problems']:
            print(f'payload mismatch: {problem}', file=sys.stderr)
        raise SystemExit(f'{len(result["problems"])} payload problem(s) for {platform}; '
                         f'see stderr and release-assets/build-manifest-{platform}.json')
    if result['mode'] == 'payload-compare':
        print(f'Verified all {len(result["files"])} application payload files '
              f'for {platform} (full tree comparison).')
    else:
        print(f'No payload tree for {platform}; emitted a self-consistent source '
              f'manifest of {len(result["files"])} files '
              f'(release-assets/build-manifest-{platform}.json).')


if __name__ == '__main__':
    main()
