"""CPU-only launcher lifecycle checks; no ROS, physics or GPU is started."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest import mock

import multi_launch as launcher


class LauncherLifecycleTests(unittest.TestCase):
    def setUp(self):
        launcher.children.clear()
        launcher.stopping = False
        self.temporary = tempfile.TemporaryDirectory(prefix='studio-launch-test-')
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        config = root/'src/brain/config/config.yaml'
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({'brain_node': {'ros__parameters': {
            'game': {}, 'communication': {}, 'vision': {},
            'strategy': {'cooperation': {}, 'enable_auto_visual_kick': False}}}}))
        here = root/'simulation/studio_t1'
        here.mkdir(parents=True)
        (here/'brain.yaml').write_text('{"brain_node": {"ros__parameters": {}}}')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (('ROOT', root), ('HERE', here), ('LOGS', root/'logs'),
                            ('CONFIGS', root/'configs')):
            self.stack.enter_context(mock.patch.object(launcher, name, value))
        self.stack.enter_context(mock.patch.object(launcher.signal, 'signal'))
        self.stack.enter_context(mock.patch.object(launcher.signal, 'SIGKILL',
                                                   getattr(signal, 'SIGKILL', 9), create=True))
        self.stack.enter_context(mock.patch.object(launcher.threading, 'Thread'))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.stack.enter_context(redirect_stderr(io.StringIO()))
        # killpg is Linux-only; mock it so the lifecycle checks can also run on Windows.
        self.killpg = self.stack.enter_context(mock.patch.object(launcher.os, 'killpg', create=True))
        self.sleep = self.stack.enter_context(mock.patch.object(launcher.time, 'sleep'))
        self.popen = self.stack.enter_context(mock.patch.object(launcher.subprocess, 'Popen'))
        self.prepare = self.stack.enter_context(mock.patch.object(launcher.subprocess, 'run'))
        self.addCleanup(launcher.children.clear)

    def process(self, pid, code=None):
        process = mock.Mock(pid=pid, returncode=code)
        process.poll.return_value = code
        process.wait.return_value = 0 if code is None else code
        return process

    def launch(self, *robots):
        with mock.patch.object(launcher.sys, 'argv', ['multi_launch.py', '--robots', *robots]):
            launcher.main()

    def test_late_start_failure_cleans_all_previously_started_children(self):
        # Observer and one full robot are running before a later brain fails to start.
        processes = [self.process(pid) for pid in (101, 102, 103, 104)]
        self.popen.side_effect = [*processes, OSError('brain executable unavailable')]
        with self.assertRaisesRegex(OSError, 'brain executable unavailable'):
            self.launch('robot1', 'robot2')
        self.assertEqual(self.popen.call_count, 5)
        self.prepare.assert_called_once()
        self.assertEqual(self.killpg.call_args_list,
                         [mock.call(process.pid, signal.SIGTERM) for process in processes])
        for process in processes:
            process.wait.assert_called_once()
        self.assertFalse(launcher.children)

    def test_observer_exit_is_reported_and_cleans_robot_children(self):
        observer = self.process(201, code=23)
        # Alive at the start of robot startup; failed when steady-state supervision begins.
        observer.poll.side_effect = [None, 23]
        processes = [observer, self.process(202), self.process(203)]
        self.popen.side_effect = processes
        with self.assertRaisesRegex(RuntimeError, 'oversight exited with code 23'):
            self.launch('robot1')
        self.assertEqual(self.killpg.call_args_list,
                         [mock.call(process.pid, signal.SIGTERM) for process in processes])
        for process in processes:
            process.wait.assert_called_once()
        self.assertFalse(launcher.children)

    def test_shutdown_during_startup_stops_before_next_robot(self):
        processes = [self.process(pid) for pid in (301, 302, 303)]
        self.popen.side_effect = processes
        self.sleep.side_effect = lambda _: launcher.stop()
        self.launch('robot1', 'robot2')
        self.assertEqual(self.popen.call_count, 3)
        for process in processes:
            process.wait.assert_called_once()
        self.assertFalse(launcher.children)

    def test_timeout_forces_kill_and_reaps_before_cleaning_next_child(self):
        stubborn, regular = self.process(401), self.process(402)
        stubborn.wait.side_effect = [subprocess.TimeoutExpired('child', 5), -9]
        launcher.children.extend([('brain-robot1', stubborn), ('bridge-robot1', regular)])
        launcher.cleanup_children()
        self.assertEqual(self.killpg.call_args_list, [
            mock.call(401, signal.SIGTERM), mock.call(402, signal.SIGTERM),
            mock.call(401, signal.SIGKILL)])
        self.assertEqual(stubborn.wait.call_count, 2)
        self.assertEqual(stubborn.wait.call_args_list[1], mock.call())
        regular.wait.assert_called_once()
        self.assertFalse(launcher.children)

    def test_process_exit_race_still_reaps_child(self):
        process = self.process(501)
        process.wait.side_effect = [subprocess.TimeoutExpired('child', 5), 0]
        self.killpg.side_effect = ProcessLookupError('group already exited')
        launcher.children.append(('brain-robot1', process))
        launcher.cleanup_children()
        self.assertEqual(process.wait.call_count, 2)
        self.assertEqual(self.killpg.call_count, 2)
        self.assertFalse(launcher.children)

    def test_unsupported_motion_profile_is_rejected_before_startup(self):
        with mock.patch.object(launcher.sys, 'argv', ['multi_launch.py', '--motion-profile', 't2-api']):
            with self.assertRaises(SystemExit) as failure:
                launcher.main()
        self.assertEqual(failure.exception.code, 2)
        self.prepare.assert_not_called()
        self.popen.assert_not_called()
        self.assertFalse(launcher.CONFIGS.exists())
        self.assertFalse(launcher.LOGS.exists())

    def test_generated_config_disables_auto_visual_kick_from_base_and_profile(self):
        base_path = launcher.ROOT/'src/brain/config/config.yaml'
        base = json.loads(base_path.read_text())
        base['brain_node']['ros__parameters']['strategy']['enable_auto_visual_kick'] = True
        base_path.write_text(json.dumps(base))
        (launcher.HERE/'brain.yaml').write_text(json.dumps({'brain_node': {'ros__parameters': {
            'strategy': {'enable_auto_visual_kick': True}}}}))
        self.popen.side_effect = [self.process(601), OSError('fixture stops after config generation')]
        with self.assertRaisesRegex(OSError, 'fixture stops after config generation'):
            self.launch('robot1')
        generated = json.loads((launcher.CONFIGS/'robot1.yaml').read_text())
        self.assertIs(generated['brain_node']['ros__parameters']['strategy']['enable_auto_visual_kick'], False)
        self.assertFalse(launcher.children)


if __name__ == '__main__':
    unittest.main()
