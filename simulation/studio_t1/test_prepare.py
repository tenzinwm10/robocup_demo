"""Bootstrap accepts only correlated native walking/soccer confirmations."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from rclpy.context import Context
from rclpy.node import Node
from rclpy.executors import SingleThreadedExecutor
from booster_msgs.msg import RpcReqMsg, RpcRespMsg

def check(mode, expected_success):
    context = Context(); context.init(args=[], domain_id=92)
    node = Node('prepare_rpc_fixture', context=context)
    executor = SingleThreadedExecutor(context=context); executor.add_node(node)
    publisher = node.create_publisher(RpcRespMsg, '/LocoApiTopic/robot1Resp', 10)
    def reply(request):
        api_id = json.loads(request.header)['api_id']
        # An unrelated successful reply must never satisfy mode readiness.
        publisher.publish(RpcRespMsg(uuid='wrong-uuid', header='{"status":0}', body='{"mode":2}'))
        publisher.publish(RpcRespMsg(uuid=request.uuid, header='{"status":0}',
                          body=json.dumps({'mode': mode}) if api_id == 2017 else '{}'))
    subscription = node.create_subscription(RpcReqMsg, '/LocoApiTopic/robot1Req', reply, 10)
    process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('prepare_robots.py')),
        '--robots', 'robot1', '--timeout', '3'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, env=dict(os.environ, ROS_DOMAIN_ID='92'))
    try:
        end = time.monotonic()+15
        while process.poll() is None and time.monotonic() < end: executor.spin_once(timeout_sec=.02)
        if process.poll() is None: raise RuntimeError('Bootstrap test timed out')
        output = process.communicate()[0]
        assert (process.returncode == 0) == expected_success, output
        assert ('confirmed' if expected_success else 'did not confirm') in output, output
    finally:
        if process.poll() is None: process.kill(); process.wait()
        executor.shutdown(); node.destroy_node(); context.shutdown()

if __name__ == '__main__':
    check(2, True); check(4, True); check(0, False)
    print(json.dumps({'result': 'PASS', 'walking': True, 'soccer': True,
                      'unready_mode_rejected': True, 'unmatched_uuid_rejected': True}))
