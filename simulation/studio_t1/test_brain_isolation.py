"""Run two real T2 brain executables against isolated synthetic sensor/RPC peers.

This verifies launch, phase handling, localization delivery and the original team
UDP protocol. It does not pretend that a synthetic RPC peer tests robot dynamics.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import yaml
from rclpy.context import Context
from rclpy.node import Node
from rclpy.executors import SingleThreadedExecutor
from geometry_msgs.msg import Pose2D, PoseStamped
from booster_interface.msg import Odometer, LowState
from booster_msgs.msg import RpcReqMsg, RpcRespMsg
from rosgraph_msgs.msg import Clock
from game_controller_interface.msg import GameControlData
from vision_interface.msg import Detections, DetectedObject
from bridge import parse_referee

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

def merge(a, b):
    for key, value in b.items():
        if isinstance(value, dict) and isinstance(a.get(key), dict): merge(a[key], value)
        else: a[key] = copy.deepcopy(value)
    return a

def main():
    base = yaml.safe_load((ROOT/'src/brain/config/config.yaml').read_text())['brain_node']['ros__parameters']
    profile = yaml.safe_load((HERE/'brain.yaml').read_text())['brain_node']['ros__parameters']
    processes, endpoints, logs, requests = [], [], [], {2: [], 3: []}
    with tempfile.TemporaryDirectory(prefix='studio-brain-test-') as temporary:
        try:
            for player in (2, 3):
                domain = 150+player
                parameters = merge(copy.deepcopy(base), profile)
                # Separate test team to avoid any live demo's UDP state ports.
                parameters['game'].update(team_id=7, player_id=player, number_of_players=3,
                    field_type='robo_league', initial_goalkeeper_id=1, player_role='striker')
                parameters['enable_com'] = True
                parameters['communication']['discovery_address'] = '127.255.255.255'
                parameters['strategy']['cooperation']['enable_role_switch'] = True
                parameters['vision']['head_pose_topic'] = '/head_pose_stamped'
                config = Path(temporary)/f'player{player}.yaml'
                config.write_text(json.dumps({'brain_node': {'ros__parameters': parameters}}))
                log_path = Path(temporary)/f'brain{player}.log'
                output = log_path.open('w'); logs.append((log_path, output))
                environment = dict(os.environ, ROS_DOMAIN_ID=str(domain))
                process = subprocess.Popen(['/work/install/brain/lib/brain/brain_node', '--ros-args',
                    '--params-file', str(config)], stdout=output, stderr=subprocess.STDOUT, env=environment)
                processes.append(process)
                context = Context(); context.init(args=[], domain_id=domain)
                node = Node(f'brain_test_peer_{player}', context=context)
                executor = SingleThreadedExecutor(context=context); executor.add_node(node)
                publishers = {name: node.create_publisher(typ, name, 10) for name, typ in (
                    ('/clock', Clock), ('/soccer/sim/localization/robot_pose', Pose2D),
                    ('/robocup/game_controller', GameControlData), ('/odometer_state', Odometer),
                    ('/low_state', LowState), ('/head_pose_stamped', PoseStamped),
                    ('/booster_vision/detection', Detections), ('/LocoApiTopicResp', RpcRespMsg))}
                def rpc(message, p=player, pubs=publishers):
                    requests[p].append(message)
                    api_id = json.loads(message.header)['api_id']
                    reply = RpcRespMsg(uuid=message.uuid, header='{"status":0}',
                                       body='{"mode":2}' if api_id == 2017 else '{}')
                    pubs['/LocoApiTopicResp'].publish(reply)
                subscription = node.create_subscription(RpcReqMsg, '/LocoApiTopicReq', rpc, 10)
                endpoints.append((player, context, node, executor, publishers, subscription))
            start = time.monotonic()
            clock_ns = 1_000_000_000
            while time.monotonic()-start < 18:
                elapsed = time.monotonic()-start
                state = 1 if elapsed < 7 else (2 if elapsed < 10 else 3)
                for player, context, node, executor, publishers, subscription in endpoints:
                    clock = Clock(); clock.clock.sec, clock.clock.nanosec = divmod(clock_ns, 1_000_000_000)
                    publishers['/clock'].publish(clock)
                    pose = Pose2D(x=-4., y=float(player-2), theta=0.)
                    publishers['/soccer/sim/localization/robot_pose'].publish(pose)
                    head = PoseStamped(); head.header.stamp = clock.clock
                    head.pose.position.z = 1.; head.pose.orientation.w = 1.
                    publishers['/head_pose_stamped'].publish(head)
                    low = LowState(); low.imu_state.acc[2] = 9.81
                    publishers['/low_state'].publish(low)
                    publishers['/odometer_state'].publish(Odometer())
                    referee = parse_referee(bytes.fromhex((HERE/'game-controller-v19.hex').read_text()))
                    referee.teams[0].team_number = 7; referee.teams[1].team_number = 8
                    referee.state = state; referee.kicking_team = 7
                    publishers['/robocup/game_controller'].publish(referee)
                    detection = Detections(); detection.header.stamp = clock.clock
                    ball = DetectedObject(); ball.label = 'Ball'; ball.confidence = 95.
                    ball.position = [2., 0., 0.]; ball.position_projection = [2., 0., 0.]
                    ball.position_confidence = 100; ball.target_uv = [160., 120.]
                    detection.detected_objects = [ball]
                    publishers['/booster_vision/detection'].publish(detection)
                    executor.spin_once(timeout_sec=.002)
                if any(process.poll() is not None for process in processes):
                    raise RuntimeError('A real brain exited unexpectedly')
                clock_ns += 33_333_333
                time.sleep(.03)
            for player, _, node, _, _, _ in endpoints:
                topics = dict(node.get_topic_names_and_types())
                assert '/LocoApiTopicReq' in topics and '/LocoApiTopicResp' in topics
                assert not any(topic.startswith('/robot') for topic in topics)
                api_ids = {json.loads(request.header)['api_id'] for request in requests[player]}
                assert 2017 in api_ids, f'Player {player}: missing native mode query'
                assert 2001 in api_ids, f'Player {player}: missing movement intent'
            for log_path, output in logs:
                output.flush(); text = log_path.read_text()
                for state in ('READY', 'SET', 'PLAY'):
                    assert f'State: {state}' in text, f'{log_path.name}: missing {state}'
                assert 'Calibrated: YES' in text, 'Ideal pose was not accepted'
                assert 'Alive: 1' in text, f'{log_path.name}: no live UDP teammate'
            print(json.dumps({'result': 'PASS', 'real_brains': 2, 'phases': ['READY', 'SET', 'PLAY'],
                'canonical_routes': True, 'team_udp': 'live teammate observed on both brains',
                'rpc_requests': {p: len(messages) for p, messages in requests.items()}}))
        finally:
            for process in processes:
                process.terminate()
                try: process.wait(timeout=4)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
            for _, context, node, executor, _, _ in endpoints:
                executor.shutdown(); node.destroy_node(); context.shutdown()
            for _, output in logs: output.close()

if __name__ == '__main__': main()
