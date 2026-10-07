"""Require fresh, usable robot streams before starting a match."""
import json
from pathlib import Path
import time

ROOT = Path('/work/studio-logs')
run = json.loads((ROOT/'run.json').read_text())
failures = []
for robot in run['robots']:
    path = ROOT/f'{robot}-transport.json'
    if not path.exists():
        failures.append(f'{robot}: adapter not ready'); continue
    report = json.loads(path.read_text())
    if report.get('updated_at', 0) < run['started_at'] or time.time()-report.get('updated_at', 0) > 20:
        failures.append(f'{robot}: stale transport health'); continue
    for suffix, topic in (('low_state', f'/{robot}/low_state'),
                          ('odometry', f'/{robot}/odometer_state'),
                          ('RGB', f'/{robot}/rgbd_camera/rgb/image_raw'),
                          ('CameraInfo', f'/{robot}/rgbd_camera/rgb/camera_info'),
                          ('RPC replies', f'/LocoApiTopic/{robot}Resp'),
                          ('perception', '/booster_vision/detection')):
        if report['received'].get(topic, 0) == 0: failures.append(f'{robot}: missing {suffix}')
    if report.get('head_pose_frames', 0) == 0: failures.append(f'{robot}: missing synchronized head poses')
    if 'referee_state' not in report: failures.append(f'{robot}: missing referee')
    if run['localization'] == 'ideal' and report.get('localization_frames', 0) == 0:
        failures.append(f'{robot}: missing field pose')
if failures:
    raise SystemExit('\n'.join(failures))
print(f"PASS: fresh camera, perception, state, referee and RPC streams for {run['robots']}")
