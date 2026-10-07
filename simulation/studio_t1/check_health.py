"""Require live inputs and a successful native RPC before starting a match.

The helpers have no ROS dependency so readiness can be tested without a robot,
simulator or GPU. Receive times use wall time; simulation-clock progress is
tracked separately so a paused simulator cannot pass on repeated messages.
"""
import argparse
import json
import math
from pathlib import Path
import time


DEFAULT_MAX_AGE = 5.0


def note_received(report, topic, now=None):
    now = time.time() if now is None else now
    received = report.setdefault('received', {})
    received[topic] = received.get(topic, 0) + 1
    report.setdefault('last_received_at', {})[topic] = now


def note_clock(report, nanoseconds, now=None):
    now = time.time() if now is None else now
    note_received(report, '/clock', now)
    clock = report.setdefault('clock', {})
    previous = clock.get('time_ns')
    if previous is not None:
        if nanoseconds > previous:
            clock['last_advanced_at'] = now
        elif nanoseconds < previous:
            # A scene reset needs a subsequent forward tick before readiness.
            clock.pop('last_advanced_at', None)
    clock['time_ns'] = nanoseconds


def note_rpc_response(report, event, now=None):
    """Accept only a successful response matched by the existing UUID tracker."""
    if (event and type(event.get('api')) is int and
            type(event.get('status')) is int and event['status'] == 0 and
            event.get('latency_ms') is not None):
        report['last_successful_rpc'] = {
            'api': event['api'], 'uuid': event['uuid'], 'status': 0,
            'received_at': time.time() if now is None else now,
        }


def required_streams(robot, localization):
    streams = [
        ('low_state', f'/{robot}/low_state'),
        ('odometry', f'/{robot}/odometer_state'),
        ('RGB', f'/{robot}/rgbd_camera/rgb/image_raw'),
        ('depth', f'/{robot}/rgbd_camera/depth/image_raw'),
        ('CameraInfo', f'/{robot}/rgbd_camera/rgb/camera_info'),
        ('RPC replies', f'/LocoApiTopic/{robot}Resp'),
        ('perception', '/booster_vision/detection'),
        ('head poses', '/head_pose_stamped'),
        ('referee', '/soccer/game_controller'),
        ('clock', '/clock'),
    ]
    if localization == 'ideal':
        streams.append(('field pose', '/soccer/sim/localization/robot_pose'))
    return streams


def fresh(timestamp, started_at, now, max_age):
    return (type(timestamp) in (int, float) and math.isfinite(timestamp) and
            started_at <= timestamp <= now + 1 and now - timestamp <= max_age)


def health_failures(run, reports, now=None, max_age=DEFAULT_MAX_AGE):
    now = time.time() if now is None else now
    if not isinstance(run, dict):
        return ['Invalid run manifest']
    robots, started_at = run.get('robots'), run.get('started_at')
    if (not isinstance(robots, list) or not robots or
            any(not isinstance(robot, str) or robot not in {f'robot{i}' for i in range(1, 7)} for robot in robots) or
            len(set(robots)) != len(robots) or
            type(started_at) not in (int, float) or not math.isfinite(started_at) or
            started_at > now + 1 or run.get('localization') not in ('ideal', 'visual')):
        return ['Invalid run manifest: robot selection, start time or localization']
    failures = []
    for robot in robots:
        report = reports.get(robot)
        if not isinstance(report, dict):
            failures.append(f'{robot}: adapter not ready or invalid transport report')
            continue
        if report.get('robot') != robot:
            failures.append(f'{robot}: transport report belongs to another robot')
            continue
        if not fresh(report.get('updated_at'), started_at, now, max_age):
            failures.append(f'{robot}: stale transport health')
            continue
        received = report.get('received', {})
        last_received = report.get('last_received_at', {})
        if not isinstance(received, dict) or not isinstance(last_received, dict):
            failures.append(f'{robot}: invalid stream telemetry')
            continue
        for label, topic in required_streams(robot, run['localization']):
            count = received.get(topic, 0)
            if type(count) is not int or count <= 0:
                failures.append(f'{robot}: missing {label}')
            elif not fresh(last_received.get(topic), started_at, now, max_age):
                failures.append(f'{robot}: stale {label} stream')
        clock = report.get('clock', {})
        if not isinstance(clock, dict) or not fresh(clock.get('last_advanced_at'), started_at, now, max_age):
            failures.append(f'{robot}: simulation clock is not advancing')
        rpc = report.get('last_successful_rpc', {})
        if (not isinstance(rpc, dict) or type(rpc.get('api')) is not int or
                type(rpc.get('status')) is not int or rpc['status'] != 0 or not rpc.get('uuid') or
                not fresh(rpc.get('received_at'), started_at, now, max_age)):
            failures.append(f'{robot}: no recent successful correlated native RPC reply')
    return failures


def check_logs(logs, max_age=DEFAULT_MAX_AGE):
    """Read the current startup evidence; incomplete files are not acceptance."""
    try:
        run = json.loads((logs/'run.json').read_text())
    except (OSError, ValueError) as error:
        return None, [f'Run manifest unavailable: {error}']
    reports = {}
    if isinstance(run, dict) and isinstance(run.get('robots'), list):
        for robot in run['robots']:
            if not isinstance(robot, str) or robot not in {f'robot{i}' for i in range(1, 7)}:
                continue
            try:
                reports[robot] = json.loads((logs/f'{robot}-transport.json').read_text())
            except (OSError, ValueError):
                reports[robot] = None
    return run, health_failures(run, reports, max_age=max_age)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--logs', type=Path, default=Path('/work/studio-logs'))
    parser.add_argument('--max-age', type=float, default=DEFAULT_MAX_AGE,
                        help=f'maximum input and native-RPC age in wall seconds (default: {DEFAULT_MAX_AGE:g})')
    parser.add_argument('--wait-timeout', type=float, default=0,
                        help='wait up to this many wall seconds for all readiness gates (default: 0)')
    args = parser.parse_args()
    if not math.isfinite(args.max_age) or args.max_age <= 0:
        parser.error('--max-age must be finite and positive')
    if not math.isfinite(args.wait_timeout) or args.wait_timeout < 0:
        parser.error('--wait-timeout must be finite and nonnegative')
    deadline = time.monotonic() + args.wait_timeout
    while True:
        run, failures = check_logs(args.logs, max_age=args.max_age)
        if not failures:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            prefix = f'Readiness timed out after {args.wait_timeout:g}s:\n' if args.wait_timeout else ''
            raise SystemExit(prefix + '\n'.join(failures))
        time.sleep(min(0.25, remaining))
    print(f"PASS: live camera/depth, perception, head/state, referee, advancing clock "
          f"and successful native RPC streams for {run['robots']}")


if __name__ == '__main__':
    main()
