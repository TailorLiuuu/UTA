"""Synthetic endpoints only: never open a physical USB or serial device."""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import uta_auth as auth
import uta_server as server
import uta

FIXTURE = Path(os.environ.get('UTA_TEST_FIXTURE', str(ROOT / 'build/native/uta-test-fixture')))


def submit(seq, length=4096, direction=1, ep=1, setup=b'\0'*8):
    return struct.pack('!IIIIIIIIII8s', 1, seq, 0x10001, direction, ep, 0, length, 0, 0xffffffff, 0, setup)


def imported(sock):
    sock.sendall(struct.pack('!HHI32s', 0x111, 0x8003, 0, b'99-1'))
    server.header(sock, 3); server.receive(sock, 312)


class AuthenticationTests(unittest.TestCase):
    def handshake(self, client_config=None):
        config = auth.create('a'*32, '99-1', 1001)
        client_config = config if client_config is None else dict(config, **client_config)
        a, b = socket.socketpair(); errors = []
        def serve():
            try:
                b.sendall(auth.server(b, config))
                self.assertEqual(b.recv(4), b'data')
            except Exception as exc: errors.append(exc)
            finally: b.close()
        worker = threading.Thread(target=serve); worker.start()
        try:
            auth.client(a, client_config); a.sendall(b'data')
        finally:
            a.close(); worker.join(4)
        self.assertFalse(errors)

    def test_mutual_auth_leaves_exact_usb_stream(self): self.handshake()

    def test_wrong_credential_account_session_or_device_rejected(self):
        for changed in ({'key':'0'*64}, {'owner_uid':1002}, {'session':'b'*32}, {'busid':'99-2'}):
            with self.subTest(changed=list(changed)), self.assertRaises((PermissionError, ConnectionError)):
                self.handshake(changed)

    def test_raw_usbip_and_slow_client_cannot_block_forever(self):
        for data in (b'\x01\x11\x80\x03'+b'\0'*37, b'U'):
            a,b=socket.socketpair()
            try:
                a.sendall(data)
                with patch.object(auth,'HANDSHAKE_SECONDS',.05), self.assertRaises((OSError,PermissionError)):
                    auth.server(b,auth.create('a'*32,'99-1',1001))
            finally:a.close();b.close()

    def test_transcript_cannot_replay_with_new_server_nonce(self):
        config=auth.create('a'*32,'99-1',1001); cn=b'c'*32
        responses=[]
        for attempt in range(2):
            a,b=socket.socketpair(); errors=[]
            def serve():
                try: b.sendall(auth.server(b,config))
                except Exception as e: errors.append(e)
                finally:b.close()
            worker=threading.Thread(target=serve);worker.start()
            a.sendall(auth.MAGIC+cn); response=server.receive(a,64); responses.append(response)
            if attempt==0:
                old_proof=auth.proof(config,b'client',cn,response[:32]);a.sendall(old_proof)
                self.assertEqual(server.receive(a,len(auth.ACK)),auth.ACK)
            else:
                a.sendall(old_proof);self.assertEqual(a.recv(1),b'')
            a.close();worker.join(4)
            self.assertEqual(bool(errors),bool(attempt))
        self.assertNotEqual(responses[0],responses[1])

    def test_configuration_is_bounded_and_does_not_echo_secrets(self):
        secret='sensitive-marker'
        for raw in (b'x'*2049, json.dumps({'key':secret}).encode()+b'\n'):
            with self.assertRaises(ValueError) as e:auth.read_config(io.BytesIO(raw))
            self.assertNotIn(secret,str(e.exception))


CREDENTIAL = dict(secret="c"*64, client_id=server.uta_client.identifier("c"*64))


class AccountTests(unittest.TestCase):
    def test_authentication_is_required_before_kernel_or_network_use(self):
        for uid, session, busid in ((1002,'a'*32,'99-1'),(1001,'b'*32,'99-1'),(1001,'a'*32,'99-2')):
            config=auth.create(session,busid,uid)
            with patch.object(server,'owner',return_value=1001), patch.object(server,'locked') as lock:
                with self.assertRaises(PermissionError):server.attach('a'*32,12345,'99-1',auth=config,client=CREDENTIAL)
                lock.assert_not_called()
        with self.assertRaises(PermissionError):server.attach('a'*32,12345,'99-1',client=CREDENTIAL)

    def test_other_account_cannot_list_read_or_detach_records(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(server,'STATE',Path(tmp)), \
             patch.object(server,'locked',contextlib.nullcontext), patch.object(server,'trusted_state_file'), \
             patch.object(server,'record_status',side_effect=lambda r:r):
            path=Path(tmp)/('a'*32+'.json');path.write_text(json.dumps({'owner':1001,'client_id':CREDENTIAL['client_id']}))
            with patch.object(server,'owner',return_value=1002):
                self.assertEqual(server.list_sessions(client=CREDENTIAL),[])
                with self.assertRaises(PermissionError):server.read_record('a'*32,CREDENTIAL)
                with self.assertRaises(PermissionError):server.detach('a'*32,CREDENTIAL)
                self.assertTrue(path.exists())
            with patch.object(server,'owner',return_value=1001):
                self.assertEqual(server.list_sessions(client=CREDENTIAL),[{'owner':1001,'client_id':CREDENTIAL['client_id']}])

    def test_symlink_records_are_never_followed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(server,'STATE',Path(tmp)):
            target=Path(tmp)/'target';target.write_text('{}')
            (Path(tmp)/('a'*32+'.json')).symlink_to(target)
            with self.assertRaises(OSError):server.read_record('a'*32,CREDENTIAL)
            self.assertEqual(target.read_text(),'{}')

    def test_insecure_or_non_root_state_file_rejected(self):
        with tempfile.NamedTemporaryFile() as f:
            info=os.fstat(f.fileno())
            for uid,mode in ((1001,0o100600),(0,0o100666)):
                fields=list(info);fields[0]=mode;fields[4]=uid
                with patch.object(server.os,'fstat',return_value=os.stat_result(fields)), self.assertRaises(PermissionError):
                    server.trusted_state_file(f.fileno())

    def test_device_nodes_restricted_to_ssh_owner_without_opening_serial(self):
        info = type('Info', (), dict(st_mode=0o020660, st_rdev=os.makedev(188,15)))()
        with patch.object(Path,'lstat',return_value=info), patch.object(server.os,'chmod') as chmod, \
             patch.object(server.os,'chown') as chown, patch.object(server.os,'open') as opened:
            server.restrict_device_node(Path('/dev/ttyUSB15'),'188:15',1001)
            chmod.assert_called_once_with(Path('/dev/ttyUSB15'),0o600)
            chown.assert_called_once_with(Path('/dev/ttyUSB15'),1001,0,follow_symlinks=False)
            opened.assert_not_called()
            chmod.reset_mock()
            with self.assertRaises(PermissionError):server.restrict_device_node(Path('/dev/ttyUSB15'),'188:16',1002)
            chmod.assert_not_called()

    def test_secret_goes_only_over_stdin_not_command(self):
        host=uta.validate_server(dict(name='lab',target='lab'))
        config=auth.create('a'*32,'99-1',1001)
        with patch.object(uta,'run',return_value='{}') as run:
            uta.remote_command(host,['/usr/local/bin/uta-server','attach','--auth-stdin'],True,
                               input=auth.encode(config).decode())
        self.assertNotIn(config['key'],str(run.call_args.args))
        self.assertIn(config['key'],run.call_args.kwargs['input'])


class NativeLimitsTests(unittest.TestCase):
    def start(self, *args):
        self.proc=subprocess.Popen([str(FIXTURE),*args],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL,text=True)
        self.addCleanup(self.close)
        self.port=int(self.proc.stdout.readline())
        sock=socket.create_connection(('127.0.0.1',self.port),timeout=4)
        imported(sock);self.addCleanup(sock.close)
        return sock
    def close(self):
        if self.proc.poll() is None:self.proc.communicate('\n',timeout=8)
        else:self.proc.communicate(timeout=2)
    def test_reservation_moves_and_releases_exactly_once(self):
        r=subprocess.run([str(FIXTURE),'--budget'],capture_output=True,timeout=5)
        self.assertEqual(r.returncode,0,r.stderr)
    def test_outstanding_request_cap_closes_only_offending_session(self):
        sock=self.start('--hold')
        sock.sendall(b''.join(submit(i,0) for i in range(1025)))
        self.assertEqual(sock.recv(1),b'')
        self.assertIsNone(self.proc.poll())
        # A fresh import succeeds after cleanup and has a fresh reservation pool.
        time.sleep(.1)
        with socket.create_connection(('127.0.0.1',self.port),timeout=4) as other:imported(other)
    def test_aggregate_bytes_rejected_before_large_allocation(self):
        sock=self.start('--hold')
        sock.sendall(b''.join(submit(i,16*1024*1024) for i in range(4)))
        self.assertEqual(sock.recv(1),b'')
        self.assertIsNone(self.proc.poll())
    def test_invalid_control_direction_is_rejected_without_payload(self):
        sock=self.start()
        # Header IN, setup OUT: must not send uninitialized IN storage to USB.
        sock.sendall(submit(1,8,1,0,struct.pack('<BBHHH',0,1,0,0,8)))
        self.assertEqual(sock.recv(1),b'')
    def test_slow_reader_queue_is_bounded(self):
        sock=self.start();sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024)
        requests=b''.join(struct.pack('!IIIIII24s',2,i,0x10001,0,0,999,b'\0'*24) for i in range(30000))
        try:sock.sendall(requests)
        except (BrokenPipeError,ConnectionResetError):pass
        sock.settimeout(4)
        try:
            while sock.recv(65536):pass
        except ConnectionResetError:pass
        self.assertIsNone(self.proc.poll())


class RelayTests(unittest.TestCase):
    def test_authorized_pipe_path_authenticates_before_helper_and_preserves_short_packets(self):
        with tempfile.TemporaryDirectory(prefix='uta-auth-test-') as directory:
            helper=Path(directory)/'helper'; marker=Path(directory)/'started'
            helper.write_text('#!'+sys.executable+'\nimport os\n'
                              +'open('+repr(str(marker))+',"w").close()\n'
                              +'os.write(1,b\'{"ready":true}\\n\')\n'
                              +'while True:\n data=os.read(0,65536)\n if not data:break\n'
                              +' while data:\n  n=os.write(1,data);data=data[n:]\n')
            helper.chmod(0o700)
            config=auth.create('a'*32,'99-1',1001)
            code=('import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);'
                  'import uta_privileged as p,uta_auth as a;p.CLIENT=Path(sys.argv[2]);'
                  'sys.exit(p.relay("99-1","1234:5678","","authorized",a.read_config(sys.stdin.buffer)))')
            proc=subprocess.Popen([sys.executable,'-c',code,str(ROOT/'python'),str(helper)],
                                  stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            proc.stdin.write(auth.encode(config));proc.stdin.close();proc.stdin=None
            try:
                endpoint=json.loads(proc.stdout.readline())['socket']
                with socket.socket(socket.AF_UNIX) as bad:
                    bad.connect(endpoint)
                    with self.assertRaises(PermissionError):auth.client(bad,dict(config,key='0'*64))
                self.assertFalse(marker.exists())
                with socket.socket(socket.AF_UNIX) as good:
                    good.settimeout(4);good.connect(endpoint);auth.client(good,config)
                    for size in (1,4108,65536,7):
                        data=os.urandom(size);good.sendall(data)
                        self.assertEqual(server.receive(good,size),data)
                self.assertEqual(proc.wait(timeout=6),0)
                self.assertTrue(marker.exists())
                self.assertFalse(Path(endpoint).exists())
            finally:
                if proc.poll() is None:proc.terminate()
                proc.communicate(timeout=6)

    def test_failed_auth_does_not_consume_lease_and_standard_stream_has_no_extra_proxy(self):
        config=auth.create('a'*32,'99-1',1001)
        code=('import sys;sys.path.insert(0,sys.argv[1]);import uta_privileged as p,uta_auth as a;'
              'sys.exit(p.relay("99-1","1234:5678","","standard",a.read_config(sys.stdin.buffer),sys.argv[2]))')
        proc=subprocess.Popen([sys.executable,'-c',code,str(ROOT/'python'),str(FIXTURE)],stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        proc.stdin.write(auth.encode(config));proc.stdin.close();proc.stdin=None
        try:
            endpoint=json.loads(proc.stdout.readline());path=endpoint['socket']
            self.assertEqual(Path(path).stat().st_mode & 0o777,0o600)
            self.assertEqual(Path(path).parent.stat().st_mode & 0o777,0o700)
            bad=socket.socket(socket.AF_UNIX);bad.connect(path)
            try:
                with self.assertRaises(PermissionError):auth.client(bad,dict(config,key='0'*64))
            finally:bad.close()
            good=socket.socket(socket.AF_UNIX);good.settimeout(4);good.connect(path)
            auth.client(good,config);imported(good)
            data=b'x'*4108
            good.sendall(submit(1,len(data),0,2)+data)
            self.assertEqual(struct.unpack('!I',server.receive(good,48)[24:28])[0],len(data))
            good.sendall(submit(2,len(data)));server.receive(good,48)
            self.assertEqual(server.receive(good,len(data)),data)
            good.close()
            self.assertEqual(proc.wait(timeout=6),0)
            self.assertFalse(Path(path).exists())
        finally:
            if proc.poll() is None:proc.terminate()
            proc.communicate(timeout=6)


if __name__=='__main__':unittest.main()
