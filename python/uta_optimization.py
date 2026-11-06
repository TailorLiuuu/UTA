"""Explicit remote administrator setup; passwords stay in SSH/Terminal."""
import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import time
import uuid


class AuthorizationRequired(RuntimeError):
    pass


def commands(server, ssh, source, remote, interactive=False):
    stage = '/tmp/uta-optimize-' + uuid.uuid4().hex + '.py'
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    # A root-private copy prevents a staged-file replacement between verification
    # and execution. Only an explicitly authorized full root shell can run this.
    script = '\n'.join([
        'set -eu', 'umask 077', 'test "$(id -u)" = 0',
        'printf "UTA_ADMIN_GRANTED\\n"',
        'unset SUDO_UID',
        'uta_work=$(mktemp -d /var/tmp/uta-setup.XXXXXXXX)',
        'uta_install=',
        'trap \'rm -rf -- "$uta_work"; if [ -n "$uta_install" ]; then rm -f -- "$uta_install"; fi; rm -f -- ' + shlex.quote(stage) + "' EXIT",
        'cp -- ' + shlex.quote(stage) + ' "$uta_work/uta-server"',
        'printf "%s  %s\\n" ' + digest + ' "$uta_work/uta-server" | sha256sum -c -',
        '/usr/bin/python3 "$uta_work/uta-server" tune-ch341 setup --install-dependencies',
        '/sbin/modprobe vhci_hcd',
        'uta_install=$(mktemp ' + shlex.quote(remote + '.setup.XXXXXXXX') + ')',
        'install -o root -g root -m 755 "$uta_work/uta-server" "$uta_install"',
        'mv -f -- "$uta_install" ' + shlex.quote(remote),
    ])
    elevated = shlex.join(['/bin/sh', '-c', script])
    sudo = 'sudo ' + ('' if interactive else '-n ') + elevated
    command = f'if [ "$(id -u)" = 0 ]; then exec {elevated}; else exec {sudo}; fi'
    args = list(ssh)
    if interactive:
        # Remove only the explicit batch option inserted by UTA; keep host-key
        # verification and ForwardAgent=no. Passwords are never passed as args.
        i = args.index('BatchMode=yes')
        del args[i-1:i+1]
    upload = args + [server['target'], 'umask 077; set -C; cat > ' + shlex.quote(stage)]
    execute = list(args)
    if interactive:
        execute = ['-tt' if a == '-T' else a for a in execute]
    execute += [server['target'], command]
    cleanup = list(ssh) + [server['target'], 'rm -f -- ' + shlex.quote(stage)]
    return upload, execute, cleanup


def execute(server, ssh, source, remote, directory, interactive=False):
    directory.mkdir(parents=True, mode=0o700)
    candidate = directory / 'uta-server.py'
    candidate.write_bytes(source.read_bytes()); candidate.chmod(0o600)
    upload, configure, cleanup = commands(server, ssh, candidate, remote, interactive)
    log = directory / 'setup.log'
    log.touch(mode=0o600)
    try:
        if interactive:
            result = directory / 'result'
            script = directory / 'authorize.command'
            script.write_text('\n'.join([
                '#!/bin/bash', 'set -euo pipefail',
                'trap ' + shlex.quote('uta_result=$?; printf "%s\\n" "$uta_result" > ' + shlex.quote(str(result))) + ' EXIT',
                'echo "UTA: authorize server optimization. Commands run automatically."',
                shlex.join(upload) + ' < ' + shlex.quote(str(candidate)),
                shlex.join(configure) + ' 2>&1 | tee ' + shlex.quote(str(log)),
                'echo "UTA: finished. Return to the app for verified status."', '',
            ]))
            script.chmod(0o700)
            subprocess.run(['/usr/bin/open', '-b', 'com.apple.Terminal', str(script)], check=True, timeout=15)
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                if result.exists() and result.read_text().strip():
                    code = int(result.read_text().strip())
                    break
                time.sleep(.5)
            else:
                raise RuntimeError('授权或服务器操作尚未结束，请先检查终端，再重新检测状态')
        else:
            with candidate.open('rb') as data, log.open('ab') as output:
                uploaded = subprocess.run(upload, stdin=data, stdout=output, stderr=output, timeout=30)
                if uploaded.returncode:
                    detail = log.read_text(errors='replace')
                    if any(word in detail.lower() for word in ('permission denied', 'authentication', 'passphrase')):
                        raise AuthorizationRequired('需要服务器管理员授权，请点击「授权并启用」')
                    raise RuntimeError('服务器优化未完成：' + detail[-2000:].strip())
                operation = subprocess.run(configure, stdout=output, stderr=output, timeout=1800)
            code = operation.returncode
        text = log.read_text(errors='replace')
        if code:
            if not interactive and 'UTA_ADMIN_GRANTED' not in text and 'sudo' in text.lower():
                raise AuthorizationRequired('需要服务器管理员授权，请点击「授权并启用」')
            raise RuntimeError('服务器优化未完成：' + text[-2000:].strip())
    except subprocess.TimeoutExpired:
        raise RuntimeError('服务器配置响应超时，操作可能尚未结束；请查看配置日志并重新检测状态') from None
    finally:
        try:
            subprocess.run(cleanup, capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
        candidate.unlink(missing_ok=True)
        (directory / 'authorize.command').unlink(missing_ok=True)
