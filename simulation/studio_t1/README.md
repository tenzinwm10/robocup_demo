# T2 strategy on Booster Studio's virtual T1

**For the Linux/RTX 3070 workstation use [WORKSTATION.md](WORKSTATION.md).** It documents the portable images, canonical T2 RPC/sensor routes, isolated DDS domains, snapshot camera renderer, actual repository Vision mode, health checks and validation limits. The Windows instructions below describe the earlier development runs.

Communications and referee visibility are documented in [OVERSIGHT.md](OVERSIGHT.md), including the localhost dashboard and the important distinction between reported budgets and actual enforcement.

## Full 3v3 scene

The generated `fcisar_t1_robo_league_3v3.bscene` uses six stock T1 bodies and controllers with a 22.003 × 14.126 m field. Physics, RGB cameras, ideal detections and the match referee run inside Studio. The original T2 behavior tree runs once per robot, with separate motion RPC channels and team communication. No movement policy is trained or replaced.

On this machine, open the T1 virtual robot, then run from the repository root:

```powershell
docker build -t robocup-t1-demo:multi -f simulation/studio_t1/Dockerfile.multi .
./simulation/studio_t1/RunMulti.ps1 -InstallScene
```

The image currently depends on the machine-local `robocup-t1-demo:prepared` base. Scene packages are generated with `build_scene.py --library "$env:APPDATA/Booster Studio/simulator-library"`; Python needs Pillow. The installer retains the bundled field, adds a stock-model asset variant without competing detection extensions, and applies guarded referee field/schema hooks with backups. A first installation restarts the virtual-robot container so the new schema takes effect. Studio runtime updates may require reapplying or adapting those hooks.

Start/end the match in Studio's Game controller panel, or through its existing container API:

```powershell
docker exec rytls-t1 curl -s -X POST http://127.0.0.1:38383/match/start
docker exec rytls-t1 curl -s -X POST http://127.0.0.1:38383/match/end
docker stop robocup-t1-multi
```

`logs/multi/multi-health.json` reports observations, nonzero movement requests, controller replies, physical displacement and referee states per robot. A launched container alone does not establish successful startup. Restart `RunMulti.ps1` after closing/reopening the virtual robot.

Validation on 2026-10-06: the scene loads six bodies (187 position coordinates, 138 actuators, six cameras); all eleven existing C++ tests and four bridge tests pass. The live retry remained up for seven minutes, reached INITIAL/READY/SET/PLAY, received over 1,300 detection frames per robot, and recorded movement requests and 4.9–9.3 m displacement for four robots. Both teams received live teammate status. One earlier run had a bridge SIGSEGV with no Python traceback; staggered participant startup avoided it on the retry, but the cause and long-run stability remain unproven. Scoring, kicking effectiveness, every penalty/set play and complete-match behavior are not yet validated.

This is an ideal-perception baseline. Studio supplies object positions and exact field poses, so it does not yet validate changes to the real T2 neural perception or visual localization. The cameras and field-line feeds provide inputs for that next integration. Disable `simulation.studio_localization` to exercise the original visual localization, and replace the ideal-detection bridge with the intended perception node to test perception. The original Thor TensorRT engine cannot be treated as a portable laptop inference model. T1 camera geometry and dynamics also differ from the physical T2.

## Earlier single-robot profile

This profile runs the `sandbox/support_T2` brain with Studio's single-T1 Soccer Field. Studio owns physics, robot motion, cameras, simulated object detections and the referee. The local `robocup-t1-demo:prepared` image runs the Kilted brain alongside the Humble simulator, sharing its network namespace and using UDP DDS. No separate physics simulator is launched.

## Start and stop

1. Open **T1 Test** in Booster Studio, select **Soccer Field**, and set the robot to **WALK**.
2. Run `./Run.ps1` from this directory in PowerShell. It requires Docker Desktop and the prepared image on this machine. It replaces only the demo's labelled container and retains logs in `logs/`.
3. Open Studio's **Game controller** panel and click **Start**. Follow the robot using its follow-camera control. It first walks to its READY position, holds during SET and acts during PLAY.
4. End the match in Studio. Stop the strategy container with `docker stop robocup-t1-live`.

After closing/reopening the virtual robot, rerun `Run.ps1`: Docker creates a new simulator network namespace, so an old strategy container cannot simply reconnect. Use `-StudioContainer <name>` if the T1 container name changes from `rytls-t1`.

## Adaptations and limits

- `bridge.py` converts Studio's `vision_msgs/Detection2DArray` objects and paired field-line poses into this branch's vision messages. These are simulated detections, not neural-model inference.
- The optional brain parameter `simulation.studio_localization` uses Studio's field pose, requires simulation time, and defaults off outside this profile. This bypasses realistic localization errors.
- The bridge normalizes wall-time head-pose stamps to the simulation clock at receipt. This provides approximate synchronization, not a measured hardware latency model.
- Studio's captured 198-byte referee v19 packets are translated to the branch's v20 message schema. Unused player slots receive the v20 substitute code. This supports the tested normal-play sequence; equivalence for every set play and penalty has not been established.
- T1 geometry and movement limits replace the T2 settings. Automatic VisualKick is disabled. The original brain's movement and kick-intent paths remain active.
- The prepared Docker image is machine-local, not published. It contains the six-package build, rebuilt brain and `ros-kilted-vision-msgs`. Code edits require rebuilding the brain; changing Python/YAML profiles takes effect after restarting the strategy container.

## Validation

The brain rebuilt successfully. All eleven existing C++ tests passed. The earlier single-robot run demonstrated autonomous field entry, READY/SET/PLAY transitions, thousands of ball observations and a reported final score of **1–0**. This was an unopposed demo, not a realistic T2 kicking test. A 30-second read-only observation recorded motion requests, SDK responses and approximately 2.6 m of robot translation; it occurred during READY/END and recorded no kick requests.

The laptop's single-robot simulator speed was approximately 0.2–0.5 times real time, varying with workload. A larger workstation needs benchmarking with the intended robot count. The full-scene launcher supplies separate robot topic namespaces, SDK command channels, unique player IDs, per-agent bridges and team communication.

## Larger field

The installed `football_pitch_T1.bscene` is a ZIP scene package with `manifest.json`, `extensions.json` and a nested MuJoCo model. `build_scene.py` derives a separate competition scene and preserves the bundled scene.

The generated field geometry, markings, referee geometry, detections and brain map use the branch's `FD_ROBOLEAGUE`: 22.003 × 14.126 m with its existing goal and penalty-area dimensions. Acceptance of those overall dimensions does not establish compliance with every competition marking or referee rule; compare the complete competition drawing before relying on this scene for rule validation.
