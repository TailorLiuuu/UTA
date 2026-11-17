#!/usr/bin/env python3
"""Build an allowlisted receiver + handoff docs ZIP; never include local state."""
import hashlib
from pathlib import Path
import subprocess
import sys
import zipfile

root = Path(__file__).resolve().parents[1]
subprocess.run([sys.executable, str(root / 'scripts/build-server.py')], check=True)
sources = {
    'uta-server': 'dist/uta-server',
    'README.md': 'docs/README-MAC-COMPILE-FLASH.md',
    'TRANSFERS.md': 'docs/TRANSFERS.md',
    'REMOTE-DEVICES.md': 'docs/REMOTE-DEVICES.md',
    'CLIENT-IDENTITY.md': 'docs/CLIENT-IDENTITY.md',
    'LINK-WORKSPACE.md': 'docs/LINK-WORKSPACE.md',
    'THIRD_PARTY.md': 'THIRD_PARTY.md',
    'LICENSE-linux-ch341.txt': 'vendor/linux-ch341/LICENSE',
}
payloads = {name: (root / source).read_bytes() for name, source in sources.items()}
payloads['SHA256SUMS'] = ''.join(
    f'{hashlib.sha256(data).hexdigest()}  {name}\n'
    for name, data in sorted(payloads.items())
).encode('ascii')
output = root / 'dist/UTA-CLI-transfer-preview.zip'
with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
    for name, data in sorted(payloads.items()):
        info = zipfile.ZipInfo('UTA-CLI-transfer-preview/' + name, (2026, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = (0o100755 if name == 'uta-server' else 0o100644) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, data)
print(output)
print('SHA256 ' + hashlib.sha256(output.read_bytes()).hexdigest())
