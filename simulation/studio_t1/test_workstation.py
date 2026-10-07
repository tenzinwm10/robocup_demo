"""Exercise real Bash launcher lifecycle with fake Docker/NVIDIA commands."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


FAKE_DOCKER = r'''
import json, os, sys
from pathlib import Path
state_path = Path(os.environ['FAKE_DOCKER_STATE'])
state = json.loads(state_path.read_text())
args = sys.argv[1:]
with open(os.environ['FAKE_COMMAND_LOG'], 'a') as output:
    output.write(json.dumps(['docker', *args]) + '\n')
def option(name):
    return args[args.index(name)+1] if name in args else None
if args[:2] == ['container', 'inspect']:
    sys.exit(0 if args[-1] in state else 1)
if args[0] == 'inspect':
    entry = state.get(args[-1])
    if entry is None: sys.exit(1)
    print(entry['label'] if '.Config.Labels' in option('-f')
          else str(entry.get('running', False)).lower())
elif args[0] == 'ps':
    if option('--filter') == 'label=task=robocup-studio-record':
        print('\n'.join(name for name, entry in state.items()
                        if entry['label'] == 'robocup-studio-record'))
    else:
        print('Unexpected Studio discovery', file=sys.stderr)
        sys.exit(91)
elif args[0] == 'run':
    name = option('--name')
    if name:
        state[name] = {'label': option('--label').split('=', 1)[1],
                       'running': True}
    print('fake-container-id')
elif args[0] == 'stop':
    state[args[-1]]['running'] = False
elif args[0] == 'rm':
    del state[args[-1]]
else:
    print('Unexpected fake Docker command: ' + repr(args), file=sys.stderr)
    sys.exit(92)
state_path.write_text(json.dumps(state))
'''


class WorkstationLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)/'source'
        self.here = self.root/'simulation'/'studio_t1'
        self.here.mkdir(parents=True)
        self.launcher = self.here/'workstation.sh'
        shutil.copyfile(Path(__file__).with_name('workstation.sh'), self.launcher)
        commands = Path(self.temporary.name)/'bin'
        commands.mkdir()
        for name, code in (
            ('docker', FAKE_DOCKER),
            ('nvidia-smi', "import json, os\nwith open(os.environ['FAKE_COMMAND_LOG'], 'a') as output:\n    output.write(json.dumps(['nvidia-smi']) + '\\n')\n"),
        ):
            path = commands/name
            path.write_text('#!' + sys.executable + '\n' + code)
            path.chmod(0o755)
        self.state_path = Path(self.temporary.name)/'state.json'
        self.log_path = Path(self.temporary.name)/'commands.jsonl'
        self.state_path.write_text('{}')
        self.log_path.write_text('')
        self.env = os.environ.copy()
        self.env.update(PATH=str(commands)+os.pathsep+self.env['PATH'],
                        FAKE_DOCKER_STATE=str(self.state_path),
                        FAKE_COMMAND_LOG=str(self.log_path),
                        STUDIO_CONTAINER='studio-test',
                        STUDIO_LIBRARY=str(Path(self.temporary.name)/'studio-library'))
        for key in ('IMAGE', 'GPU_ENABLED', 'GPU_CHECK_IMAGE'):
            self.env.pop(key, None)

    def seed(self, entries):
        self.state_path.write_text(json.dumps({name: {'label': label, 'running': running}
                                               for name, label, running in entries}))

    def invoke(self, *args, studio=True, success=True):
        env = self.env.copy()
        if not studio: env.pop('STUDIO_CONTAINER', None)
        result = subprocess.run(['bash', str(self.launcher), *args], env=env,
                                text=True, capture_output=True, timeout=10)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout+result.stderr)
        return result

    def calls(self, command=None):
        calls = [json.loads(line) for line in self.log_path.read_text().splitlines()]
        return calls if command is None else [call for call in calls if call[:2] == ['docker', command]]

    def assert_no_discovery(self):
        self.assertTrue(all('--filter' in call and 'label=task=robocup-studio-record' in call
                            for call in self.calls('ps')), self.calls('ps'))

    def test_foreign_name_collision_never_stops_or_removes_container(self):
        for command, name in (
            ('start', 'robocup-t2-studio'),
            ('cameras', 'robocup-studio-cameras'),
            ('oversight', 'robocup-communications-ui'),
            ('record', 'robocup-studio-record-11'),
        ):
            with self.subTest(command=command):
                self.seed([(name, 'unrelated-task', True)])
                self.log_path.write_text('')
                result = self.invoke(command, success=False)
                self.assertIn('owned by another task', result.stderr)
                self.assertFalse(self.calls('stop') or self.calls('rm') or self.calls('run'))

    def test_repeated_oversight_reuses_owned_running_ui(self):
        self.invoke('oversight', studio=False)
        self.invoke('oversight', studio=False)
        self.assertEqual(len(self.calls('run')), 1)
        self.assertFalse(self.calls('stop') or self.calls('rm'))
        self.assert_no_discovery()

    def test_stopped_oversight_is_replaced(self):
        self.seed([('robocup-communications-ui', 'robocup-communications-ui', False)])
        self.invoke('oversight', studio=False)
        self.assertEqual(len(self.calls('stop')), 1)
        self.assertEqual(len(self.calls('rm')), 1)
        self.assertEqual(len(self.calls('run')), 1)
        self.assert_no_discovery()

    def test_each_stop_command_works_after_studio_has_closed(self):
        for command, name, label, extra in (
            ('stop', 'robocup-t2-studio', 'robocup-t2-studio', []),
            ('cameras-stop', 'robocup-studio-cameras', 'robocup-studio-cameras', []),
            ('oversight-stop', 'robocup-communications-ui', 'robocup-communications-ui', []),
            ('record-stop', 'robocup-studio-record-12', 'robocup-studio-record', ['12']),
        ):
            with self.subTest(command=command):
                self.seed([(name, label, True)])
                self.log_path.write_text('')
                self.invoke(command, *extra, studio=False)
                self.assertEqual(self.calls('stop'), [['docker', 'stop', '--timeout', '30', name]])
                self.assertEqual(self.calls('rm'), [['docker', 'rm', name]])
                self.assert_no_discovery()
                self.invoke(command, *extra, studio=False)  # absent helper is safe

    def test_stop_all_flushes_recorders_before_strategy_and_helpers(self):
        names = ['robocup-studio-record-12', 'robocup-studio-record-13',
                 'robocup-t2-studio', 'robocup-studio-cameras', 'robocup-communications-ui']
        labels = ['robocup-studio-record', 'robocup-studio-record',
                  'robocup-t2-studio', 'robocup-studio-cameras', 'robocup-communications-ui']
        self.seed([*[(name, label, True) for name, label in zip(names, labels)],
                   ('unrelated-recorder', 'another-task', True)])
        self.invoke('stop-all', studio=False)
        self.assertEqual(self.calls('stop'), [['docker', 'stop', '--timeout', '30', name]
                                              for name in names])
        self.assertEqual(list(json.loads(self.state_path.read_text())), ['unrelated-recorder'])
        self.assert_no_discovery()

    def test_recorder_has_flush_signal_and_exec_and_stops_without_studio(self):
        self.invoke('record', '12')
        run = self.calls('run')[0]
        for option, value in (('--name', 'robocup-studio-record-12'),
                              ('--label', 'task=robocup-studio-record'),
                              ('--stop-signal', 'SIGINT'), ('--stop-timeout', '30'),
                              ('-e', 'ROS_DOMAIN_ID=12')):
            self.assertEqual(run[run.index(option)+1], value)
        self.assertIn('exec ros2 bag record', run[-1])
        self.invoke('record-stop', '12', studio=False)
        self.assertEqual(self.calls('stop')[0][-3:], ['--timeout', '30', 'robocup-studio-record-12'])
        self.assert_no_discovery()

    def test_duplicate_running_recorder_is_rejected_without_restart(self):
        self.seed([('robocup-studio-record-12', 'robocup-studio-record', True)])
        result = self.invoke('record', '12', success=False)
        self.assertIn('already active', result.stderr)
        self.assertFalse(self.calls('stop') or self.calls('rm') or self.calls('run'))

    def test_domain_alias_cannot_duplicate_or_miss_the_same_recorder(self):
        self.invoke('record', '012')
        self.assertIn('robocup-studio-record-12', json.loads(self.state_path.read_text()))
        self.invoke('record', '12', success=False)
        self.assertEqual(len(self.calls('run')), 1)
        self.invoke('record-stop', '012', studio=False)
        self.assertEqual(json.loads(self.state_path.read_text()), {})

    def test_cameras_mount_current_source_and_persistent_health(self):
        self.invoke('cameras', '--robots', 'robot2', '--fps', '5')
        run = self.calls('run')[0]
        self.assertIn(f'type=bind,source={self.root},target=/source,readonly', run)
        self.assertIn(f'type=bind,source={self.here}/logs/workstation/cameras,target=/work/camera-logs', run)
        self.assertEqual(run[run.index('--health')+1], '/work/camera-logs/health.json')
        self.assertEqual(run[-4:], ['--robots', 'robot2', '--fps', '5'])
        self.assertTrue((self.here/'logs/workstation/cameras/renderer.log').exists())

    def test_gpu_check_mounts_current_source_and_honors_image_override(self):
        self.env['GPU_CHECK_IMAGE'] = 'robocup-t2-studio:edited'
        self.invoke('gpu-check', studio=False)
        self.assertEqual(self.calls()[0], ['nvidia-smi'])
        run = self.calls('run')[0]
        self.assertIn(f'type=bind,source={self.root},target=/source,readonly', run)
        self.assertIn('robocup-t2-studio:edited', run)
        self.assertEqual(run[-1], '/source/simulation/studio_t1/check_gpu.sh')
        self.assert_no_discovery()

    def test_start_preserves_selected_robots_and_arguments(self):
        self.invoke('start', '--robots', 'robot1', 'robot2', 'robot3', '--localization', 'visual')
        self.assertEqual(self.calls('run')[0][-6:], ['--robots', 'robot1', 'robot2', 'robot3',
                                                   '--localization', 'visual'])


if __name__ == '__main__':
    unittest.main()
