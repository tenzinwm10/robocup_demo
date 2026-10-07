#!/usr/bin/env bash
set -eo pipefail
source /opt/ros/kilted/setup.bash
source /work/install/setup.bash
unset FASTRTPS_DEFAULT_PROFILES_FILE
export FASTDDS_DEFAULT_PROFILES_FILE=/source/simulation/studio_t1/fastdds.xml
export ROS_DOMAIN_ID=0
cd /source
exec python3 simulation/studio_t1/multi_launch.py "$@"
