"""Management-only tests. No physical USB, existing daemon, or real cloud state."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import uta
import uta_client as identity
import uta_server as server


def cred(char):
    secret = char * 64
    return dict(client_id=identity.identifier(secret), secret=secret)


class CredentialTests(unittest.TestCase):
    def test_stable_across_processes_and_distinct_for_installations_and_servers(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as other:
            value = identity.credential(home, 'a'*32)
            self.assertEqual(identity.credential(home, 'a'*32), value)
            self.assertNotEqual(identity.credential(other, 'a'*32), value)
            self.assertNotEqual(identity.credential(home, 'b'*32), value)
            code = ('import sys;sys.path.insert(0,sys.argv[1]);import uta_client;'
                    'print(uta_client.credential(sys.argv[2],"a"*32)["client_id"])')
            actual = subprocess.check_output([sys.executable, '-c', code, str(ROOT/'python'), home], text=True)
            self.assertEqual(actual.strip(), value['client_id'])
            self.assertEqual((Path(home)/'.client-identity.json').stat().st_mode & 0o777, 0o600)

    def test_concurrent_first_use_has_one_identity(self):
        with tempfile.TemporaryDirectory() as home, ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(lambda _: identity.credential(home, 'a'*32)['client_id'], range(16)))
            self.assertEqual(len(set(values)), 1)

    def test_id_alone_or_another_secret_is_not_a_credential(self):
        for invalid in ({'client_id':cred('a')['client_id']}, dict(cred('a'),secret='b'*64),
                        dict(cred('a'),client_id='z'*64)):
            with self.assertRaises(PermissionError): identity.validate(invalid)

    def test_private_export_no_overwrite_symlink_or_world_readable_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'client.json';identity.export_file(path,cred('a'))
            self.assertEqual(identity.read_file(path),cred('a'))
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            with self.assertRaises(FileExistsError):identity.export_file(path,cred('b'))
            link=Path(tmp)/'link';link.symlink_to(path)
            with self.assertRaises(OSError):identity.read_file(link)
            path.chmod(0o644)
            with self.assertRaises(PermissionError):identity.read_file(path)

    def test_corruption_never_silently_regenerates_identity(self):
        with tempfile.TemporaryDirectory() as home:
            identity.credential(home,'a'*32);path=Path(home)/'.client-identity.json'
            path.write_text('broken\n')
            with self.assertRaises(PermissionError):identity.credential(home,'a'*32)
            self.assertEqual(path.read_text(),'broken\n')

    def test_bounded_input_and_errors_do_not_echo_secret(self):
        for raw in (b'x'*2049,b'{"secret":"private-marker"}\n'):
            with self.assertRaises(PermissionError) as error:identity.read_stream(io.BytesIO(raw))
            self.assertNotIn('private-marker',str(error.exception))

    def test_mac_cli_export_does_not_start_daemon_or_print_secrets(self):
        with tempfile.TemporaryDirectory() as home:
            Path(home,'config.json').write_text(json.dumps(dict(servers=[dict(id='a'*32)])))
            env=dict(os.environ,UTA_HOME=home)
            command=[sys.executable,str(ROOT/'python/uta.py'),'client','export','--server','a'*32,
                     '--output',str(Path(home)/'export.json')]
            proc=subprocess.run(command,env=env,capture_output=True,text=True,timeout=5)
            self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
            value=identity.read_file(Path(home)/'export.json')
            self.assertNotIn(value['secret'],proc.stdout+proc.stderr)
            self.assertEqual(json.loads(proc.stdout)['result']['client_id'],value['client_id'])
            self.assertFalse(Path(home,'daemon.log').exists())


class SameRootClientTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.state=Path(tmp.name)
        for target,name,value in ((server,'STATE',self.state),(server,'locked',contextlib.nullcontext),
                                  (server,'owner',lambda:0),(server,'trusted_state_file',lambda _:None),
                                  (server,'record_status',lambda r:dict(r))):
            p=patch.object(target,name,value);p.start();self.addCleanup(p.stop)
        self.a,self.b=cred('a'),cred('b')
        self.write('a'*32,self.a,route='f'*32)
        self.write('b'*32,self.b,route='f'*32)
        self.write('c'*32,None)

    def write(self,session,client,route=None):
        row=dict(session=session,owner=0,route_id=route,state='attached',nodes=['/dev/synthetic-'+session[0]])
        if client:row['client_id']=client['client_id']
        (self.state/(session+'.json')).write_text(json.dumps(row))
        return row

    def test_same_root_sees_only_own_records_not_legacy(self):
        self.assertEqual([r['session'] for r in server.list_sessions(client=self.a)],['a'*32])
        self.assertEqual([r['session'] for r in server.list_sessions(client=self.b)],['b'*32])
        self.assertEqual(len(server.list_sessions(admin=True)),3)

    def test_same_root_cannot_read_or_disconnect_another_client(self):
        with patch.object(server,'matching_port') as matching:
            for session in ('b'*32,'c'*32):
                with self.assertRaises(PermissionError):server.list_sessions(session,self.a)
                with self.assertRaises(PermissionError):server.detach(session,self.a)
                self.assertTrue((self.state/(session+'.json')).exists())
            matching.assert_not_called()

    def test_no_context_fails_closed_even_for_root_or_nonexistent_session(self):
        for op in (lambda:server.list_sessions(),lambda:server.detach('d'*32),
                   lambda:server.attach('a'*32,30000,'99-1')):
            with self.assertRaises(PermissionError):op()

    def test_forged_context_and_root_without_admin_cannot_bypass_uid(self):
        with self.assertRaises(PermissionError):server.list_sessions(client=dict(self.a,secret=self.b['secret']))
        with patch.object(server,'owner',return_value=1001):
            self.assertEqual(server.list_sessions(client=self.a),[])
            with self.assertRaises(PermissionError):server.list_sessions(admin=True)

    def test_own_cleanup_does_not_remove_any_other_record(self):
        with patch.object(server,'matching_port',return_value=None):server.detach('a'*32,self.a)
        self.assertFalse((self.state/('a'*32+'.json')).exists())
        self.assertEqual(len(list(self.state.glob('*.json'))),2)

    def test_legacy_cleanup_requires_explicit_admin(self):
        with patch.object(server,'matching_port',return_value=None):server.detach('c'*32,admin=True)
        self.assertEqual(len(server.list_sessions(admin=True)),2)

    def test_existing_session_cannot_be_claimed_by_another_client(self):
        auth=server.uta_auth.create('a'*32,'99-1',0)
        with patch.object(server.socket,'create_connection') as connect:
            with self.assertRaises(PermissionError):server.attach('a'*32,30000,'99-1',auth=auth,client=self.b)
            connect.assert_not_called()
            self.assertEqual(server.read_record('a'*32,self.a)['client_id'],self.a['client_id'])

    def test_route_resolution_is_scoped_and_follows_reenumeration(self):
        self.assertEqual(server.resolve_route('f'*32,self.a)['nodes'],['/dev/synthetic-a'])
        row=self.write('a'*32,self.a,route='f'*32);row.update(state='disconnected',nodes=[])
        (self.state/('a'*32+'.json')).write_text(json.dumps(row))
        with self.assertRaises(RuntimeError):server.resolve_route('f'*32,self.a)
        self.write('d'*32,self.a,route='f'*32)
        self.assertEqual(server.resolve_route('f'*32,self.a)['nodes'],['/dev/synthetic-d'])
        self.write('e'*32,self.a,route='f'*32)
        with self.assertRaises(RuntimeError):server.resolve_route('f'*32,self.a)

    def cli(self,args,raw=b''):
        output=io.StringIO()
        with patch.object(sys,'argv',['uta-server',*args]), patch.object(sys,'platform','linux'), \
             patch.object(sys,'stdin',io.TextIOWrapper(io.BytesIO(raw))), patch.object(sys,'stdout',output), \
             patch.dict(os.environ,{},clear=True):
            result=server.main()
        return result,json.loads(output.getvalue())

    def test_cli_default_is_not_all_and_stdin_scopes_results(self):
        code,result=self.cli(['list']);self.assertEqual(code,1);self.assertFalse(result['ok'])
        code,result=self.cli(['list','--client-stdin'],identity.encode(self.a))
        self.assertEqual(code,0);self.assertEqual([r['session'] for r in result['result']],['a'*32])
        code,result=self.cli(['list','--admin']);self.assertEqual(code,0);self.assertEqual(len(result['result']),3)

    def test_cli_reads_client_then_separate_session_auth_line(self):
        auth=server.uta_auth.create('d'*32,'99-1',0)
        with patch.object(server,'attach',return_value={'state':'enumerating'}) as attach:
            code,_=self.cli(['attach','--session','d'*32,'--port','30000','--busid','99-1',
                             '--client-stdin','--auth-stdin'],identity.encode(self.a)+server.uta_auth.encode(auth))
        self.assertEqual(code,0);self.assertEqual(attach.call_args.args[-2:],(auth,self.a))


class ControllerIdentityTests(unittest.TestCase):
    def test_management_uses_private_stdin_not_arguments_and_preserves_auth_payload(self):
        with tempfile.TemporaryDirectory() as home, patch.object(uta,'HOME',Path(home)), \
             patch.object(uta,'remote_command',return_value='{"ok":true,"result":{}}') as remote:
            host=uta.validate_server(dict(name='synthetic',target='synthetic'))
            auth=server.uta_auth.create('a'*32,'99-1',0)
            for command in (['attach','--auth-stdin'],['status'],['detach'],['list'],['resolve']):
                extra=server.uta_auth.encode(auth).decode() if command[0]=='attach' else None
                uta.remote_json(host,command,input=extra)
                args=remote.call_args.args;data=remote.call_args.kwargs['input'];lines=data.splitlines()
                credential=json.loads(lines[0]);identity.validate(credential)
                self.assertIn('--client-stdin',args[1]);self.assertNotIn(credential['secret'],str(args))
                if extra:self.assertEqual(json.loads(lines[1]),auth)
                else:self.assertEqual(len(lines),1)
            uta.remote_json(host,['doctor'])
            self.assertIsNone(remote.call_args.kwargs['input'])
            self.assertNotIn('--client-stdin',remote.call_args.args[1])
            with self.assertRaises(ValueError):uta.remote_json(host,['list','--admin'])


if __name__=='__main__':unittest.main()
