"""Bridge two DDS domains; the brain sees the same routes as a single physical T2.

RPC UUID, header and body are forwarded without parsing, filtering or rewriting.
Studio domain and application domains must differ. No fake RPC success is emitted.
"""
import argparse
import json
import time
from pathlib import Path
import struct
import rclpy
from rclpy.context import Context
from rclpy.executors import SingleThreadedExecutor, ExternalShutdownException
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import qos_profile_sensor_data, QoSProfile, DurabilityPolicy, ReliabilityPolicy
from std_msgs.msg import String
from game_controller_interface.msg import GameControlData
from bridge import StudioBridge, parse_referee_json
from t2_routes import SENSOR_ROUTES, Clock, RpcReqMsg, RpcRespMsg, rpc_topics, RPC_REQUEST, RPC_RESPONSE
from oversight_metrics import RPCMetrics
from event_log import EventLog
from check_health import note_received, note_clock, note_rpc_response


class T2Adapter:
    def __init__(self, robot='robot1', simulator_domain=0, agent_domain=11,
                 mirror=False, perception='ideal', localization='ideal', health_path=None,
                 snapshot_head_pose=False):
        if simulator_domain == agent_domain:
            raise ValueError('Simulator and application domains must differ')
        self.sim_context, self.agent_context = Context(), Context()
        self.sim_context.init(args=[], domain_id=simulator_domain)
        self.agent_context.init(args=[], domain_id=agent_domain)
        self.agent = Node(f'studio_t2_transport_{agent_domain}', context=self.agent_context,
            parameter_overrides=[Parameter('use_sim_time', value=True)])
        self.sim = StudioBridge(robot, mirror, False, context=self.sim_context,
            output_node=self.agent, ideal_perception=perception == 'ideal',
            ideal_localization=localization == 'ideal', snapshot_head_pose=snapshot_head_pose)
        self.sim_executor = SingleThreadedExecutor(context=self.sim_context)
        self.agent_executor = SingleThreadedExecutor(context=self.agent_context)
        self.sim_executor.add_node(self.sim)
        self.agent_executor.add_node(self.agent)
        self.entities = []
        self.health_path = Path(health_path) if health_path else None
        self.rpc = RPCMetrics()
        self.rpc_events = EventLog(self.health_path.parent/'oversight'/f'{robot}-rpc.ndjson') if self.health_path else None
        self.health = {'robot': robot, 'simulator_domain': simulator_domain,
                      'agent_domain': agent_domain, 'perception': perception,
                      'localization': localization, 'received': {}, 'referee_errors': 0}
        self.last_write = 0
        from vision_interface.msg import Detections
        from geometry_msgs.msg import PoseStamped, Pose2D
        def perception(message):
            note_received(self.health, '/booster_vision/detection')
        self.entities.append(self.agent.create_subscription(Detections, '/booster_vision/detection', perception, 10))
        # Observe the canonical outputs after the bridge's timestamp/pose handling.
        # This records liveness without changing any payload or publisher route.
        self.entities.append(self.agent.create_subscription(PoseStamped, '/head_pose_stamped',
            lambda message: note_received(self.health, '/head_pose_stamped'), 10))
        if localization == 'ideal':
            self.entities.append(self.agent.create_subscription(Pose2D, '/soccer/sim/localization/robot_pose',
                lambda message: note_received(self.health, '/soccer/sim/localization/robot_pose'), 10))
        prefix = f'/{robot}' if robot else ''
        for suffix, target, message_type in SENSOR_ROUTES:
            self.forward(self.sim, self.agent, message_type, prefix+suffix, target,
                         qos_profile_sensor_data, target_qos=10)
        # Clock must precede timestamp normalization in the perception adapter.
        self.forward(self.sim, self.agent, Clock, '/clock', '/clock', qos_profile_sensor_data)
        request, response = rpc_topics(robot)
        self.forward(self.agent, self.sim, RpcReqMsg, RPC_REQUEST, request, 10)
        self.forward(self.sim, self.agent, RpcRespMsg, response, RPC_RESPONSE, 10)
        self.referee_pub = self.agent.create_publisher(GameControlData, '/robocup/game_controller', 10)
        self.entities.append(self.sim.create_subscription(String, '/soccer/game_controller', self.referee, 10))
        # /kick_ball is a branch diagnostic/intent output, not a replacement RPC.
        from brain.msg import Kick
        self.forward(self.agent, self.sim, Kick, '/kick_ball', prefix+'/kick_ball', 10)

    def forward(self, source_node, target_node, message_type, source, target, qos, target_qos=None):
        # Studio sensor inputs are best effort. The original T2 Vision requests
        # reliable CameraInfo/image_transport streams; reliable output serves both.
        publisher = target_node.create_publisher(message_type, target, target_qos or qos)
        def callback(message):
            publisher.publish(message)
            if message_type == Clock:
                note_clock(self.health, message.clock.sec*1_000_000_000+message.clock.nanosec)
            else:
                note_received(self.health, source)
            if message_type == RpcReqMsg:
                event = self.rpc.request(message)
                if self.rpc_events and event and event['api'] in (2000, 2008, 2024, 2038, 2047): self.rpc_events.emit(event)
            elif message_type == RpcRespMsg:
                event = self.rpc.response(message)
                note_rpc_response(self.health, event)
                if self.rpc_events and event['status'] != 0: self.rpc_events.emit(event)
        self.entities.extend([publisher, source_node.create_subscription(message_type, source, callback, qos)])

    def referee(self, source):
        try:
            message = parse_referee_json(source.data)
        except (ValueError, KeyError, TypeError, struct.error) as error:
            self.health['referee_errors'] += 1
            self.sim.get_logger().error(f'Invalid referee: {error}')
            return
        self.referee_pub.publish(message)
        note_received(self.health, '/soccer/game_controller')
        self.health['referee_state'] = message.state
        self.health['score'] = [team.score for team in message.teams]

    def spin_once(self):
        self.sim_executor.spin_once(timeout_sec=0.002)
        self.agent_executor.spin_once(timeout_sec=0.002)
        if self.health_path and time.monotonic()-self.last_write >= 2:
            self.health.update(updated_at=time.time(), detection_frames=self.sim.frames,
                               ball_observations=self.sim.balls, head_pose_frames=self.sim.head_frames,
                               localization_frames=self.sim.pose_frames)
            self.health['rpc'] = self.rpc.snapshot()
            self.health['rpc_log_drops'] = self.rpc_events.dropped if self.rpc_events else 0
            self.health_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.health_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.health, indent=2))
            temporary.replace(self.health_path)
            self.last_write = time.monotonic()

    def close(self):
        if self.rpc_events: self.rpc_events.close()
        self.sim_executor.shutdown()
        self.agent_executor.shutdown()
        self.sim.destroy_node()
        self.agent.destroy_node()
        if self.sim_context.ok(): self.sim_context.shutdown()
        if self.agent_context.ok(): self.agent_context.shutdown()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--robot', default='robot1')
    parser.add_argument('--simulator-domain', type=int, default=0)
    parser.add_argument('--agent-domain', type=int, required=True)
    parser.add_argument('--mirror', action='store_true')
    parser.add_argument('--perception', choices=('ideal', 'external'), default='ideal')
    parser.add_argument('--localization', choices=('ideal', 'visual'), default='ideal')
    parser.add_argument('--health-path')
    parser.add_argument('--snapshot-head-pose', action='store_true')
    args = parser.parse_args()
    adapter = T2Adapter(**vars(args))
    try:
        while adapter.sim_context.ok() and adapter.agent_context.ok():
            adapter.spin_once()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        adapter.close()

if __name__ == '__main__':
    main()
