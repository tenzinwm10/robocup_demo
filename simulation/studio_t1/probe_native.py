"""Read native Studio sensors and verify correlated RPC replies through T2 routes."""
import argparse
import json
import time
import uuid
from pathlib import Path
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from geometry_msgs.msg import PoseStamped
from vision_interface.msg import Detections, LineSegments
from t2_adapter import T2Adapter
from t2_routes import SENSOR_ROUTES, RpcReqMsg, RpcRespMsg

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--robot', default='')
    parser.add_argument('--domain', type=int, default=81)
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--output', default='/work/studio-logs/native-probe.json')
    parser.add_argument('--snapshot-head-pose', action='store_true')
    args = parser.parse_args()
    adapter = T2Adapter(args.robot, 0, args.domain, snapshot_head_pose=args.snapshot_head_pose)
    node = Node('native_probe', context=adapter.agent_context)
    adapter.agent_executor.add_node(node)
    report = {'counts': {}, 'rpc': {}, 'native_execution': 'T1 controller; not physical T2 motion validation'}
    entities, pending = [], {}
    def sensor(topic):
        return lambda _: report['counts'].__setitem__(topic, report['counts'].get(topic, 0)+1)
    for _, topic, typ in SENSOR_ROUTES:
        entities.append(node.create_subscription(typ, topic, sensor(topic), qos_profile_sensor_data))
    for typ, topic in ((PoseStamped, '/head_pose_stamped'), (Detections, '/booster_vision/detection'),
                      (LineSegments, '/booster_vision/line_segments')):
        entities.append(node.create_subscription(typ, topic, sensor(topic), qos_profile_sensor_data))
    def response(message):
        if message.uuid in pending:
            report['rpc'][str(pending[message.uuid])] = {'uuid': message.uuid,
                'header': message.header, 'body': message.body}
    entities.append(node.create_subscription(RpcRespMsg, '/LocoApiTopicResp', response, 10))
    publisher = node.create_publisher(RpcReqMsg, '/LocoApiTopicReq', 10)
    # GetMode, zero velocity, head pose and stop-VisualKick. No synthetic success.
    checks = [(2000, {'mode': 2}), (2017, {}), (2001, {'vx': 0., 'vy': 0., 'vyaw': 0.}),
              (2004, {'pitch': .1, 'yaw': 0.}), (2038, {'start': False, 'version': 1})]
    start = time.monotonic(); next_request = start+2; index = 0
    try:
        while time.monotonic()-start < args.seconds:
            adapter.spin_once()
            if time.monotonic() >= next_request:
                api_id, body = checks[index % len(checks)]
                request = RpcReqMsg(uuid=str(uuid.uuid4()), header=json.dumps({'api_id': api_id}), body=json.dumps(body))
                pending[request.uuid] = api_id; publisher.publish(request)
                index += 1; next_request = time.monotonic()+3
        report['transport'] = adapter.health
        report['result'] = 'PASS' if '2017' in report['rpc'] else 'FAIL_NO_NATIVE_MODE_REPLY'
        output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2)); print(json.dumps(report, indent=2))
        if report['result'] != 'PASS': raise RuntimeError(report['result'])
    finally:
        node.destroy_node(); adapter.close()

if __name__ == '__main__': main()
