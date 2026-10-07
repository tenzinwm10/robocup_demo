"""Exercise stock T2 RPCs and record physics movement; no replacement policies."""
import json, sys, time, uuid
from pathlib import Path
sys.path.insert(0, '/usr/local/booster_robot/booster_robocup_sim')
import rclpy
from rclpy.node import Node
from booster_msgs.msg import RpcReqMsg, RpcRespMsg
from core.physics_transport import PhysicsTransportConfig
from transport.websocket.physics_ws import PhysicsWebSocketClient
report = {'rpc': [], 'positions': []}
latest = {}
def snapshot(s): latest.update(time=s.time, qpos=list(s.qpos[:14]))
client = PhysicsWebSocketClient(PhysicsTransportConfig(host='127.0.0.1', port=8788), owner='t2-motion-probe')
client.subscribe_physics_state(snapshot); client.connect()
rclpy.init(args=[]); node = Node('native_t2_motion_probe')
replies = {}
def receive(msg): replies[msg.uuid] = {'header': msg.header, 'body': msg.body}
sub = node.create_subscription(RpcRespMsg, '/LocoApiTopicResp', receive, 10)
pub = node.create_publisher(RpcReqMsg, '/LocoApiTopicReq', 10)
def spin(seconds):
    end = time.monotonic()+seconds
    while time.monotonic()<end: rclpy.spin_once(node, timeout_sec=.1)
def call(api, body):
    identity = str(uuid.uuid4())
    pub.publish(RpcReqMsg(uuid=identity, header=json.dumps({'api_id': api}), body=json.dumps(body)))
    end = time.monotonic()+5
    while identity not in replies and time.monotonic()<end: rclpy.spin_once(node, timeout_sec=.1)
    result = {'api': api, 'request': body, 'reply': replies.get(identity)}
    report['rpc'].append(result); print(json.dumps(result), flush=True)
    return result['reply']
try:
    spin(2)
    call(2000, {'mode': 2}); spin(3)
    mode = call(2017, {})
    call(2004, {'pitch': .1, 'yaw': 0.})
    call(2038, {'start': False, 'version': 0})
    call(2038, {'start': False, 'version': 1})
    call(2000, {'mode': 4}); spin(1); mode = call(2017, {})
    if mode and json.loads(mode['body'] or '{}').get('mode') in (2, 4):
        report['positions'].append(dict(latest))
        for _ in range(10): call(2001, {'vx': .15, 'vy': 0., 'vyaw': 0.}); spin(.2)
        call(2001, {'vx': 0., 'vy': 0., 'vyaw': 0.}); spin(1)
        report['positions'].append(dict(latest))
    else:
        report['walking_skipped'] = 'Controller did not confirm walking/soccer mode'
finally:
    call(2001, {'vx': 0., 'vy': 0., 'vyaw': 0.})
    Path('/tmp/native-t2-motion-validation.json').write_text(json.dumps(report, indent=2))
    client.disconnect(); node.destroy_node(); rclpy.shutdown()
