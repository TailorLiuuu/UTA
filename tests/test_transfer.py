import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
import uta_transfer as t


def identity():
    secret=secrets.token_hex(32)
    return dict(secret=secret,client_id=hashlib.sha256(b'UTA control client v1\0'+bytes.fromhex(secret)).hexdigest())


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name); self.root=self.base/'receiver'; self.client=identity(); self.other=identity()
        self.source=self.base/'firmware.bin'; self.source.write_bytes(bytes(range(256))*8192+b'\x00\r\n\xfftail')
        self.env=dict(os.environ,UTA_TRANSFER_HOME=str(self.root))
        self.argv=[sys.executable,'-B',str(Path(t.__file__).with_name('uta_server.py')),'transfer','serve']
    def store(self,client=None,space='board'):
        return t.Store(client or self.client,space,self.root)
    def upload(self,source=None,client=None,space='board'):
        return t.upload(self.argv,client or self.client,space,source or self.source,retries=0,env=self.env)
    def rpc(self,action,client=None,space='board',**kw):
        return t.rpc(self.argv,client or self.client,space,action,env=self.env,**kw)
    def request(self,**kw):
        return dict(version=1,client=self.client,workspace='board',**kw)
    def test_binary_roundtrip_and_completed_retry(self):
        result=self.upload(); self.assertEqual(result['state'],'complete')
        self.assertEqual(Path(result['entry']).read_bytes(),self.source.read_bytes())
        repeat=self.upload(); self.assertEqual(repeat['sent_bytes'],0);self.assertEqual(repeat['id'],result['id'])
    def test_directory_and_empty_directory(self):
        source=self.base/'package'; (source/'nested').mkdir(parents=True); (source/'empty').mkdir()
        (source/'nested'/'固件.bin').write_bytes(b'abc'); (source/'readme.txt').write_text('hello')
        result=self.upload(source); self.assertTrue((Path(result['entry'])/'empty').is_dir())
        self.assertEqual((Path(result['entry'])/'nested'/'固件.bin').read_bytes(),b'abc')
    def test_zero_length_file(self):
        self.source.write_bytes(b'');r=self.upload();self.assertEqual(Path(r['entry']).read_bytes(),b'')
    def test_cross_client_cannot_list_status_remove_or_execute(self):
        a=self.upload(); self.assertEqual(self.rpc('list',client=self.other),[])
        for op in ('status','remove','run'):
            with self.assertRaises(t.TransferError):self.rpc(op,client=self.other,id=a['id'],run_id='a'*32,argv=[sys.executable,'-c','pass'])
        b=self.upload(client=self.other)
        self.assertNotEqual(a['entry'],b['entry']); self.assertEqual(Path(a['entry']).read_bytes(),Path(b['entry']).read_bytes())
    def test_same_client_different_project_and_same_name_different_contents(self):
        a=self.upload(); b=self.upload(space='another'); self.assertNotEqual(a['entry'],b['entry'])
        self.source.write_bytes(b'new firmware'); c=self.upload(); self.assertNotEqual(a['id'],c['id'])
        self.assertEqual(Path(c['entry']).read_bytes(),b'new firmware')
    def test_forged_client_id_rejected(self):
        bad=dict(self.other,client_id=self.client['client_id'])
        with self.assertRaises(t.TransferError):self.rpc('list',client=bad)
    def test_path_traversal_and_portability(self):
        manifest,_=t.snapshot(self.source)
        for name in ('../escape','/absolute','C:/escape','a\\b','a//b','CON','foo.','a\nname','a/../b'):
            with self.subTest(name=name),self.assertRaises(t.TransferError):
                t.validate_manifest(dict(manifest,files=[dict(manifest['files'][0],path=name)]))
    def test_unicode_and_case_collisions(self):
        m,_=t.snapshot(self.source); f=m['files'][0]
        for a,b in [('A.bin','a.bin'),('é.bin','e\u0301.bin')]:
            with self.assertRaises(t.TransferError):t.validate_manifest(dict(files=[dict(f,path=a),dict(f,path=b)],directories=[],entry=a))
    def test_source_symlink_rejected(self):
        if os.name=='nt':self.skipTest('Windows symlink requires extra privilege')
        link=self.base/'link.bin';link.symlink_to(self.source)
        with self.assertRaises(t.TransferError):self.upload(link)
    def test_receiver_symlink_rejected(self):
        if os.name=='nt':self.skipTest('Windows symlink requires extra privilege')
        s=self.store();m,_=t.snapshot(self.source);job=s.reserve(m);partial=t.safe_dir(s.task(job)/'partial')
        victim=self.base/'outside';victim.write_bytes(b'untouched');(partial/self.source.name).symlink_to(victim)
        with self.assertRaises(t.TransferError):self.upload()
        self.assertEqual(victim.read_bytes(),b'untouched')
    def test_hardlink_rejected(self):
        link=self.base/'linked';os.link(self.source,link)
        with self.assertRaises(t.TransferError):self.upload()
    def test_live_interruption_resume_and_prefix_validation(self):
        manifest,_=t.snapshot(self.source); request=self.request(action='put',manifest=manifest)
        channel=t.Channel(self.argv,env=self.env)
        try:
            channel.send(request);offer=channel.read();self.assertEqual(offer['event'],'offer')
            self.assertEqual(channel.read()['offset'],0);channel.send(dict(offset=0));channel.read()
            channel.write(self.source.read_bytes()[:t.BLOCK]);channel.proc.stdin.close();channel.proc.wait(timeout=5)
        finally:channel.close()
        partial=self.store().task(offer['id'])/'partial'/self.source.name
        self.assertEqual(partial.stat().st_size,t.BLOCK)
        r=self.upload();self.assertEqual(r['resumed_bytes'],t.BLOCK);self.assertEqual(r['sent_bytes'],self.source.stat().st_size-t.BLOCK)
        self.assertEqual(Path(r['entry']).read_bytes(),self.source.read_bytes())
    def test_corrupt_prefix_restarts_file(self):
        s=self.store();m,_=t.snapshot(self.source);job=s.reserve(m);partial=t.safe_dir(s.task(job)/'partial')
        p=partial/self.source.name;p.write_bytes(b'corrupt prefix');p.chmod(0o600)
        r=self.upload();self.assertEqual(r['resumed_bytes'],0);self.assertEqual(Path(r['entry']).read_bytes(),self.source.read_bytes())
    def test_final_checksum_mismatch_never_commits(self):
        m,_=t.snapshot(self.source);m['files'][0]['sha256']='0'*64
        body=t.encoded(self.request(action='put',manifest=m))+b'\n'+t.encoded({'offset':0})+b'\n'+self.source.read_bytes()
        output=io.BytesIO();self.assertEqual(t.serve(io.BytesIO(body),output,self.root),1)
        self.assertIn(b'SHA-256 mismatch',output.getvalue());self.assertFalse(any(self.root.glob('clients/*/*/*/files')))
    def test_quota_reserves_partial_tasks(self):
        s=self.store();m,_=t.snapshot(self.source)
        with patch.object(t,'CLIENT_QUOTA',1),self.assertRaises(t.TransferError):s.reserve(m)
        with patch.object(t,'ACCOUNT_QUOTA',1),self.assertRaises(t.TransferError):s.reserve(m)
        with patch.object(t,'MAX_JOBS',0),self.assertRaises(t.TransferError):s.reserve(m)
    def test_busy_task_cannot_be_overwritten_or_removed(self):
        r=self.upload();s=self.store()
        with t.locked(s.base/(r['id']+'.lock')):
            with self.assertRaises(t.TransferError):self.upload()
            with self.assertRaises(t.TransferError):self.rpc('remove',id=r['id'])
    def test_completed_file_tampering_prevents_execution(self):
        r=self.upload();Path(r['entry']).write_bytes(b'changed')
        with self.assertRaises(t.TransferError):self.rpc('run',id=r['id'],run_id='a'*32,argv=[sys.executable,'-c','pass'])
    def test_command_argv_not_shell_and_same_run_id_never_replays(self):
        r=self.upload();arg='hello; $(touch nope) & "quote"'
        command=[sys.executable,'-c','import pathlib,sys; pathlib.Path("count").open("a").write("x"); print(sys.argv[1])',arg]
        events=[]
        a=t.rpc(self.argv,self.client,'board','run',id=r['id'],run_id='a'*32,argv=command,env=self.env,progress=events.append)
        b=self.rpc('run',id=r['id'],run_id='a'*32,argv=command)
        self.assertEqual(a,b);self.assertEqual(a['exit_code'],0)
        self.assertEqual((Path(r['directory'])/'count').read_text(),'x')
        self.assertIn(arg,''.join(e.get('text','') for e in events))
    def test_command_timeout_and_exit_status(self):
        r=self.upload();a=self.rpc('run',id=r['id'],run_id='a'*32,argv=[sys.executable,'-c','import time; time.sleep(30)'],timeout=1)
        self.assertEqual(a['state'],'timeout')
        b=self.rpc('run',id=r['id'],run_id='b'*32,argv=[sys.executable,'-c','raise SystemExit(7)'])
        self.assertEqual(b['exit_code'],7)
    def test_stale_execution_reported_unknown(self):
        r=self.upload();s=self.store();runs=t.safe_dir(s.task(r['id'])/'runs')
        t.save(runs/('c'*32+'.json'),dict(run_id='c'*32,id=r['id'],state='running'))
        value=self.rpc('run-status',id=r['id'],run_id='c'*32)
        self.assertEqual(value['state'],'unknown')
    def test_remove_only_owned_task(self):
        a=self.upload();b=self.upload(client=self.other)
        self.rpc('remove',id=a['id']);self.assertEqual(self.rpc('list'),[]);self.assertTrue(Path(b['entry']).exists())
    def test_client_credential_compatible_with_existing_module(self):
        if os.name=='nt':self.skipTest('POSIX original client module')
        import uta_client
        self.assertEqual(t.credential(self.client),uta_client.identifier(self.client['secret']))
    def test_server_selection_never_guesses_duplicate_name(self):
        servers=[dict(id='a',name='same'),dict(id='b',name='same')]
        with self.assertRaises(t.TransferError):t.select_server(servers,'same')
        self.assertEqual(t.select_server(servers,'b')['id'],'b')
    def test_oversized_control_line(self):
        output=io.BytesIO();self.assertEqual(t.serve(io.BytesIO(b'x'*(t.MAX_JSON+2)),output,self.root),1)
    def test_invalid_manifest_sizes(self):
        m,_=t.snapshot(self.source)
        for value in (-1,True,t.MAX_BYTES+1):
            with self.assertRaises(t.TransferError):t.validate_manifest(dict(m,files=[dict(m['files'][0],size=value)]))
    def test_account_concurrency_limit(self):
        s=self.store()
        with contextlib.ExitStack() as stack:
            for _ in range(t.MAX_ACTIVE):stack.enter_context(s.slot())
            with self.assertRaises(t.TransferError):stack.enter_context(s.slot())
    def test_crash_between_rename_and_commit_is_recoverable(self):
        r=self.upload();s=self.store();record=s.record(r['id']);record['state']='partial'
        t.save(s.task(r['id'])/'record.json',record)
        resumed=self.upload();self.assertEqual(resumed['sent_bytes'],0);self.assertEqual(resumed['state'],'complete')
    def test_crash_before_initial_record_is_recoverable(self):
        s=self.store();m,_=t.snapshot(self.source);job=hashlib.sha256(t.encoded(m)).hexdigest()[:32]
        t.safe_dir(s.base/job)
        self.assertEqual(self.upload()['state'],'complete')
    def test_output_limit_is_bounded_and_reserved_in_quota(self):
        r=self.upload();s=self.store();out=io.BytesIO();wire=t.Wire(io.BytesIO(),out)
        with patch.object(t,'MAX_OUTPUT',1024):
            t.execute(s,r['id'],dict(run_id='f'*32,argv=[sys.executable,'-c','print("x"*2000)'],timeout=5),wire)
        result=json.loads(out.getvalue().splitlines()[-1])['result']
        self.assertEqual(result['state'],'output_limit')
        self.assertEqual(s.record(r['id'])['run_bytes_reserved'],1024)
    def test_same_run_id_rejects_changed_command(self):
        r=self.upload();kw=dict(id=r['id'],run_id='a'*32)
        self.rpc('run',**kw,argv=[sys.executable,'-c','pass'])
        with self.assertRaises(t.TransferError):self.rpc('run',**kw,argv=[sys.executable,'-c','print(1)'])
    def test_split_utf8_command_output(self):
        r=self.upload();events=[]
        command=[sys.executable,'-c','import os,time; b="中文".encode(); os.write(1,b[:1]); time.sleep(.1); os.write(1,b[1:])']
        t.rpc(self.argv,self.client,'board','run',id=r['id'],run_id='e'*32,argv=command,env=self.env,progress=events.append)
        self.assertEqual(''.join(e.get('text','') for e in events),'中文')
    def test_transport_failure_retries_upload_but_not_command(self):
        original=t.Channel
        class FailFirst(original):
            used=False
            def write(inner,data):
                if len(data)>=t.BLOCK and not FailFirst.used:
                    FailFirst.used=True;super(FailFirst,inner).write(data)
                    inner.proc.stdin.close();inner.proc.wait(timeout=5)
                    raise t.TransportError('Synthetic connection loss')
                super(FailFirst,inner).write(data)
        with patch.object(t,'Channel',FailFirst):
            r=t.upload(self.argv,self.client,'board',self.source,retries=1,env=self.env)
        self.assertEqual(Path(r['entry']).read_bytes(),self.source.read_bytes());self.assertGreater(r['resumed_bytes'],0)


if __name__=='__main__':unittest.main()
