"""Read-only communications accounting. No transmission limits are enforced here."""
from collections import Counter, OrderedDict, deque
import json
import math
import struct
import time

API_NAMES = {2000: 'ChangeMode', 2001: 'Move', 2004: 'RotateHead', 2008: 'GetUp',
             2017: 'GetMode', 2024: 'Shoot', 2038: 'VisualKick', 2047: 'TrainedTrajectoryStatus'}
STATE_NAMES = ['INITIAL', 'READY', 'SET', 'PLAY', 'FINISHED']
SET_PLAY_NAMES = ['NONE', 'DIRECT_FREE_KICK', 'INDIRECT_FREE_KICK', 'PENALTY_KICK', 'THROW_IN', 'GOAL_KICK', 'CORNER_KICK']

class RPCMetrics:
    def __init__(self):
        self.requests = Counter(); self.responses = Counter(); self.statuses = Counter()
        self.pending = OrderedDict(); self.samples = deque(maxlen=1000)
        self.expired = self.orphans = self.malformed = 0
        self.last_response = None

    def request(self, message, now=None):
        now = time.monotonic() if now is None else now
        try: api = json.loads(message.header)['api_id']
        except (ValueError, KeyError, TypeError): self.malformed += 1; return None
        if not isinstance(api, int): self.malformed += 1; return None
        self.requests[str(api)] += 1; self.pending[message.uuid] = (api, now)
        while len(self.pending) > 10000: self.pending.popitem(last=False); self.expired += 1
        return {'event': 'rpc_request', 'api': api, 'name': API_NAMES.get(api, str(api)),
                'uuid': message.uuid, 'header': message.header, 'body': message.body}

    def response(self, message, now=None):
        now = time.monotonic() if now is None else now
        pending = self.pending.pop(message.uuid, None)
        if pending: api, start = pending; latency = max(0., (now-start)*1000); self.samples.append(latency)
        else: api, latency = None, None; self.orphans += 1
        try: status = json.loads(message.header or '{}').get('status', 'missing')
        except (ValueError, TypeError, AttributeError): status = 'malformed'; self.malformed += 1
        self.responses[str(api)] += 1; self.statuses[str(status)] += 1
        self.last_response = {'event': 'rpc_response', 'api': api, 'name': API_NAMES.get(api, str(api)),
            'uuid': message.uuid, 'status': status, 'latency_ms': latency,
            'header': message.header, 'body': message.body, 'received_at': time.time()}
        return self.last_response

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        expired = [uuid for uuid, (_, sent) in self.pending.items() if now-sent > 10]
        for uuid in expired: self.pending.pop(uuid); self.expired += 1
        values = sorted(self.samples)
        return {'requests_by_api': dict(self.requests), 'responses_by_api': dict(self.responses),
                'statuses': dict(self.statuses), 'pending': len(self.pending), 'unanswered_after_10s': self.expired,
                'orphan_replies': self.orphans, 'malformed': self.malformed, 'last_response': self.last_response,
                'latency_avg_ms': sum(values)/len(values) if values else None,
                'latency_p95_ms': values[min(len(values)-1, math.ceil(.95*len(values))-1)] if values else None}

def extract_udp(frame):
    if len(frame) < 42 or frame[12:14] != b'\x08\x00': return None
    offset = 14; ihl = (frame[offset] & 15)*4
    if frame[offset] >> 4 != 4 or ihl < 20 or frame[offset+9] != 17: return None
    fragment = struct.unpack_from('!H', frame, offset+6)[0]
    if fragment & 0x3fff: return None  # Don't mistake fragments for whole datagrams.
    offset += ihl
    if len(frame) < offset+8: return None
    source, target, size = struct.unpack_from('!HHH', frame, offset)
    if size < 8 or offset+size > len(frame): return None
    return source, target, frame[offset+8:offset+size]

def decode_return(payload):
    if len(payload) != 32 or payload[:4] != b'RGrt' or payload[4] != 4:
        raise ValueError('Invalid GameController return packet')
    version, player, team, fallen = payload[4:8]
    values = struct.unpack_from('<6f', payload, 8)
    if not all(math.isfinite(value) for value in values): raise ValueError('Non-finite GameController return values')
    return {'kind': 'gamecontroller_return', 'version': version, 'player': player, 'team': team,
            'fallen': fallen, 'pose_mm_rad': list(values[:3]), 'ball_age_s': values[3], 'ball_mm': list(values[4:])}

class RadioMetrics:
    def __init__(self):
        self.started = time.monotonic(); self.events = deque(maxlen=100000)
        self.streams = {}; self.counts = Counter(); self.team_totals = Counter()
        self.invalid = Counter(); self.return_packets = {}

    def observe(self, source_port, target_port, payload, decoded, now=None):
        now = time.monotonic() if now is None else now
        if target_port == 3939:
            self.counts['gamecontroller_return'] += 1
            if decoded.get('valid', True): self.return_packets[f"{decoded['team']}/{decoded['player']}"] = dict(decoded, last_at=now)
            return
        team = target_port-10000
        self.team_totals[team] += 1; self.events.append((now, team, len(payload), decoded.get('kind', 'invalid')))
        if len(payload) > 512: self.counts['oversize'] += 1
        if not decoded.get('valid'):
            self.invalid[decoded.get('error') or 'invalid layout'] += 1; return
        if decoded['team'] != team: self.invalid['team/channel mismatch'] += 1; return
        kind = decoded['kind']; self.counts[kind] += 1
        key = f"{team}/{decoded['player']}/{kind}"
        entry = self.streams.setdefault(key, {'packets': 0, 'sequence_gaps': 0, 'duplicates': 0,
            'out_of_order': 0, 'boot_changes': 0, 'max_payload_bytes': 0})
        if entry.get('boot_id') == decoded['boot_id']:
            delta = (decoded['sequence']-entry['sequence']) & 0xffffffff
            if delta == 0: entry['duplicates'] += 1
            elif delta >= 0x80000000: entry['out_of_order'] += 1
            else: entry['sequence_gaps'] += delta-1
        elif 'boot_id' in entry: entry['boot_changes'] += 1
        if entry.get('boot_id') != decoded['boot_id'] or ((decoded['sequence']-entry.get('sequence', 0)) & 0xffffffff) < 0x80000000:
            entry.update(boot_id=decoded['boot_id'], sequence=decoded['sequence'])
        entry.update(packets=entry['packets']+1, max_payload_bytes=max(entry['max_payload_bytes'], len(payload)),
                     last_at=now, source_port=source_port, destination_port=target_port, latest=decoded)

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        while self.events and now-self.events[0][0] > 60: self.events.popleft()
        span = min(60., max(1., now-self.started))
        teams = {}
        for team in self.team_totals:
            events = [event for event in self.events if event[1] == team]
            teams[str(team)] = {'observed_packets': self.team_totals[team],
                'packets_per_wall_second': len(events)/span, 'payload_bytes_per_wall_second': sum(e[2] for e in events)/span,
                'state_packets_per_wall_second': sum(e[3] == 'state' for e in events)/span,
                'discovery_packets_per_wall_second': sum(e[3] == 'discovery' for e in events)/span}
        streams = {key: dict(value, age_ms=max(0., (now-value['last_at'])*1000)) for key, value in self.streams.items()}
        returns = {key: dict(value, age_ms=max(0., (now-value['last_at'])*1000)) for key, value in self.return_packets.items()}
        return {'teams': teams, 'streams': streams, 'counts': dict(self.counts), 'invalid': dict(self.invalid),
                'return_packets': returns, 'measurement': 'passive local observation; not recipient acceptance or end-to-end loss'}

def budget_projection(budget, packets_per_wall_second, match_seconds_remaining, sim_speed):
    if budget is None or match_seconds_remaining is None or sim_speed is None or sim_speed <= .001: return None
    needed = packets_per_wall_second*max(0, match_seconds_remaining)/sim_speed
    return {'estimated_packets_needed': round(needed), 'remaining_budget': budget,
            'estimated_surplus': round(budget-needed), 'risk': needed > budget,
            'assumption': 'all observed team-channel datagrams count; observed rate/simulation speed stay constant'}
