"""Launch six unchanged strategy instances with isolated Studio robot interfaces."""
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import argparse
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
LOGS = Path('/work/studio-logs')
children = []
stopping = False


def log_process(process, name):
    with (LOGS/f'{name}.log').open('w', buffering=1) as output:
        for line in process.stdout:
            # Drain verbose brain output without continuously writing through Windows mounts.
            if name.startswith('brain'):
                if not any(key in line for key in ('ERROR', 'WARN', 'FIELD_POSE', '\tx:', 'State:', 'Score:',
                                                   'Role:', 'Cost:', 'Alive:', 'RobotClient', 'Team channel', 'Duplicate')):
                    continue
            output.write(line)


def start(command, name, domain=None):
    environment = os.environ.copy()
    if domain is not None:
        environment['ROS_DOMAIN_ID'] = str(domain)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1, start_new_session=True, env=environment)
    children.append((name, process))
    threading.Thread(target=log_process, args=(process, name), daemon=True).start()
    print(f'Started {name}, pid={process.pid}', flush=True)


def stop(*_):
    global stopping
    stopping = True


def main():
    os.environ['PYTHONFAULTHANDLER'] = '1'
    parser = argparse.ArgumentParser()
    parser.add_argument('--robots', nargs='+', default=[f'robot{i}' for i in range(1, 7)])
    parser.add_argument('--domain-base', type=int, default=10)
    parser.add_argument('--perception', choices=('ideal', 'external'), default='ideal')
    parser.add_argument('--localization', choices=('ideal', 'visual'), default='ideal')
    parser.add_argument('--motion-profile', choices=('t1-safe', 't2-api'), default='t1-safe')
    parser.add_argument('--skip-prepare', action='store_true')
    parser.add_argument('--vision-executable', default='')
    args = parser.parse_args()
    allowed = {f'robot{i}' for i in range(1, 7)}
    if not set(args.robots) <= allowed or len(set(args.robots)) != len(args.robots):
        parser.error('Select unique robot1 through robot6')
    if not 1 <= args.domain_base <= 220:
        parser.error('Domain base must be 1 through 220')
    if args.perception == 'external' and not args.vision_executable:
        parser.error('External perception requires --vision-executable; no detector is substituted')
    if args.vision_executable and (args.perception != 'external' or not Path(args.vision_executable).is_file()):
        parser.error('Provide an installed vision executable with --perception external')
    LOGS.mkdir(parents=True, exist_ok=True)
    (LOGS/'run.json').write_text(json.dumps(dict(vars(args), started_at=time.time()), indent=2))
    generated = Path('/work/studio-configs')
    generated.mkdir(exist_ok=True)
    base = yaml.safe_load((ROOT/'src/brain/config/config.yaml').read_text())['brain_node']['ros__parameters']
    profile = yaml.safe_load((HERE/'brain.yaml').read_text())['brain_node']['ros__parameters']
    def merge(a, b):
        for key, value in b.items():
            if isinstance(value, dict) and isinstance(a.get(key), dict):
                merge(a[key], value)
            else:
                a[key] = copy.deepcopy(value)
        return a
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if not args.skip_prepare:
        subprocess.run([sys.executable, str(HERE/'prepare_robots.py'), '--robots', *args.robots], check=True)
    start([sys.executable, str(HERE/'communications_observer.py')], 'oversight')
    for robot in args.robots:
        index = int(robot[5:])
        domain = args.domain_base + index
        team, player = (1, index) if index <= 3 else (2, index-3)
        parameters = merge(copy.deepcopy(base), profile)
        parameters['game'].update(team_id=team, player_id=player, number_of_players=3,
                                  field_type='robo_league', initial_goalkeeper_id=1,
                                  player_role='goal_keeper' if player == 1 else 'striker')
        parameters['enable_com'] = True
        parameters['communication']['discovery_address'] = '127.255.255.255'
        parameters['strategy']['cooperation']['enable_role_switch'] = True
        parameters['simulation.studio_localization'] = args.localization == 'ideal'
        parameters['vision'].update(
            image_topic='/boostercamera/head/rgb',
            depth_image_topic='/boostercamera/head/depth',
            camera_info_topic='/boostercamera/head/rgb/camera_info',
            head_pose_topic='/head_pose_stamped')
        if args.motion_profile == 't2-api':
            parameters['strategy']['enable_auto_visual_kick'] = base['strategy']['enable_auto_visual_kick']
        config = generated/f'{robot}.yaml'
        config.write_text(json.dumps({'brain_node': {'ros__parameters': parameters}}, indent=2))
        bridge_command = [sys.executable, str(HERE/'t2_adapter.py'), '--robot', robot,
                          '--agent-domain', str(domain), '--perception', args.perception,
                          '--localization', args.localization, '--snapshot-head-pose',
                          '--health-path', str(LOGS/f'{robot}-transport.json')]
        if team == 2:
            bridge_command.insert(2, '--mirror')
        start(bridge_command, f'bridge-{robot}')
        command = ['/work/install/brain/lib/brain/brain_node', '--ros-args', '--params-file', str(config)]
        start(command, f'brain-{robot}', domain=domain)
        if args.vision_executable:
            if args.perception != 'external':
                raise ValueError('A vision executable requires --perception external to avoid duplicate detections')
            start([args.vision_executable, str(ROOT/'src/vision/config/vision.yaml'),
                   str(HERE/'vision_external.yaml'), '--ros-args', '-p', 'use_sim_time:=true',
                   '-p', 'save_data:=false', '-p', 'save_depth:=false'], f'vision-{robot}', domain=domain)
        # Let DDS discovery settle before adding another pair of participants.
        time.sleep(2)
    print('Canonical T2 routes active in isolated application domains; RPC payloads are unchanged.', flush=True)
    try:
        while not stopping:
            for name, process in children:
                if process.poll() is not None and name != 'oversight':
                    raise RuntimeError(f'{name} exited with code {process.returncode}; see its log')
            time.sleep(1)
    finally:
        for _, process in children:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic()+5
        for _, process in children:
            try:
                process.wait(timeout=max(0.1, deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)


if __name__ == '__main__':
    main()
