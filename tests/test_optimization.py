import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
import uta
import uta_ch341 as tune
import uta_optimization as optimization


class SetupTests(unittest.TestCase):
    def test_enabled_is_idempotent_even_with_active_ports(self):
        info = dict(state='enabled', ports=['ttyUSB0'])
        with patch.object(tune, 'root_only'), patch.object(tune, 'require_supported'), \
             patch.object(tune, 'status', return_value=info), patch.object(tune, 'idle') as idle, \
             patch.object(tune, 'build') as build:
            self.assertEqual(tune.setup(True), info)
            idle.assert_not_called(); build.assert_not_called()

    def test_unknown_driver_is_not_unloaded(self):
        with patch.object(tune, 'root_only'), patch.object(tune, 'require_supported'), \
             patch.object(tune, 'status', return_value=dict(state='external_driver', legacy_driver=False)), \
             patch.object(tune, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'Unrecognized'): tune.setup(True)
            run.assert_not_called()

    def test_busy_blocks_dependency_install_and_build(self):
        with patch.object(tune, 'root_only'), patch.object(tune, 'require_supported'), \
             patch.object(tune, 'status', return_value=dict(state='disabled', ports=['ttyUSB0'])), \
             patch.object(tune, 'run') as run, patch.object(tune, 'build') as build:
            with self.assertRaisesRegex(RuntimeError, 'Disconnect'): tune.setup(True)
            run.assert_not_called(); build.assert_not_called()

    def test_dependencies_require_explicit_opt_in(self):
        with patch.object(tune, 'root_only'), patch.object(tune, 'require_supported', return_value='5.15.0-1'), \
             patch.object(tune, 'status', return_value=dict(state='disabled')), patch.object(tune, 'idle'), \
             patch.object(tune, 'missing_dependencies', return_value=['build-essential']), patch.object(tune, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'missing build dependencies'): tune.setup()
            run.assert_not_called()

    def test_build_failure_keeps_old_driver_loaded(self):
        with patch.object(tune, 'root_only'), patch.object(tune, 'require_supported'), \
             patch.object(tune, 'status', return_value=dict(state='external_driver', legacy_driver=True)), \
             patch.object(tune, 'idle'), patch.object(tune, 'missing_dependencies', return_value=[]), \
             patch.object(tune, 'build', side_effect=RuntimeError('compile failed')), patch.object(tune, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'compile failed'): tune.setup(True)
            run.assert_not_called()

    def test_hotplug_during_build_blocks_switch(self):
        with patch.object(tune, 'root_only'), patch.object(tune, 'require_supported'), \
             patch.object(tune, 'status', return_value=dict(state='disabled')), \
             patch.object(tune, 'idle', side_effect=[None, RuntimeError('Disconnect devices')]), \
             patch.object(tune, 'missing_dependencies', return_value=[]), patch.object(tune, 'build'), \
             patch.object(tune, 'enable') as enable:
            with self.assertRaisesRegex(RuntimeError, 'Disconnect'): tune.setup(True)
            enable.assert_not_called()

    def test_known_legacy_driver_upgrades_only_after_successful_build(self):
        events = []
        with patch.object(tune, 'root_only'), patch.object(tune, 'require_supported'), \
             patch.object(tune, 'status', return_value=dict(state='external_driver', legacy_driver=True)), \
             patch.object(tune, 'idle', side_effect=lambda **kw: events.append('idle')), \
             patch.object(tune, 'missing_dependencies', return_value=[]), \
             patch.object(tune, 'build', side_effect=lambda: events.append('build')), \
             patch.object(tune, 'run', side_effect=lambda argv: events.append(argv)), \
             patch.object(tune, 'enable', return_value=dict(state='enabled')):
            self.assertEqual(tune.setup()['state'], 'enabled')
        self.assertEqual(events, ['idle', 'build', 'idle', ['/sbin/rmmod', tune.MODULE], ['/sbin/modprobe', 'ch341']])

    def test_dependency_install_is_bounded_and_does_not_upgrade_system(self):
        with patch.object(tune, 'root_only'), patch.object(tune, 'require_supported', return_value='5.15.0-1'), \
             patch.object(tune, 'status', return_value=dict(state='disabled')), patch.object(tune, 'idle'), \
             patch.object(tune, 'missing_dependencies', side_effect=[['build-essential'], []]), \
             patch.object(Path, 'is_file', return_value=True), patch.object(tune, 'build'), \
             patch.object(tune, 'enable'), patch.object(tune, 'run') as run:
            tune.setup(True)
        self.assertEqual(len(run.call_args_list), 2)
        self.assertEqual(run.call_args_list[0].args[0][-1], 'update')
        self.assertEqual(run.call_args_list[1].args[0][-4:], ['install', '-y', '--no-install-recommends', 'build-essential'])
        self.assertTrue(all(call.kwargs['timeout'] <= 600 for call in run.call_args_list))


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="uta optimization ' ")
        self.home = Path(self.tmp.name)
        self.host = uta.validate_server(dict(name='lab', target='lab', use_ssh_config=True))
        self.source = self.home / 'agent.py'; self.source.write_text('# candidate')
    def tearDown(self): self.tmp.cleanup()

    def controller(self):
        with patch.object(uta, 'HOME', self.home), patch.object(threading.Thread, 'start'):
            result = uta.Controller()
        result.config['servers'] = [self.host]
        return result

    def test_root_authorization_and_staged_hash_are_not_bypassed(self):
        for interactive in (False, True):
            upload, configure, cleanup = optimization.commands(self.host, uta.ssh_args(self.host), self.source, uta.REMOTE, interactive)
            self.assertEqual('BatchMode=yes' in configure, not interactive)
            self.assertIn('StrictHostKeyChecking=yes', configure)
            self.assertIn('ForwardAgent=no', configure)
            self.assertIn('sudo ' + ('' if interactive else '-n ') + '/bin/sh -c', configure[-1])
            self.assertIn('sha256sum -c -', configure[-1])
            self.assertIn('test "$(id -u)" = 0', configure[-1])
            self.assertIn('tune-ch341 setup --install-dependencies', configure[-1])
            for cmd in (upload, configure, cleanup):
                subprocess.run(['/bin/sh', '-n', '-c', cmd[-1]], check=True)

    def test_no_setup_when_session_is_active_or_cleanup_pending(self):
        c = self.controller()
        for state, pending in [('attached', False), ('waiting_reconnect', False), ('local', True)]:
            c.sessions = {'1-1': dict(busid='1-1', server_id=self.host['id'], state=state, remote_cleanup_pending=pending)}
            with patch.object(c, 'background') as background:
                with self.assertRaisesRegex(RuntimeError, '先断开'): c.handle(dict(action='server_optimize', id=self.host['id']))
                background.assert_not_called()

    def test_new_attach_is_blocked_during_optimization(self):
        c = self.controller(); c.devices = [dict(busid='1-1')]
        c.optimizations[self.host['id']] = dict(state='running')
        with patch.object(c, 'background') as background:
            with self.assertRaisesRegex(RuntimeError, '正在配置'): c.handle(dict(action='connect', busid='1-1', server_id=self.host['id']))
            background.assert_not_called()

    def test_zero_exit_requires_fresh_readback_of_both_buffers(self):
        c = self.controller()
        with patch.object(uta, 'HOME', self.home), patch.object(uta, 'agent_source', return_value=self.source), \
             patch.object(optimization, 'execute'), patch.object(uta, 'remote_json', return_value=dict(state='enabled', bulk_out_bytes=4096)), \
             patch.object(c, 'check_server') as check:
            c.optimize_server(self.host)
            self.assertEqual(c.optimizations[self.host['id']]['state'], 'error')
            check.assert_not_called()

    def test_sudo_denial_offers_terminal_authorization(self):
        c = self.controller()
        with patch.object(uta, 'HOME', self.home), patch.object(uta, 'agent_source', return_value=self.source), \
             patch.object(optimization, 'execute', side_effect=optimization.AuthorizationRequired('auth needed')):
            c.optimize_server(self.host)
        self.assertEqual(c.optimizations[self.host['id']]['state'], 'authorization_required')

    def test_only_current_transmit_revision_can_confirm_success(self):
        for revision, out_bytes, expected in (('uta-ch341-rx1', 4096, 'error'),
                                               ('uta-ch341-rx1', 8192, 'error'),
                                               ('uta-ch341-tx2', 8192, 'enabled')):
            c = self.controller()
            info = dict(state='enabled', bulk_in_bytes=4096, bulk_out_bytes=out_bytes, revision=revision)
            with patch.object(uta, 'HOME', self.home), patch.object(uta, 'agent_source', return_value=self.source), \
                 patch.object(optimization, 'execute'), patch.object(uta, 'remote_json', return_value=info), \
                 patch.object(c, 'check_server'):
                c.optimize_server(self.host)
            self.assertEqual(c.optimizations[self.host['id']]['state'], expected)

    def test_read_only_tuning_remains_visible_without_passwordless_sudo(self):
        c = self.controller()
        tuning = dict(state='enabled', bulk_in_bytes=4096, bulk_out_bytes=4096)
        with patch.object(uta, 'remote_command', return_value='installed'), \
             patch.object(uta, 'remote_json', side_effect=[RuntimeError('sudo: password required'), tuning]) as remote:
            c.check_server(self.host)
        check = c.checks[self.host['id']]
        self.assertEqual(check['state'], 'error')  # Attach permissions remain unconfirmed.
        self.assertEqual(check['details']['ch341_tuning'], tuning)
        self.assertEqual(remote.call_args.kwargs, dict(privileged=False))

    def test_timeout_reports_uncertain_status_without_dumping_command(self):
        with patch.object(subprocess, 'run', side_effect=subprocess.TimeoutExpired(['ssh', 'private-command'], 30)):
            with self.assertRaisesRegex(RuntimeError, '操作可能尚未结束') as error:
                optimization.execute(self.host, uta.ssh_args(self.host), self.source, uta.REMOTE, self.home/'timeout')
        self.assertNotIn('private-command', str(error.exception))

    def test_terminal_script_needs_no_pasted_commands_and_cleans_staging(self):
        directory = self.home / 'job'
        def run(argv, **kw):
            if argv[0] == '/usr/bin/open':
                script = Path(argv[-1]); contents = script.read_text()
                self.assertEqual(script.stat().st_mode & 0o777, 0o700)
                subprocess.run_original(['/bin/bash', '-n', str(script)], check=True)
                self.assertIn('set -euo pipefail', contents)
                self.assertIn('sudo /bin/sh -c', contents)
                (directory/'result').write_text('0\n')
            return subprocess.CompletedProcess(argv, 0)
        original = subprocess.run
        with patch.object(subprocess, 'run_original', original, create=True), patch.object(subprocess, 'run', side_effect=run):
            optimization.execute(self.host, uta.ssh_args(self.host), self.source, uta.REMOTE, directory, True)
        self.assertFalse((directory/'authorize.command').exists())
        self.assertFalse((directory/'uta-server.py').exists())


if __name__ == '__main__': unittest.main()
