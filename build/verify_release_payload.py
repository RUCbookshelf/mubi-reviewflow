"""Check copied/frozen release payload against the exact checked-out sources."""
import hashlib
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
platform = sys.argv[1]
payload = {
    'windows': root / 'build/staging/ReviewFlow',
    'macos': root / 'build/dist/ReviewFlow.app',
    'linux': root / 'build/work-linux/AppDir',
}[platform]
files = ['VERSION', 'custom_backend/main.py', 'custom_backend/report_evidence_api.py',
         'custom_backend/review_imports_api.py', 'custom_frontend/index.html',
         'custom_frontend/report_evidence.js', 'custom_frontend/report_evidence.css',
         'custom_frontend/report_evidence_locale.js', 'custom_frontend/unified_import_workspace.js',
         'custom_frontend/workflow_controls.js', 'custom_frontend/import_review_updates.js']
verified = {}
for relative in files:
    expected = hashlib.sha256((root / relative).read_bytes()).hexdigest()
    candidates = [p for p in payload.rglob(Path(relative).name)
                  if relative == 'VERSION' or p.as_posix().endswith(relative)]
    if not any(hashlib.sha256(p.read_bytes()).hexdigest() == expected for p in candidates if p.is_file()):
        raise SystemExit(f'Release payload is missing or differs from source: {relative}')
    verified[relative] = expected
output = root / 'release-assets'
output.mkdir(exist_ok=True)
(output / f'build-manifest-{platform}.json').write_text(json.dumps({
    'version': (root / 'VERSION').read_text().strip(), 'platform': platform,
    'commit': os.environ.get('GITHUB_SHA'), 'files': verified,
}, indent=2) + '\n', encoding='utf-8')
print(f'Verified {len(verified)} versioned payload files for {platform}.')
