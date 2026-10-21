import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from test_bridge import client


class ReconnectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with patch.object(client, 'HOME', Path(self.tmp.name)), patch.object(threading.Thread, 'start'):
            self.c = client.Controller()
        self.host = client.validate_server(dict(name='test', target='test-host'))
        self.c.config['servers'] = [self.host]
        self.anchor = dict(busid='2-1', vidpid='1200:0001', serial='', allowed_vidpids=['1200:0001', '1200:0002'])
        self.c.sessions['2-1'] = dict(self.anchor, name='Test USB device', server_id=self.host['id'],
            session='a'*32, state='attached', nodes=['/dev/bus/usb/001/002'],
            reconnect_enabled=True, reconnect_anchor=self.anchor)

    def lost(self, offline=False):
        with patch.object(client, 'remote_json', side_effect=RuntimeError('offline') if offline else None,
                          return_value={'state': 'disconnected'}):
            self.c.connection_lost('2-1', 'USB reset')

    def test_configured_identity_changes_only_same_slot(self):
        for pid in ('1200:0002', '1200:0001'):
            self.assertTrue(client.reconnect_matches(self.anchor, dict(self.anchor, vidpid=pid)))
            self.assertFalse(client.reconnect_matches(self.anchor, dict(self.anchor, vidpid=pid, busid='2-2')))
        self.assertFalse(client.reconnect_matches(self.anchor, dict(self.anchor, vidpid='1200:9999')))
        a = dict(self.anchor, serial='original')
        self.assertFalse(client.reconnect_matches(a, dict(a, serial='other')))
        self.assertFalse(client.reconnect_matches(a, dict(a, serial='')))
        self.assertTrue(client.reconnect_matches(a, dict(a, vidpid='1200:0002', serial='')))

    def test_changed_identity_requires_explicit_configuration(self):
        plain = dict(self.anchor, allowed_vidpids=[])
        changed = dict(plain, vidpid='1200:0002')
        self.assertFalse(client.reconnect_matches(plain, changed))
        self.assertEqual(client.reconnect_ids(['ABCD:0001', 'abcd:0001']), ['abcd:0001'])
        for value in (['bad'], ['*:*'], '1200:0002', [None]):
            with self.assertRaises(ValueError): client.reconnect_ids(value)

    def test_invalid_settings_do_not_change_policy(self):
        before = self.c.sessions['2-1']['reconnect_enabled']
        with self.assertRaises(ValueError):
            self.c.handle(dict(action='reconnect_policy',busid='2-1',enabled=False,vidpids=['bad']))
        self.assertEqual(self.c.sessions['2-1']['reconnect_enabled'],before)

    def test_loss_cleans_old_session_then_schedules_original_server(self):
        self.lost()
        s = self.c.sessions['2-1']
        self.assertEqual(s['state'], 'waiting_reconnect')
        self.assertEqual(s['nodes'], [])
        self.assertGreater(s['reconnect_deadline'], time.time())
        self.c.devices = [dict(self.anchor, vidpid='1200:0002')]
        with patch.object(self.c, 'background', side_effect=lambda key, task: task()), patch.object(self.c, 'connect') as connect:
            self.c.retry_reconnect()
            connect.assert_called_once_with('2-1', self.host, automatic=True)
            self.c.retry_reconnect()
            self.assertEqual(connect.call_count, 1)  # backoff

    def test_cleanup_and_scan_failure_block_reconnect(self):
        self.lost(offline=True)
        self.c.devices = [dict(self.anchor, vidpid='1200:0002')]
        with patch.object(self.c, 'background') as schedule:
            self.c.retry_reconnect()
            schedule.assert_not_called()
            self.c.sessions['2-1']['remote_cleanup_pending'] = False
            self.c.scan_error = 'scan timeout'
            self.c.retry_reconnect()
            schedule.assert_not_called()

    def test_manual_disconnect_and_toggle_cancel_intent(self):
        for operation in ('disconnect', 'toggle'):
            with self.subTest(operation=operation):
                self.c.sessions['2-1']['reconnect_enabled'] = True
                self.lost()
                with patch.object(client, 'remote_json', return_value={}):
                    if operation == 'disconnect': self.c.disconnect('2-1')
                    else: self.c.handle(dict(action='reconnect_policy', busid='2-1', enabled=False))
                self.c.devices = [self.anchor]
                with patch.object(self.c, 'background') as schedule:
                    self.c.retry_reconnect()
                    schedule.assert_not_called()
                with patch.object(client, 'remote_json') as remote:
                    self.c.connect('2-1', self.host, automatic=True)
                    remote.assert_not_called()  # late queued work stays cancelled

    def test_expiry_attempt_limit_and_identity_mismatch(self):
        self.lost()
        s = self.c.sessions['2-1']
        self.c.devices = [dict(self.anchor, vidpid='1234:5678')]
        with patch.object(self.c, 'background') as schedule:
            self.c.retry_reconnect()
            schedule.assert_not_called()
            self.assertIn('不匹配', s['message'])
            s['reconnect_attempts'] = 3
            self.c.retry_reconnect()
            self.assertEqual(s['state'], 'error')
            self.assertEqual(s['reconnect_deadline'], 0)
            s.update(reconnect_deadline=time.time()-1, state='waiting_reconnect')
            self.c.retry_reconnect()
            self.assertEqual(s['state'], 'local')

    def test_expired_enumeration_releases_resources(self):
        s = self.c.sessions['2-1']
        s.update(reconnect_deadline=time.time()-1, state='enumerating')
        with patch.object(self.c, 'background', side_effect=lambda key, task: task()), patch.object(self.c, 'disconnect') as detach:
            self.c.retry_reconnect()
            detach.assert_called_once()

    def test_success_rearms_next_mode_change(self):
        self.lost()
        s = self.c.sessions['2-1']; s['state'] = 'enumerating'
        with patch.object(client, 'remote_json', return_value=dict(state='attached', nodes=['new'])):
            self.c.monitor_one('2-1')
        self.assertEqual(s['reconnect_deadline'], 0)
        self.lost()
        self.assertGreater(s['reconnect_deadline'], time.time())
