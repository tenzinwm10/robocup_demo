#!/usr/bin/env python3
"""Adapt Booster Studio's built-in soccer perception to this branch's messages."""
import math
import socket
import struct
import argparse
import json
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from vision_msgs.msg import Detection2DArray
from vision_interface.msg import Detections, DetectedObject, LineSegments
from geometry_msgs.msg import PoseArray, PoseStamped, Pose2D
from std_msgs.msg import String
from game_controller_interface.msg import GameControlData


def parse_referee(data):
    if len(data) not in (158, 198) or data[:4] != b'RGme' or data[4] not in (19, 20):
        raise ValueError('Unsupported Studio GameController packet')
    if (data[4], len(data)) not in ((19, 198), (20, 158)):
        raise ValueError('GameController version/length mismatch')
    out = GameControlData()
    out.header = list(b'RGme')
    out.version = 20
    out.packet_number, out.players_per_team, out.competition_type = data[5:8]
    out.stopped, out.game_phase, out.state = bool(data[8]), data[9], data[10]
    out.set_play, out.first_half, out.kicking_team = data[11], bool(data[12]), data[13]
    out.secs_remaining, out.secondary_time = struct.unpack_from('<hh', data, 14)
    stride = 4 if data[4] == 19 else 3
    for i, team in enumerate(out.teams):
        offset = 18 + i * (10 + 20 * stride)
        (team.team_number, team.field_player_colour, team.goalkeeper_colour,
         team.goalkeeper, team.score, team.penalty_shot) = data[offset:offset + 6]
        team.single_shots, team.message_budget = struct.unpack_from('<HH', data, offset + 6)
        for j, player in enumerate(team.players):
            start = offset + 10 + j * stride
            player.penalty = data[start]
            if data[4] == 19:
                penalties = {0: 0, 1: 1, 2: 2, 3: 4, 4: 5, 5: 6,
                             6: 7, 7: 8, 8: 9, 9: 10, 10: 12, 11: 13}
                if player.penalty not in penalties:
                    raise ValueError('Unsupported Studio penalty code')
                player.penalty = penalties[player.penalty]
            player.secs_till_unpenalised = data[start + 1]
            player.cautions = data[start + (3 if data[4] == 19 else 2)]
    if not 1 <= out.players_per_team <= 20 or out.state > 4 or out.competition_type > 2:
        raise ValueError('Invalid GameController fields')
    return out


def mirrored_pose(source, mirror):
    out = Pose2D()
    out.x = -source.x if mirror else source.x
    out.y = -source.y if mirror else source.y
    out.theta = math.atan2(math.sin(source.theta + (math.pi if mirror else 0)),
                           math.cos(source.theta + (math.pi if mirror else 0)))
    return out


def parse_referee_json(payload):
    message = json.loads(payload)
    message = message.get('controlMessage', message)
    def enum(value, names):
        return value if isinstance(value, int) else names.index(value)
    competition = enum(message['competitionType'], ['SMALL', 'MIDDLE', 'LARGE'])
    phase = enum(message['gamePhase'], ['NORMAL', 'PENALTY_SHOOT_OUT', 'EXTRA_TIME', 'TIMEOUT'])
    state = enum(message['state'], ['INITIAL', 'READY', 'SET', 'PLAYING', 'FINISHED'])
    play = enum(message['setPlay'], ['NONE', 'DIRECT_FREE_KICK', 'INDIRECT_FREE_KICK',
                                   'PENALTY_KICK', 'THROW_IN', 'GOAL_KICK', 'CORNER_KICK'])
    packet = bytearray(struct.pack('<4s10Bhh', b'RGme', message['version'],
        message['packetNumber'], message['playersPerTeam'], competition,
        int(message['stopped']), phase, state, play,
        int(message['firstHalf']), message['kickingTeam'], message['secsRemaining'], message['secondaryTime']))
    for team in message['teams']:
        packet.extend(struct.pack('<6BHH', team.get('teamNumber', team.get('number')),
            team.get('fieldPlayerColour', team.get('fieldPlayerColor')),
            team.get('goalkeeperColour', team.get('goalkeeperColor')),
            team['goalkeeper'], team['score'], team['penaltyShot'],
            team['singleShots'], team['messageBudget']))
        for player in team['players']:
            penalty = enum(player['penalty'], ['NONE', 'ILLEGAL_POSITIONING', 'MOTION_IN_SET',
                'LOCAL_GAME_STUCK', 'INCAPABLE_ROBOT', 'PICKED_UP', 'BALL_HOLDING',
                'LEAVING_THE_FIELD', 'PLAYING_WITH_ARMS_HANDS', 'PUSHING', 'SENT_OFF', 'SUBSTITUTE'])
            seconds = player.get('secsTillUnpenalised', player.get('secsTillUnpenalized'))
            if message['version'] == 19:
                packet.extend(struct.pack('<4B', penalty, seconds,
                    player['warnings'], player['cautions']))
            else:
                packet.extend(struct.pack('<3B', penalty, seconds, player['cautions']))
    return parse_referee(bytes(packet))


class RefereeBridge(Node):
    def __init__(self):
        super().__init__('studio_referee_bridge')
        self.pub = self.create_publisher(GameControlData, '/robocup/game_controller', 10)
        self.sub = self.create_subscription(String, '/soccer/game_controller', self.convert, 10)
        self.last_state = None

    def convert(self, source):
        try:
            message = parse_referee_json(source.data)
        except (ValueError, KeyError, struct.error, TypeError) as error:
            self.get_logger().error(f'Invalid Studio referee message: {error}')
            return
        self.pub.publish(message)
        if message.state != self.last_state:
            self.get_logger().info(f'Referee state={message.state}; score={[t.score for t in message.teams]}')
            self.last_state = message.state


class StudioBridge(Node):
    def __init__(self, robot_name='', mirror=False, referee=True, *, context=None,
                 output_node=None, ideal_perception=True, ideal_localization=True,
                 snapshot_head_pose=False):
        super().__init__('studio_t1_bridge', namespace=robot_name, context=context)
        self.prefix = f'/{robot_name}' if robot_name else ''
        self.output = output_node or self
        self.mirror = mirror
        self.snapshot_head_pose = snapshot_head_pose
        topic = lambda name: self.prefix + name
        output_topic = (lambda name: name) if output_node else topic
        self.declare_parameter('use_sim_time', True) if not self.has_parameter('use_sim_time') else None
        self.pub = self.output.create_publisher(Detections, output_topic('/booster_vision/detection'), 10)
        self.sub = self.create_subscription(
            Detection2DArray, topic('/soccer/sim/vision/detections'), self.convert,
            qos_profile_sensor_data) if ideal_perception else None
        self.line_pub = self.output.create_publisher(LineSegments, output_topic('/booster_vision/line_segments'), 10)
        self.line_sub = self.create_subscription(
            PoseArray, topic('/soccer/sim/vision/field_lines'), self.lines,
            qos_profile_sensor_data) if ideal_perception else None
        self.head_pub = self.output.create_publisher(PoseStamped,
            '/head_pose_stamped' if output_node else topic('/studio/head_pose_stamped'), 10)
        self.head_sub = self.create_subscription(
            PoseStamped, topic('/simulation/head_pose_stamped' if snapshot_head_pose else '/head_pose_stamped'), self.head, qos_profile_sensor_data)
        self.pose_pub = self.output.create_publisher(Pose2D,
            '/soccer/sim/localization/robot_pose' if output_node else topic('/studio/field_pose'), 10)
        self.truth_pub = self.output.create_publisher(Pose2D,
            output_topic('/simulation/ground_truth/robot_pose'), 10)
        self.ideal_localization = ideal_localization
        self.pose_sub = self.create_subscription(
            Pose2D, topic('/soccer/sim/localization/robot_pose'), self.pose, qos_profile_sensor_data)
        self.frames = self.balls = 0
        self.head_frames = self.pose_frames = 0
        self.gc = self.output.create_publisher(GameControlData, '/robocup/game_controller', 10)
        self.socket = None
        if referee:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.bind(('0.0.0.0', 3838))
            self.socket.setblocking(False)
        self.last_state = None
        if referee:
            self.create_timer(0.05, self.referee)
        self.create_timer(5.0, self.report)

    def referee(self):
        while True:
            try:
                data, _ = self.socket.recvfrom(4096)
            except BlockingIOError:
                return
            try:
                message = parse_referee(data)
            except ValueError as error:
                self.get_logger().warning(str(error))
                continue
            self.gc.publish(message)
            if message.state != self.last_state:
                self.get_logger().info(f'Studio GameController state={message.state}, '
                                       f'teams={[t.team_number for t in message.teams]}')
                self.last_state = message.state

    def lines(self, source):
        if len(source.poses) % 2:
            return
        out = LineSegments()
        out.header.stamp = self.output.get_clock().now().to_msg()
        out.header.frame_id = 'base_link'
        for a, b in zip(source.poses[::2], source.poses[1::2]):
            xy = [a.position.x, a.position.y, b.position.x, b.position.y]
            uv = [a.orientation.x, a.orientation.y, b.orientation.x, b.orientation.y]
            if all(math.isfinite(v) for v in xy + uv):
                out.coordinates.extend(float(v) for v in xy)
                out.coordinates_uv.extend(float(v) for v in uv)
        self.line_pub.publish(out)

    def head(self, source):
        # Studio uses wall stamps on head pose and simulation stamps on images.
        # Normalize the pose to the same simulation clock at message receipt.
        if not self.snapshot_head_pose:
            source.header.stamp = self.output.get_clock().now().to_msg()
        self.head_pub.publish(source)
        self.head_frames += 1

    def pose(self, source):
        if all(math.isfinite(v) for v in (source.x, source.y, source.theta)):
            pose = mirrored_pose(source, self.mirror)
            self.truth_pub.publish(pose)
            self.pose_frames += 1
            if self.ideal_localization:
                self.pose_pub.publish(pose)

    def convert(self, source):
        out = Detections()
        out.header = source.header
        aliases = {'L_cross': 'LCross', 'T_cross': 'TCross', 'X_cross': 'XCross',
                   'Penalty_point': 'PenaltyPoint', 'Robot': 'Opponent', 'Person': 'Opponent'}
        for detection in source.detections:
            if not detection.results:
                continue
            result = max(detection.results, key=lambda r: r.hypothesis.score)
            p = result.pose.pose.position
            if not all(math.isfinite(v) for v in (p.x, p.y, p.z)):
                continue
            item = DetectedObject()
            item.label = aliases.get(result.hypothesis.class_id, result.hypothesis.class_id)
            item.confidence = 100.0 * result.hypothesis.score
            center = detection.bbox.center.position
            sx, sy = detection.bbox.size_x, detection.bbox.size_y
            item.xmin, item.xmax = int(center.x - sx / 2), int(center.x + sx / 2)
            item.ymin, item.ymax = int(center.y - sy / 2), int(center.y + sy / 2)
            item.target_uv = [float(center.x), float(center.y)]
            # Studio publishes these positions in the robot's planar frame.
            item.position_projection = [float(p.x), float(p.y), float(p.z)]
            item.position = list(item.position_projection)
            item.position_confidence = 100
            out.detected_objects.append(item)
            self.balls += int(item.label == 'Ball')
        self.pub.publish(out)
        self.frames += 1

    def report(self):
        self.get_logger().info(f'Studio frames={self.frames}, ball observations={self.balls}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--robot', default='')
    parser.add_argument('--mirror', action='store_true')
    parser.add_argument('--no-referee', action='store_true')
    parser.add_argument('--referee-only', action='store_true')
    args, ros_args = parser.parse_known_args()
    rclpy.init(args=ros_args)
    node = RefereeBridge() if args.referee_only else StudioBridge(args.robot, args.mirror, not args.no_referee)
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
