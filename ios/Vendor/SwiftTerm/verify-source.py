#!/usr/bin/env python3
"""Verify vendored bytes and reverse the documented patch to original hashes.

Read-only for this source tree. Uses only a disposable copy, with no network.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'SOURCE-MANIFEST.json').read_text())
assert manifest['upstreamRevision'] == 'a183c15b52858530a17de57dcc72b93eeebc9857'
for name, item in manifest['files'].items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == item['sha256'], name
with tempfile.TemporaryDirectory(prefix='swiftterm-provenance-') as folder:
    candidate = Path(folder)
    for name in manifest['files']:
        target = candidate / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    subprocess.run(['git', 'apply', '--reverse', str(root / 'DOWNSTREAM.patch')], cwd=candidate, check=True)
    for name, item in manifest['files'].items():
        assert hashlib.sha256((candidate / name).read_bytes()).hexdigest() == item['upstreamSHA256'], name
print('Verified', len(manifest['files']), 'vendored files and exact reverse patch to upstream', manifest['upstreamRevision'])
