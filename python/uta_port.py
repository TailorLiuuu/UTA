"""Device-scoped remote operations over existing SSH; portable client half."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import shlex
import sys
import uuid
import uta_transfer as transfer


def add_commands(sub):
    port = sub.add_parser('port', help='Owned/shared remote devices: status, tools and on-demand logs')
    ops = port.add_subparsers(dest='port_action', required=True)
    for action in ('identity', 'list', 'status', 'share', 'grants', 'revoke', 'run', 'logs', 'run-status', 'stop', 'forget'):
        p = ops.add_parser(action)
        p.add_argument('--to', required=True, help='Saved server ID or unique name')
        p.add_argument('--remote-cli', default='/usr/local/bin/uta-server')
        if action in ('status', 'share', 'grants', 'revoke', 'run', 'logs', 'run-status', 'stop', 'forget'):
            p.add_argument('--id', required=True, help='Device ID returned by port list; never a guessed tty number')
        if action in ('share', 'revoke'):
            p.add_argument('--recipient', required=True, help='Receiving client ID; public identifier, not a secret')
        if action == 'share':
            p.add_argument('--hours', type=int, default=24, help='Grant lifetime, 1..720 hours')
        if action in ('run-status', 'stop', 'forget'):
            p.add_argument('--run-id', required=True)
        if action in ('run', 'logs'):
            p.add_argument('--node', help='Explicit node from current status, required when serial selection is ambiguous')
            p.add_argument('--run-id', help='Existing IDs return their receipt and never replay an operation')
        if action == 'logs':
            p.add_argument('--baud', type=int, required=True)
            p.add_argument('--seconds', type=int, default=60)
            p.add_argument('--raw', action='store_true', help='Write lossless serial bytes to stdout; JSON metadata to stderr')
        if action == 'run':
            p.add_argument('--upload', help='Optional firmware file/directory to verify and send first')
            p.add_argument('--workspace', default='default')
            p.add_argument('--timeout', type=int, default=600)
            p.add_argument('--cwd', help='Existing absolute working directory on the receiver')
            p.add_argument('argv', nargs=argparse.REMAINDER)


def ssh_command(server, ssh_args, remote_cli):
    if not re.fullmatch(r'/[A-Za-z0-9_./ -]{1,510}', remote_cli):
        raise ValueError('Use an absolute POSIX receiver CLI path')
    command = shlex.join([remote_cli, 'port', '--client-stdin'])
    command = 'if [ "$(id -u)" = 0 ]; then exec '+command+'; else exec sudo -n '+command+'; fi'
    return ssh_args(server)+[server['target'], command]


def call(argv, client, request, progress=None):
    with transfer.Channel(argv) as wire:
        wire.send(client)
        wire.send(request)
        while True:
            value = wire.read()
            if value.get('event') == 'result':
                return value['result']
            if progress:
                progress(value)


def dispatch(args, servers, identity, ssh_args):
    server = transfer.select_server(servers, args.to)
    client = identity(server['id'])
    if args.port_action == 'identity':
        return dict(client_id=client['client_id'], server_id=server['id'])
    request = dict(action=args.port_action)
    for key in ('id', 'recipient', 'hours', 'node', 'run_id', 'baud', 'seconds', 'timeout', 'cwd'):
        value = getattr(args, key, None)
        if value is not None:
            request[key] = value
    def progress(value):
        if getattr(args, 'raw', False) and value.get('event') == 'serial':
            sys.stdout.buffer.write(base64.b64decode(value['data'], validate=True))
            sys.stdout.buffer.flush()
        else:
            print(json.dumps(value, ensure_ascii=True), file=sys.stderr, flush=True)
    argv = ssh_command(server, ssh_args, args.remote_cli)
    if args.port_action in ('run', 'logs'):
        request['run_id'] = args.run_id or uuid.uuid4().hex
        # Check current ownership/readiness before uploading potentially large firmware.
        current = call(argv, client, dict(action='status', id=args.id))
        if current['state'] != 'attached':
            raise RuntimeError('Device is not ready; wait for its owner to reconnect it')
        if args.port_action == 'run':
            command = args.argv[1:] if args.argv[:1] == ['--'] else args.argv
            if not command or not any(token in arg for arg in command for token in ('{port}', '{usb}', '{device}')):
                raise ValueError('Provide the original tool after --, using {port}, {usb} or {device} for its target')
            if args.upload:
                upload_args = argparse.Namespace(command='send', source=args.upload, to=args.to,
                    workspace=args.workspace, remote_cli=args.remote_cli, remote_platform='posix', retries=2)
                uploaded = transfer.dispatch(upload_args, servers, identity, ssh_args)
                progress(dict(event='upload', result=uploaded))
                command = [v.replace('{upload}', uploaded['entry']).replace('{dir}', uploaded['directory']) for v in command]
            elif any('{upload}' in v or '{dir}' in v for v in command):
                raise ValueError('{upload}/{dir} require --upload')
            request['argv'] = command
        progress(dict(event='execution', device=args.id, run_id=request['run_id']))
    return call(argv, client, request, progress)


def succeeded(result, operation=None):
    if operation in ('run', 'logs') and result.get('state') != 'completed':
        return False
    return result.get('state') not in ('failed', 'interrupted', 'timeout', 'output_limit', 'unknown') and result.get('exit_code', 0) in (None, 0)
