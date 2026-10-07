#!/usr/bin/env bash
set -euo pipefail
source /opt/ros/kilted/setup.bash
source /work/install/setup.bash
echo 'Checking the exact support_T2 serialized model; no conversion or substitution.'
nvidia-smi
sha256sum /source/src/vision/model/T2_0804_digua.engine
trtexec=/usr/src/tensorrt/bin/trtexec
[[ -x "$trtexec" ]] || trtexec="$(command -v trtexec)"
"$trtexec" --loadEngine=/source/src/vision/model/T2_0804_digua.engine --warmUp=100 --duration=1
echo 'Model loaded and inference completed. Check one real Vision instance before scaling to six.'
