import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
import uta


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with patch.object(uta, 'HOME', Path(self.tmp.name)), patch.object(threading.Thread, 'start'):
            self.c = uta.Controller()
        self.owner = os.getpid()

    def request(self, action):
        return self.c.handle(dict(action=action, pid=self.owner))

    def test_update_reserves_controller_and_cancel_restores_operations(self):
        self.request('update_prepare')
        self.c.handle(dict(action='snapshot'))
        with self.assertRaisesRegex(RuntimeError, '正在更新'):
            self.c.handle(dict(action='connect'))
        with self.assertRaises(RuntimeError):
            self.c.background('usb', lambda: None)
        self.c.handle(dict(action='update_cancel', pid=self.owner + 1))
        self.assertTrue(self.c.update_reserved())
        self.request('update_cancel')
        self.assertFalse(self.c.update_reserved())
        with patch.object(threading.Thread, 'start'):
            self.c.background('usb', lambda: None)

    def test_all_live_states_cleanup_and_reconnect_deadlines_block_update(self):
        for state in uta.ACTIVE | {'disconnecting'}:
            self.c.sessions = {'a': dict(state=state)}
            with self.subTest(state=state), self.assertRaises(RuntimeError):
                self.request('update_prepare')
        for extra in ({'remote_cleanup_pending': True}, {'reconnect_deadline': time.time()+30}):
            self.c.sessions = {'a': dict(state='error', **extra)}
            with self.assertRaises(RuntimeError): self.request('update_prepare')
        self.c.sessions = {}
        self.c.busy.add('server-install')
        with self.assertRaises(RuntimeError): self.request('update_prepare')
        self.c.busy.clear()
        self.c.processes['usb'] = object()
        with self.assertRaises(RuntimeError): self.request('update_prepare')
        self.assertIsNone(self.c.update_owner)

    def test_dead_updater_releases_reservation(self):
        self.c.update_owner = 999999
        with patch.object(os, 'kill', side_effect=ProcessLookupError):
            self.assertFalse(self.c.update_reserved())
        self.assertIsNone(self.c.update_owner)

    def test_shutdown_rechecks_idle_and_stops_new_background_jobs(self):
        self.request('update_prepare')
        self.c.sessions = {'a': dict(state='attached')}
        with patch.object(threading.Timer, 'start') as start:
            with self.assertRaises(RuntimeError): self.request('update_shutdown')
            start.assert_not_called()
            self.assertFalse(self.c.stopping.is_set())
            self.c.sessions = {}
            self.assertEqual(self.request('update_shutdown')['pid'], os.getpid())
            start.assert_called_once()
            self.assertTrue(self.c.stopping.is_set())
        self.request('update_cancel')
        with self.assertRaises(RuntimeError): self.c.background('usb', lambda: None)

    def test_invalid_owner_and_other_live_updater_are_rejected(self):
        for owner in (0, 1, -1, '123', True, None):
            with self.assertRaises(ValueError):
                self.c.handle(dict(action='update_prepare', pid=owner))
        self.request('update_prepare')
        with patch.object(os, 'kill'), self.assertRaisesRegex(RuntimeError, '另一个'):
            self.c.handle(dict(action='update_prepare', pid=self.owner+1))


if __name__ == '__main__': unittest.main()
