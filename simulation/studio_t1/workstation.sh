#!/usr/bin/env bash
# Linux Docker launcher. Studio continues to own physics and stock T1 motion.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd -- "$HERE/../.." && pwd)"
STUDIO_CONTAINER="${STUDIO_CONTAINER:-}"
IMAGE="${IMAGE:-robocup-t2-studio:cpu-ready}"
TASK_CONTAINER="robocup-t2-studio"
command="${1:-status}"; shift || true
if [[ -z "$STUDIO_CONTAINER" && "$command" != test && "$command" != gpu-check && "$command" != oversight && "$command" != oversight-stop ]]; then
  mapfile -t candidates < <(docker ps --format '{{.Names}} {{.Image}}' | awk '/virtual-robot\/virtual-robot/ {print $1}')
  if [[ ${#candidates[@]} != 1 ]]; then
    echo 'Set STUDIO_CONTAINER to the one T1 virtual-robot container opened by Studio.' >&2; exit 2
  fi
  STUDIO_CONTAINER="${candidates[0]}"
fi
case "$command" in
  install)
    [[ -n "${STUDIO_LIBRARY:-}" ]] || { echo 'Set STUDIO_LIBRARY to Studio simulator-library directory.' >&2; exit 2; }
    mkdir -p "$STUDIO_LIBRARY/scenes" "$STUDIO_LIBRARY/assets"
    cp "$HERE/scenes/fcisar_t1_robo_league_3v3.bscene" "$STUDIO_LIBRARY/scenes/"
    cp "$HERE/scenes/robot_t1_fcisar.basset" "$STUDIO_LIBRARY/assets/"
    docker cp "$HERE/patch_studio_field.py" "$STUDIO_CONTAINER:/tmp/fcisar-field-patch.py"
    patch_output="$(docker exec "$STUDIO_CONTAINER" python3 /tmp/fcisar-field-patch.py)"
    printf '%s\n' "$patch_output"
    if [[ "$patch_output" == *'Configured:'* ]]; then
      docker restart "$STUDIO_CONTAINER" >/dev/null
      ready=false
      for ((attempt=0; attempt<30; attempt++)); do
        if docker exec "$STUDIO_CONTAINER" curl -sf --max-time 1 http://127.0.0.1:38383/health >/dev/null; then ready=true; break; fi
        sleep 2
      done
      $ready || { echo 'Studio backend did not recover after field-schema restart.' >&2; exit 1; }
    fi
    docker cp "$HERE/studio_command.py" "$STUDIO_CONTAINER:/tmp/fcisar-studio-command.py"
    docker exec "$STUDIO_CONTAINER" /usr/local/booster_robot/booster_robocup_sim/.venv/bin/python3 \
      /tmp/fcisar-studio-command.py switch_scene '{"scene_key":"fcisar_t1_robo_league_3v3"}'
    ;;
  start)
    if docker container inspect "$TASK_CONTAINER" >/dev/null 2>&1; then
      [[ "$(docker inspect -f '{{index .Config.Labels "task"}}' "$TASK_CONTAINER")" == 'robocup-t2-studio' ]] || { echo 'Container name is owned by another task.' >&2; exit 1; }
      docker rm -f "$TASK_CONTAINER" >/dev/null
    fi
    mkdir -p "$HERE/logs/workstation"
    gpu_args=()
    if [[ "$IMAGE" == *':gpu' || "$IMAGE" == *':gpu-ready' || "${GPU_ENABLED:-0}" == 1 ]]; then gpu_args=(--gpus all); fi
    docker run -d --name "$TASK_CONTAINER" --label task=robocup-t2-studio \
      --network "container:$STUDIO_CONTAINER" --cap-add NET_RAW "${gpu_args[@]}" \
      --mount "type=bind,source=$SOURCE_ROOT,target=/source,readonly" \
      --mount "type=bind,source=$HERE/logs/workstation,target=/work/studio-logs" \
      "$IMAGE" "$@"
    echo 'Container launched. Verify each robot transport report before starting a match.'
    ;;
  match-start|match-end)
    action="${command#match-}"
    if [[ "$action" == start ]]; then
      docker exec "$TASK_CONTAINER" python3 /source/simulation/studio_t1/check_health.py
    fi
    docker exec "$STUDIO_CONTAINER" curl -fsS --max-time 10 -X POST "http://127.0.0.1:38383/match/$action"
    ;;
  cameras)
    [[ -n "${STUDIO_LIBRARY:-}" ]] || { echo 'Set STUDIO_LIBRARY first.' >&2; exit 2; }
    # A separate renderer has NVIDIA exposure even when Studio created its
    # managed T1 container without GPU flags. It only reads physics snapshots.
    docker run --rm --name robocup-studio-cameras --label task=robocup-studio-cameras \
      --gpus all --network "container:$STUDIO_CONTAINER" \
      --mount "type=bind,source=$STUDIO_LIBRARY,target=/opt/booster-studio/library,readonly" \
      robocup-t2-studio:renderer "$@"
    ;;
  stop)
    if docker container inspect "$TASK_CONTAINER" >/dev/null 2>&1; then
      [[ "$(docker inspect -f '{{index .Config.Labels "task"}}' "$TASK_CONTAINER")" == 'robocup-t2-studio' ]] || exit 1
      docker stop "$TASK_CONTAINER"
    fi
    ;;
  oversight)
    mkdir -p "$HERE/logs/workstation"
    docker run -d --name robocup-communications-ui --label task=robocup-communications-ui \
      -p 127.0.0.1:8768:8768 \
      --mount "type=bind,source=$SOURCE_ROOT,target=/source,readonly" \
      --mount "type=bind,source=$HERE/logs/workstation,target=/work/studio-logs,readonly" \
      --entrypoint python3 "$IMAGE" /source/simulation/studio_t1/oversight_server.py \
      --logs /work/studio-logs --host 0.0.0.0 --port 8768
    echo 'Read-only oversight: http://127.0.0.1:8768'
    ;;
  oversight-stop)
    [[ "$(docker inspect -f '{{index .Config.Labels "task"}}' robocup-communications-ui)" == 'robocup-communications-ui' ]] || exit 1
    docker rm -f robocup-communications-ui
    ;;
  status)
    docker exec "$STUDIO_CONTAINER" curl -fsS --max-time 5 http://127.0.0.1:38383/health
    docker ps -a --filter "name=^/$TASK_CONTAINER$" --format '{{.Names}} {{.Status}}'
    for file in "$HERE"/logs/workstation/robot*-transport.json; do [[ ! -f "$file" ]] || cat "$file"; done
    ;;
  test)
    docker run --rm --mount "type=bind,source=$SOURCE_ROOT,target=/source,readonly" --entrypoint bash "$IMAGE" -lc \
      'source /opt/ros/kilted/setup.bash; source /work/install/setup.bash; ctest --test-dir /work/build/brain --output-on-failure -R "_test$" && python3 /source/simulation/studio_t1/test_bridge.py && python3 /source/simulation/studio_t1/test_transport.py && python3 /source/simulation/studio_t1/test_brain_isolation.py && python3 /source/simulation/studio_t1/test_prepare.py && python3 /source/simulation/studio_t1/test_oversight.py && python3 /source/simulation/studio_t1/test_observer_live.py'
    ;;
  gpu-check)
    nvidia-smi
    docker run --rm --gpus all --entrypoint bash robocup-t2-studio:gpu-ready \
      /source/simulation/studio_t1/check_gpu.sh
    ;;
  record)
    domain="${1:-11}"
    [[ "$domain" =~ ^[0-9]+$ ]] || { echo 'record argument must be an application DDS domain.' >&2; exit 2; }
    mkdir -p "$HERE/logs/bags"
    docker run --rm --network "container:$STUDIO_CONTAINER" -e "ROS_DOMAIN_ID=$domain" \
      --mount "type=bind,source=$HERE/logs/bags,target=/records" --entrypoint bash "$IMAGE" -lc \
      'source /opt/ros/kilted/setup.bash; source /work/install/setup.bash; ros2 bag record -o "/records/domain-${ROS_DOMAIN_ID}-$(date +%Y%m%d-%H%M%S)" /clock /boostercamera/head/rgb /boostercamera/head/depth /boostercamera/head/rgb/camera_info /head_pose_stamped /odometer_state /low_state /booster_vision/detection /booster_vision/line_segments /robocup/game_controller /LocoApiTopicReq /LocoApiTopicResp /simulation/ground_truth/robot_pose'
    ;;
  *) echo 'Commands: install cameras start stop status oversight oversight-stop test gpu-check match-start match-end record [domain]' >&2; exit 2 ;;
esac
