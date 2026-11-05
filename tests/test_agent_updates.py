import hashlib
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from test_bridge import client


class AgentUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.source = self.home / 'agent.py'
        self.source.write_text('VERSION = "0.1.0-preview.10"\n')
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.host = client.validate_server(dict(name='test', target='test-host'))
        self.addCleanup(patch.stopall)
        patch.object(client, 'agent_source', return_value=self.source).start()

    def controller(self):
        with patch.object(client, 'HOME', self.home), patch.object(threading.Thread, 'start'):
            controller = client.Controller()
        controller.config['servers'] = [self.host]
        return controller

    def test_same_bundle_does_not_prompt_after_mac_only_update(self):
        with patch.object(client, 'remote_command', return_value=self.digest + '  /usr/local/bin/uta-server') as run:
            info = client.agent_update_info(self.host, '0.1.0-preview.10')
        self.assertEqual(info['state'], 'current')
        self.assertEqual(run.call_args.args[1], ['sha256sum', client.REMOTE])

    def test_versions_are_numeric_and_newer_cloud_is_never_recommended_for_downgrade(self):
        for version, state in [('0.1.0-preview.9', 'available'), ('0.1.0-preview.11', 'newer'),
                               ('0.2.0', 'newer'), ('0.1.0', 'different'), ('custom', 'different'),
                               ('0.1.0-preview.10', 'different')]:
            with self.subTest(version=version), patch.object(client, 'remote_command', return_value='a' * 64):
                self.assertEqual(client.agent_update_info(self.host, version)['state'], state)

    def test_failed_or_invalid_checksum_is_unknown_not_current(self):
        for value in ['installed', '', 'a'*63]:
            with patch.object(client, 'remote_command', return_value=value):
                self.assertEqual(client.agent_update_info(self.host, '0.1.0-preview.10')['state'], 'unknown')
        with patch.object(client, 'remote_command', side_effect=RuntimeError('offline')):
            self.assertEqual(client.agent_update_info(self.host, '0.1.0-preview.10')['state'], 'unknown')

    def test_check_only_reads_and_does_not_install(self):
        controller = self.controller()
        with patch.object(client, 'remote_command', side_effect=['installed', 'a'*64]) as run, \
             patch.object(client, 'remote_json', return_value=dict(root=True, vhci_available=True, version='0.1.0-preview.9')), \
             patch.object(client, 'run') as upload:
            controller.check_server(self.host)
        self.assertEqual(controller.checks[self.host['id']]['agent_update']['state'], 'available')
        self.assertEqual(controller.checks[self.host['id']]['state'], 'ready')
        upload.assert_not_called()
        self.assertFalse(any(c.args[1][0] == 'install' for c in run.call_args_list))

    def test_checksum_failure_preserves_connection_readiness(self):
        controller = self.controller()
        with patch.object(client, 'remote_command', side_effect=['installed', RuntimeError('timeout')]), \
             patch.object(client, 'remote_json', return_value=dict(root=True, vhci_available=True, version='0.1.0')):
            controller.check_server(self.host)
        self.assertEqual(controller.checks[self.host['id']]['state'], 'ready')
        self.assertEqual(controller.checks[self.host['id']]['agent_update']['state'], 'unknown')

    def test_update_blocked_for_active_reconnect_cleanup_and_pending_operations(self):
        controller = self.controller()
        for state, pending, busy, deadline in [('attached', False, False, 0), ('waiting_reconnect', False, False, 0),
                ('local', True, False, 0), ('local', False, True, 0), ('error', False, False, time.time()+60)]:
            controller.sessions = {'2-1': dict(busid='2-1', server_id=self.host['id'], state=state,
                                                remote_cleanup_pending=pending, reconnect_deadline=deadline)}
            controller.busy = {'2-1'} if busy else set()
            with patch.object(controller, 'background') as launch:
                with self.assertRaisesRegex(RuntimeError, '先断开'):
                    controller.handle(dict(action='server_install', id=self.host['id']))
            launch.assert_not_called()
            self.assertEqual(controller.agent_installing, set())

    def test_accepted_update_blocks_new_attach_before_worker_starts(self):
        controller = self.controller()
        controller.devices = [dict(busid='2-1')]
        with patch.object(controller, 'background'):
            controller.handle(dict(action='server_install', id=self.host['id']))
            with self.assertRaisesRegex(RuntimeError, '正在更新'):
                controller.handle(dict(action='connect', busid='2-1', server_id=self.host['id']))
        with patch.object(controller, 'check_server', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError): controller.install_agent(self.host)
        self.assertEqual(controller.agent_installing, set())

    def test_failed_worker_schedule_does_not_leave_update_reservation(self):
        controller = self.controller()
        with patch.object(controller, 'background', side_effect=RuntimeError('shutting down')):
            with self.assertRaises(RuntimeError):
                controller.handle(dict(action='server_install', id=self.host['id']))
        self.assertEqual(controller.agent_installing, set())

    def test_install_success_requires_readback_of_exact_uploaded_bundle(self):
        for digest in [self.digest, 'a'*64]:
            controller = self.controller()
            def command(server, argv, **kwargs):
                if argv[0] == 'sha256sum': return digest
                if argv[0] == 'sh': return 'installed'
                return ''
            with patch.object(client, 'remote_command', side_effect=command), \
                 patch.object(client, 'run'), \
                 patch.object(client, 'remote_json', return_value=dict(root=True, vhci_available=True, version='0.1.0-preview.10')):
                controller.check_server(self.host, install=True)
            check = controller.checks[self.host['id']]
            self.assertEqual(check['state'], 'ready' if digest == self.digest else 'error')
            if digest == self.digest:
                self.assertEqual(check['agent_update']['state'], 'current')


if __name__ == '__main__':
    unittest.main()
