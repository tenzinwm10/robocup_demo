# FC ISAR supported simulation completion plan

Run the existing `BoosterRobotics/robocup_demo` `sandbox/support_T2` application
in Booster Studio on the Ubuntu NVIDIA workstation. Preserve its brain,
behavior tree, robot RPC payloads, team protocol and perception models. Complete
the simulation using the motion available in the installed firmware.

**VisualKick is outside scope.** Keep `--motion-profile t1-safe`, which disables
automatic VisualKick. Its absence is not a failed test or a reason to obtain
new firmware, train a motion policy or change the strategy's RPC implementation.
Referee scores and state transitions can be exercised without VisualKick.

Preparation and CPU checks can be completed on the laptop. GPU inference,
live rendered perception and match acceptance require the workstation. Mark
those results pending until their evidence is recorded.

## Fixed baseline

| Item | Baseline |
|---|---|
| Source | `tenzinwm10/robocup_demo`, `codex/studio-t1-demo` |
| Upstream | `sandbox/support_T2`, `2d56d3622ac6dbfdbda96d27a2042eb543f199ae` |
| Runtime handoff | Private release `workstation-2026-10-07` |
| Packaged scene | Six stock T1 bodies, field 22.003 by 14.126 metres |
| Application identities | robot1 to robot3: team 1; robot4 to robot6: team 2 |
| Roles | robot1 and robot4 goalkeepers; remaining players strikers |
| Application DDS domains | robot1 to robot6: 11 to 16; Studio: 0 |
| Launch progression | robot2; robot1 robot2 robot3; all six |
| Motion | Existing WALK, velocity and head control; automatic VisualKick off |

Selecting fewer brains does not remove the other physical bodies from the
six-body scene. The packaged T1 scene exercises the T2 application interfaces;
its body dynamics and camera geometry are simulation approximations. Native
T2 walking/head/velocity already passed short probes. A future native T2 scene
profile can improve geometry fidelity without making VisualKick a dependency.
That scene profile is not supplied by the current six-T1 launcher.

## 1 Prepare the workstation

Install GitHub CLI, unzip, Docker Engine and Booster Studio. Check the NVIDIA
driver and container GPU access before running inference. Use the
[NVIDIA Container Toolkit installation guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
for the host's Docker configuration. Configure/restart Docker before opening
Studio's virtual robot, since a daemon restart interrupts running containers.

Download the release while signed in as `tenzinwm10`. The release's
`SUPPORTED_START_HERE.md` identifies the current source package and checksums.
Keep the original release assets for reproducibility; the four Docker parts
are shared by the updated source package.

```bash
gh auth login
mkdir -p ~/robocup-t2-studio-handoff
gh release download workstation-2026-10-07 \
  --repo tenzinwm10/robocup-t2-studio-handoff \
  --dir ~/robocup-t2-studio-handoff --skip-existing
cd ~/robocup-t2-studio-handoff
sha256sum -c SUPPORTED_SHA256SUMS
cat docker-images.tar.part01 docker-images.tar.part02 \
    docker-images.tar.part03 docker-images.tar.part04 > docker-images.tar
printf '%s  %s\n' \
  b7ca7985aa2e01dc73679cf0956d353294cfa4c2d9ef721d14b96e5b5f464766 \
  docker-images.tar | sha256sum -c -
docker load -i docker-images.tar
unzip source-supported.zip -d source-supported
cd source-supported/simulation/studio_t1
bash workstation.sh test
python3 test_scene_package.py
```

Allow space for both the 7 GB parts and 7 GB joined archive, Docker layers,
source and recordings. A 50 GB free-space reserve is a planning allowance;
check `df -h .` and `docker system df` against the actual machine. The unpacked
GPU image alone reports roughly 21 GB before shared-layer deduplication.

Open one T1 virtual robot in Studio and identify its running container and
the simulator-library directory. Set actual paths rather than copying the
placeholders below:

```bash
export STUDIO_CONTAINER=<Studio-virtual-robot-container>
export STUDIO_LIBRARY=<absolute-simulator-library-directory>
bash workstation.sh install
bash workstation.sh status
```

Pass when checksums and CPU tests pass, the intended scene is loaded, the
referee health endpoint responds and selected native controller interfaces
are present. Wait for initialization after scene switching. Keep Studio's
physics running so the simulation clock advances.

## 2 Establish the exact model and camera path

```bash
bash workstation.sh gpu-check
```

This loads and executes the repository's serialized detection engine. If it
fails, save the full error, engine SHA-256, driver, CUDA and TensorRT versions.
Continue the explicit ideal-perception baseline while resolving the original
model's compatibility with the workstation. Rebuilding C++ for Ampere does
not convert a serialized engine. Obtain a compatible export of the same model
or its original training export before rebuilding an engine; do not silently
swap detectors. NVIDIA documents the platform/version/hardware limits in its
[TensorRT support matrix](https://docs.nvidia.com/deeplearning/tensorrt/latest/getting-started/support-matrix.html).

In a separate terminal with the same environment and working directory:

```bash
bash workstation.sh cameras --robots robot2 --fps 10
```

The renderer reads Studio snapshots and publishes RGB, metric depth,
CameraInfo and snapshot head poses; it does not advance physics. Its health
and log persist in `logs/workstation/cameras/`. Keep the robot selection the
same as the strategy selection and restart rendering after switching scenes.

## 3 Complete the one robot baseline

Start the supported motion and ideal sensor baseline first:

```bash
bash workstation.sh start --robots robot2 \
  --perception ideal --localization ideal --motion-profile t1-safe
bash workstation.sh oversight
bash workstation.sh ready
bash workstation.sh match-start
```

Use `http://127.0.0.1:8768` to observe native RPC results, team traffic and raw
versus translated GameController data. Record robot2's application domain in
another terminal:

```bash
bash workstation.sh record 12
```

Pass when INITIAL, READY, SET, PLAY and END are represented by the referee and
handled by the brain, supported native RPCs succeed, movement is visible,
camera/state streams remain live and stopping ends the strategy cleanly.
Validate legal referee score changes and representative penalty/unpenalty and
restart transitions through Studio. Do not require an automatic visual kick.
An ideal-perception pass demonstrates strategy/transport integration only.

## 4 Isolate perception and localization

End the previous match and stop its strategy before each configuration:

```bash
bash workstation.sh match-end
bash workstation.sh record-stop
bash workstation.sh stop
```

| Configuration | What it establishes |
|---|---|
| ideal perception and ideal localization | Strategy, supported motion, transport and referee |
| external perception and ideal localization | Actual Vision on rendered RGB/depth, with pose held exact |
| ideal perception and visual localization | Original localization with ideal object/line inputs |
| external perception and visual localization | Actual Vision and original localization together |

Start the real-perception isolation configuration:

```bash
IMAGE=robocup-t2-studio:gpu-ready bash workstation.sh start \
  --robots robot2 --perception external --localization ideal \
  --motion-profile t1-safe \
  --vision-executable /work/install/vision/lib/vision/vision_node
bash workstation.sh ready
```

After it passes, repeat with `--perception ideal --localization visual`
using the CPU image and no Vision executable. Then repeat the GPU command
with `--localization visual`. Run `ready` before `match-start` each time.
Inspect the brain's calibrated pose against the separate diagnostic ground
truth, applying the documented team-side coordinate convention. The readiness
gate proves stream delivery and native replies; pose accuracy remains a
separate acceptance check.

Upstream segmentation is disabled. Do not assume real Vision emits field
lines or that visual localization will calibrate just because image topics
are active. If the combined configuration fails calibration, retain the
working external/ideal configuration as the declared baseline and record
the missing landmark/line inputs or calibration error as the remaining
localization task. Do not inject ideal pose into a run labelled visual.

Initial engineering targets are at least 5 RGB/detection frames per wall
second with rendering requested at 10 FPS, no required active-stream stall
beyond the readiness age limit, calibrated position error at most 0.5 m and
heading error at most 15 degrees within 30 simulation seconds. These are
project acceptance targets, not competition rules or measured results.
Check image/depth/head stamps against the source's synchronization settings,
not just message receipt rates.

## 5 Scale to three players and then 3v3

Stop the old match, recorders, strategy and renderer before changing stage.
Leave the oversight UI open if desired; repeated `oversight` reuses it.

```bash
bash workstation.sh match-end
bash workstation.sh record-stop
bash workstation.sh stop
bash workstation.sh cameras-stop
```

Run the renderer in its separate terminal with `--robots robot1 robot2 robot3`.
Run the exact configuration that passed stage 4 with the same three names:

```bash
IMAGE=robocup-t2-studio:gpu-ready bash workstation.sh start \
  --robots robot1 robot2 robot3 --perception external --localization visual \
  --motion-profile t1-safe \
  --vision-executable /work/install/vision/lib/vision/vision_node
bash workstation.sh ready
bash workstation.sh match-start
```

If visual localization is still pending, use the explicitly labelled
`--localization ideal` configuration instead. If the engine is pending, use
the CPU ideal baseline. Record the selected mode in every result.

Pass the three-player gate before repeating renderer and strategy commands
with `--robots robot1 robot2 robot3 robot4 robot5 robot6`. Keep at least 1 GB
of measured GPU memory headroom as an initial target; six Vision instances
are not guaranteed to fit or meet the target rate. If six fail, preserve the
largest passing player count and diagnose capacity before increasing it.
Lowering renderer FPS affects timing and requires a fresh baseline.

For each selected robot, require advancing clock and fresh RGB/depth/head,
state, perception and referee streams; successful correlated supported RPCs;
unique identity/domain; receiver-reported teammate liveness; coherent roles
and mirrored team coordinates. Require no process crash, unexplained restart,
growing backlog or sustained inference error during a ten-minute wall-time
soak. Then complete both halves at the configured referee duration and
exercise scoring, penalties and available restarts. Save wall time and
simulation time separately.

The observer reports communication and budgets; it does not enforce a budget
or establish tournament compliance. Use the team's actual rules before
assigning a compliance threshold to its traffic projection.

## 6 Save evidence and prove repeatability

Keep the source commit, image IDs, engine SHA-256, Studio/runtime versions,
robot count, perception/localization modes, camera rates, simulation speed,
GPU memory and errors with each result. Save `run.json`, transport reports,
per-process logs, camera health/logs, oversight events and selected ROS bags.
The launcher copies the preceding workstation logs to `logs/runs/` when a new
strategy run begins. Bags remain under `logs/bags/`.

```bash
bash workstation.sh match-end
bash workstation.sh record-stop
bash workstation.sh stop-all
```

`stop-all` stops only labelled simulation helpers, flushes recorders using
SIGINT and leaves Studio's own virtual robot intact. It works even after
Studio closes. Reload the same scene and repeat startup, readiness, a short
match and shutdown twice with the passing configuration. Reopen Studio and
repeat once more to confirm the new Docker network namespace is used.

Completion means the chosen supported configuration passes all applicable
workstation gates and its limits are recorded. VisualKick is excluded.
GPU/model loading, real visual-localization quality and complete live matches
remain pending until tested on that workstation; laptop CPU regressions
cannot certify them.
