#!/usr/bin/env python3
"""Bundle the Linux agent into one auditable Python file, with pinned source."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import uta_ch341
agent = uta_ch341.bundle_agent((ROOT / 'python/uta_server.py').read_text())
output = ROOT / 'dist/uta-server'
output.parent.mkdir(exist_ok=True)
output.write_text(agent)
output.chmod(0o755)
print(output)
