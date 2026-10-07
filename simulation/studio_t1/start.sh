#!/usr/bin/env bash
set -eo pipefail
source /opt/ros/kilted/setup.bash
source /work/install/setup.bash
unset FASTRTPS_DEFAULT_PROFILES_FILE
export FASTDDS_DEFAULT_PROFILES_FILE=/source/simulation/studio_t1/fastdds.xml
export ROS_DOMAIN_ID=0
cd /source
mkdir -p /work/studio-logs
python3 simulation/studio_t1/bridge.py --ros-args -p use_sim_time:=true > /work/studio-logs/bridge.log 2>&1 &
bridge_pid=$!
trap 'kill "$bridge_pid" 2>/dev/null || true' EXIT
ros2 run brain brain_node --ros-args --params-file src/brain/config/config.yaml \
  --params-file simulation/studio_t1/brain.yaml 2>&1 | tee /work/studio-logs/brain.log
