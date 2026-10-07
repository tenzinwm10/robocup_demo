"""Passive team-channel capture and raw/translated referee inspection.

AF_PACKET binds only the chosen interface and observes the configured team ports,
3838 and 3939. It does not bind a robot UDP port, consume its packets or send data.
"""
import argparse
from collections import deque
import ctypes
import json
import math
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time
import yaml
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from std_msgs.msg import String
from rosgraph_msgs.msg import Clock
from rosidl_runtime_py.convert import message_to_ordereddict
from bridge import parse_referee_json, parse_referee
from event_log import EventLog
from oversight_metrics import RadioMetrics, extract_udp, decode_return

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

class Decoder:
    cached_library = None
    def __init__(self, secret):
        self.secret = secret.encode()
        if Decoder.cached_library is None:
            library = Path('/tmp/studio-team-inspector.so')
            subprocess.run(['g++', '-std=c++17', '-O2', '-shared', '-fPIC',
                '-I'+str(ROOT/'src/brain/include'),
                '-I'+str(ROOT/'src/booster_ros2_interface/include/booster_interface'),
                str(HERE/'team_decoder.cpp'), str(ROOT/'src/brain/src/team_communication_protocol.cpp'),
                '-o', str(library)], check=True, capture_output=True)
            Decoder.cached_library = ctypes.CDLL(str(library))
        self.library = Decoder.cached_library
        self.library.inspect_team.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
        self.library.inspect_team.restype = ctypes.c_int

    def decode(self, payload, secret=None):
        output = ctypes.create_string_buffer(8192)
        key = secret.encode() if secret else self.secret
        if self.library.inspect_team(payload, len(payload), key, output, len(output)) != 0:
            return {'valid': False, 'error': 'decoder output unavailable'}
        return json.loads(output.value)

class Observer(Node):
    def __init__(self, logs, interface):
        super().__init__('studio_communications_observer')
        self.logs = Path(logs); self.logs.mkdir(parents=True, exist_ok=True)
        self.events = EventLog(self.logs/'communications.ndjson')
        self.radio = RadioMetrics(); self.lock = threading.Lock(); self.running = True
        self.secrets = {}
        self.status = {'capture_status': 'starting', 'interface': interface, 'referee': None,
                       'referee_errors': 0, 'referee_messages': 0, 'packet_number_gaps': 0,
                       'clock': None, 'sim_speed': None, 'transitions': []}
        self.previous_signature = None
        self.clock_window = deque(maxlen=10000); self.previous_packet = None
        parameters = yaml.safe_load((ROOT/'src/brain/config/config.yaml').read_text())['brain_node']['ros__parameters']
        rate = min(20., max(.1, parameters['communication']['team_broadcast_rate_hz']))
        interval = max(50, math.floor(1000/rate+.5))
        self.status['limits'] = {'state_rate_hz': rate, 'discovery_rate_hz': 2., 'max_team_payload_bytes': 512,
            'configured_tactical_timeout_ms': parameters['strategy']['cooperation']['tactical_packet_timeout_ms'],
            'effective_tactical_timeout_ms': max(parameters['strategy']['cooperation']['tactical_packet_timeout_ms'], 100, 3*interval+100), 'protocol_version': 6,
            'budget_enforced_by_brain': False, 'studio_budget_accounting': 'not implemented in inspected referee source',
            'budget_projection_scope': 'all observed team-channel UDP datagrams'}
        try:
            self.decoder = Decoder(parameters['communication']['team_secret_hex'])
        except Exception as error:
            self.decoder = None; self.status['decoder_error'] = type(error).__name__
        self.referee_subscription = self.create_subscription(String, '/soccer/game_controller', self.referee, 10)
        self.clock_subscription = self.create_subscription(Clock, '/clock', self.clock, qos_profile_sensor_data)
        self.thread = threading.Thread(target=self.capture, args=(interface,), daemon=True); self.thread.start()
        self.timer = self.create_timer(1., self.report)

    def clock(self, message):
        now = time.monotonic(); value = message.clock.sec+message.clock.nanosec/1e9
        self.clock_window.append((now, value)); self.status['clock'] = value
        self.status['clock_received_at'] = time.time()
        while len(self.clock_window) > 1 and now-self.clock_window[0][0] > 10: self.clock_window.popleft()
        first_time, first_value = self.clock_window[0]
        if now-first_time > 1:
            self.status['sim_speed'] = max(0., (value-first_value)/(now-first_time))

    def referee(self, source):
        try:
            raw = json.loads(source.data)
            converted = parse_referee_json(source.data)
            typed = message_to_ordereddict(converted)
            packet = converted.packet_number
            if self.previous_packet is not None:
                delta = (packet-self.previous_packet) & 255
                if 1 < delta < 128: self.status['packet_number_gaps'] += delta-1
            self.previous_packet = packet
            signature = (converted.state, converted.set_play, converted.stopped, converted.kicking_team,
                converted.first_half, tuple((team.score, tuple((p.penalty, p.cautions) for p in team.players)) for team in converted.teams))
            if signature != self.previous_signature:
                self.status['transitions'].append({'received_at': time.time(), 'state': converted.state,
                    'set_play': converted.set_play, 'stopped': converted.stopped, 'kicking_team': converted.kicking_team,
                    'first_half': converted.first_half, 'score': [team.score for team in converted.teams]})
                self.status['transitions'] = self.status['transitions'][-64:]
                self.previous_signature = signature
            self.status.update(referee={'raw': raw, 'translated': typed, 'received_at': time.time()},
                               referee_messages=self.status['referee_messages']+1)
            self.events.emit({'event': 'game_controller', 'raw': raw, 'translated': typed})
        except Exception as error:
            self.status['referee_errors'] += 1
            self.events.emit({'event': 'game_controller_error', 'error': str(error), 'raw': source.data[:4000]})

    def capture(self, interface):
        capture = None
        try:
            capture = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
            capture.bind((interface, 0)); capture.settimeout(.5)
            self.status['capture_status'] = 'capturing'
            while self.running:
                try: frame, address = capture.recvfrom(65535)
                except socket.timeout: continue
                if address[2] == 4: continue  # Skip outgoing loopback duplicate.
                datagram = extract_udp(frame)
                if not datagram: continue
                source_port, target_port, payload = datagram
                if target_port not in (10001, 10002, 3838, 3939): continue
                if target_port == 3838:
                    try:
                        message = parse_referee(payload)
                        self.events.emit({'event': 'game_controller_udp', 'payload_bytes': len(payload),
                                          'translated': message_to_ordereddict(message)})
                    except ValueError as error: self.events.emit({'event': 'game_controller_udp_error', 'error': str(error)})
                    continue
                if target_port == 3939:
                    try: decoded = decode_return(payload)
                    except ValueError as error: decoded = {'valid': False, 'error': str(error)}
                else:
                    decoded = self.decoder.decode(payload, self.secrets.get(target_port-10000)) if self.decoder else {'valid': False, 'error': 'authentication not checked'}
                with self.lock: self.radio.observe(source_port, target_port, payload, decoded)
                self.events.emit({'event': 'radio', 'source_port': source_port, 'destination_port': target_port,
                                  'payload_bytes': len(payload), 'decoded': decoded})
        except Exception as error:
            self.status.update(capture_status='unavailable', capture_error=str(error))
        finally:
            if capture: capture.close()

    def report(self):
        configs, secrets = {}, {}
        for path in Path('/work/studio-configs').glob('robot*.yaml'):
            try:
                node_config = yaml.safe_load(path.read_text())['brain_node']['ros__parameters']
                rate = float(node_config['communication']['team_broadcast_rate_hz'])
                rate = min(20., max(.1, rate)) if math.isfinite(rate) else 20.
                configured = float(node_config['strategy']['cooperation']['tactical_packet_timeout_ms'])
                effective = max(configured if math.isfinite(configured) else 0, 100, 3*max(50, math.floor(1000/rate+.5))+100)
                configs[path.stem] = {'team': node_config['game']['team_id'], 'player': node_config['game']['player_id'],
                    'state_rate_hz': rate, 'configured_tactical_timeout_ms': configured, 'effective_tactical_timeout_ms': effective}
                secrets[node_config['game']['team_id']] = node_config['communication']['team_secret_hex']
            except (OSError, ValueError, TypeError, KeyError): continue
        self.secrets = secrets
        self.status['robot_configuration'] = configs
        with self.lock: radio = self.radio.snapshot()
        if time.time()-self.status.get('clock_received_at', 0) > 3: self.status['sim_speed'] = None
        snapshot = dict(self.status, radio=radio, updated_at=time.time(), log_drops=self.events.dropped)
        path = self.logs/'communications.json'; temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(snapshot, allow_nan=False, indent=2)); temporary.replace(path)

    def close(self):
        self.running = False; self.thread.join(timeout=2); self.report(); self.events.close()

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--logs', default='/work/studio-logs/oversight')
    parser.add_argument('--interface', default='lo'); args = parser.parse_args()
    rclpy.init(args=[]); node = Observer(args.logs, args.interface)
    try: rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException): pass
    finally:
        node.close(); node.destroy_node()
        if rclpy.ok(): rclpy.shutdown()

if __name__ == '__main__': main()
