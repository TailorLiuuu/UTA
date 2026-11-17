"""Synthetic device records and PTYs only; never opens a physical USB device."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import pty
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'python'))
import uta_client
import uta_port
import uta_port_server as p
import uta_server as s


def client(char):
    secret=char*64
    return dict(secret=secret, client_id=uta_client.identifier(secret))


class Collector:
    def __init__(self): self.events=[]
    def write(self,value): self.events.append(value)


class PortsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.a=client('a'); self.b=client('b'); self.c=client('c')
        for target,name,value in ((s,'STATE',self.root),(s,'PORT_HISTORY',self.root/'history'),(s,'owner',lambda:os.getuid()),
            (s,'locked',contextlib.nullcontext),(s,'trusted_state_file',lambda fd:None),
            (s,'matching_port',lambda r: {} if r.get('offline') else {'status':6}),
            (s,'record_status',lambda r: dict(r,state='disconnected' if r.get('offline') else 'attached',nodes=r['nodes'])),
            (p,'process_options',lambda uid,cwd=None:dict(cwd=cwd or self.tmp.name,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)),
            (p,'other_openers',lambda node:False)):
            patcher=patch.object(target,name,value);patcher.start();self.addCleanup(patcher.stop)
        self.row=dict(session='1'*32,route_id='2'*32,client_id=self.a['client_id'],owner=os.getuid(),nodes=['/dev/ttyTEST'],busid='9-9')
        self.persist()
    def persist(self): (self.root/(self.row['session']+'.json')).write_text(json.dumps(self.row))
    def share(self,who=None,hours=24):
        return p.grant_change(s,self.a,dict(action='share',id='2'*32,recipient=(who or self.b)['client_id'],hours=hours))
    def request(self,**kw):
        return dict(action='run',id='2'*32,run_id='3'*32,timeout=5,argv=[sys.executable,'-c','import sys; print(sys.argv[1])','{port}'],**kw)
    def execute(self,request=None,who=None):
        wire=Collector();p.execute(s,who or self.a,request or self.request(),wire,io.BytesIO())
        return wire.events
    def test_list_is_private_and_share_is_one_device_only(self):
        self.assertEqual(p.visible(s,self.b),[]);self.share()
        other=dict(self.row,session='4'*32,route_id='5'*32);(self.root/('4'*32+'.json')).write_text(json.dumps(other))
        self.assertEqual([x['id'] for x in p.visible(s,self.b)],['2'*32])
        self.assertEqual(p.visible(s,self.c),[])
        self.assertEqual(p.resolve(s,self.b,'2'*32)['access'],'shared')
    def test_recipient_cannot_redelegate_or_view_grants(self):
        self.share()
        for action in ('share','revoke','grants'):
            with self.assertRaises(PermissionError): p.grant_change(s,self.b,dict(action=action,id='2'*32,recipient=self.c['client_id']))
    def test_expiry_revocation_and_cross_uid(self):
        self.share()
        with patch.object(p.time,'time',return_value=time.time()+90000):self.assertEqual(p.visible(s,self.b),[])
        with patch.object(s,'owner',return_value=os.getuid()+1):self.assertEqual(p.visible(s,self.b),[])
        p.grant_change(s,self.a,dict(action='revoke',id='2'*32,recipient=self.b['client_id']))
        self.assertEqual(p.visible(s,self.b),[])
    def test_reenumerated_node_resolved_by_route_not_old_tty(self):
        self.share(); self.row['nodes']=['/dev/ttyNEW'];self.persist()
        self.assertEqual(p.resolve(s,self.b,'2'*32)['nodes'],['/dev/ttyNEW'])
        old=dict(self.row,session='6'*32,offline=True);(self.root/('6'*32+'.json')).write_text(json.dumps(old))
        self.assertEqual(p.resolve(s,self.b,'2'*32)['session'],'1'*32)
    def test_ambiguous_route_and_serial_refused(self):
        (self.root/('6'*32+'.json')).write_text(json.dumps(dict(self.row,session='6'*32)))
        with self.assertRaises(RuntimeError): p.resolve(s,self.a,'2'*32)
        with self.assertRaises(ValueError):p.node_for(dict(nodes=['/dev/tty0','/dev/tty1']),{})
        with self.assertRaises(ValueError):p.node_for(dict(nodes=['/dev/tty0']),dict(node='/dev/tty2'))
    def test_idle_status_never_opens_device(self):
        with patch.object(p,'serial_open',side_effect=AssertionError('Must not open')),patch.object(p.subprocess,'Popen',side_effect=AssertionError('Must not execute')):
            self.assertEqual(p.visible(s,self.a)[0]['state'],'attached')
            self.share()
    def test_tool_stream_return_code_and_no_shell(self):
        request=self.request();request['argv'][-1]='{port}; echo NOT_A_SHELL'
        events=self.execute(request)
        output=''.join(e.get('text','') for e in events)
        self.assertIn('/dev/ttyTEST; echo NOT_A_SHELL',output)
        self.assertEqual(events[-1]['result']['state'],'completed')
        self.assertEqual(events[-1]['result']['exit_code'],0)
    def test_receipt_no_replay_or_changed_command(self):
        req=self.request();self.execute(req)
        with patch.object(p.subprocess,'Popen',side_effect=AssertionError('Replay')):
            self.assertEqual(self.execute(req)[-1]['result']['state'],'completed')
        req['argv'].append('other')
        with self.assertRaises(ValueError):self.execute(req)
        with self.assertRaises(PermissionError):p.receipt(s,self.b,'2'*32,'3'*32)
    def test_shared_run_cannot_select_unshared_or_wrong_node(self):
        self.share();events=self.execute(who=self.b)
        self.assertEqual(events[-1]['result']['state'],'completed')
        req=self.request();req.update(run_id='4'*32,node='/dev/ttyOTHER')
        self.assertEqual(self.execute(req,who=self.b)[-1]['result']['state'],'failed')
    def test_busy_serial_excludes_flashing(self):
        fd=os.open(self.root/('port-busy-'+'1'*32),os.O_CREAT|os.O_RDWR,0o600)
        try:
            import fcntl
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError,'Device is busy'): self.execute()
        finally:os.close(fd)
    def test_disconnect_revocation_and_timeout_stop_tool(self):
        self.share()
        req=self.request();req['argv']=[sys.executable,'-c','import time;time.sleep(10)','{port}']
        req['timeout']=1
        started=time.monotonic();self.assertEqual(self.execute(req,who=self.b)[-1]['result']['state'],'timeout')
        self.assertLess(time.monotonic()-started,4)
        req.update(run_id='4'*32,timeout=5)
        def revoke():
            time.sleep(.15)
            p.grant_change(s,self.a,dict(action='revoke',id='2'*32,recipient=self.b['client_id']))
        t=threading.Thread(target=revoke);t.start()
        self.assertEqual(self.execute(req,who=self.b)[-1]['result']['state'],'failed');t.join()
    def test_output_limit_and_failure_exit(self):
        req=self.request();req['argv']=[sys.executable,'-c','print("a"*5000)','{port}']
        with patch.object(p,'MAX_OUTPUT',100):self.assertEqual(self.execute(req)[-1]['result']['state'],'output_limit')
        req.update(run_id='4'*32,argv=[sys.executable,'-c','raise SystemExit(7)','{port}'])
        result=self.execute(req)[-1]['result'];self.assertEqual(result['exit_code'],7);self.assertEqual(result['state'],'failed')
    def test_lossless_serial_stream_and_release(self):
        master,slave=pty.openpty();self.addCleanup(os.close,master);self.addCleanup(os.close,slave)
        self.row['nodes']=[os.ttyname(slave)];self.persist()
        data=b'hello\x00\xff\r\n'+bytes(range(256))*8
        req=dict(action='logs',id='2'*32,run_id='3'*32,seconds=1,baud=115200)
        # Synthetic PTY paths are /dev/ttysNNN on macOS, /dev/pts/N on Linux.
        with patch.object(p,'node_for',return_value=os.ttyname(slave)):
            t=threading.Thread(target=lambda:(time.sleep(.15),os.write(master,data)));t.start()
            result=self.execute(req);t.join()
        actual=b''.join(base64.b64decode(e['data']) for e in result if e.get('event')=='serial')
        self.assertEqual(actual,data);self.assertEqual(result[-1]['result']['state'],'completed')
        reopened=os.open(os.ttyname(slave),os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK);os.close(reopened)
    def test_non_root_tool_uses_ssh_uid_not_broker_root(self):
        # Test the real kwargs, not the patched execution fixture.
        with patch.object(p.pwd,'getpwuid') as get,patch.object(p.os,'getgrouplist',return_value=[123]):
            get.return_value=type('User',(),dict(pw_dir='/home/test',pw_gid=123,pw_name='test'))()
            # Reload function definition in a separate module namespace to retain isolated process fixture.
            import importlib.util
            spec=importlib.util.spec_from_file_location('port_options_check',p.__file__);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            options=module.process_options(456)
            self.assertEqual(options['user'],456);self.assertEqual(options['group'],123)
            self.assertNotIn('SUDO_UID',options['env'])
    def receiver_script(self):
        source = Path(self.tmp.name)/'receiver.py'
        source.write_text("""import contextlib,sys,os,subprocess
from pathlib import Path
sys.path.insert(0,sys.argv[1])
if os.environ.get('UTA_TEST_BUNDLE'):
    import importlib.machinery,importlib.util
    loader=importlib.machinery.SourceFileLoader('uta_server',os.environ['UTA_TEST_BUNDLE'])
    spec=importlib.util.spec_from_loader(loader.name,loader)
    s=importlib.util.module_from_spec(spec);sys.modules[loader.name]=s;loader.exec_module(s)
else: import uta_server as s
p=s.uta_port_server
s.STATE=Path(sys.argv[2]);s.PORT_HISTORY=s.STATE/'history'
s.locked=contextlib.nullcontext;s.owner=lambda:os.getuid()
s.trusted_state_file=lambda fd:None
s.matching_port=lambda r:{'status':6}
s.record_status=lambda r:dict(r,state='attached',nodes=r['nodes'])
p.other_openers=lambda node:False
p.process_options=lambda uid,cwd=None:dict(cwd=cwd or str(s.STATE),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
sys.argv=['uta-server','port','--client-stdin']
s.sys.platform='linux'  # Synthetic entrypoint test; all OS/device adapters above are replaced.
sys.exit(s.main())
""")
        return [sys.executable,str(source),str(Path(p.__file__).parent),str(self.root)]

    def test_full_authenticated_stdio_roundtrip(self):
        events=[]
        result=uta_port.call(self.receiver_script(),self.a,self.request(),events.append)
        self.assertEqual(result['state'],'completed')
        self.assertIn('/dev/ttyTEST',''.join(e.get('text','') for e in events))
        with self.assertRaises(Exception):uta_port.call(self.receiver_script(),self.b,dict(action='status',id='2'*32))

    def test_client_pipe_close_releases_tool_and_device_lock(self):
        args=self.receiver_script()
        request=self.request();request['argv']=[sys.executable,'-c','import time;time.sleep(20)','{port}']
        receiver=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            receiver.stdin.write((json.dumps(self.a)+'\n'+json.dumps(request)+'\n').encode());receiver.stdin.flush()
            event=json.loads(receiver.stdout.readline())
            self.assertEqual(event['event'],'execution')
            receiver.stdin.close();receiver.stdin=None
            out,err=receiver.communicate(timeout=4)
            self.assertEqual(receiver.returncode,0,err)
            self.assertIn(b'interrupted',out)
            self.assertFalse(p.busy(s,'1'*32))
        finally:
            if receiver.poll() is None:receiver.kill();receiver.wait()
            for stream in (receiver.stdin,receiver.stdout,receiver.stderr):
                if stream is not None:stream.close()

    @unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Linux root privilege-drop verification')
    def test_real_linux_child_drops_root(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('port_real_options',p.__file__)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        account=module.pwd.getpwnam('nobody')
        child=subprocess.Popen([sys.executable,'-c','import os;print(os.getuid())'],**module.process_options(account.pw_uid,'/tmp'))
        out,err=child.communicate(timeout=5)
        self.assertEqual(child.returncode,0,err)
        self.assertEqual(int(out),account.pw_uid)

    def test_forget_requires_owner_and_finished_operation(self):
        self.execute()
        request=dict(action='forget',id='2'*32,run_id='3'*32)
        incoming=io.BytesIO((json.dumps(request)+'\n').encode());out=io.BytesIO()
        p.serve(s,self.b,incoming,out)
        self.assertFalse(json.loads(out.getvalue())['ok'])
        incoming=io.BytesIO((json.dumps(request)+'\n').encode());out=io.BytesIO()
        p.serve(s,self.a,incoming,out)
        self.assertTrue(json.loads(out.getvalue())['result']['forgotten'])

    def test_stalled_output_is_bounded(self):
        read_fd,write_fd=os.pipe()
        out=os.fdopen(write_fd,'wb',buffering=0)
        wire=p.PortWire(s,io.BytesIO(),out)
        try:
            with patch.object(wire,'WRITE_TIMEOUT',.1):
                start=time.monotonic()
                with self.assertRaises(TimeoutError):
                    for _ in range(32):wire.write(dict(ok=True,text='x'*32768))
                self.assertLess(time.monotonic()-start,1)
                with self.assertRaises(BrokenPipeError):wire.write(dict(ok=True))
        finally:wire.restore();out.close();os.close(read_fd)

    def test_client_parser_requires_explicit_baud_and_node_id(self):
        import argparse
        parser=argparse.ArgumentParser();uta_port.add_commands(parser.add_subparsers(dest='command'))
        args=parser.parse_args(['port','logs','--to','server','--id','2'*32,'--baud','115200'])
        self.assertEqual(args.seconds,60)
        self.assertFalse(uta_port.succeeded(dict(state='timeout')))
        self.assertFalse(uta_port.succeeded(dict(state='running'),'run'))

if __name__=='__main__':unittest.main()
