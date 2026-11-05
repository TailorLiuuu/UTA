import copy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from test_bridge import client


class AssignmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with patch.object(client, 'HOME', Path(self.tmp.name)), patch.object(threading.Thread, 'start'):
            self.c = client.Controller()
        self.a = client.validate_server(dict(name='original', target='original-host'))
        self.b = client.validate_server(dict(name='other', target='other-host'))
        self.c.config['servers'] = [self.a, self.b]
        self.c.devices = [dict(busid='2-1')]

    def session(self, state='attached', **kwargs):
        self.c.sessions['2-1'] = dict(busid='2-1', server_id=self.a['id'], state=state,
                                     session='a'*32, nodes=['/dev/ttyUSB0'], **kwargs)

    def request(self, host=None):
        return self.c.handle(dict(action='connect', busid='2-1', server_id=(host or self.b)['id']))

    def test_all_owned_states_reject_cli_and_worker_without_touching_original(self):
        cases = [(s,{}) for s in ['connecting','enumerating','attached','disconnecting','waiting_reconnect']]
        cases += [('local',dict(remote_cleanup_pending=True)),('error',dict(remote_cleanup_pending=True)),
                  ('error',dict(reconnect_deadline=time.time()+60))]
        for state, kwargs in cases:
            with self.subTest(state=state, kwargs=kwargs):
                self.session(state, **kwargs)
                before=copy.deepcopy(self.c.sessions)
                with patch.object(self.c,'background') as work, patch.object(self.c,'disconnect') as detach, \
                     patch.object(self.c,'cleanup') as cleanup, patch.object(client,'remote_json') as remote:
                    with self.assertRaisesRegex(RuntimeError,'设备已分配给 original'): self.request()
                    with self.assertRaisesRegex(RuntimeError,'设备已分配给 original'): self.c.connect('2-1',self.b)
                    work.assert_not_called();detach.assert_not_called();cleanup.assert_not_called();remote.assert_not_called()
                self.assertEqual(self.c.sessions,before)

    def test_finished_history_is_available_but_live_transport_still_reserves(self):
        for state in ['local','error','permission_required']:
            self.session(state, reconnect_deadline=0)
            with patch.object(self.c,'background') as work:
                self.assertTrue(self.request()['started']);work.assert_called_once()
        self.c.processes['2-1']=[object()]
        with self.assertRaisesRegex(RuntimeError,'设备已分配'): self.request()

    def test_explicit_release_allows_reassignment_only_after_cleanup_succeeds(self):
        self.session()
        with patch.object(client,'remote_json',side_effect=RuntimeError('offline')):
            self.c.disconnect('2-1')
        with self.assertRaisesRegex(RuntimeError,'设备已分配'): self.request()
        with patch.object(client,'remote_json',return_value={}): self.c.disconnect('2-1')
        with patch.object(self.c,'background') as work:
            self.assertTrue(self.request()['started']);work.assert_called_once()

    def test_same_server_auto_reconnect_is_not_rejected_as_foreign(self):
        self.session('waiting_reconnect',reconnect_deadline=time.time()+60)
        with self.c.lock: self.c.require_assignment_available('2-1',self.a)
        with patch.object(self.c,'background') as work:
            self.assertTrue(self.request(self.a)['started']);work.assert_called_once()

    def test_two_requests_before_worker_starts_cannot_both_reserve_bus(self):
        self.c.sessions.clear()
        with patch.object(threading.Thread,'start'):
            self.assertTrue(self.request(self.a)['started'])
            with self.assertRaisesRegex(RuntimeError,'正在执行操作'): self.request(self.b)
        self.assertEqual(self.c.busy,{'2-1'})
