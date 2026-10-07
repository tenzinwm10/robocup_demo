import json
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from communications_observer import Decoder, ROOT, HERE
from oversight_metrics import RadioMetrics, RPCMetrics, extract_udp, decode_return, budget_projection
from oversight_server import summary

KEY = '000102030405060708090a0b0c0d0e0f'

class OversightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.decoder = Decoder(KEY)
        subprocess.run(['g++', '-std=c++17', '-O2', '-I'+str(ROOT/'src/brain/include'),
            '-I'+str(ROOT/'src/booster_ros2_interface/include/booster_interface'),
            str(HERE/'team_fixture.cpp'), str(ROOT/'src/brain/src/team_communication_protocol.cpp'),
            '-o', '/tmp/team-fixture'], check=True, capture_output=True)
        cls.fixtures = json.loads(subprocess.check_output(['/tmp/team-fixture'], input=KEY.encode()))

    def test_original_crc_auth_and_packet_sizes(self):
        for kind, expected in [('discovery', 37), ('state', 268)]:
            packet = bytes.fromhex(self.fixtures[kind]); result = self.decoder.decode(packet)
            self.assertTrue(result['valid']); self.assertEqual(len(packet), expected)
            self.assertEqual(result['team'], 1); self.assertEqual(result['player'], 2)
            corrupt = bytearray(packet); corrupt[25] ^= 1
            self.assertFalse(self.decoder.decode(bytes(corrupt))['valid'])
            self.assertFalse(Decoder('11111111111111111111111111111111').decode(packet)['valid'])

    def test_sequence_gaps_reordering_reboot_and_staleness(self):
        radio = RadioMetrics(); radio.started = 0
        packet = bytes.fromhex(self.fixtures['state']); message = self.decoder.decode(packet)
        for seq in (7, 9, 8, 9): radio.observe(30022, 10001, packet, dict(message, sequence=seq), now=seq)
        entry = radio.snapshot(now=12)['streams']['1/2/state']
        self.assertEqual(entry['sequence_gaps'], 1); self.assertEqual(entry['out_of_order'], 1)
        self.assertEqual(entry['duplicates'], 1); self.assertEqual(entry['age_ms'], 3000)
        radio.observe(30022,10001,packet,dict(message,boot_id='456',sequence=0),now=13)
        self.assertEqual(radio.snapshot(now=13)['streams']['1/2/state']['boot_changes'],1)

    def test_budget_clock_and_radio_categories(self):
        projection = budget_projection(12000,12,600,1)
        self.assertEqual(projection['estimated_packets_needed'],7200); self.assertFalse(projection['risk'])
        self.assertTrue(budget_projection(12000,12,600,.2)['risk'])
        self.assertIsNone(budget_projection(12000,12,600,0))
        radio = RadioMetrics(); radio.started=0
        radio.observe(1234,3939,b'x'*32,dict(valid=True,team=1,player=1),now=1)
        self.assertEqual(radio.snapshot(now=1)['teams'],{})

    def test_rpc_error_uuid_latency_timeout_and_malformed(self):
        metrics = RPCMetrics()
        request = SimpleNamespace(uuid='abc',header='{"api_id":2038}',body='{"start":true}')
        metrics.request(request,now=1)
        reply = SimpleNamespace(uuid='abc',header='{"status":501}',body='unsupported')
        result = metrics.response(reply,now=1.025)
        self.assertEqual(result['status'],501); self.assertAlmostEqual(result['latency_ms'],25)
        metrics.request(request,now=2); metrics.response(SimpleNamespace(uuid='wrong',header='{}',body=''),now=3)
        snapshot=metrics.snapshot(now=13)
        self.assertEqual(snapshot['unanswered_after_10s'],1); self.assertEqual(snapshot['orphan_replies'],1)
        self.assertEqual(reply.header,'{"status":501}')
        self.assertIsNone(metrics.request(SimpleNamespace(uuid='x',header='{"api_id":{}}',body=''),now=14))

    def test_udp_frame_and_return_layout(self):
        payload=b'TD2S'; ip=bytearray(20);ip[0]=0x45;ip[9]=17
        frame=b'\0'*12+b'\x08\x00'+bytes(ip)+struct.pack('!HHHH',30022,10001,12,0)+payload
        self.assertEqual(extract_udp(frame),(30022,10001,payload))
        self.assertIsNone(extract_udp(frame[:-1]))
        returned=b'RGrt'+bytes([4,2,1,0])+struct.pack('<6f',1000.,2000.,.1,2.,300.,400.)
        self.assertEqual(decode_return(returned)['pose_mm_rad'][:2],[1000.,2000.])

    def test_dashboard_missing_data_and_alerts(self):
        with tempfile.TemporaryDirectory() as directory:
            data=summary(Path(directory)); self.assertEqual(data['state_name'],'WAITING')
            self.assertTrue(data['alerts']); self.assertEqual(len(data['robots']),6)

if __name__=='__main__': unittest.main(verbosity=2)
