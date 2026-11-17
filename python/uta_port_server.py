"""Linux device delegation and bounded on-demand operations. No idle serial opens."""
import base64
import codecs
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import select
import signal
import stat
import subprocess
import termios
import time

PROTOCOL = 1
MAX_OUTPUT = 16 * 1024 * 1024
MAX_RECORD = 65536


def component(value, size=32):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{'+str(size)+'}', value):
        raise ValueError('Invalid device, client or operation identifier')
    return value


def read(server, path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as f:
        server.trusted_state_file(f.fileno())
        raw = f.read(MAX_RECORD+1)
    if len(raw) > MAX_RECORD:
        raise ValueError('Oversized device management record')
    return json.loads(raw)


def save(path, value):
    data = json.dumps(value, ensure_ascii=True).encode()
    if len(data) > MAX_RECORD:
        raise ValueError('Oversized device management record')
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def records(server):
    for path in sorted(server.STATE.glob('*.json')):
        if re.fullmatch('[0-9a-f]{32}', path.stem):
            yield read(server, path)


def device_id(record):
    return record.get('route_id') or record['session']


def grants_path(server):
    return server.STATE/'port-grants.json'


def grants(server):
    try:
        return read(server, grants_path(server))
    except FileNotFoundError:
        return []


def allowed(server, row, cid, entries):
    if row.get('owner') != server.owner():
        return False
    if row.get('client_id') == cid:
        return True
    return any(g['uid'] == server.owner() and g['owner'] == row.get('client_id')
               and g['id'] == device_id(row) and g['recipient'] == cid and g['expires'] > time.time()
               for g in entries)


def busy(server, session):
    try:
        fd = os.open(server.STATE/('port-busy-'+component(session)), os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    try:
        server.trusted_state_file(fd)
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return True
        return False
    finally: os.close(fd)


def check_active(server, client, device):
    row = read(server, server.STATE/(component(device['session'])+'.json'))
    if device_id(row) != device['id'] or not allowed(server, row, server.client_scope(client), grants(server)):
        raise PermissionError('Device authorization was revoked or expired')
    current = server.matching_port(row)
    if not current or current.get('status') != 6:
        raise RuntimeError('Device disconnected or changed mode; operation stopped without replay')


def visible(server, client, identifier=None):
    cid = server.client_scope(client)
    entries = grants(server)
    groups = {}
    for row in records(server):
        if identifier and device_id(row) != identifier:
            continue
        if allowed(server, row, cid, entries):
            groups.setdefault(device_id(row), []).append(row)
    result = []
    for key, rows in groups.items():
        live = [r for r in rows if server.matching_port(r)]
        if len(live) > 1:
            raise RuntimeError('Ambiguous device identity; reconnect only the intended device')
        row = live[0] if live else rows[-1]
        status = server.record_status(row)
        result.append(dict(id=key, session=row['session'], state=status['state'],
                           nodes=status['nodes'], busy=busy(server, row['session']), busid=row.get('busid', ''), vidpid=row.get('vidpid', ''),
                           access='owner' if row.get('client_id') == cid else 'shared'))
    return result


def resolve(server, client, identifier, ready=False):
    matches = visible(server, client, component(identifier))
    if len(matches) != 1:
        raise PermissionError('Device is unavailable or not authorized for this client')
    result = matches[0]
    if ready and result['state'] != 'attached':
        raise RuntimeError('Device is disconnected or enumerating; wait and query again')
    return result


def grant_change(server, client, request):
    device = resolve(server, client, request['id'])
    if device['access'] != 'owner':
        raise PermissionError('Only the providing client can manage this device grant')
    cid = server.client_scope(client)
    entries = grants(server)
    if request['action'] == 'grants':
        return [g for g in entries if g['id'] == device['id'] and g['owner'] == cid and g['uid'] == server.owner() and g['expires'] > time.time()]
    recipient = component(request.get('recipient'), 64)
    entries = [g for g in entries if g['expires'] > time.time() and not
               (g['uid'] == server.owner() and g['id'] == device['id'] and g['owner'] == cid and g['recipient'] == recipient)]
    if request['action'] == 'share':
        hours = request.get('hours', 24)
        if type(hours) is not int or not 1 <= hours <= 720:
            raise ValueError('Authorization lifetime must be 1..720 hours')
        if sum(g['uid'] == server.owner() for g in entries) >= 128:
            raise RuntimeError('Account device grant limit reached; revoke unused grants')
        if len(entries) >= 256:
            raise RuntimeError('Server device grant limit reached')
        entries.append(dict(id=device['id'], uid=server.owner(), owner=cid, recipient=recipient,
                            expires=time.time()+hours*3600))
    save(grants_path(server), entries)
    return dict(id=device['id'], recipient=recipient, authorized=request['action']=='share')


def history(server):
    path = server.PORT_HISTORY
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise PermissionError('Unsafe device operation history directory')
    return path


def run_path(server, run_id):
    return history(server)/('port-run-'+component(run_id)+'.json')


def receipt(server, client, identifier, run_id):
    result = read(server, run_path(server, run_id))
    if result['uid'] != server.owner() or result['client_id'] != server.client_scope(client) or result['id'] != identifier:
        raise PermissionError('Operation is not owned by this client')
    return result


def pid_start(pid):
    try:
        return Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def is_running(value):
    return value.get('pid_start') is not None and pid_start(value['pid']) == value['pid_start']


def node_for(device, request, kind='tty'):
    nodes = device['nodes']
    chosen = request.get('node')
    candidates = [n for n in nodes if (n.startswith('/dev/tty') if kind == 'tty' else n.startswith('/dev/bus/usb/') if kind == 'usb' else True)]
    if chosen:
        if chosen not in nodes:
            raise ValueError('Selected node no longer belongs to this device')
        if chosen not in candidates:
            raise ValueError('Selected node is not the requested device type')
        return chosen
    if len(candidates) != 1:
        raise ValueError('No unique device node; select --node from current status')
    return candidates[0]


def other_openers(path):
    """Best effort foreign-tool detection; root can always bypass cooperative locks."""
    target = os.stat(path)
    for directory in Path('/proc').glob('[0-9]*/fd'):
        if directory.parent.name == str(os.getpid()):
            continue
        try:
            for fd in directory.iterdir():
                try:
                    info = fd.stat()
                    if stat.S_ISCHR(info.st_mode) and info.st_rdev == target.st_rdev:
                        return True
                except OSError:
                    pass
        except OSError:
            continue
    return False


def serial_open(path, baud):
    speed = getattr(termios, 'B'+str(baud), None)
    if type(baud) is not int or speed is None or baud <= 0:
        raise ValueError('Unsupported serial baud rate')
    if other_openers(path):
        raise RuntimeError('Serial port is open in another process; close that monitor/tool first')
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        if not stat.S_ISCHR(os.fstat(fd).st_mode):
            raise ValueError('Serial node is not a character device')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.ioctl(fd, termios.TIOCEXCL)
        attrs = termios.tcgetattr(fd)
        # Raw 8N1, no flow control. Do not flush pending logs or send reset/data.
        attrs[0] = 0; attrs[1] = 0; attrs[3] = 0
        attrs[2] = (attrs[2] & ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | getattr(termios, 'CRTSCTS', 0) | termios.HUPCL)) | termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[4] = speed; attrs[5] = speed
        attrs[6][termios.VMIN] = 0; attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        return fd
    except BaseException:
        with contextlib.suppress(OSError): fcntl.ioctl(fd, termios.TIOCNXCL)
        os.close(fd)
        raise


def process_options(uid, cwd=None):
    account = pwd.getpwuid(uid)
    return dict(cwd=cwd or account.pw_dir, user=uid, group=account.pw_gid,
                extra_groups=os.getgrouplist(account.pw_name, account.pw_gid),
                env=dict(HOME=account.pw_dir, USER=account.pw_name, LOGNAME=account.pw_name,
                         PATH='/usr/local/bin:/usr/bin:/bin', LANG='C.UTF-8'),
                start_new_session=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def terminate(proc):
    if proc is None:
        return
    # A foreground tool may have exited leaving descendants; always signal its group.
    with contextlib.suppress(ProcessLookupError): os.killpg(proc.pid, signal.SIGTERM)
    try: proc.wait(timeout=2)
    except subprocess.TimeoutExpired: pass
    with contextlib.suppress(ProcessLookupError): os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()


def execute(server, client, request, wire, incoming):
    identifier = component(request['id']); run_id = component(request['run_id'])
    duration = request.get('seconds', 60) if request['action'] == 'logs' else request.get('timeout', 600)
    if type(duration) is not int or not 1 <= duration <= 3600:
        raise ValueError('Duration must be 1..3600 seconds')
    command_hash = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    lock = None; proc = None; serial = None; value = None
    previous_handlers = {}
    def interrupted(*_): raise InterruptedError('Operation stopped')
    with server.locked():
        resolve(server, client, identifier, ready=True)
        path = run_path(server, run_id)
        if path.exists():
            old = receipt(server, client, identifier, run_id)
            if old['command_hash'] != command_hash:
                raise ValueError('Run ID already belongs to a different operation')
            if old['state'] == 'running' and not is_running(old): old['state'] = 'unknown'
            wire.write(dict(ok=True, event='result', result=old)); return
        device = resolve(server, client, identifier, ready=True)
        lock = os.open(server.STATE/('port-busy-'+component(device['session'])), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            server.trusted_state_file(lock)
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Device is busy with another UTA operation; stop its logs/tool before retrying') from None
            existing = [(p, read(server, p)) for p in history(server).glob('port-run-*.json')]
            owned = [(p,r) for p,r in existing if r['uid'] == server.owner()]
            if sum(r['state']=='running' and is_running(r) for _,r in owned) >= 4:
                raise RuntimeError('Account device operation limit reached')
            if len(existing) >= 512:
                raise RuntimeError('Server operation history limit reached')
            if len(owned) >= 128:
                raise RuntimeError('Account operation history limit reached; explicitly forget an old completed run')
            value = dict(id=identifier, run_id=run_id, uid=server.owner(), client_id=server.client_scope(client),
                         session=device['session'], state='running', started=time.time(), pid=os.getpid(),
                         pid_start=pid_start(os.getpid()), command_hash=command_hash, output_bytes=0, tail='', exit_code=None)
            save(path, value)
        except BaseException:
            os.close(lock); lock=None; raise
    try:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            previous_handlers[sig] = signal.signal(sig, interrupted)
        wire.write(dict(ok=True, event='execution', run_id=run_id, id=identifier, nodes=device['nodes']))
        if request['action'] == 'logs':
            serial = serial_open(node_for(device, request), request.get('baud'))
            streams = [(serial, 'serial')]
        else:
            argv = request.get('argv')
            if not isinstance(argv, list) or not 1 <= len(argv) <= 256 or any(not isinstance(a,str) or '\0' in a or len(a)>8192 for a in argv):
                raise ValueError('Invalid tool argument array')
            if not any(t in a for a in argv for t in ('{port}', '{usb}', '{device}')):
                raise ValueError('Tool must explicitly select {port}, {usb} or {device}')
            for token, kind in (('{port}', 'tty'), ('{usb}', 'usb'), ('{device}', 'any')):
                if any(token in a for a in argv):
                    node = node_for(device, request, kind)
                    if kind == 'tty' and other_openers(node):
                        raise RuntimeError('Serial port is open in another process')
                    argv = [a.replace(token, node) for a in argv]
            cwd = request.get('cwd')
            if cwd is not None and (not isinstance(cwd, str) or not cwd.startswith('/') or '\0' in cwd):
                raise ValueError('Working directory must be an absolute receiver path')
            # Sudo only brokers ownership and locks. Original tools run as the SSH UID.
            proc = subprocess.Popen(argv, **process_options(server.owner(), cwd))
            streams = [(proc.stdout, 'stdout'), (proc.stderr, 'stderr')]
        decoders = {name: codecs.getincrementaldecoder('utf-8')(errors='replace') for name in ('stdout','stderr')}
        deadline = time.monotonic()+duration; checked = 0; heartbeat = 0
        with selectors.DefaultSelector() as selector:
            for stream, label in streams: selector.register(stream, selectors.EVENT_READ, label)
            try: selector.register(incoming.fileno(), selectors.EVENT_READ, 'control')
            except (AttributeError, OSError, ValueError): pass
            while True:
                now = time.monotonic()
                if now >= deadline:
                    value['state'] = 'completed' if serial is not None else 'timeout'; break
                if now-checked >= 1:
                    with server.locked():
                        check_active(server, client, device)
                    checked = now
                if now-heartbeat >= 5:
                    wire.write(dict(ok=True, event='heartbeat')); heartbeat = now
                active = [k for k in selector.get_map().values() if k.data != 'control']
                if not active:
                    if proc and proc.poll() is None: time.sleep(.05); continue
                    value['state'] = 'completed'; break
                for key, _ in selector.select(.1):
                    if key.data == 'control':
                        if not os.read(key.fd, 1): raise InterruptedError('Client disconnected')
                        raise ValueError('Unexpected input during operation')
                    data = os.read(key.fd, 32768)
                    if not data:
                        if key.data == 'serial': raise RuntimeError('Serial device disconnected')
                        selector.unregister(key.fileobj)
                        tail = decoders[key.data].decode(b'', final=True)
                        if tail: wire.write(dict(ok=True,event='output',stream=key.data,text=tail))
                        continue
                    if key.data == 'serial':
                        # At most ~50 short frames/sec for continuous logs; never wait to fill a large block.
                        until = time.monotonic()+.02
                        while len(data)<32768 and time.monotonic()<until:
                            if not select.select([key.fd], [], [], max(0, until-time.monotonic()))[0]: break
                            try: more=os.read(key.fd,32768-len(data))
                            except BlockingIOError: break
                            if not more: break
                            data += more
                    value['output_bytes'] += len(data)
                    if value['output_bytes'] > MAX_OUTPUT:
                        value['state']='output_limit'; break
                    if key.data == 'serial':
                        wire.write(dict(ok=True,event='serial',encoding='base64',data=base64.b64encode(data).decode()))
                    else:
                        text=decoders[key.data].decode(data)
                        value['tail']=(value['tail']+text)[-4096:]
                        wire.write(dict(ok=True,event='output',stream=key.data,text=text))
                if value['state']=='output_limit': break
        if proc and proc.poll() is not None: value['exit_code']=proc.returncode
        if proc and value['exit_code'] not in (None, 0): value['state']='failed'
    except BaseException as exc:
        value['state']='interrupted' if isinstance(exc, (InterruptedError, KeyboardInterrupt, BrokenPipeError)) else 'failed'
        value['error']=str(exc)[:1000]
    finally:
        terminate(proc)
        if proc:
            value['exit_code']=proc.returncode
            for stream in (proc.stdout, proc.stderr): stream.close()
        if serial is not None:
            with contextlib.suppress(OSError): fcntl.ioctl(serial, termios.TIOCNXCL)
            os.close(serial)
        value['finished']=time.time()
        with server.locked(): save(path, value)
        if lock is not None: os.close(lock)
        for sig, handler in previous_handlers.items(): signal.signal(sig, handler)
    wire.write(dict(ok=True,event='result',result=value))


class PortWire:
    """Bound slow/disappearing readers as well as the serial/tool data source."""
    WRITE_TIMEOUT = 15
    def __init__(self, server, incoming, outgoing):
        self.base = server.uta_transfer.Wire(incoming, outgoing)
        self.encode = server.uta_transfer.encoded
        self.limit = server.uta_transfer.MAX_JSON
        self.fd = None
        self.failed = False
        try:
            self.fd = outgoing.fileno()
            self.was_blocking = os.get_blocking(self.fd)
            os.set_blocking(self.fd, False)
        except (AttributeError, OSError, ValueError): self.fd = None
    def read(self): return self.base.read()
    def write(self, value):
        if self.failed: raise BrokenPipeError("Client output unavailable")
        if self.fd is None: return self.base.write(value)
        data = self.encode(value)+b'\n'
        if len(data)>self.limit: raise ValueError('Oversized device response')
        offset = 0; deadline = time.monotonic()+self.WRITE_TIMEOUT
        while offset<len(data):
            try:
                count=os.write(self.fd,data[offset:])
                if not count: raise BrokenPipeError('Client output closed')
                offset+=count
            except BlockingIOError:
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    self.failed=True
                    raise TimeoutError('Client output stalled; operation stopped')
                select.select([], [self.fd], [], min(.2,remaining))
    def restore(self):
        if self.fd is not None: os.set_blocking(self.fd,self.was_blocking)


def serve(server, client, incoming, outgoing):
    # Wire framing is shared with transfers, but this endpoint accepts no file data.
    wire = PortWire(server, incoming, outgoing)
    try:
        request = wire.read()
        action = request.get('action')
        if action in ('run', 'logs'):
            execute(server, client, request, wire, incoming); return
        with server.locked():
            if action == 'list': result = visible(server, client)
            elif action == 'status': result = resolve(server, client, request.get('id'))
            elif action in ('share', 'grants', 'revoke'): result = grant_change(server, client, request)
            elif action in ('run-status', 'stop', 'forget'):
                identifier = component(request.get('id'))
                result = receipt(server, client, identifier, request.get('run_id'))
                if action == 'forget':
                    if result['state'] == 'running' and is_running(result):
                        raise RuntimeError('Cannot forget an active operation')
                    run_path(server, request['run_id']).unlink()
                    result = dict(forgotten=True, run_id=request['run_id'])
                if result.get('state') == 'running':
                    if not is_running(result): result['state']='unknown'
                    elif action == 'stop':
                        os.kill(result['pid'], signal.SIGTERM); result=dict(result,stop_requested=True)
            else: raise ValueError('Unknown device operation')
        wire.write(dict(ok=True,event='result',result=result))
    except Exception as exc:
        with contextlib.suppress(BrokenPipeError, TimeoutError):
            wire.write(dict(ok=False,error=str(exc)[:1000]))
    finally:
        wire.restore()
