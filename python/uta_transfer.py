"""Client-scoped, resumable files over OpenSSH stdio; no listening socket or sudo.

Wire v1: bounded JSON lines for control, exact-length binary file streams.
Encryption/authentication of the transport is exclusively OpenSSH's job.
"""
import argparse
import contextlib
import codecs
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import queue
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
import unicodedata

PROTOCOL = 1
BLOCK = 1024 * 1024
MAX_JSON = 2 * BLOCK
MAX_FILES = 4096
MAX_BYTES = 8 * 1024**3
CLIENT_QUOTA = 16 * 1024**3
ACCOUNT_QUOTA = 64 * 1024**3
MAX_JOBS = 1024
MAX_ACTIVE = 4
IDLE_SECONDS = 120
MAX_OUTPUT = 16 * BLOCK
HEX = re.compile(r'[a-f0-9]{64}')
JOB = re.compile(r'[a-f0-9]{32}')
SPACE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


class TransferError(Exception):
    pass


class TransportError(TransferError):
    pass


def encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()


def credential(value):
    if not isinstance(value, dict) or set(value) != {'secret', 'client_id'}:
        raise TransferError('Client credential required')
    if any(not isinstance(v, str) or not HEX.fullmatch(v) for v in value.values()):
        raise TransferError('Invalid client credential')
    expected = hashlib.sha256(b'UTA control client v1\0' + bytes.fromhex(value['secret'])).hexdigest()
    if not hmac.compare_digest(expected, value['client_id']):
        raise TransferError('Invalid client credential')
    return value['client_id']


def component(value, pattern=SPACE):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise TransferError('Invalid workspace or task identifier')
    return value


def relative(value):
    if not isinstance(value, str) or len(value.encode('utf-8')) > 1024:
        raise TransferError('Invalid file name')
    parts = value.split('/')
    for part in parts:
        if (not part or part in ('.', '..') or len(part.encode('utf-8')) > 200 or
                part.endswith((' ', '.')) or any(ord(c) < 32 or c in '\\:*?"<>|' for c in part) or
                part.split('.')[0].upper() in {'CON','PRN','AUX','NUL', *('COM'+str(i) for i in range(1,10)), *('LPT'+str(i) for i in range(1,10))}):
            raise TransferError('File name is not portable or contains traversal')
    return value


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or set(manifest) != {'files', 'directories', 'entry'}:
        raise TransferError('Invalid manifest')
    files, dirs = manifest['files'], manifest['directories']
    if not isinstance(files, list) or not isinstance(dirs, list) or len(files)+len(dirs) > MAX_FILES:
        raise TransferError('Too many files or directories')
    names = set(); total = 0
    for name in dirs:
        key = unicodedata.normalize('NFC', relative(name)).casefold()
        if key in names: raise TransferError('Duplicate or case-colliding paths')
        names.add(key)
    directory_names = set(names)
    for item in files:
        if not isinstance(item, dict) or set(item) != {'path','size','sha256'}:
            raise TransferError('Invalid file manifest')
        key = unicodedata.normalize('NFC', relative(item['path'])).casefold()
        if key in names: raise TransferError('Duplicate or case-colliding paths')
        names.add(key)
        if type(item['size']) != int or not 0 <= item['size'] <= MAX_BYTES:
            raise TransferError('Invalid file size')
        if not isinstance(item['sha256'], str) or not HEX.fullmatch(item['sha256']):
            raise TransferError('Invalid file checksum')
        total += item['size']
    if total > MAX_BYTES: raise TransferError('Package exceeds 8 GiB limit')
    entry = relative(manifest['entry'])
    if unicodedata.normalize('NFC',entry).casefold() not in names: raise TransferError('Missing package entry')
    for name in names:
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.' and str(parent) not in directory_names:
                raise TransferError('Missing parent directory')
    if len(encoded(manifest)) > MAX_JSON - 4096: raise TransferError('Manifest too large')
    return total


def file_hash(path, count=None):
    h = hashlib.sha256()
    with safe_open(path, os.O_RDONLY) as f:
        remaining = count
        while remaining is None or remaining:
            data = f.read(BLOCK if remaining is None else min(BLOCK, remaining))
            if not data:
                if remaining: raise TransferError('File changed while hashing')
                break
            h.update(data)
            if remaining is not None: remaining -= len(data)
    return h.hexdigest()


def reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def safe_dir(path):
    path = Path(path)
    try: path.mkdir(mode=0o700)
    except FileExistsError: pass
    s = path.lstat()
    if reparse(s) or not stat.S_ISDIR(s.st_mode): raise TransferError('Unsafe storage directory')
    if os.name != 'nt' and (s.st_uid != os.getuid() or s.st_mode & 0o077):
        raise TransferError('Transfer directory must be owned by this account and mode 0700')
    return path


@contextlib.contextmanager
def safe_open(path, flags, private=False):
    path = Path(path)
    if path.exists() or path.is_symlink():
        s = path.lstat()
        if reparse(s) or not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
            raise TransferError('Unsafe file type or hard link')
    fd = os.open(path, flags | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), 0o600)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1: raise TransferError('Unsafe file')
        if private and os.name != 'nt' and (s.st_uid != os.getuid() or s.st_mode & 0o077):
            raise TransferError('Unsafe private file permissions')
        mode = 'r+b' if flags & (os.O_RDWR | os.O_WRONLY) else 'rb'
        with os.fdopen(fd, mode) as f:
            fd = None
            yield f
    finally:
        if fd is not None: os.close(fd)


def fsync_dir(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try: os.fsync(fd)
        finally: os.close(fd)


def save(path, value):
    tmp = path.with_name('.'+uuid.uuid4().hex+'.tmp')
    try:
        with safe_open(tmp, os.O_RDWR | os.O_CREAT | os.O_EXCL) as f:
            f.write(encoded(value)); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path); fsync_dir(path.parent)
    finally: tmp.unlink(missing_ok=True)


def load(path):
    with safe_open(path, os.O_RDONLY, private=True) as f:
        data = f.read(MAX_JSON+1)
    if len(data) > MAX_JSON: raise TransferError('State file too large')
    return json.loads(data)


@contextlib.contextmanager
def locked(path):
    with safe_open(path, os.O_RDWR | os.O_CREAT, private=True) as f:
        if os.name == 'nt':
            import msvcrt
            if not os.fstat(f.fileno()).st_size: f.write(b'0'); f.flush()
            f.seek(0)
            try: msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError: raise TransferError('Task or storage is busy; retry later') from None
        else:
            import fcntl
            try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise TransferError('Task or storage is busy; retry later') from None
        yield


def default_root():
    if os.name == 'nt': return Path(os.environ['LOCALAPPDATA'])/'UTA'/'Transfers'
    if sys.platform == 'darwin': return Path.home()/'Library/Application Support/UTA/Transfers'
    return Path.home()/'.local/share/uta/transfers'


class Store:
    def __init__(self, client, workspace, root=None):
        self.client = credential(client); self.workspace = component(workspace)
        self.root = Path(root or os.environ.get('UTA_TRANSFER_HOME', default_root())).absolute()
        self.root.parent.mkdir(parents=True, exist_ok=True)
        safe_dir(self.root)
        if os.name == 'nt':
            # Inherited user-profile ACLs vary; explicitly protect the storage root.
            user = subprocess.check_output(['whoami'], text=True, creationflags=FLAGS).strip()
            subprocess.run(['icacls', str(self.root), '/inheritance:r', '/grant:r', user+':(OI)(CI)F',
                            '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=FLAGS)
        self.clients = safe_dir(self.root/'clients')
        self.base = safe_dir(safe_dir(self.clients/self.client)/self.workspace)

    def task(self, job):
        component(job, JOB)
        p = self.base/job
        if not p.exists(): raise TransferError('Task not found for this client and workspace')
        safe_dir(p)
        return p

    def record(self, job):
        p = self.task(job); value = load(p/'record.json')
        if value['client_id'] != self.client or value['workspace'] != self.workspace or value['id'] != job:
            raise TransferError('Task identity mismatch')
        return value

    def result(self, value):
        result = {k: value[k] for k in ('id','client_id','workspace','state','bytes','created')}
        if value['state'] == 'complete':
            result['directory'] = str(self.base/value['id']/'files')
            result['entry'] = str(self.base/value['id']/'files'/value['manifest']['entry'])
        return result

    def reserve(self, manifest):
        total = validate_manifest(manifest); job = hashlib.sha256(encoded(manifest)).hexdigest()[:32]
        with locked(self.root/'quota.lock'):
            if (self.base/job/'record.json').exists():
                old = self.record(job)
                if old['manifest'] != manifest: raise TransferError('Task collision')
                return job
            account,own,count = self.usage()
            if count >= MAX_JOBS or own+total > CLIENT_QUOTA or account+total > ACCOUNT_QUOTA:
                raise TransferError('Transfer quota reached; remove owned tasks explicitly')
            if shutil.disk_usage(self.root).free < total + 64*BLOCK: raise TransferError('Insufficient disk space')
            path = safe_dir(self.base/job)
            save(path/'record.json', dict(id=job, client_id=self.client, workspace=self.workspace,
                 state='partial', bytes=total, created=time.time(), manifest=manifest))
        return job

    def usage(self):
        account = own = count = 0
        # Caller holds the account quota lock. Partial uploads and bounded log
        # reservations count even when the writer crashes.
        for client in self.clients.iterdir():
            safe_dir(client)
            for space in client.iterdir():
                safe_dir(space)
                for task in space.iterdir():
                    if not JOB.fullmatch(task.name): continue
                    safe_dir(task)
                    if not (task/'record.json').exists(): continue
                    record=load(task/'record.json'); amount=record['bytes']+record.get('run_bytes_reserved',0)
                    account+=amount; count+=1
                    if client.name==self.client: own+=amount
        return account,own,count

    def reserve_output(self,job):
        with locked(self.root/'quota.lock'):
            account,own,_=self.usage()
            if own+MAX_OUTPUT>CLIENT_QUOTA or account+MAX_OUTPUT>ACCOUNT_QUOTA:
                raise TransferError('Insufficient quota for bounded execution logs')
            if shutil.disk_usage(self.root).free<MAX_OUTPUT+64*BLOCK: raise TransferError('Insufficient disk space for execution log')
            record=self.record(job);record['run_bytes_reserved']=record.get('run_bytes_reserved',0)+MAX_OUTPUT
            save(self.task(job)/'record.json',record)

    @contextlib.contextmanager
    def slot(self):
        with contextlib.ExitStack() as stack:
            for i in range(MAX_ACTIVE):
                try: stack.enter_context(locked(self.root/f'active-{i}.lock')); break
                except TransferError:
                    if i == MAX_ACTIVE-1: raise TransferError('Four transfers/commands already active for this account')
            yield

    def verify(self, job):
        record = self.record(job)
        if record['state'] != 'complete': raise TransferError('Transfer is not complete')
        base = safe_dir(self.task(job)/'files')
        for d in record['manifest']['directories']: self.path(base, d, directory=True)
        for item in record['manifest']['files']:
            p = self.path(base, item['path'])
            if p.stat().st_size != item['size'] or file_hash(p) != item['sha256']:
                raise TransferError('Completed file checksum mismatch')
        return record

    def path(self, base, name, directory=False):
        parts = relative(name).split('/'); p = base
        for part in parts[:-1]: p = safe_dir(p/part)
        p = p/parts[-1]
        return safe_dir(p) if directory else p

    def remove(self, job):
        with locked(self.root/'quota.lock'):
            with locked(self.base/(component(job, JOB)+'.lock')):
                self.record(job)
                shutil.rmtree(self.task(job)); fsync_dir(self.base)
        return dict(id=job, removed=True)


class Wire:
    def __init__(self, incoming, outgoing):
        self.incoming = incoming; self.outgoing = outgoing; self.activity = time.monotonic(); self.child = None

    def read(self):
        raw = self.incoming.readline(MAX_JSON+1)
        self.activity = time.monotonic()
        if not raw: raise TransportError('Transport closed; transfer can be resumed')
        if len(raw)>MAX_JSON or not raw.endswith(b'\n'): raise TransferError('Invalid control frame')
        value = json.loads(raw)
        if not isinstance(value, dict): raise TransferError('Invalid control object')
        return value

    def write(self, value):
        data = encoded(value)+b'\n'
        if len(data)>MAX_JSON: raise TransferError('Response too large')
        self.outgoing.write(data); self.outgoing.flush(); self.activity = time.monotonic()


def receive_upload(store, manifest, wire):
    job = store.reserve(manifest)
    with store.slot(), locked(store.base/(job+'.lock')):
        record = store.record(job); path = store.task(job)
        if record['state'] == 'complete':
            store.verify(job); wire.write(dict(ok=True, event='complete', result=store.result(record))); return
        if (path/'files').exists():
            # Crash after directory rename and before the commit record.
            os.replace(path/'files', path/'partial')
        base = safe_dir(path/'partial')
        for name in sorted(manifest['directories'], key=lambda x:(x.count('/'),x)):
            store.path(base, name, directory=True)
        wire.write(dict(ok=True, event='offer', id=job))
        for item in manifest['files']:
            target = store.path(base, item['path'])
            with safe_open(target, os.O_CREAT | os.O_RDWR, private=True) as f:
                offset = os.fstat(f.fileno()).st_size
                if offset > item['size']: f.truncate(0); offset = 0
                h = hashlib.sha256(); f.seek(0)
                while True:
                    data=f.read(BLOCK)
                    if not data: break
                    h.update(data); wire.activity=time.monotonic()
                wire.write(dict(ok=True,event='file',path=item['path'],offset=offset,prefix_sha256=h.hexdigest()))
                choice=wire.read()
                if choice not in ({'offset':0},{'offset':offset}): raise TransferError('Invalid resume offset')
                if choice['offset']==0: f.seek(0); f.truncate(0); offset=0; h=hashlib.sha256()
                f.seek(offset); remaining=item['size']-offset
                wire.write(dict(ok=True,event='ready'))
                while remaining:
                    data=wire.incoming.read(min(BLOCK,remaining)); wire.activity=time.monotonic()
                    if not data: raise TransportError('Upload interrupted; partial data retained')
                    f.write(data); h.update(data); remaining-=len(data)
                f.flush(); os.fsync(f.fileno())
                if h.hexdigest()!=item['sha256']:
                    f.seek(0); f.truncate(0); f.flush(); os.fsync(f.fileno())
                    raise TransferError('SHA-256 mismatch; source changed or data is corrupt')
            wire.write(dict(ok=True,event='verified'))
        os.replace(base,path/'files'); fsync_dir(path)
        record['state']='complete'; save(path/'record.json',record)
        wire.write(dict(ok=True,event='complete',result=store.result(record)))


def terminate(proc):
    if os.name == 'nt':
        if proc.poll() is not None: return
        subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,creationflags=FLAGS)
    else:
        with contextlib.suppress(ProcessLookupError): os.killpg(proc.pid,signal.SIGTERM)
    try: proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if os.name != 'nt':
            with contextlib.suppress(ProcessLookupError): os.killpg(proc.pid,signal.SIGKILL)
        else: proc.kill()
        proc.wait()
    if os.name != 'nt':
        # A child may still own stdout after the original tool has exited.
        with contextlib.suppress(ProcessLookupError): os.killpg(proc.pid,signal.SIGKILL)


def execute(store, job, request, wire):
    run_id=component(request.get('run_id'),JOB); argv=request.get('argv'); timeout=request.get('timeout',600)
    if (not isinstance(argv,list) or not argv or len(argv)>256 or
        any(not isinstance(a,str) or '\0' in a or len(a)>8192 for a in argv)):
        raise TransferError('Command must be an argument array')
    if type(timeout)!=int or not 1<=timeout<=3600: raise TransferError('Timeout must be 1..3600 seconds')
    with store.slot(), locked(store.base/(component(job,JOB)+'.lock')):
        record=store.verify(job); task=store.task(job); runs=safe_dir(task/'runs'); receipt=runs/(run_id+'.json')
        command_hash=hashlib.sha256(encoded(dict(argv=argv,timeout=timeout))).hexdigest()
        if receipt.exists():
            previous=load(receipt)
            if previous.get('command_hash')!=command_hash: raise TransferError('Run ID already belongs to a different command')
            wire.write(dict(ok=True,event='result',result=previous)); return
        if len(list(runs.glob('*.json')))>=32: raise TransferError('Execution history limit reached for this transfer')
        directory=task/'files'; entry=directory/record['manifest']['entry']
        argv=[a.replace('{upload}',str(entry)).replace('{dir}',str(directory)) for a in argv]
        # Do not interpolate into a shell. Windows .bat/.cmd must be explicitly invoked via cmd.exe.
        if os.name=='nt' and Path(argv[0]).suffix.lower() in ('.bat','.cmd'):
            raise TransferError('Invoke batch files explicitly with cmd.exe; native executables are preferred')
        store.reserve_output(job)
        value=dict(run_id=run_id,id=job,state='starting',started=time.time(),exit_code=None,command_hash=command_hash)
        save(receipt,value) # Any retry sees the receipt; commands are never auto-replayed.
        wire.write(dict(ok=True,event='started',run_id=run_id))
        proc=None; log=None; total=0; q=queue.Queue(maxsize=16); done=0; deadline=time.monotonic()+timeout
        stop=threading.Event(); readers=[]
        decoders={name:codecs.getincrementaldecoder('utf-8')(errors='replace') for name in ('stdout','stderr')}
        try:
            proc=subprocess.Popen(argv,cwd=directory,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,creationflags=FLAGS,start_new_session=os.name!='nt')
            wire.child=proc
            value['state']='running'; save(receipt,value)
            def pump(stream,label):
                def enqueue(data):
                    while not stop.is_set():
                        try: q.put((label,data),timeout=0.1); return
                        except queue.Full: pass
                try:
                    with stream:
                        while not stop.is_set():
                            data=stream.read1(32768)
                            if not data: break
                            enqueue(data)
                finally: enqueue(None)
            for stream,label in [(proc.stdout,'stdout'),(proc.stderr,'stderr')]:
                reader=threading.Thread(target=pump,args=(stream,label),daemon=True)
                readers.append(reader); reader.start()
            log=safe_open(runs/(run_id+'.log'),os.O_CREAT|os.O_EXCL|os.O_RDWR)
            with log as out:
                while done<2 or proc.poll() is None:
                    if time.monotonic()>deadline:
                        value['state']='timeout'; terminate(proc); break
                    try: label,data=q.get(timeout=1)
                    except queue.Empty:
                        wire.write(dict(ok=True,event='heartbeat')); continue
                    if data is None:
                        done+=1; tail=decoders[label].decode(b'',final=True)
                        if tail: wire.write(dict(ok=True,event='output',stream=label,text=tail))
                        continue
                    total+=len(data)
                    if total>MAX_OUTPUT:
                        value['state']='output_limit'; terminate(proc); break
                    # JSON-escaped output cannot corrupt framing; original bytes are retained in the bounded log.
                    out.write(data)
                    wire.write(dict(ok=True,event='output',stream=label,text=decoders[label].decode(data)))
                out.flush(); os.fsync(out.fileno())
            if value['state']=='running': value['state']='completed'
            value['exit_code']=proc.wait(timeout=3)
        except BaseException:
            value['state']='interrupted'
            if proc: terminate(proc); value['exit_code']=proc.returncode
            raise
        finally:
            stop.set()
            for reader in readers: reader.join(timeout=1)
            wire.child=None
            value.update(finished=time.time(),output_bytes=total); save(receipt,value)
        wire.write(dict(ok=True,event='result',result=value))


def serve(incoming=None,outgoing=None,root=None,watchdog=False):
    wire=Wire(incoming or sys.stdin.buffer,outgoing or sys.stdout.buffer)
    finished=threading.Event()
    if watchdog:
        def expire():
            while not finished.wait(2):
                if time.monotonic()-wire.activity>IDLE_SECONDS:
                    if wire.child: terminate(wire.child)
                    os._exit(124) # Kernel releases file locks; partial files remain resumable.
        threading.Thread(target=expire,daemon=True).start()
    try:
        request=wire.read()
        if request.get('version')!=PROTOCOL: raise TransferError('Unsupported transfer protocol')
        store=Store(request.get('client'),request.get('workspace','default'),root)
        action=request.get('action'); job=request.get('id')
        if action=='put': receive_upload(store,request.get('manifest'),wire); return 0
        if action=='run': execute(store,job,request,wire); return 0
        if action=='list':
            result=[store.result(store.record(p.name)) for p in sorted(store.base.iterdir()) if JOB.fullmatch(p.name)]
        elif action=='status': result=store.result(store.record(job))
        elif action=='remove': result=store.remove(job)
        elif action=='run-status':
            receipt=safe_dir(store.task(job)/'runs')/(component(request.get('run_id'),JOB)+'.json')
            result=load(receipt)
            if result['state'] in ('starting','running'):
                try:
                    with locked(store.base/(job+'.lock')):
                        result=load(receipt)
                        if result['state'] in ('starting','running'): result['state']='unknown'
                except TransferError: pass
        elif action=='hello': result=dict(protocol=PROTOCOL,platform=sys.platform,max_bytes=MAX_BYTES,max_files=MAX_FILES,
                                        client_quota=CLIENT_QUOTA,account_quota=ACCOUNT_QUOTA,max_active=MAX_ACTIVE)
        else: raise TransferError('Unknown transfer operation')
        wire.write(dict(ok=True,event='result',result=result)); return 0
    except (Exception,KeyboardInterrupt) as exc:
        # No request/credential echo and no traceback on the wire.
        with contextlib.suppress(Exception): wire.write(dict(ok=False,error=str(exc) or 'Interrupted'))
        return 1
    finally: finished.set()


def snapshot(source):
    source=Path(source).expanduser().absolute()
    if source.is_symlink(): raise TransferError('Symlink sources are not supported')
    files=[]; dirs=[]; paths={}; total=0
    def add(path):
        nonlocal total
        s=path.lstat(); name=relative(path.relative_to(source.parent).as_posix())
        if reparse(s): raise TransferError('Symlinks/junctions are not transferred')
        if stat.S_ISDIR(s.st_mode): dirs.append(name); return
        if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1: raise TransferError('Only regular unlinked files are supported')
        total+=s.st_size
        if total>MAX_BYTES: raise TransferError('Package exceeds transfer size limit')
        digest=file_hash(path)
        after=path.stat()
        if (s.st_size,s.st_mtime_ns,s.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):
            raise TransferError('Source changed while preparing transfer')
        files.append(dict(path=name,size=s.st_size,sha256=digest)); paths[name]=path
    add(source)
    if source.is_dir():
        for parent,subdirs,names in os.walk(source,followlinks=False):
            for name in sorted(subdirs+names):
                add(Path(parent)/name)
                if len(files)+len(dirs)>MAX_FILES: raise TransferError('Too many source files')
    manifest=dict(files=sorted(files,key=lambda x:x['path']),directories=sorted(dirs),entry=source.name)
    validate_manifest(manifest)
    return manifest,paths


class Channel:
    """Bounded response queue and stderr; watchdog interrupts stalled SSH pipes."""
    def __init__(self,argv,timeout=IDLE_SECONDS,env=None):
        self.proc=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                   creationflags=FLAGS,env=env)
        self.responses=queue.Queue(maxsize=16); self.errors=bytearray(); self.closed=threading.Event()
        self.activity=time.monotonic(); self.timeout=timeout
        def enqueue(value):
            while not self.closed.is_set():
                try: self.responses.put(value,timeout=0.1); return
                except queue.Full: pass
        def read():
            try:
                while not self.closed.is_set():
                    raw=self.proc.stdout.readline(MAX_JSON+1)
                    if not raw: break
                    if len(raw)>MAX_JSON: raise TransportError('Receiver sent an oversized frame')
                    enqueue(json.loads(raw))
            except Exception: pass
            finally: enqueue(None)
        def stderr():
            while not self.closed.is_set():
                data=self.proc.stderr.read1(4096)
                if not data: return
                self.errors.extend(data); del self.errors[:-8192]
        def watchdog():
            while not self.closed.wait(1):
                if time.monotonic()-self.activity>self.timeout:
                    self.proc.kill(); return
        for fn in (read,stderr,watchdog): threading.Thread(target=fn,daemon=True).start()
    def write(self,data):
        try: self.proc.stdin.write(data); self.proc.stdin.flush(); self.activity=time.monotonic()
        except (OSError,ValueError): raise TransportError('SSH stream closed; transfer may be resumed') from None
    def send(self,value): self.write(encoded(value)+b'\n')
    def read(self):
        try: value=self.responses.get(timeout=self.timeout+2)
        except queue.Empty: raise TransportError('SSH receiver timed out') from None
        self.activity=time.monotonic()
        if value is None:
            error=self.errors.decode('utf-8',errors='replace')[-1200:]
            if any(marker in error.lower() for marker in ('permission denied','host key verification failed','remote host identification has changed')):
                raise TransferError('SSH authentication/host verification failed: '+error)
            raise TransportError('SSH receiver closed: '+error)
        if not isinstance(value,dict) or not value.get('ok'): raise TransferError(value.get('error','Receiver failed') if isinstance(value,dict) else 'Invalid receiver response')
        return value
    def close(self):
        self.closed.set()
        with contextlib.suppress(OSError): self.proc.stdin.close()
        try: self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait()
        self.proc.stdout.close(); self.proc.stderr.close()
    def __enter__(self): return self
    def __exit__(self,*args): self.close()


def upload(argv,client,workspace,source,retries=2,progress=None,env=None):
    manifest,paths=snapshot(source); component(workspace); credential(client)
    request=dict(version=PROTOCOL,client=client,workspace=workspace,action='put',manifest=manifest)
    started=time.monotonic(); sent=0; resumed=0
    for attempt in range(retries+1):
        try:
            with Channel(argv,env=env) as wire:
                wire.send(request); response=wire.read()
                if response['event']=='complete':
                    return dict(**response['result'],sent_bytes=sent,resumed_bytes=sum(x['size'] for x in manifest['files']),elapsed_seconds=time.monotonic()-started)
                if response['event']!='offer': raise TransferError('Unexpected receiver response')
                resumed=0
                for item in manifest['files']:
                    offer=wire.read()
                    if offer.get('event')!='file' or offer.get('path')!=item['path']: raise TransferError('File order mismatch')
                    offset=offer.get('offset')
                    if type(offset)!=int or not 0<=offset<=item['size']: raise TransferError('Invalid receiver offset')
                    if file_hash(paths[item['path']],offset)!=offer.get('prefix_sha256'): offset=0
                    wire.send(dict(offset=offset))
                    if wire.read().get('event')!='ready': raise TransferError('Receiver not ready')
                    resumed+=offset
                    with safe_open(paths[item['path']],os.O_RDONLY) as source_file:
                        source_file.seek(offset); remaining=item['size']-offset
                        while remaining:
                            data=source_file.read(min(BLOCK,remaining))
                            if not data: raise TransferError('Source changed during upload')
                            wire.write(data); sent+=len(data); remaining-=len(data)
                            if progress: progress(dict(event='progress',path=item['path'],file_bytes=item['size']-remaining,file_size=item['size']))
                    if wire.read().get('event')!='verified': raise TransferError('File was not verified')
                response=wire.read()
                if response.get('event')!='complete': raise TransferError('Transfer not committed')
                return dict(**response['result'],sent_bytes=sent,resumed_bytes=resumed,elapsed_seconds=time.monotonic()-started)
        except TransportError:
            if attempt>=retries: raise
            if progress: progress(dict(event='retry',attempt=attempt+1))
            time.sleep(2**attempt)


def rpc(transport_argv,client,workspace,action,progress=None,env=None,**fields):
    credential(client); component(workspace)
    with Channel(transport_argv,env=env) as wire:
        wire.send(dict(version=PROTOCOL,client=client,workspace=workspace,action=action,**fields))
        while True:
            event=wire.read()
            if event.get('event')=='result': return event['result']
            if progress: progress(event)


def add_commands(sub):
    send=sub.add_parser('send',help='Encrypted, verified, resumable upload into your private workspace')
    send.add_argument('source')
    listing=sub.add_parser('transfers',help='List or manage only your own transfer tasks')
    listing.add_argument('operation',choices=('list','status','remove','check','run-status'))
    listing.add_argument('--id'); listing.add_argument('--run-id')
    run=sub.add_parser('run',help='Upload a package and execute an explicit command once on its receiver')
    run.add_argument('--upload',required=True); run.add_argument('--timeout',type=int,default=600)
    run.add_argument('--run-id',default=None,help='Reuse only to query an existing execution, never to repeat it')
    run.add_argument('argv',nargs=argparse.REMAINDER)
    for p in (send,listing,run):
        p.add_argument('--to',required=True,help='Saved UTA server ID or unique name')
        p.add_argument('--workspace',default='default',help='Project namespace within this client identity')
        p.add_argument('--remote-cli',default='/usr/local/bin/uta-server',help='Receiver CLI path; Windows: path to uta-server.cmd')
        p.add_argument('--remote-platform',choices=('posix','windows'),default='posix')
        p.add_argument('--retries',type=int,choices=range(4),default=2,help='Upload retries only; commands are never retried')


def select_server(servers,value):
    matches=[s for s in servers if value==s['id']]
    if not matches: matches=[s for s in servers if value==s.get('name')]
    if len(matches)!=1: raise TransferError('Select an existing server ID or an unambiguous name')
    return matches[0]


def dispatch(args,servers,identity,ssh_args):
    import shlex
    server=select_server(servers,args.to); client=identity(server['id'])
    # Paths are configuration, never embedded file names or credentials. Keep Windows cmd metacharacters out.
    if not re.fullmatch(r'[A-Za-z0-9_./:\\ -]{1,512}',args.remote_cli): raise TransferError('Invalid receiver executable path')
    command=(subprocess.list2cmdline([args.remote_cli,'transfer','serve']) if args.remote_platform=='windows'
             else shlex.join([args.remote_cli,'transfer','serve']))
    argv=ssh_args(server)+['-o','Compression=no',server['target'],command]
    def progress(value):
        print(json.dumps(value,ensure_ascii=True),file=sys.stderr,flush=True)
    if args.command=='transfers':
        op={'check':'hello'}.get(args.operation,args.operation)
        if op in ('status','remove','run-status'): component(args.id,JOB)
        if op=='run-status': component(args.run_id,JOB)
        return rpc(argv,client,args.workspace,op,id=args.id,run_id=args.run_id)
    if args.command=='run':
        command_args=args.argv[1:] if args.argv[:1]==['--'] else args.argv
        if not command_args: raise TransferError('Supply the target tool command after --')
        if not 1<=args.timeout<=3600: raise TransferError('Timeout must be 1..3600 seconds')
        run_id=component(args.run_id or uuid.uuid4().hex,JOB)
    result=upload(argv,client,args.workspace,args.source if args.command=='send' else args.upload,args.retries,progress)
    if args.command=='send': return result
    progress(dict(event='execution',id=result['id'],run_id=run_id,workspace=args.workspace))
    try:
        execution=rpc(argv,client,args.workspace,'run',progress,id=result['id'],run_id=run_id,argv=command_args,timeout=args.timeout)
    except TransportError as exc:
        raise TransferError(f'Execution outcome unknown; do not rerun automatically. Query task {result["id"]}, run {run_id}: {exc}') from None
    return dict(transfer=result,execution=execution)


def main():
    args=sys.argv[1:]
    if args[:1]==['transfer']: args=args[1:]
    if args!=['serve']: raise SystemExit('Usage: uta-server transfer serve (SSH stdio only)')
    # Windows must not translate CR/LF in binary payloads.
    if os.name=='nt':
        import msvcrt
        for stream in (sys.stdin,sys.stdout): msvcrt.setmode(stream.fileno(),os.O_BINARY)
    return serve(watchdog=True)


if __name__=='__main__': sys.exit(main())
