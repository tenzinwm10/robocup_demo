"""Put only the six named local Studio robots into the existing WALK mode."""
import json
import time
import uuid
import argparse
import rclpy
from rclpy.node import Node
from booster_msgs.msg import RpcReqMsg, RpcRespMsg

parser = argparse.ArgumentParser()
parser.add_argument('--robots', nargs='+', default=[f'robot{i}' for i in range(1, 7)])
parser.add_argument('--timeout', type=float, default=45)
args = parser.parse_args()
rclpy.init(args=[])
node = Node('studio_walk_setup')
pubs = [node.create_publisher(RpcReqMsg, f'/LocoApiTopic/{name}Req', 10) for name in args.robots]
pending, modes, statuses, subscriptions = {}, {}, {}, []
def response(message):
    robot = pending.get(message.uuid)
    if robot is None: return
    try:
        status = json.loads(message.header or '{}').get('status', 0)
        body = json.loads(message.body or '{}')
    except ValueError: return
    statuses[robot] = status
    if status == 0 and 'mode' in body: modes[robot] = body['mode']
for name in args.robots:
    subscriptions.append(node.create_subscription(RpcRespMsg, f'/LocoApiTopic/{name}Resp', response, 10))
end = time.monotonic()+args.timeout
while time.monotonic() < end and any(pub.get_subscription_count() == 0 for pub in pubs):
    rclpy.spin_once(node, timeout_sec=0.1)
missing = [name for name, pub in zip(args.robots, pubs) if pub.get_subscription_count() == 0]
if missing:
    raise RuntimeError(f'Studio motion interfaces were not ready: {missing}')
for _ in range(3):
    for publisher in pubs:
        request = RpcReqMsg()
        request.uuid = str(uuid.uuid4())
        request.header = json.dumps({'api_id': 2000})
        request.body = json.dumps({'mode': 2})
        publisher.publish(request)
    end = time.monotonic()+1
    while time.monotonic() < end:
        rclpy.spin_once(node, timeout_sec=0.1)
end, next_query = time.monotonic()+args.timeout, 0
while time.monotonic() < end and any(modes.get(name) not in (2, 4) for name in args.robots):
    if time.monotonic() >= next_query:
        for name, publisher in zip(args.robots, pubs):
            request = RpcReqMsg(uuid=str(uuid.uuid4()), header=json.dumps({'api_id': 2017}), body='{}')
            pending[request.uuid] = name; publisher.publish(request)
        next_query = time.monotonic()+1
    rclpy.spin_once(node, timeout_sec=.1)
missing = [name for name in args.robots if modes.get(name) not in (2, 4)]
if missing:
    raise RuntimeError(f'Stock T1 controllers did not confirm WALK/SOCCER: {missing}; modes={modes}, statuses={statuses}. '
                       'Set those robots to WALK using Studio controls, then rerun. No mode response was fabricated.')
print(f'WALK/SOCCER confirmed for {args.robots}; controllers remain the stock T1 policies.')
node.destroy_node()
rclpy.shutdown()
