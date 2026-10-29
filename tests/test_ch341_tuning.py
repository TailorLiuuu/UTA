import base64
import gzip
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import uta_ch341 as tune


class TuningTests(unittest.TestCase):
    def test_bundled_agent_has_no_adjacent_module_dependency(self):
        agent=tune.bundle_agent((ROOT/'python/uta_server.py').read_text())
        namespace={'__name__':'bundle_test'}
        exec(compile(agent,'<test bundled agent>','exec'),namespace)
        self.assertEqual(namespace['uta_ch341'].source(),tune.source())
        self.assertIn('ch341_tuning',namespace['doctor']())

    def test_kernel_scope_is_bounded(self):
        for kernel in ('5.15.0-173-generic', '5.15.0-198-generic'):
            self.assertTrue(tune.supported(kernel))
        for kernel in ('6.8.0-1', '5.4.0-generic', '../../x', '5.15.0-x/../../x', '5.15.0-x\n'):
            self.assertFalse(tune.supported(kernel))

    def test_privilege_is_not_inherited_from_delegated_sudo(self):
        with patch.object(tune.os, 'geteuid', return_value=0), patch.dict(os.environ, SUDO_UID='1000'):
            with self.assertRaises(PermissionError): tune.root_only()
        with patch.object(tune.os, 'geteuid', return_value=1000):
            with self.assertRaises(PermissionError): tune.root_only()

    def test_pinned_source_and_packaged_source_are_identical(self):
        expected = tune.source()
        raw = (ROOT / 'vendor/linux-ch341/ch341.c').read_bytes()
        blob = base64.b64encode(gzip.compress(raw)).decode()
        with patch.object(tune, 'SOURCE_GZIP_B64', blob):
            self.assertEqual(tune.source(), expected)
        self.assertIn('.bulk_out_size     = 4096,', expected)
        self.assertIn('MODULE_VERSION("uta-preview7")', expected)
        with patch.object(tune, 'SOURCE_GZIP_B64', base64.b64encode(gzip.compress(b'wrong source')).decode()):
            with self.assertRaisesRegex(RuntimeError, 'checksum'): tune.source()

    def test_status_distinguishes_managed_and_external_driver(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); module=root/tune.MODULE; module.mkdir(); (module/'parameters').mkdir()
            with patch.object(tune,'SYS_MODULE',root), patch.object(tune,'SERIAL_DRIVER',root/'ports'):
                self.assertEqual(tune.status()['state'],'external_driver')
                (module/'version').write_text(tune.REVISION)
                (module/'parameters/uta_bulk_out_bytes').write_text('4096')
                self.assertEqual(tune.status()['state'],'enabled')
                (module/'parameters/uta_bulk_out_bytes').write_text('32')
                self.assertEqual(tune.status()['state'],'external_driver')

    def test_active_ports_and_lockdown_block_enable(self):
        with patch.object(tune,'status',return_value={'ports':['ttyUSB0']}):
            with self.assertRaisesRegex(RuntimeError,'Disconnect every'): tune.idle(True)
        with patch.object(tune,'status',return_value={'ports':[]}), patch.object(tune,'read',return_value='none [integrity]'):
            with self.assertRaisesRegex(RuntimeError,'lockdown'): tune.idle(True)
            tune.idle(False)  # Rollback to signed system driver is still allowed.

    def test_unrecognized_module_is_never_unloaded(self):
        with patch.object(tune,'root_only'), patch.object(tune,'status',return_value={'state':'external_driver'}), patch.object(tune,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'unrecognized'): tune.disable()
            run.assert_not_called()

    def test_supported_gate_precedes_build_and_load(self):
        with patch.object(tune,'root_only'),patch.object(tune.platform,'release',return_value='6.8.0-1'),patch.object(tune,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'5.15'): tune.build()
            with self.assertRaisesRegex(RuntimeError,'5.15'): tune.enable()
            run.assert_not_called()

    def test_busy_guard_precedes_module_changes(self):
        info={'state':'disabled','ports':['ttyUSB0']}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'5.15.0-1-test').mkdir()
            with patch.object(tune,'root_only'),patch.object(tune,'require_supported',return_value='5.15.0-1'),patch.object(tune,'status',return_value=info),patch.object(tune,'store',return_value=root),patch.object(tune,'verify',return_value=root/'test.ko'),patch.object(tune,'run') as run:
                with self.assertRaisesRegex(RuntimeError,'Disconnect'): tune.enable()
                run.assert_not_called()

    def test_failed_load_restores_system_driver(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'5.15.0-1-test').mkdir(); modules=root/'modules'; modules.mkdir(); (modules/'ch341').mkdir()
            def command(argv, **kw):
                if argv[0]=='/sbin/insmod': raise RuntimeError('load rejected')
                return ''
            with patch.object(tune,'root_only'),patch.object(tune,'require_supported',return_value='5.15.0-1'),patch.object(tune,'status',return_value={'state':'disabled'}),patch.object(tune,'store',return_value=root),patch.object(tune,'verify',return_value=root/'test.ko'),patch.object(tune,'idle'),patch.object(tune,'SYS_MODULE',modules),patch.object(tune,'run',side_effect=command) as run:
                with self.assertRaisesRegex(RuntimeError,'system ch341 restored'): tune.enable()
                self.assertEqual(run.call_args.args[0],['/sbin/modprobe','ch341'])
                self.assertEqual(run.call_args_list[0].args[0],['/sbin/rmmod','ch341'])

    def test_manifest_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(tune,'trusted'):
            root=Path(tmp); (root/'ch341_uta.ko').write_bytes(b'module')
            (root/'manifest.json').write_text(json.dumps({'kernel':'5.15.0-1','revision':tune.REVISION,'sha256':'wrong'}))
            with self.assertRaisesRegex(RuntimeError,'checksum'):tune.verify(root,'5.15.0-1')

    def test_symlink_is_not_a_trusted_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'original').write_text('x');(root/'link').symlink_to(root/'original')
            with self.assertRaisesRegex(RuntimeError,'Unsafe'): tune.trusted(root/'link')

    def test_enable_is_idempotent(self):
        info={'state':'enabled'}
        with patch.object(tune,'root_only'),patch.object(tune,'require_supported'),patch.object(tune,'status',return_value=info),patch.object(tune,'run') as run:
            self.assertEqual(tune.enable(),info);run.assert_not_called()

if __name__ == '__main__': unittest.main()
