"""DDS integration tests with synthetic Studio endpoints, no simulator/GPU needed."""
import copy
import json
import time
import unittest
from rclpy.context import Context
from rclpy.node import Node
from rclpy.executors import SingleThreadedExecutor
from rclpy.serialization import serialize_message
from rclpy.qos import qos_profile_sensor_data
from geometry_msgs.msg import Pose2D, PoseStamped, PoseArray, Pose
from std_msgs.msg import String
from vision_msgs.msg import Detection2DArray, Detection2D, ObjectHypothesisWithPose
from vision_interface.msg import Detections, LineSegments
from game_controller_interface.msg import GameControlData
from t2_adapter import T2Adapter
from t2_routes import SENSOR_ROUTES, RpcReqMsg, RpcRespMsg, Clock


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contexts, cls.executors, cls.nodes = [], [], []
        for domain in (71, 72, 73):
            context = Context(); context.init(args=[], domain_id=domain)
            node = Node(f'test_domain_{domain}', context=context)
            executor = SingleThreadedExecutor(context=context); executor.add_node(node)
            cls.contexts.append(context); cls.nodes.append(node); cls.executors.append(executor)
        cls.adapters = [T2Adapter('robot1', 71, 72), T2Adapter('robot2', 71, 73, mirror=True)]
        cls.entities = []

    @classmethod
    def tearDownClass(cls):
        for adapter in cls.adapters: adapter.close()
        for node, executor, context in zip(cls.nodes, cls.executors, cls.contexts):
            executor.shutdown(); node.destroy_node(); context.shutdown()

    def wire(self, source, target, message_type, source_topic, target_topic, qos=10, target_type=None):
        received = []
        publisher = source.create_publisher(message_type, source_topic, qos)
        subscription = target.create_subscription(target_type or message_type, target_topic, received.append, qos)
        self.entities.extend([publisher, subscription])
        return publisher, received

    def pump(self, condition, publish=None, timeout=12):
        end, next_publish = time.monotonic()+timeout, 0
        while time.monotonic() < end:
            if publish and time.monotonic() >= next_publish:
                publish(); next_publish = time.monotonic()+0.1
            for adapter in self.adapters: adapter.spin_once()
            for executor in self.executors: executor.spin_once(timeout_sec=0.002)
            if condition(): return
        self.fail('DDS delivery timed out')

    def test_rpc_payloads_and_robot_isolation(self):
        # Includes mode, move, head, GetUp, GetMode, Shoot, VisualKick and trajectory APIs.
        payloads = {2000: {'mode': 4}, 2001: {'vx': .31, 'vy': -.22, 'vyaw': .4},
                    2004: {'pitch': .2, 'yaw': -.1}, 2008: {'version': 1}, 2017: {},
                    2024: {'power': .5}, 2038: {'start': True, 'version': 1},
                    2047: {'traj_id': 'contract-test'}}
        pub, received = self.wire(self.nodes[1], self.nodes[0], RpcReqMsg,
                                 '/LocoApiTopicReq', '/LocoApiTopic/robot1Req')
        wrong = []
        self.entities.append(self.nodes[0].create_subscription(RpcReqMsg,
                              '/LocoApiTopic/robot2Req', wrong.append, 10))
        for api_id, body in payloads.items():
            message = RpcReqMsg(uuid=f'exact-{api_id}', header=json.dumps({'api_id': api_id}),
                                body=json.dumps(body, separators=(',', ':')))
            self.pump(lambda: any(m.uuid == message.uuid for m in received), lambda: pub.publish(message))
            actual = next(m for m in received if m.uuid == message.uuid)
            self.assertEqual(serialize_message(actual), serialize_message(message))
        self.assertEqual(wrong, [])
        # Arbitrary native failures must reach the caller unchanged, never fake success.
        reply_pub, replies = self.wire(self.nodes[0], self.nodes[1], RpcRespMsg,
                                      '/LocoApiTopic/robot1Resp', '/LocoApiTopicResp')
        reply = RpcRespMsg(uuid='exact-2038', header='{"status":-123}', body='{"error":"unsupported"}')
        self.pump(lambda: replies, lambda: reply_pub.publish(reply))
        self.assertEqual(serialize_message(replies[-1]), serialize_message(reply))

    def test_all_sensor_routes_preserve_serialized_message(self):
        for suffix, canonical, message_type in SENSOR_ROUTES:
            with self.subTest(topic=canonical):
                pub, received = self.wire(self.nodes[0], self.nodes[1], message_type,
                                         '/robot1'+suffix, canonical, qos_profile_sensor_data)
                message = message_type()
                if hasattr(message, 'header'):
                    message.header.frame_id = 'hardware-frame'
                    message.header.stamp.sec = 12
                if hasattr(message, 'encoding'):
                    message.height = 1; message.width = 2
                    message.encoding = '16UC1' if 'depth' in suffix else 'rgb8'
                    message.step = 4 if 'depth' in suffix else 6
                    message.data = [1, 0, 2, 0] if 'depth' in suffix else [1, 2, 3, 4, 5, 6]
                self.pump(lambda: received, lambda: pub.publish(message))
                self.assertEqual(serialize_message(received[-1]), serialize_message(message))

    def test_pose_and_away_coordinate_convention(self):
        pub, received = self.wire(self.nodes[0], self.nodes[2], Pose2D,
            '/robot2/soccer/sim/localization/robot_pose', '/soccer/sim/localization/robot_pose', qos_profile_sensor_data)
        message = Pose2D(x=4., y=-2., theta=0.)
        self.pump(lambda: received, lambda: pub.publish(message))
        self.assertEqual((received[-1].x, received[-1].y), (-4., 2.))
        self.assertAlmostEqual(abs(received[-1].theta), 3.141592653589793)

    def test_perception_conversion_uses_body_coordinates(self):
        pub, received = self.wire(self.nodes[0], self.nodes[1], Detection2DArray,
            '/robot1/soccer/sim/vision/detections', '/booster_vision/detection', qos_profile_sensor_data, Detections)
        message = Detection2DArray(); message.header.stamp.sec = 42
        detection = Detection2D(); detection.bbox.center.position.x = 160.; detection.bbox.center.position.y = 120.
        detection.bbox.size_x = 20.; detection.bbox.size_y = 10.
        result = ObjectHypothesisWithPose(); result.hypothesis.class_id = 'Ball'; result.hypothesis.score = .8
        result.pose.pose.position.x = 2.; result.pose.pose.position.y = -.3
        detection.results = [result]; message.detections = [detection]
        self.pump(lambda: received, lambda: pub.publish(message))
        item = received[-1].detected_objects[0]
        self.assertEqual(item.label, 'Ball'); self.assertEqual(item.confidence, 80.)
        for actual, expected in zip(item.position_projection, [2., -.3, 0.]):
            self.assertAlmostEqual(actual, expected, places=6)
        self.assertEqual(received[-1].header.stamp.sec, 42)

    def test_field_lines(self):
        pub, received = self.wire(self.nodes[0], self.nodes[1], PoseArray,
            '/robot1/soccer/sim/vision/field_lines', '/booster_vision/line_segments', qos_profile_sensor_data, LineSegments)
        message = PoseArray(); a, b = Pose(), Pose()
        a.position.x = 1.; a.position.y = -2.; b.position.x = 3.; b.position.y = 4.
        a.orientation.x = 10.; a.orientation.y = 20.; b.orientation.x = 30.; b.orientation.y = 40.
        message.poses = [a, b]
        self.pump(lambda: received, lambda: pub.publish(message))
        self.assertEqual(list(received[-1].coordinates), [1., -2., 3., 4.])
        self.assertEqual(list(received[-1].coordinates_uv), [10., 20., 30., 40.])

    def test_external_perception_and_visual_localization_have_no_truth_injection(self):
        adapter = T2Adapter('robot3', 71, 74, perception='external', localization='visual')
        try:
            self.assertIsNone(adapter.sim.sub)
            self.assertIsNone(adapter.sim.line_sub)
            self.assertFalse(adapter.sim.ideal_localization)
        finally: adapter.close()

if __name__ == '__main__': unittest.main(verbosity=2)
