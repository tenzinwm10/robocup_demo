"""Read-only, wall-time observation of Studio inputs and brain motion requests."""
import json
import math
import time
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from geometry_msgs.msg import Pose2D
from booster_msgs.msg import RpcReqMsg, RpcRespMsg
from vision_interface.msg import Detections
from game_controller_interface.msg import GameControlData
from brain.msg import Kick

rclpy.init()
node = Node('studio_demo_observer')
report = {'poses': [], 'states': [], 'detection_frames': 0,
          'ball_observations': 0, 'motion_requests': 0, 'responses': 0, 'kick_requests': 0,
          'ball_field': []}
latest_pose = None

def pose(m):
    global latest_pose
    latest_pose = m
    if not report['poses'] or math.hypot(m.x-report['poses'][-1][0], m.y-report['poses'][-1][1]) > 0.2:
        report['poses'].append([m.x, m.y, m.theta])

def state(m):
    if not report['states'] or report['states'][-1] != m.state:
        report['states'].append(m.state)

def detection(m):
    report['detection_frames'] += 1
    report['ball_observations'] += sum(d.label == 'Ball' for d in m.detected_objects)
    for d in m.detected_objects:
        if d.label == 'Ball' and latest_pose is not None and len(d.position_projection) >= 2:
            x, y = d.position_projection[:2]
            c, s = math.cos(latest_pose.theta), math.sin(latest_pose.theta)
            point = [latest_pose.x + c*x - s*y, latest_pose.y + s*x + c*y]
            if not report['ball_field'] or math.dist(point, report['ball_field'][-1]) > 0.3:
                report['ball_field'].append(point)

def request(m):
    try:
        h = json.loads(m.header)
        b = json.loads(m.body) if m.body else {}
    except (ValueError, TypeError):
        return
    if h.get('api_id') == 2001 and any(abs(b.get(k, 0)) > 0.01 for k in ('vx', 'vy', 'vyaw')):
        report['motion_requests'] += 1

def response(m):
    report['responses'] += 1

def kick(m):
    report['kick_requests'] += 1

subs = [
    node.create_subscription(Pose2D, '/soccer/sim/localization/robot_pose', pose, qos_profile_sensor_data),
    node.create_subscription(GameControlData, '/robocup/game_controller', state, 10),
    node.create_subscription(Detections, '/booster_vision/detection', detection, 10),
    node.create_subscription(RpcReqMsg, '/LocoApiTopicReq', request, 10),
    node.create_subscription(RpcRespMsg, '/LocoApiTopicResp', response, 10),
    node.create_subscription(Kick, '/kick_ball', kick, 10),
]
end = time.monotonic() + 30
while time.monotonic() < end:
    rclpy.spin_once(node, timeout_sec=0.2)
with open('/work/studio-logs/runtime-check.json', 'w') as f:
    json.dump(report, f, indent=2)
print(json.dumps(report))
node.destroy_node()
rclpy.shutdown()
