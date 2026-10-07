"""Record robot-specific input, command, response and physical-motion evidence."""
import json
import math
import time
from pathlib import Path
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from geometry_msgs.msg import Pose2D
from booster_msgs.msg import RpcReqMsg, RpcRespMsg
from vision_interface.msg import Detections
from game_controller_interface.msg import GameControlData

rclpy.init()
node = Node('studio_multi_monitor')
report = {'robots': {f'robot{i}': {'poses': 0, 'frames': 0, 'balls': 0,
    'moves': 0, 'responses': 0, 'max_displacement_m': 0.0} for i in range(1, 7)}, 'states': []}
subscriptions = []

def pose(name, message):
    entry = report['robots'][name]
    entry['poses'] += 1
    point = [message.x, message.y, message.theta]
    entry.setdefault('first_pose', point)
    entry['last_pose'] = point
    entry['max_displacement_m'] = max(entry['max_displacement_m'], math.dist(point[:2], entry['first_pose'][:2]))

def detections(name, message):
    entry = report['robots'][name]
    entry['frames'] += 1
    entry['balls'] += sum(d.label == 'Ball' for d in message.detected_objects)

def request(name, message):
    try:
        header, body = json.loads(message.header), json.loads(message.body or '{}')
    except ValueError:
        return
    if header.get('api_id') == 2001 and any(abs(body.get(k, 0)) > 0.01 for k in ('vx', 'vy', 'vyaw')):
        report['robots'][name]['moves'] += 1

def response(name, _):
    report['robots'][name]['responses'] += 1

def referee(message):
    if not report['states'] or report['states'][-1] != message.state:
        report['states'].append(message.state)
    report['score'] = [team.score for team in message.teams]

for name in report['robots']:
    subscriptions.extend([
        node.create_subscription(Pose2D, f'/{name}/soccer/sim/localization/robot_pose',
            lambda m, n=name: pose(n, m), qos_profile_sensor_data),
        node.create_subscription(Detections, f'/{name}/booster_vision/detection',
            lambda m, n=name: detections(n, m), 10),
        node.create_subscription(RpcReqMsg, f'/LocoApiTopic/{name}Req',
            lambda m, n=name: request(n, m), 10),
        node.create_subscription(RpcRespMsg, f'/LocoApiTopic/{name}Resp',
            lambda m, n=name: response(n, m), 10),
    ])
subscriptions.append(node.create_subscription(GameControlData, '/robocup/game_controller', referee, 10))
next_write = time.monotonic()
try:
    while rclpy.ok():
        rclpy.spin_once(node, timeout_sec=0.1)
        if time.monotonic() >= next_write:
            report['updated_at'] = time.time()
            Path('/work/studio-logs/multi-health.json').write_text(json.dumps(report, indent=2))
            next_write = time.monotonic()+5
finally:
    node.destroy_node()
    rclpy.shutdown()
