"""Isolated six-camera test using Studio's actual WebSocket serializer/server.

No physics is stepped; known snapshots are sent through the real transport.
"""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CameraInfo
from geometry_msgs.msg import PoseStamped
sys.path.insert(0, '/usr/local/booster_robot/booster_robocup_sim')
from core.scene_source import load_scene
from core.physics_transport import PhysicsTransportConfig, PhysicsStateData
from transport.websocket.physics_ws import PhysicsWebSocketServer

def main():
    scene = '/opt/booster-studio/library/scenes/fcisar_t1_robo_league_3v3.bscene'
    loaded = load_scene(scene); model = loaded.model
    server = PhysicsWebSocketServer(PhysicsTransportConfig(host='127.0.0.1', port=8788))
    server.start()
    rclpy.init(args=[]); node = Node('camera_stream_test')
    samples, subscriptions = {}, []
    for robot in [f'robot{i}' for i in range(1, 7)]:
        for key, typ, suffix in (('rgb', Image, '/rgbd_camera/rgb/image_raw'),
                                ('depth', Image, '/rgbd_camera/depth/image_raw'),
                                ('info', CameraInfo, '/rgbd_camera/rgb/camera_info'),
                                ('head', PoseStamped, '/simulation/head_pose_stamped')):
            def receive(message, r=robot, k=key):
                stamp = (message.header.stamp.sec, message.header.stamp.nanosec)
                samples.setdefault(r, {}).setdefault(stamp, {})[k] = message
            subscriptions.append(node.create_subscription(typ, f'/{robot}'+suffix, receive, 10))
    with tempfile.TemporaryDirectory() as temporary:
        output = open(Path(temporary)/'renderer.log', 'w')
        process = subprocess.Popen([sys.executable, '/source/simulation/studio_t1/render_cameras.py',
            '--fps', '2', '--seconds', '25', '--health', str(Path(temporary)/'health.json')],
            stdout=output, stderr=subprocess.STDOUT)
        try:
            start, frame = time.monotonic(), 0
            while time.monotonic()-start < 35:
                frame += 1
                server.publish_physics_state(PhysicsStateData(model.qpos0.copy(),
                    np.zeros(model.nv), np.zeros(model.nu), 100.+frame*.02, frame))
                rclpy.spin_once(node, timeout_sec=.01)
                complete = {robot: [(stamp, streams) for stamp, streams in frames.items()
                            if set(streams) == {'rgb', 'depth', 'info', 'head'}]
                            for robot, frames in samples.items()}
                if len(complete) == 6 and all(frames for frames in complete.values()): break
                if process.poll() is not None:
                    output.flush(); raise RuntimeError(Path(temporary, 'renderer.log').read_text())
            assert len(complete) == 6 and all(complete.values()), 'Missing synchronized robot camera streams'
            for robot, frames in complete.items():
                stamp, streams = frames[-1]
                rgb, depth, info, head = [streams[key] for key in ('rgb', 'depth', 'info', 'head')]
                assert rgb.encoding == 'rgb8' and depth.encoding == '32FC1'
                assert len(rgb.data) == 320*240*3 and len(depth.data) == 320*240*4
                assert 216. < info.k[0] < 217. and info.width == 320 and info.height == 240
                pixels = np.frombuffer(bytes(rgb.data), dtype=np.uint8)
                distances = np.frombuffer(bytes(depth.data), dtype='<f4')
                assert pixels.max() > pixels.min() and np.isfinite(distances).all() and distances.min() > 0
                assert head.pose.position.z > .8
                orientation = head.pose.orientation
                assert abs(sum(value*value for value in (orientation.x, orientation.y, orientation.z, orientation.w))-1) < 1e-6
            print(json.dumps({'result': 'PASS', 'robots': 6, 'streams': ['rgb', 'depth', 'camera_info', 'head_pose'],
                              'shared_snapshot_timestamps': True, 'physics_steps': 0}))
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
            output.close(); node.destroy_node(); rclpy.shutdown(); server.stop()

if __name__ == '__main__': main()
