"""Render RGB/depth from Studio's authoritative snapshots, without stepping physics.

Run in Studio's Python environment, which supplies its scene loader and MuJoCo.
The Studio desktop currently omits its native camera model for multi-robot scenes.
This read-only renderer supplies the missing camera streams for real T2 Vision.
"""
import argparse
import json
import math
import os
from pathlib import Path
import signal
import sys
import threading
import time
import numpy as np
os.environ.setdefault('MUJOCO_GL', 'egl')
import mujoco
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CameraInfo
from geometry_msgs.msg import PoseStamped
sys.path.insert(0, '/usr/local/booster_robot/booster_robocup_sim')
from core.scene_source import load_scene
from core.physics_transport import PhysicsTransportConfig
from transport.websocket.physics_ws import PhysicsWebSocketClient

def quaternion_from_rotation(matrix):
    quaternion = np.zeros(4)
    mujoco.mju_mat2Quat(quaternion, matrix.reshape(9))
    return quaternion  # MuJoCo w,x,y,z.

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scene', default='/opt/booster-studio/library/scenes/fcisar_t1_robo_league_3v3.bscene')
    parser.add_argument('--robots', nargs='+', default=[f'robot{i}' for i in range(1, 7)])
    parser.add_argument('--width', type=int, default=320)
    parser.add_argument('--height', type=int, default=240)
    parser.add_argument('--fps', type=float, default=10.)
    parser.add_argument('--seconds', type=float, default=0.)
    parser.add_argument('--offline-test', action='store_true')
    parser.add_argument('--health', default='/tmp/fcisar-camera-health.json')
    args = parser.parse_args()
    if args.width <= 0 or args.height <= 0 or not 0 < args.fps <= 60:
        parser.error('Positive dimensions and FPS in (0,60] required')
    loaded = load_scene(args.scene)
    model, data = loaded.model, mujoco.MjData(loaded.model)
    model.vis.global_.offwidth = max(model.vis.global_.offwidth, args.width)
    model.vis.global_.offheight = max(model.vis.global_.offheight, args.height)
    renderer = mujoco.Renderer(model, height=args.height, width=args.width)
    rclpy.init(args=[]); node = Node('studio_snapshot_cameras')
    cameras = []
    for camera_id in range(model.ncam):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_CAMERA, camera_id)
        robot = next((robot for robot in args.robots if name.startswith(robot+'_')), None)
        if not robot: continue
        root_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, robot+'_Trunk')
        if root_id < 0: raise RuntimeError(f'Missing robot root for camera {name}')
        pubs = {key: node.create_publisher(typ, f'/{robot}'+suffix, 10) for key, typ, suffix in (
            ('rgb', Image, '/rgbd_camera/rgb/image_raw'), ('depth', Image, '/rgbd_camera/depth/image_raw'),
            ('info', CameraInfo, '/rgbd_camera/rgb/camera_info'), ('depth_info', CameraInfo, '/rgbd_camera/depth/camera_info'),
            ('head', PoseStamped, '/simulation/head_pose_stamped'))}
        cameras.append((camera_id, robot, root_id, pubs))
    if len(cameras) != len(args.robots): raise RuntimeError('Selected robot camera names do not match this scene')
    latest, lock = {}, threading.Lock()
    def state(snapshot):
        with lock: latest['snapshot'] = snapshot
    client = None
    if not args.offline_test:
        client = PhysicsWebSocketClient(PhysicsTransportConfig(host='127.0.0.1', port=8788), owner='fcisar-camera-renderer')
        client.subscribe_physics_state(state); client.connect()
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    health = {'result': 'WAITING_FOR_STATE', 'frames': {}, 'physics_steps': 0,
              'width': args.width, 'height': args.height, 'camera_fps_target': args.fps}
    start, next_frame, last_write, last_snapshot = time.monotonic(), 0., 0., None
    try:
        while running and (args.seconds <= 0 or time.monotonic()-start < args.seconds):
            now = time.monotonic()
            if now < next_frame:
                time.sleep(min(.02, next_frame-now)); continue
            next_frame = now+1/args.fps
            with lock: snapshot = latest.get('snapshot')
            if args.offline_test:
                data.time = 1.
            elif snapshot is None:
                if now-start > 120: raise RuntimeError('No Studio physics snapshot received; check scene readiness and pause state')
                continue
            else:
                if len(snapshot.qpos) != model.nq or len(snapshot.qvel) != model.nv:
                    raise RuntimeError('Active Studio scene differs from camera model; reinstall/reset the intended scene')
                if snapshot.frame_id == last_snapshot: continue
                last_snapshot = snapshot.frame_id
                data.qpos[:] = snapshot.qpos; data.qvel[:] = snapshot.qvel
                data.ctrl[:] = snapshot.ctrl; data.time = snapshot.time
                health['simulation_time'] = snapshot.time
            # Kinematics only: no mj_step, integration, control or motion-policy calls.
            mujoco.mj_kinematics(model, data); mujoco.mj_camlight(model, data)
            stamp_sec = int(data.time); stamp_ns = int((data.time-stamp_sec)*1e9)
            for camera_id, robot, root_id, pubs in cameras:
                renderer.disable_depth_rendering(); renderer.update_scene(data, camera=camera_id)
                rgb = renderer.render().copy()
                renderer.enable_depth_rendering(); depth = renderer.render().astype('<f4')
                info = CameraInfo(); info.header.stamp.sec = stamp_sec; info.header.stamp.nanosec = stamp_ns
                info.header.frame_id = robot+'/camera_optical_frame'
                info.width = args.width; info.height = args.height
                focal = .5*args.height/math.tan(math.radians(float(model.cam_fovy[camera_id]))/2)
                info.k = [focal, 0., args.width/2, 0., focal, args.height/2, 0., 0., 1.]
                info.p = [focal, 0., args.width/2, 0., 0., focal, args.height/2, 0., 0., 0., 1., 0.]
                info.distortion_model = 'plumb_bob'; info.d = [0.]*5
                for key, pixels, encoding, channels in (('rgb', rgb, 'rgb8', 3), ('depth', depth, '32FC1', 4)):
                    image = Image(); image.header = info.header; image.width = args.width; image.height = args.height
                    image.encoding = encoding; image.step = args.width*channels; image.data = pixels.tobytes()
                    pubs[key].publish(image)
                pubs['info'].publish(info); pubs['depth_info'].publish(info)
                # Use the exact camera snapshot to construct the branch's head pose.
                # The base origin is ground level at trunk XY, with planar body yaw.
                root_rotation = data.xmat[root_id].reshape(3, 3)
                yaw = math.atan2(root_rotation[1, 0], root_rotation[0, 0])
                c, s = math.cos(yaw), math.sin(yaw)
                to_base = np.array([[c, s, 0.], [-s, c, 0.], [0., 0., 1.]])
                optical_world = data.cam_xmat[camera_id].reshape(3, 3) @ np.diag([1., -1., -1.])
                camera_to_head = np.array([[0., 0., 1.], [-1., 0., 0.], [0., -1., 0.]])
                head_rotation = to_base @ optical_world @ camera_to_head.T
                origin = np.array([data.xpos[root_id, 0], data.xpos[root_id, 1], 0.])
                head_translation = to_base @ (data.cam_xpos[camera_id]-origin) - head_rotation @ np.array([.0613, 0., .108])
                head = PoseStamped(); head.header = info.header; head.header.frame_id = robot+'/base_link'
                head.pose.position.x, head.pose.position.y, head.pose.position.z = map(float, head_translation)
                w, x, y, z = quaternion_from_rotation(head_rotation)
                head.pose.orientation.w, head.pose.orientation.x = float(w), float(x)
                head.pose.orientation.y, head.pose.orientation.z = float(y), float(z)
                pubs['head'].publish(head)
                health['frames'][robot] = health['frames'].get(robot, 0)+1
                health['rgb_range'] = [int(rgb.min()), int(rgb.max())]
                health['depth_range_m'] = [float(depth.min()), float(depth.max())]
                health['result'] = 'PASS_RENDERING'
            if now-last_write >= 2:
                health['updated_at'] = time.time(); Path(args.health).write_text(json.dumps(health, indent=2)); last_write = now
            if args.offline_test: break
        Path(args.health).write_text(json.dumps(health, indent=2)); print(json.dumps(health, indent=2))
        if not health['frames']: raise RuntimeError('No camera frames published')
    finally:
        if client: client.disconnect()
        renderer.close(); node.destroy_node(); rclpy.shutdown()

if __name__ == '__main__': main()
