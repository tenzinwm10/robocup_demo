"""Regression checks against a packet captured from Studio 1.12.7's T1 scene."""
import unittest
import json
import math
from pathlib import Path
from bridge import parse_referee, parse_referee_json, mirrored_pose
from geometry_msgs.msg import Pose2D

CAPTURE = bytes.fromhex(Path(__file__).with_name('game-controller-v19.hex').read_text())


class RefereeTest(unittest.TestCase):
    def test_real_studio_packet(self):
        message = parse_referee(CAPTURE)
        self.assertEqual(message.version, 20)
        self.assertEqual(message.players_per_team, 3)
        self.assertEqual(message.secs_remaining, 600)
        self.assertEqual([t.team_number for t in message.teams], [1, 2])
        self.assertEqual(message.teams[0].players[2].penalty, 0)
        self.assertEqual(message.teams[0].players[3].penalty, 13)

    def test_reject_invalid_datagrams(self):
        for packet in (b'', CAPTURE[:-1], b'nope' + CAPTURE[4:]):
            with self.assertRaises(ValueError):
                parse_referee(packet)

    def test_named_json_referee_and_penalties(self):
        players = [{'penalty': 'NONE', 'secsTillUnpenalised': 0, 'warnings': 0, 'cautions': 0}
                   for _ in range(20)]
        players[2] = {**players[2], 'penalty': 'LEAVING_THE_FIELD', 'cautions': 2}
        team = {'teamNumber': 1, 'fieldPlayerColour': 1, 'goalkeeperColour': 0,
                'goalkeeper': 1, 'score': 0, 'penaltyShot': 0, 'singleShots': 0,
                'messageBudget': 12000, 'players': players}
        payload = {'version': 19, 'packetNumber': 3, 'playersPerTeam': 3,
                   'competitionType': 'LARGE', 'stopped': False, 'gamePhase': 'NORMAL',
                   'state': 'PLAYING', 'setPlay': 'CORNER_KICK', 'firstHalf': True,
                   'kickingTeam': 1, 'secsRemaining': 600, 'secondaryTime': 0,
                   'teams': [team, {**team, 'teamNumber': 2}]}
        message = parse_referee_json(json.dumps(payload))
        self.assertEqual(message.state, 3)
        self.assertEqual(message.set_play, 6)
        self.assertEqual(message.teams[0].players[2].penalty, 8)
        self.assertEqual(message.teams[0].players[2].cautions, 2)

    def test_opposing_team_pose_rotation(self):
        physical = Pose2D(x=8.0, y=-2.5, theta=math.pi)
        team_pose = mirrored_pose(physical, True)
        self.assertAlmostEqual(team_pose.x, -8.0)
        self.assertAlmostEqual(team_pose.y, 2.5)
        self.assertAlmostEqual(team_pose.theta, 0.0)
        original = mirrored_pose(team_pose, True)
        self.assertAlmostEqual(original.x, physical.x)
        self.assertAlmostEqual(original.y, physical.y)
        self.assertAlmostEqual(abs(original.theta), math.pi)


if __name__ == '__main__':
    unittest.main()
