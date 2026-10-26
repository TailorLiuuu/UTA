import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
import uta

class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with patch.object(uta, 'HOME', Path(self.tmp.name)), patch.object(threading.Thread, 'start'):
            self.c = uta.Controller()
        self.host = uta.validate_server(dict(name='test', target='test-host'))
        self.c.config['servers'] = [self.host]
        self.c.sessions['2-1'] = dict(busid='2-1', session='a'*32, server_id=self.host['id'],
                                    state='attached', nodes=['/dev/ttyACM0'], last_verified=123)
        self.proc = Mock(); self.proc.poll.return_value = None
        self.c.processes['2-1'] = [self.proc]

    def probe(self, now, result=None, error=None):
        with patch.object(uta.time, 'monotonic', return_value=now), patch.object(uta, 'remote_json',
                return_value=result or dict(state='attached', nodes=['/dev/ttyACM0']), side_effect=error) as remote:
            self.c.monitor_one('2-1')
            return remote.call_count

    def test_one_control_ssh_failure_preserves_data_tunnel_then_recovers(self):
        with patch.object(self.c, 'connection_lost') as lost:
            self.probe(100, error=TimeoutError('SSH status timeout'))
            s=self.c.sessions['2-1']
            self.assertEqual(s['state'], 'attached')
            self.assertEqual(s['nodes'], ['/dev/ttyACM0'])
            self.assertEqual(s['last_verified'], 123)
            self.assertEqual(s['monitor_failures'], 1)
            self.assertEqual(self.probe(103), 0)
            self.probe(110)
            lost.assert_not_called()
            self.assertEqual(s['monitor_failures'], 0)
            self.assertIsNone(s['monitor_error'])
        events=[json.loads(l)['event'] for l in self.c.events_path.read_text().splitlines()]
        self.assertEqual(events, ['status_probe_failed','status_probe_recovered'])

    def test_persistent_probe_failure_expires_only_after_bounded_grace(self):
        with patch.object(self.c, 'connection_lost') as lost:
            for t in [100,110,120]: self.probe(t,error=RuntimeError('offline'))
            lost.assert_not_called()
            self.probe(130,error=RuntimeError('offline'))
            lost.assert_called_once()

    def test_process_exit_bypasses_probe_throttle(self):
        self.probe(100)
        self.proc.poll.return_value=255
        with patch.object(self.c,'connection_lost') as lost:
            self.assertEqual(self.probe(103),0)
            lost.assert_called_once()
        events=[json.loads(l) for l in self.c.events_path.read_text().splitlines()]
        self.assertEqual(events[-1]['event'],'transport_exited')
        self.assertEqual(events[-1]['processes'][0]['returncode'],255)

    def test_confirmed_remote_disconnect_is_immediate(self):
        with patch.object(self.c,'connection_lost') as lost:
            self.probe(100,result={'state':'disconnected'})
            lost.assert_called_once()

    def test_new_session_does_not_inherit_probe_throttle_or_failure(self):
        self.probe(100,error=TimeoutError())
        self.c.sessions['2-1']=dict(self.c.sessions['2-1'],session='b'*32)
        self.assertEqual(self.probe(101),1)
        self.assertEqual(self.c.sessions['2-1']['monitor_failures'],0)

    def test_success_resets_failure_grace(self):
        self.probe(100,error=TimeoutError())
        self.probe(110)
        with patch.object(self.c,'connection_lost') as lost:
            self.probe(200,error=TimeoutError())
            lost.assert_not_called()
            self.assertEqual(self.c.sessions['2-1']['monitor_failures'],1)

    def test_enumeration_timeout_is_not_a_transient_ssh_failure(self):
        self.c.sessions['2-1'].update(state='enumerating',created=0)
        with patch.object(self.c,'connection_lost') as lost:
            self.probe(100,result={'state':'enumerating'})
            lost.assert_called_once()

    def test_diagnostic_io_failure_does_not_break_probe(self):
        self.c.events_path=Path(self.tmp.name)/'missing/events.jsonl'
        self.probe(100,error=TimeoutError())
        self.probe(110)
        self.assertEqual(self.c.sessions['2-1']['monitor_failures'],0)

if __name__ == '__main__':unittest.main()
