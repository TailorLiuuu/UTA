import socket,struct,threading,unittest,tempfile
from pathlib import Path
from unittest.mock import patch
import uta_windows as w
import uta_auth

class WindowsTests(unittest.TestCase):
    def test_import_scoped_to_one_device(self):
        h=struct.pack('!HHI32s',0x111,0x8003,0,b'6-3')
        w.check_import(h,'6-3')
        for header in [struct.pack('!HHI32s',0x111,0x8005,0,b'6-3'),struct.pack('!HHI32s',0x111,0x8003,0,b'6-4')]:
            with self.assertRaises(PermissionError):w.check_import(header,'6-3')
    def test_credential_stable_and_per_server(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(w,'HOME',Path(tmp)):
            self.assertEqual(w.credential('a'*32),w.credential('a'*32))
            self.assertNotEqual(w.credential('a'*32),w.credential('b'*32))
    def test_nonlinux_rejected_before_sudo(self):
        with patch.object(w,'run',return_value='Darwin'),patch.object(w,'remote') as remote:
            with self.assertRaisesRegex(RuntimeError,'Linux'):w.doctor('host')
            remote.assert_not_called()
    def test_authenticated_stream_and_short_packets(self):
        server=socket.socket();server.bind(('127.0.0.1',0));server.listen(1);server.settimeout(5)
        config=uta_auth.create('a'*32,'6-3',0);r=w.Relay(config,server.getsockname()[1]);seen=[]
        def echo():
            with server.accept()[0] as s:
                seen.append(w.receive(s,40));s.sendall(b'ok')
                while True:
                    d=s.recv(65536)
                    if not d:break
                    s.sendall(d)
        t=threading.Thread(target=echo);t.start()
        try:
            with socket.create_connection(('127.0.0.1',r.port),timeout=5) as s:
                uta_auth.client(s,config);s.sendall(struct.pack('!HHI32s',0x111,0x8003,0,b'6-3'));self.assertEqual(w.receive(s,2),b'ok')
                for length in [1,7,64,8192,65536]:
                    data=bytes(i%251 for i in range(length));s.sendall(data);self.assertEqual(w.receive(s,length),data)
            t.join(5);self.assertFalse(t.is_alive());self.assertEqual(len(seen),1)
        finally:r.close();server.close()

if __name__=='__main__':unittest.main()
