#!/usr/bin/env python3
"""Synthetic GUI CLI receiver. No network, USB or application configuration."""
import json
import sys
import time
args = sys.argv[1:]
if args == ['wait']:
    time.sleep(30)
elif args[:2] == ['transfers', 'check']:
    assert args[args.index('--to') + 1] == 'a' * 32
    print(json.dumps(dict(ok=True, result=dict(protocol=1, platform='linux'))))
elif args[:1] == ['send']:
    assert args[1] == "/tmp/file with 'quotes'; no-shell.bin"
    assert args[args.index('--to') + 1] == 'a' * 32
    assert args[args.index('--workspace') + 1] == 'sample-project'
    assert args[args.index('--remote-cli') + 1] == '/tmp/receiver path/uta-server'
    print(json.dumps(dict(file_bytes=100, file_size=100)), file=sys.stderr)
    print(json.dumps(dict(ok=True, result=dict(entry='/private/test/verified.bin', id='b' * 32))))
else:
    print(json.dumps(dict(ok=True, args=args)))
