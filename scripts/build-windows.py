#!/usr/bin/env python3
"""Source preview bundle: explicit allowlist, no local settings or credentials."""
from pathlib import Path
import zipfile

root=Path(__file__).resolve().parents[1]
output=root/'dist/UTA-Windows-preview.zip'
output.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
    for name in ('uta_windows.py','uta_auth.py','UTA.ps1','UTA.ico','install.ps1','README.md'):
        archive.write(root/'windows'/name,'UTA/'+name)
    archive.write(root/'python/uta_transfer.py','UTA/uta_transfer.py')
    archive.write(root/'python/uta_port.py','UTA/uta_port.py')
    archive.write(root/'docs/TRANSFERS.md','UTA/TRANSFERS.md')
    archive.write(root/'docs/REMOTE-DEVICES.md','UTA/REMOTE-DEVICES.md')
print(output)
