#!/usr/bin/env bash
set -e
source "/opt/ros/${ROS_DISTRO:-humble}/setup.bash"
source /opt/booster/BoosterRos2/install/setup.bash
export LD_LIBRARY_PATH="/opt/booster/Gait/lib:/opt/booster/Gait/lib/motion:${LD_LIBRARY_PATH:-}"
export FASTRTPS_DEFAULT_PROFILES_FILE=/opt/booster/BoosterRos2/fastdds_profile.xml
export PYTHONUNBUFFERED=1
printf 'SCENE_KEY=football_pitch_T2\nSIM_TRANSPORT=shm\n' > /root/.env
cd /usr/local/booster_robot/booster_robocup_sim
exec .venv/bin/python app_in_container.py --no-camera-relay
