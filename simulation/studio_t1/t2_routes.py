"""Canonical application routes taken from the support_T2 source, not T1 aliases."""
from booster_msgs.msg import RpcReqMsg, RpcRespMsg
from booster_interface.msg import Odometer, LowState, RawBytesMsg
from geometry_msgs.msg import Pose
from sensor_msgs.msg import Image, CameraInfo, CompressedImage, Imu, JointState
from rosgraph_msgs.msg import Clock

# Each tuple is (simulator suffix, canonical T2 topic, ROS message type).
SENSOR_ROUTES = (
    ('/odometer_state', '/odometer_state', Odometer),
    ('/low_state', '/low_state', LowState),
    ('/head_pose', '/head_pose', Pose),
    ('/fall_down_recovery_state', '/fall_down_recovery_state', RawBytesMsg),
    ('/rgbd_camera/rgb/image_raw', '/boostercamera/head/rgb', Image),
    ('/rgbd_camera/rgb/image_compressed', '/boostercamera/head/rgb/compressed', CompressedImage),
    ('/rgbd_camera/rgb/camera_info', '/boostercamera/head/rgb/camera_info', CameraInfo),
    ('/rgbd_camera/depth/image_raw', '/boostercamera/head/depth', Image),
    ('/rgbd_camera/depth/camera_info', '/boostercamera/head/depth/camera_info', CameraInfo),
    ('/imu/data', '/imu/data', Imu),
    ('/joint_states', '/joint_states', JointState),
)
RPC_REQUEST = '/LocoApiTopicReq'
RPC_RESPONSE = '/LocoApiTopicResp'

def rpc_topics(robot):
    return (f'/LocoApiTopic/{robot}Req', f'/LocoApiTopic/{robot}Resp') if robot else (RPC_REQUEST, RPC_RESPONSE)
