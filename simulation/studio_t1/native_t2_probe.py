"""Inspect a single stock T2 Studio runtime without substituting controllers."""
import json
import sys
import time
import uuid
from pathlib import Path
sys.path.insert(0, '/usr/local/booster_robot/booster_robocup_sim')
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from geometry_msgs.msg import PoseStamped
from booster_msgs.msg import RpcReqMsg, RpcRespMsg
from core.scene_source import load_scene
from core.physics_transport import PhysicsTransportConfig
from transport.websocket.physics_ws import PhysicsWebSocketClient

loaded = load_scene('/opt/booster-studio/library/scenes/football_pitch_T2.bscene')
report = {'scene': 'football_pitch_T2', 'nq': loaded.model.nq,
          'nv': loaded.model.nv, 'actuators': loaded.model.nu,
          'cameras': loaded.model.ncam, 'snapshots': 0, 'pose_messages': 0,
          'rpc_replies': [], 'requests': []}
def state(snapshot):
    report['snapshots'] += 1
    report.setdefault('first_sim_time', snapshot.time)
    report['last_sim_time'] = snapshot.time
    report['snapshot_qpos_count'] = len(snapshot.qpos)
client = PhysicsWebSocketClient(PhysicsTransportConfig(host='127.0.0.1', port=8788), owner='native-t2-probe')
client.subscribe_physics_state(state)
client.connect()
rclpy.init(args=[])
node = Node('native_t2_validation')
def pose(_): report['pose_messages'] += 1
sub = node.create_subscription(PoseStamped, '/head_pose_stamped', pose, qos_profile_sensor_data)
pending = set()
def response(msg):
    if msg.uuid in pending:
        report['rpc_replies'].append({'uuid': msg.uuid, 'header': msg.header, 'body': msg.body})
rpc_sub = node.create_subscription(RpcRespMsg, '/LocoApiTopicResp', response, 10)
pub = node.create_publisher(RpcReqMsg, '/LocoApiTopicReq', 10)
start = time.monotonic()
sent = False
while time.monotonic()-start < 15:
    rclpy.spin_once(node, timeout_sec=.1)
    if not sent and time.monotonic()-start > 3:
        report['rpc_subscriber_count'] = pub.get_subscription_count()
        for api_id, body in ((2017, {}), (2001, {'vx': 0., 'vy': 0., 'vyaw': 0.}),
                             (2038, {'start': False, 'version': 1})):
            identity = str(uuid.uuid4()); pending.add(identity)
            pub.publish(RpcReqMsg(uuid=identity, header=json.dumps({'api_id': api_id}), body=json.dumps(body)))
            report['requests'].append({'api_id': api_id, 'uuid': identity})
        sent = True
client.disconnect()
node.destroy_node(); rclpy.shutdown()
Path('/tmp/native-t2-validation.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
