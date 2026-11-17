"""UTA Windows preview: usbipd-win exporter + authenticated SSH/Linux importer."""
import argparse, contextlib, hashlib, json, os, re, secrets, shlex, shutil, socket, struct
import subprocess, sys, threading, time, uuid
from pathlib import Path
import uta_auth
try:
    import uta_transfer
except ModuleNotFoundError:
    # Source checkout; installer copies the same shared module beside this CLI.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
    import uta_transfer

import uta_port

HOME = Path(os.environ.get('UTA_WINDOWS_HOME', str(Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'UTAWindows')))
USBIPD = shutil.which('usbipd') or r'C:\Program Files\usbipd-win\usbipd.exe'
def ssh_executable():
    if os.name != 'nt': return 'ssh'
    # Git's OpenSSH supports redirected background pipes on Windows. Some inbox
    # OpenSSH versions hang before connecting when launched from a Python pipe.
    git_ssh = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Git/usr/bin/ssh.exe'
    return str(git_ssh) if git_ssh.exists() else r'C:\Windows\System32\OpenSSH\ssh.exe'

SSH = ssh_executable()
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
BUSID = re.compile(r'[0-9]{1,3}-[0-9.]{1,24}')
TARGET = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]{0,150}')


def run(args, input=None, timeout=25):
    p = subprocess.run(args, input=input, capture_output=True, encoding='utf-8', errors='replace', timeout=timeout, creationflags=FLAGS)
    if p.returncode: raise RuntimeError((p.stderr or p.stdout)[-2000:])
    return p.stdout.strip()


def init():
    HOME.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        # Credentials/config are private to this Windows identity and administrators.
        user = run(['whoami'])
        run(['icacls', str(HOME), '/inheritance:r', '/grant:r', user+':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'])
    else: os.chmod(HOME, 0o700)


def read(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def save(path, value):
    tmp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with tmp.open('x', encoding='utf-8') as f: json.dump(value, f, ensure_ascii=False, indent=2)
    if os.name != 'nt': os.chmod(tmp, 0o600)
    os.replace(tmp, path)


@contextlib.contextmanager
def lock(name):
    with (HOME/(name+'.lock')).open('a+b') as f:
        f.seek(0); f.write(b'0'); f.flush(); f.seek(0)
        if os.name == 'nt':
            import msvcrt
            try: msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError: raise RuntimeError('Device or configuration already in use')
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def devices():
    return [dict(busid=d['BusId'], name=d['Description'], instance=d['InstanceId'],
                 shared=bool(d.get('PersistedGuid')), attached=bool(d.get('ClientIPAddress')))
            for d in json.loads(run([USBIPD, 'state']))['Devices'] if d.get('BusId')]


def servers(): return read(HOME/'servers.json', {})
def ssh_args(target):
    if not TARGET.fullmatch(target): raise ValueError('Use an SSH config alias or user@host')
    return [SSH, '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ForwardAgent=no',
            '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3']


def credential(sid):
    p=HOME/(sid+'.credential.json')
    with lock('identity-'+sid):
        if not p.exists():
            secret=secrets.token_hex(32)
            save(p, dict(secret=secret, client_id=hashlib.sha256(b'UTA control client v1\0'+bytes.fromhex(secret)).hexdigest()))
        v=read(p,{})
        if not re.fullmatch('[0-9a-f]{64}',v.get('secret','')) or v.get('client_id')!=hashlib.sha256(b'UTA control client v1\0'+bytes.fromhex(v['secret'])).hexdigest():
            raise RuntimeError('Invalid client identity; do not regenerate while sessions exist')
        return v


def remote(host, args, cred=None, auth=None):
    command=shlex.join(['/usr/local/bin/uta-server',*args,*(['--client-stdin'] if cred else [])])
    command='if [ "$(id -u)" = 0 ]; then exec '+command+'; else exec sudo -n '+command+'; fi'
    data=(json.dumps(cred)+'\n' if cred else '')+(uta_auth.encode(auth).decode() if auth else '')
    result=json.loads(run(ssh_args(host)+[host,command],input=data))
    if not result.get('ok'): raise RuntimeError(result.get('error','Remote operation failed'))
    return result['result']


def doctor(host):
    system=run(ssh_args(host)+[host,'uname -s'],input='')
    if system!='Linux': raise RuntimeError('This UTA receiver requires Linux; target reports '+system)
    info=remote(host,['doctor'])
    if not all([info.get('root'),info.get('vhci_available'),info.get('session_auth')==1,info.get('client_identity')==1,info.get('session_lease')]):
        raise RuntimeError('Receiver needs UTA Preview 15+, vhci_hcd and root or restricted passwordless sudo')
    return info


def receive(sock, n):
    data=b''
    while len(data)<n:
        part=sock.recv(n-len(data))
        if not part: raise EOFError('USB/IP connection closed')
        data+=part
    return data


def check_import(header,busid):
    version,op,status,raw=struct.unpack('!HHI32s',header)
    if (version,op,status)!=(0x111,0x8003,0) or raw!=busid.encode().ljust(32,b'\0'):
        raise PermissionError('Only the selected USB import is allowed')


class Relay:
    def __init__(self,auth,upstream_port=3240):
        self.upstream_port=upstream_port; self.auth=auth; self.stop=threading.Event(); self.peers=[]; self.claimed=False
        self.listener=socket.socket(); self.listener.bind(('127.0.0.1',0)); self.listener.listen(4); self.listener.settimeout(.3)
        self.port=self.listener.getsockname()[1]
        threading.Thread(target=self.serve,daemon=True).start()
    def serve(self):
        while not self.stop.is_set() and not self.claimed:
            try: peer,_=self.listener.accept()
            except socket.timeout: continue
            except OSError: return
            try:
                ack=uta_auth.server(peer,self.auth); peer.sendall(ack)
                peer.settimeout(4); header=receive(peer,40); check_import(header,self.auth['busid'])
                upstream=socket.create_connection(('127.0.0.1',self.upstream_port),timeout=5)
                self.peers=[peer,upstream]; self.claimed=True
                upstream.sendall(header)
                for s in self.peers: s.settimeout(None); s.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                for a,b in [(peer,upstream),(upstream,peer)]: threading.Thread(target=self.pump,args=(a,b),daemon=True).start()
            except (OSError,ValueError,EOFError): peer.close()
    def pump(self,a,b):
        try:
            while not self.stop.is_set():
                data=a.recv(65536)
                if not data: break
                b.sendall(data)
        except OSError: pass
        finally: self.close()
    def close(self):
        self.stop.set()
        self.listener.close()
        for s in self.peers:
            with contextlib.suppress(OSError): s.shutdown(socket.SHUT_RDWR)
            s.close()


def worker(busid,sid):
    if not BUSID.fullmatch(busid) or sid not in servers(): raise ValueError('Invalid device or server')
    with lock('device-'+busid):
        statepath=HOME/(busid+'.session.json'); previous=read(statepath,{})
        if previous.get('cleanup_pending'): raise RuntimeError('Retry cleanup before reconnecting')
        host=servers()[sid]['target']; cred=credential(sid); session=uuid.uuid4().hex; route=uuid.uuid4().hex
        state=dict(busid=busid,server_id=sid,session=session,route_id=route,state='checking',nodes=[],cleanup_pending=False)
        stopfile=HOME/(session+'.stop'); relay=None; tunnel=None; bound=False; attempted=False
        def update(**kw): state.update(kw); save(statepath,state)
        update()
        try:
            info=doctor(host) # no remote session exists before attach
            device=next(d for d in devices() if d['busid']==busid)
            if device['attached']: raise RuntimeError('Device is already attached by another client')
            if device['shared']: raise RuntimeError('Device is already shared outside this UTA session; unbind it first')
            run([USBIPD,'bind','--busid',busid]); bound=True; update(instance=device['instance'])
            if next(d for d in devices() if d['busid']==busid)['instance']!=device['instance']: raise RuntimeError('USB identity changed')
            auth=uta_auth.create(session,busid,info['uid']); relay=Relay(auth)
            log=HOME/(session+'.ssh.log')
            with log.open('w',encoding='utf-8') as f:
                tunnel=subprocess.Popen(ssh_args(host)+['-o','ExitOnForwardFailure=yes','-R',f'127.0.0.1:0:127.0.0.1:{relay.port}',host,'cat >/dev/null'],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=f,creationflags=FLAGS)
            deadline=time.monotonic()+15;port=None
            while time.monotonic()<deadline:
                match=re.search(r'Allocated port (\d+) for remote forward',log.read_text(encoding='utf-8'))
                if match: port=int(match[1]);break
                if tunnel.poll() is not None: raise RuntimeError('SSH tunnel failed: '+log.read_text(encoding='utf-8')[-1000:])
                time.sleep(.1)
            if not port: raise RuntimeError('SSH reverse forwarding was not confirmed')
            attempted=True; update(state='connecting',cleanup_pending=True)
            result=remote(host,['attach','--session',session,'--port',str(port),'--busid',busid,'--auth-stdin','--lease-seconds','60',*(['--route',route] if info.get('route_identity') else [])],cred,auth)
            update(state=result['state'],nodes=result.get('nodes',[]))
            while not stopfile.exists() and not relay.stop.wait(1):
                if tunnel.poll() is not None: raise RuntimeError('SSH tunnel stopped')
                if int(time.monotonic())%5==0:
                    result=remote(host,['status','--session',session],cred)
                    if result['state'] not in ('attached','enumerating'): break
                    update(state=result['state'],nodes=result.get('nodes',[]))
        except Exception as exc: update(error=str(exc))
        finally:
            if relay: relay.close()
            if tunnel:
                tunnel.terminate()
                try:tunnel.wait(timeout=5)
                except subprocess.TimeoutExpired:tunnel.kill();tunnel.wait()
            cleanup_error=None
            if attempted:
                try:remote(host,['detach','--session',session],cred)
                except Exception as exc:cleanup_error=str(exc)
            if bound:
                try:run([USBIPD,'unbind','--busid',busid])
                except Exception as exc:state['unbind_error']=str(exc)
            update(state='error' if state.get('error') or cleanup_error or state.get('unbind_error') else 'local',cleanup_pending=bool(cleanup_error),cleanup_error=cleanup_error,nodes=[])
            stopfile.unlink(missing_ok=True)


def main():
    p=argparse.ArgumentParser(prog='uta');sub=p.add_subparsers(dest='command',required=True)
    uta_transfer.add_commands(sub)
    uta_port.add_commands(sub)
    for name in ['devices','status']:sub.add_parser(name)
    x=sub.add_parser('server-add');x.add_argument('name');x.add_argument('target')
    x=sub.add_parser('check');x.add_argument('--server',required=True)
    for name in ['connect','worker']:
        x=sub.add_parser(name);x.add_argument('--device',required=True);x.add_argument('--server',required=True)
    for name in ['disconnect','cleanup']:
        x=sub.add_parser(name);x.add_argument('--device',required=True)
    a=p.parse_args();init()
    if a.command == 'port':
        if getattr(a,'raw',False) and os.name == 'nt':
            import msvcrt
            msvcrt.setmode(sys.stdout.fileno(),os.O_BINARY)
        values=[dict(id=sid,**v) for sid,v in servers().items()]
        result=uta_port.dispatch(a,values,credential,lambda s:ssh_args(s['target']))
        success=not isinstance(result,dict) or uta_port.succeeded(result, a.port_action)
        print(json.dumps(dict(ok=success,result=result),ensure_ascii=False),file=sys.stderr if getattr(a,'raw',False) else sys.stdout)
        sys.exit(0 if success else 1)
    if a.command in ('send','transfers','run'):
        values=[dict(id=sid,**v) for sid,v in servers().items()]
        result=uta_transfer.dispatch(a,values,credential,lambda s:ssh_args(s['target']))
        execution=result.get('execution',{})
        if execution and (execution.get('state')!='completed' or execution.get('exit_code')!=0):
            print(json.dumps(dict(ok=False,result=result),ensure_ascii=False));sys.exit(1)
        return result
    if a.command=='devices':return devices()
    if a.command=='status':return dict(devices=devices(),servers=servers(),sessions=[read(x,{}) for x in HOME.glob('*.session.json')])
    if a.command=='server-add':
        ssh_args(a.target)
        with lock('servers'):
            values=servers();sid=hashlib.sha256(a.target.encode()).hexdigest()[:32];values[sid]=dict(name=a.name,target=a.target);save(HOME/'servers.json',values)
        return dict(id=sid)
    if a.command=='check':return doctor(servers()[a.server]['target'])
    if not BUSID.fullmatch(a.device):raise ValueError('Invalid USB busid')
    if a.command=='worker':worker(a.device,a.server);return {}
    if a.command=='connect':
        if a.server not in servers():raise ValueError('Unknown server')
        with lock('device-'+a.device):
            if read(HOME/(a.device+'.session.json'),{}).get('cleanup_pending'):raise RuntimeError('Cleanup required')
        log=(HOME/(a.device+'.worker.log')).open('a',encoding='utf-8')
        subprocess.Popen([sys.executable,__file__,'worker','--device',a.device,'--server',a.server],stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=FLAGS)
        return dict(state='starting',device=a.device)
    path=HOME/(a.device+'.session.json');state=read(path,{})
    if not state:return dict(state='local')
    if a.command=='disconnect':
        (HOME/(state['session']+'.stop')).touch();return dict(state='disconnect_requested')
    with lock('device-'+a.device):
        if state.get('cleanup_pending'):
            remote(servers()[state['server_id']]['target'],['detach','--session',state['session']],credential(state['server_id']))
            state.update(cleanup_pending=False,cleanup_error=None,state='local');save(path,state)
    return state

if __name__=='__main__':
    try:print(json.dumps(dict(ok=True,result=main()),ensure_ascii=False))
    except Exception as exc:print(json.dumps(dict(ok=False,error=str(exc)),ensure_ascii=False),file=sys.stderr if '--raw' in sys.argv else sys.stdout);sys.exit(1)
