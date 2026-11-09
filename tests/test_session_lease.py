"""Lease expiry uses temporary files only; never opens USB or real sysfs."""
import contextlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
import uta_server as server


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.client = dict(secret='c'*64, client_id=server.uta_client.identifier('c'*64))
        self.session = 'a'*32
        self.record = dict(session=self.session, owner=1001, client_id=self.client['client_id'],
                           vhci_port=5, lease_seconds=60, lease_deadline=160, nodes=['fake'])
        self.file = self.root / (self.session + '.json')
        self.file.write_text(json.dumps(self.record))
        (self.root/'detach').write_text('untouched')
        for target, value in [('STATE',self.root),('VHCI',self.root),
                              ('locked',contextlib.nullcontext)]:
            p=patch.object(server,target,value);p.start();self.addCleanup(p.stop)
        for target, kwargs in [('owner',dict(return_value=1001)),
                               ('trusted_state_file',dict(return_value=None)),
                               ('matching_port',dict(return_value={'port':5})),
                               ('record_status',dict(side_effect=lambda r:dict(r,state='attached')))]:
            p=patch.object(server,target,**kwargs);p.start();self.addCleanup(p.stop)

    def test_live_heartbeat_renews_only_owned_session(self):
        with patch.object(server.time,'monotonic',return_value=140):
            self.assertEqual(server.list_sessions(self.session,self.client)['state'],'attached')
        self.assertEqual(json.loads(self.file.read_text())['lease_deadline'],200)
        other=dict(secret='d'*64,client_id=server.uta_client.identifier('d'*64))
        with self.assertRaises(PermissionError):server.list_sessions(self.session,other)
        self.assertEqual(json.loads(self.file.read_text())['lease_deadline'],200)

    def test_list_and_admin_status_do_not_renew(self):
        server.list_sessions(client=self.client)
        with patch.object(server,'owner',return_value=0):
            server.list_sessions(self.session,admin=True)
        self.assertEqual(json.loads(self.file.read_text())['lease_deadline'],160)

    def test_expiry_detaches_only_matching_port_once(self):
        with patch.object(server.time,'monotonic',return_value=161):
            self.assertFalse(server.lease_tick(self.session,self.client))
            self.assertEqual((self.root/'detach').read_text(),'5')
            (self.root/'detach').write_text('untouched')
            self.assertFalse(server.lease_tick(self.session,self.client))
            self.assertEqual(server.list_sessions(self.session,self.client)['state'],'disconnected')
        self.assertEqual((self.root/'detach').read_text(),'untouched')
        self.assertTrue(json.loads(self.file.read_text())['lease_expired'])

    def test_late_heartbeat_cannot_resurrect_expired_session(self):
        with patch.object(server.time,'monotonic',return_value=161):
            self.assertEqual(server.list_sessions(self.session,self.client)['state'],'disconnected')
        self.assertEqual(json.loads(self.file.read_text())['lease_deadline'],160)

    def test_disappeared_session_and_reused_port_are_not_detached(self):
        with patch.object(server.time,'monotonic',return_value=161), \
                patch.object(server,'matching_port',return_value=None):
            self.assertFalse(server.lease_tick(self.session,self.client))
        self.file.unlink()
        self.assertFalse(server.lease_tick(self.session,self.client))
        self.assertEqual((self.root/'detach').read_text(),'untouched')

    def test_old_cli_sessions_have_no_implicit_expiry(self):
        self.record.pop('lease_seconds');self.record.pop('lease_deadline')
        self.file.write_text(json.dumps(self.record))
        self.assertFalse(server.lease_tick(self.session,self.client))
        self.assertEqual(server.list_sessions(self.session,self.client)['state'],'attached')
        self.assertEqual((self.root/'detach').read_text(),'untouched')

    def test_wrong_client_cannot_expire_another_client_session(self):
        other=dict(secret='d'*64,client_id=server.uta_client.identifier('d'*64))
        with self.assertRaises(PermissionError):server.lease_tick(self.session,other)
        self.assertEqual((self.root/'detach').read_text(),'untouched')


if __name__ == '__main__':unittest.main()
