# Linux / RTX 3070 handoff

Follow [WORKSTATION_PLAN.md](WORKSTATION_PLAN.md) for ordered setup, one robot,
three-player and 3v3 acceptance gates. VisualKick is excluded from the supported
simulation scope; use the default `t1-safe` motion profile.

This runs the `BoosterRobotics/robocup_demo` `sandbox/support_T2` source at commit `2d56d3622ac6dbfdbda96d27a2042eb543f199ae`. `robot_client.cpp`, the SDK API IDs, RPC message schema, team protocol and `game.xml` behavior tree are unchanged. The optional source changes are localized to Studio field-pose ingestion and localization guards, defaulting off. T1 geometry/camera calibration, field size, robot identities and simulation speed limits live in a separate launch profile.

## Transfer and start

Copy the handoff directory to the Linux workstation. Install/open Booster Studio and create one T1 virtual robot. Docker must be running. For real Vision, the host needs an NVIDIA driver and NVIDIA Container Toolkit; `nvidia-smi` and a Docker GPU probe must succeed. The RTX 3070 has 8 GB VRAM: start with one Vision instance and measure memory before enabling all six.

```bash
docker load -i docker-images.tar
cd source/simulation/studio_t1
export STUDIO_CONTAINER=<the-T1-container-name>
export STUDIO_LIBRARY=<Booster-Studio-simulator-library-directory>
bash workstation.sh test
python3 test_scene_package.py
bash workstation.sh install
```

The scene installs separately and preserves the bundled pitch. The guarded custom-field patch keeps backups in the Studio container. On first installation it restarts that container to reload the referee schema. A scene-switch acknowledgement precedes complete initialization; wait for the referee `/health` to be ready and robot-specific interfaces to exist before launching brains.

The launcher mounts the transferred source read-only into the containers. Python adapter and YAML edits take effect after a restart; C++ perception/localization edits require the rebuild below. The images contain the compiled dependencies and binaries, so no machine-local build base is needed after loading the archive.

Startup requests the existing WALK mode and requires a correlated native GetMode confirmation for every selected robot. If a T1 controller refuses the mode API, set that robot to WALK through Studio's controls and retry; the launcher reports the failure instead of pretending that the body is ready.

In a separate terminal, start the camera renderer:

```bash
bash workstation.sh cameras --fps 10
```

It reconstructs Studio's snapshots and renders all six cameras without stepping physics or changing motion policies. RGB, metric depth, CameraInfo and head transforms share the same simulation timestamp. It rejects a model/state dimension mismatch. Restart it after editing/switching the scene. The renderer runs in its own container with `--gpus all`, so it can use NVIDIA EGL even when Studio's managed physics container has no GPU flags. Its native scene-loader support is copied from Studio 1.12.7 and MuJoCo is pinned to 3.10.0; verify compatibility before using a different Studio runtime.

For a gameplay baseline with ideal detections and exact localization:

```bash
bash workstation.sh start
bash workstation.sh status
bash workstation.sh match-start
```

For the repository's actual Vision and original visual localization, first check the exact repository engine:

```bash
bash workstation.sh gpu-check
IMAGE=robocup-t2-studio:gpu-ready bash workstation.sh start \
  --perception external --localization visual \
  --vision-executable /work/install/vision/lib/vision/vision_node \
  --robots robot2
```

After that one instance is healthy, repeat with `--robots robot1 robot2 robot3`.
Only after the three-player gate passes, select all six. Restart the renderer
with the same selection at each stage. There is no ideal-detection or field-pose
injection into the brain in external/visual mode; ground truth remains on a
separate diagnostic topic. `--perception external --localization ideal` tests
real perception with exact localization. `--perception ideal --localization visual`
tests localization against Studio's ideal object/line observations.

Use `bash workstation.sh ready` before a match. It waits up to 60 wall seconds
for asynchronous controller, adapter and sensor initialization. The readiness gate
checks required stream receipt ages, depth, advancing simulation clock and
successful correlated native RPC replies. `match-start` runs the same gate.
For visual localization, also inspect the brain's calibrated pose and compare
it with diagnostic ground truth. GPU inference errors stop the supervised
brain processes; they are not replaced by ideal detections.

`--motion-profile t1-safe` is the supported profile. It disables automatic
VisualKick while applying the existing T1 simulation speed limits. The launcher
does not expose the former `t2-api` profile that enabled unavailable motions.
Native RPC payloads and unsupported responses still reach the brain unchanged.

```bash
bash workstation.sh record 12  # robot2's application domain
bash workstation.sh match-end
bash workstation.sh record-stop
bash workstation.sh stop-all
```

Recording captures canonical sensor/perception/RPC/referee topics and separate ground truth. Replay a bag into an otherwise idle application domain, with simulation time enabled; stop the live adapter for that domain to avoid duplicate clock and sensor publishers. Use `ros2 bag play <bag> --clock` only if not replaying its recorded `/clock` topic as well.

`stop` stops the strategy only; `cameras-stop` stops the renderer and
`record-stop [domain]` flushes a selected recorder (all recorders if omitted).
`stop-all` stops these helpers plus the oversight UI without stopping Studio.
Stopping helpers does not require a running Studio container. Repeating
`oversight` reuses its running UI. Previous strategy logs are copied to
`logs/runs/` before a new strategy run starts. Renderer health/logs persist in
`logs/workstation/cameras/`.

To iterate on perception/localization, edit the transferred source and rebuild from the repository root using the included GPU development image:

```bash
docker build -t robocup-t2-studio:edited -f simulation/studio_t1/Dockerfile.rebuild .
IMAGE=robocup-t2-studio:edited bash simulation/studio_t1/workstation.sh test
```

When launching that edited image, pass GPU access by tagging it with a name ending in `:gpu-ready` or set `GPU_ENABLED=1`. Re-run the one-robot model/camera/localization checks before scaling the edited stack.

To run the model probe with an edited runtime, use
`GPU_CHECK_IMAGE=robocup-t2-studio:edited bash workstation.sh gpu-check`.
The probe and renderer mount the current source instead of reading stale
launcher/model files baked into the original image.

## Exact application routes

Each brain runs without a namespace or command remaps, in its own DDS domain: robot1=11 through robot6=16. The transport adapter alone maps Studio's global robot names to those domains. UUID, API ID, JSON header/body, response status and response body are never translated. The source's current motion path is asynchronous `booster_msgs` topic RPC; it does not call the legacy `booster_interface/RpcService` service. No new HTTP motion API or fake RPC service is introduced.

| T2 application topic | Type / function | Studio transport behind adapter |
|---|---|---|
| `/LocoApiTopicReq` | `booster_msgs/RpcReqMsg` | `/LocoApiTopic/robotNReq` |
| `/LocoApiTopicResp` | `booster_msgs/RpcRespMsg` | `/LocoApiTopic/robotNResp` |
| `/low_state`, `/odometer_state` | original `booster_interface` messages | `/robotN/...` |
| `/fall_down_recovery_state` | original `RawBytesMsg` | `/robotN/...` |
| `/head_pose`, `/head_pose_stamped` | original geometry messages | native pose / snapshot-stamped pose |
| `/boostercamera/head/rgb` | raw RGB `sensor_msgs/Image` | `/robotN/rgbd_camera/rgb/image_raw` |
| `/boostercamera/head/depth` | metric `sensor_msgs/Image` | `/robotN/rgbd_camera/depth/image_raw` |
| `/boostercamera/head/rgb/camera_info` | original runtime intrinsics | `/robotN/rgbd_camera/rgb/camera_info` |
| `/booster_vision/detection`, `/booster_vision/line_segments` | original vision-interface outputs | real Vision or explicit ideal mode |
| `/robocup/game_controller` | branch's v20 typed message | Studio v19 JSON is translated at boundary |
| `/kick_ball` | original kick intent/diagnostic | `/robotN/kick_ball`; motion still uses RPC |

Team discovery and tactical state both broadcast to UDP `10000+team_id`. Tactical state is sent from source UDP `30000+team_id*20+player_id`; this is not a separate state destination port. The simulator broadcast address is loopback broadcast. GameController return packets keep UDP 3939 and the original source behavior. The built-in Studio match/referee HTTP API is only orchestration, not a T2 robot API. The referee's v19-to-v20 conversion is an explicit simulation adaptation.

## Communications oversight

```bash
bash workstation.sh oversight
# Open http://127.0.0.1:8768
```

The dashboard is read-only and bound to localhost. The observer starts automatically with the brains; it captures only the simulated team channels and GameController ports on loopback. It does not bind a team UDP port or consume a robot's packets. CRC, authentication and layout are checked using the unmodified branch C++ decoder. Native RPC payloads continue through the same routes and are only observed for UUID correlation, latency, status codes and unanswered requests.

Views include state/discovery traffic rates and bytes, per-sender sequence gaps/reordering/reboots, reported roles/leader/ball owner, brain-reported teammate liveness, RPC errors/RTT, GameController state/set play/kicking team/half/score, per-player penalties/cautions, raw v19 versus translated v20 messages, remaining budgets, and original RGrt return messages. `logs/workstation/oversight/` holds bounded NDJSON logs and JSON snapshots. Three 20 MB generations per event log are retained; dropped log events are counted.

The configured state rate is 2 Hz per robot, plus 2 Hz discovery. State/discovery payloads are currently 268/37 bytes; the branch cap is 512 bytes. At this rate, the brain raises the configured 600 ms tactical timeout to 1600 ms. Supported state-rate settings are 0.1–20 Hz; changing the configuration requires restarting the brains.

The T2 branch does not throttle team traffic against GameController `messageBudget`. The inspected Studio referee initializes that budget to 12000 but has no packet-accounting decrement path. The dashboard therefore reports observed traffic separately and flags projected exhaustion; it does not claim budget enforcement or tournament compliance. Projection assumes all state/discovery datagrams count and adjusts wall-clock send rate for simulation speed. For three robots at 1× speed, the default 12 team-channel packets/s implies roughly 7200 packets per 600-second match; at 0.2× speed the same wall-clock traffic implies roughly 36000 packets. Validate the competition's actual budget accounting before adopting either estimate.

Capture sequence gaps are not end-to-end packet-loss measurements, and authenticated packets do not prove that a brain accepted them. Receiver liveness is shown from the brain's own logs. The monitor only warns; it does not change strategy, rate limits or robot behavior. Stop the web UI with `bash workstation.sh oversight-stop`.

## Validation limits

The supplied engine and segmentation engine are exactly the repository files. TensorRT serialized engines generally require compatible runtime and GPU architecture. The RTX 3070 model-load test is mandatory; if it fails, the repository alone does not supply a compatible replacement engine or ONNX model. No other detector is silently used. See [NVIDIA engine portability](https://docs.nvidia.com/deeplearning/tensorrt/latest/getting-started/support-matrix.html).

Branch segmentation is disabled by its existing configuration. T1 cameras/body dynamics differ from T2. Studio's native controller has returned `501` for VisualKick in isolation; passing an API route test does not validate physical execution. T2 motion remains a physical-robot validation responsibility. Scoring, all restarts/penalties, complete matches, GPU inference and six-camera throughput require the workstation checks. Read `validation.json` for the exact completed checks and unresolved items.
