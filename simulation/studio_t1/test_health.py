"""Readiness regressions: synthetic streams only, no ROS, simulator or GPU."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

from check_health import health_failures, note_clock, note_received, note_rpc_response, required_streams
from oversight_metrics import RPCMetrics


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.run = {'robots': ['robot2'], 'started_at': 50.0, 'localization': 'ideal'}
        self.report = {'robot': 'robot2', 'updated_at': self.now}
        for _, topic in required_streams('robot2', 'ideal'):
            note_received(self.report, topic, now=99.0)
        note_clock(self.report, 1_000_000_000, now=98.0)
        note_clock(self.report, 2_000_000_000, now=99.0)
        self.metrics = RPCMetrics()
        self.request = SimpleNamespace(uuid='native-request', header='{"api_id":2004}', body='{"pitch":0,"yaw":0}')
        self.metrics.request(self.request, now=1.0)
        self.reply = SimpleNamespace(uuid='native-request', header='{"status":0}', body='{}')
        event = self.metrics.response(self.reply, now=1.01)
        note_rpc_response(self.report, event, now=99.0)

    def failures(self, report=None, run=None):
        return health_failures(run or self.run, {'robot2': self.report if report is None else report}, now=self.now)

    def test_live_ideal_and_visual_modes_pass(self):
        self.assertEqual(self.failures(), [])
        visual = dict(self.run, localization='visual')
        report = copy.deepcopy(self.report)
        topic = '/soccer/sim/localization/robot_pose'
        report['received'].pop(topic)
        report['last_received_at'].pop(topic)
        self.assertEqual(self.failures(report, visual), [])
        self.assertTrue(any('field pose' in item for item in self.failures(report)))

    def test_stalled_inputs_fail_despite_continually_updated_report(self):
        for label, topic in required_streams('robot2', 'ideal'):
            with self.subTest(stream=label):
                report = copy.deepcopy(self.report)
                report['last_received_at'][topic] = 60.0
                self.assertIn(f'robot2: stale {label} stream', self.failures(report))

    def test_five_second_default_and_explicit_slow_workload_override(self):
        topic = '/robot2/rgbd_camera/rgb/image_raw'
        self.report['last_received_at'][topic] = 95.0
        self.assertEqual(self.failures(), [])
        self.report['last_received_at'][topic] = 94.9
        self.assertIn('robot2: stale RGB stream', self.failures())
        self.assertEqual(health_failures(self.run, {'robot2': self.report}, now=self.now, max_age=10), [])

    def test_depth_is_required_even_with_live_rgb(self):
        self.report['received'].pop('/robot2/rgbd_camera/depth/image_raw')
        self.assertIn('robot2: missing depth', self.failures())

    def test_repeated_clock_messages_do_not_prove_progress(self):
        self.report['clock']['last_advanced_at'] = 60.0
        note_clock(self.report, 2_000_000_000, now=100.0)
        self.assertIn('robot2: simulation clock is not advancing', self.failures())

    def test_first_clock_tick_and_scene_reset_need_forward_progress(self):
        self.report.pop('clock')
        note_clock(self.report, 3_000_000_000, now=99.0)
        self.assertIn('robot2: simulation clock is not advancing', self.failures())
        note_clock(self.report, 4_000_000_000, now=99.1)
        self.assertEqual(self.failures(), [])
        note_clock(self.report, 0, now=99.2)
        self.assertIn('robot2: simulation clock is not advancing', self.failures())
        note_clock(self.report, 1, now=99.3)
        self.assertEqual(self.failures(), [])

    def test_orphan_or_native_failure_does_not_satisfy_rpc_readiness(self):
        self.report.pop('last_successful_rpc')
        orphan = SimpleNamespace(uuid='unknown', header='{"status":0}', body='{}')
        note_rpc_response(self.report, self.metrics.response(orphan, now=2.0), now=99.0)
        self.assertTrue(any('correlated native RPC' in item for item in self.failures()))
        self.metrics.request(self.request, now=3.0)
        failure = SimpleNamespace(uuid=self.request.uuid, header='{"status":501}', body='unsupported')
        note_rpc_response(self.report, self.metrics.response(failure, now=3.01), now=99.0)
        self.assertTrue(any('correlated native RPC' in item for item in self.failures()))
        self.assertEqual(failure.header, '{"status":501}')
        self.assertEqual(failure.body, 'unsupported')

    def test_unsupported_optional_api_does_not_erase_supported_rpc_success(self):
        optional = SimpleNamespace(uuid='optional', header='{"api_id":2038}', body='{"start":false}')
        self.metrics.request(optional, now=2.0)
        reply = SimpleNamespace(uuid=optional.uuid, header='{"status":501}', body='unsupported')
        note_rpc_response(self.report, self.metrics.response(reply, now=2.01), now=100.0)
        self.assertEqual(self.failures(), [])
        self.assertEqual(self.report['last_successful_rpc']['api'], 2004)

    def test_rpc_success_must_be_recent_and_from_current_run(self):
        for timestamp in (60.0, 49.0):
            self.report['last_successful_rpc']['received_at'] = timestamp
            self.assertTrue(any('correlated native RPC' in item for item in self.failures()))

    def test_old_streams_cannot_pass_with_a_new_report_file(self):
        self.report['last_received_at']['/booster_vision/detection'] = 49.0
        self.assertIn('robot2: stale perception stream', self.failures())

    def test_future_or_malformed_timestamps_fail_closed(self):
        for timestamp in (103.0, '99', None, float('nan'), float('inf')):
            with self.subTest(timestamp=timestamp):
                report = copy.deepcopy(self.report)
                report['last_received_at']['/head_pose_stamped'] = timestamp
                self.assertIn('robot2: stale head poses stream', self.failures(report))

    def test_wrong_robot_and_legacy_report_are_rejected(self):
        wrong = dict(self.report, robot='robot3')
        self.assertIn('robot2: transport report belongs to another robot', self.failures(wrong))
        legacy = copy.deepcopy(self.report)
        legacy.pop('last_received_at')
        self.assertTrue(self.failures(legacy))

    def test_every_selected_robot_must_be_ready(self):
        run = dict(self.run, robots=['robot1', 'robot2', 'robot3'])
        failures = health_failures(run, {'robot2': self.report}, now=self.now)
        self.assertTrue(any(item.startswith('robot1:') for item in failures))
        self.assertTrue(any(item.startswith('robot3:') for item in failures))
        self.assertFalse(any(item.startswith('robot2:') for item in failures))

    def test_invalid_manifest_and_bad_stream_metadata_are_reported(self):
        for run in (None, {}, dict(self.run, robots=[]), dict(self.run, robots=['robot2', 'robot2']),
                    dict(self.run, robots=[{}])):
            with self.subTest(run=run):
                self.assertTrue(health_failures(run, {}, now=self.now))
        self.report['received'] = []
        self.assertIn('robot2: invalid stream telemetry', self.failures())

    def test_cli_missing_files_and_malformed_report_return_readable_failures(self):
        script = str(Path(__file__).with_name('check_health.py'))
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, script, '--logs', directory]
            missing = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn('Run manifest unavailable', missing.stderr)
            run = dict(self.run, started_at=time.time()-10)
            (Path(directory)/'run.json').write_text(json.dumps(run))
            (Path(directory)/'robot2-transport.json').write_text('{incomplete')
            malformed = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(malformed.returncode, 0)
            self.assertIn('invalid transport report', malformed.stderr)
            self.assertNotIn('Traceback', malformed.stderr)

    def cli_fixture(self):
        now = time.time()
        run = dict(self.run, started_at=now-1)
        report = copy.deepcopy(self.report)
        report['updated_at'] = now
        report['last_received_at'] = {topic: now for topic in report['last_received_at']}
        report['clock']['last_advanced_at'] = now
        report['last_successful_rpc']['received_at'] = now
        return run, report

    def test_cli_wait_accepts_late_complete_evidence(self):
        script = str(Path(__file__).with_name('check_health.py'))
        with tempfile.TemporaryDirectory() as directory:
            logs = Path(directory)
            # Both the manifest and the adapter can appear after docker start returns.
            def publish_late():
                time.sleep(0.15)
                run, report = self.cli_fixture()
                (logs/'run.json').write_text(json.dumps(run))
                time.sleep(0.15)
                (logs/'robot2-transport.json').write_text(json.dumps(report))
            writer = threading.Thread(target=publish_late)
            writer.start()
            try:
                result = subprocess.run([sys.executable, script, '--logs', directory, '--wait-timeout', '2'],
                                        capture_output=True, text=True, timeout=5)
            finally:
                writer.join()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('PASS:', result.stdout)

    def test_cli_timeout_reports_final_unmet_gate_without_accepting_partial_evidence(self):
        script = str(Path(__file__).with_name('check_health.py'))
        with tempfile.TemporaryDirectory() as directory:
            logs = Path(directory)
            def publish_partial():
                time.sleep(0.1)
                run, report = self.cli_fixture()
                report['received'].pop('/robot2/rgbd_camera/depth/image_raw')
                (logs/'run.json').write_text(json.dumps(run))
                (logs/'robot2-transport.json').write_text(json.dumps(report))
            writer = threading.Thread(target=publish_partial)
            writer.start()
            started = time.monotonic()
            try:
                result = subprocess.run([sys.executable, script, '--logs', directory, '--wait-timeout', '0.35'],
                                        capture_output=True, text=True, timeout=5)
            finally:
                writer.join()
            self.assertGreaterEqual(time.monotonic()-started, 0.35)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Readiness timed out after 0.35s', result.stderr)
            self.assertIn('robot2: missing depth', result.stderr)
            self.assertNotIn('Run manifest unavailable', result.stderr)
            self.assertNotIn('PASS:', result.stdout)

    def test_cli_rejects_nonfinite_or_invalid_time_limits(self):
        script = str(Path(__file__).with_name('check_health.py'))
        for option, values, message in (
                ('--wait-timeout', ('-1', 'nan', 'inf', '-inf'), 'finite and nonnegative'),
                ('--max-age', ('0', '-1', 'nan', 'inf', '-inf'), 'finite and positive')):
            for value in values:
                with self.subTest(option=option, value=value):
                    result = subprocess.run([sys.executable, script, f'{option}={value}'],
                                            capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(message, result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
