"""Local read-only dashboard. Reads observer files; never connects to a robot API."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from oversight_metrics import budget_projection, STATE_NAMES, SET_PLAY_NAMES

HERE = Path(__file__).resolve().parent

def read_json(path):
    try: return json.loads(path.read_text())
    except (OSError, ValueError): return None

def tail(path, size=65536):
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2); stream.seek(max(0, stream.tell()-size))
            return stream.read().decode('utf-8', errors='replace')
    except OSError: return ''

def summary(logs):
    now = time.time(); run = read_json(logs/'run.json') or {}
    observer = read_json(logs/'oversight/communications.json') or {}
    robots = []
    for name in run.get('robots', [f'robot{i}' for i in range(1, 7)]):
        report = read_json(logs/f'{name}-transport.json') or {}
        text = tail(logs/f'brain-{name}.log')
        peers = re.findall(r'Teammates: OnField:\s*(\d+).*?Alive:\s*(\d+)', text)
        robots.append({'name': name, 'report': report, 'age_s': now-report['updated_at'] if 'updated_at' in report else None,
                       'brain_teammates': {'on_field': int(peers[-1][0]), 'alive': int(peers[-1][1])} if peers else None})
    referee = observer.get('referee') or {}
    typed = referee.get('translated') or {}
    teams = []
    for team in typed.get('teams', []):
        traffic = observer.get('radio', {}).get('teams', {}).get(str(team['team_number']), {})
        projection = budget_projection(team['message_budget'], traffic.get('packets_per_wall_second', 0),
                                       typed.get('secs_remaining'), observer.get('sim_speed'))
        teams.append(dict(team, traffic=traffic, projection=projection))
    alerts = []
    if not observer or now-observer.get('updated_at', 0) > 5: alerts.append('Observer is stopped or stale; traffic metrics are not current.')
    if observer.get('capture_status') != 'capturing': alerts.append('Passive radio capture is unavailable: '+observer.get('capture_error', 'waiting for capture'))
    if observer.get('decoder_error'): alerts.append('Original packet decoder is unavailable; authentication has not been checked.')
    if referee and now-referee.get('received_at', 0) > 3: alerts.append('GameController feed is stale (>3 wall seconds).')
    if observer.get('referee_errors', 0): alerts.append('GameController decode errors have been observed.')
    if observer.get('radio', {}).get('invalid'): alerts.append('Invalid/unauthenticated team packets have been observed.')
    if observer.get('radio', {}).get('counts', {}).get('oversize'): alerts.append('Team payload exceeded the branch 512-byte cap.')
    timeout = observer.get('limits', {}).get('effective_tactical_timeout_ms', 1600)
    configured = observer.get('robot_configuration', {})
    if observer and now-observer.get('updated_at', 0) <= 5:
        for key, stream in observer.get('radio', {}).get('streams', {}).items():
            team = stream.get('latest', {}).get('team')
            team_timeout = max([config['effective_tactical_timeout_ms'] for config in configured.values() if config['team'] == team] or [timeout])
            if key.endswith('/state') and stream.get('age_ms', 0) > team_timeout:
                alerts.append(f'{key}: observed state is older than the effective tactical timeout.')
    for team in teams:
        if team['message_budget'] == 0: alerts.append(f"Team {team['team_number']}: GameController budget is exhausted.")
        if team['projection'] and team['projection']['risk']:
            alerts.append(f"Team {team['team_number']}: projected traffic exceeds the reported remaining budget.")
    for robot in robots:
        rpc = robot['report'].get('rpc', {})
        if any(key != '0' and value for key, value in rpc.get('statuses', {}).items()):
            alerts.append(f"{robot['name']}: native RPC error/unsupported replies observed.")
        if rpc.get('unanswered_after_10s'): alerts.append(f"{robot['name']}: unanswered RPC requests older than 10 seconds.")
    return {'updated_at': now, 'run': run, 'observer': observer, 'robots': robots, 'teams': teams, 'alerts': alerts,
            'state_name': STATE_NAMES[typed['state']] if typed.get('state') in range(5) else 'WAITING',
            'set_play_name': SET_PLAY_NAMES[typed['set_play']] if typed.get('set_play') in range(7) else 'UNKNOWN'}

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--logs', required=True)
    parser.add_argument('--host', default='127.0.0.1'); parser.add_argument('--port', type=int, default=8768)
    args = parser.parse_args(); logs = Path(args.logs)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/': payload = (HERE/'oversight.html').read_bytes(); kind = 'text/html; charset=utf-8'
            elif path == '/api/summary': payload = json.dumps(summary(logs), allow_nan=False).encode(); kind = 'application/json'
            elif path == '/api/referee': payload = json.dumps((read_json(logs/'oversight/communications.json') or {}).get('referee'), allow_nan=False).encode(); kind = 'application/json'
            else: self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type', kind); self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)
        def log_message(self, *_): pass
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'Oversight: http://{args.host}:{args.port} (read-only)', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__ == '__main__': main()
