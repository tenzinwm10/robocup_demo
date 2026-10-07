# FC ISAR workstation continuation

Repository: https://github.com/tenzinwm10/robocup_demo
Branch: `codex/studio-t1-demo`
Upstream: `BoosterRobotics/robocup_demo`, `sandbox/support_T2`, commit
`2d56d3622ac6dbfdbda96d27a2042eb543f199ae`.

## User objective and constraints

**Current scope:** finish the supported `sandbox/support_T2` simulation.
VisualKick is not available in the user's firmware and is outside acceptance.
Keep automatic VisualKick disabled; its absence does not block this setup.
Follow [WORKSTATION_PLAN.md](WORKSTATION_PLAN.md) for the complete staged plan.

Run the full soccer demo using Booster Studio built-in simulation, referee and
gameplay features on Ubuntu with an NVIDIA RTX 3070. Use the existing T2 brain,
perception models, behavior tree and robot API/RPC routes. The field may be
22.003 x 14.126 metres. Prepare multi-robot operation, initially three players,
with oversight of team communications and GameController traffic.

Do not train new movements or silently replace motion policies. Physical T2
movement capability has already been verified by the user. Intended core repo
changes concern perception and localization. The T1-body fallback is packaged;
native T2-body support is being investigated to improve perception fidelity.

## Current results, 2026-10-07

Read `T2_COMPATIBILITY.md`, `WORKSTATION.md` and `OVERSIGHT.md` before running.

- Existing T2 branch brain and original protocol/model files are preserved.
- Optional ideal Studio localization adds 29 lines to three brain files;
  default behavior stays off. Simulation adaptations live in this directory.
- Original brain and CPU vision tests, ROS isolation/transport, scene,
  bootstrap, camera-stream and oversight tests passed on the laptop.
- Full match behavior, RTX 3070 TensorRT model loading and multi-robot GPU
  throughput still require workstation testing. Do not treat preparation as
  proof of a complete soccer match.
- Native Studio T2 scene compiled with 31 actuators. The installed
  `0.8.4-beta-humble` controller crashed on a missing T2 module/policies.
- Official `0.9.5-alpha-humble` includes updated T2 controllers and policies.
  Using the Studio runtime, WALK mode, head rotation and velocity RPCs passed;
  the T2 moved 0.250538 m and stayed upright in a short test.
- Both valid VisualKick versions returned 501. Soccer mode requests fell back
  to mode 2/BoosterAgent. The shipped T2 graph sets `robocup_enable=false` and
  has no visual-kick module. Equivalent full T2 soccer motion is unresolved.
- Booster publishes `t2_walk.pt` in `booster_deploy`; this is a low-level
  policy runner, not evidence of factory soccer RPC compatibility.
- The newer Kilted image also passed walking/head/velocity tests with about
  0.198 m of upright movement. It likewise returned 501 for both valid
  VisualKick stop requests and stayed in mode 2 after soccer selection.
  Switching ROS distributions does not fix the default soccer graph.

## Prepared binary handoff

The complete binary/source handoff is published as a private GitHub release:
https://github.com/tenzinwm10/robocup-t2-studio-handoff/releases/tag/workstation-2026-10-07
Sign in to GitHub as `tenzinwm10`. Read `SUPPORTED_START_HERE.md` and use
`source-supported.zip` for the current launcher and plan. Its
`SUPPORTED_SHA256SUMS` verifies the new source/evidence and the same original
Docker parts. The earlier `START_HERE.md` and `source.zip` remain as a snapshot.
The 7 GB Docker archive is split into four release assets, each below 2 GB.

The laptop has `Downloads/robocup-t2-studio-handoff` containing `source.zip`,
`docker-images.tar`, `manifest.json`, `validation.json` and `START_HERE.md`.
Copy the whole directory via SSH or removable storage. The Docker archive is
about 7 GB and includes:

- `robocup-t2-studio:cpu-ready`
- `robocup-t2-studio:gpu-ready`
- `robocup-t2-studio:renderer`

The source ZIP includes generated scenes and Studio runtime support omitted
from Git. A Git clone alone does not include those binary/runtime resources.
Extract the ZIP and use that source directory for the prepared launchers.
The newer native T2 image is separately downloadable from Booster's registry.

## Next work on Ubuntu

1. Inspect OS, `nvidia-smi`, Docker and NVIDIA container runtime availability.
2. Copy/import the handoff, verify its manifest hashes, and read the launch docs.
3. Run GPU model-load and isolated camera/perception/localization checks before
   launching a match. Preserve the branch's actual TensorRT models.
4. Validate supported walking, head and velocity commands through the native
   RPC routes. Keep automatic VisualKick off; no new soccer firmware is needed
   to complete the supported simulation baseline.
5. Start with one robot, then three players and finally all six after transport
   and perception checks pass. Record referee scoring/state behavior,
   communications, resource usage and repeatable startup/shutdown.

The user requested GitHub transfer instead of SSH. Continue locally on the
Ubuntu workstation after downloading the private release. No workstation jobs
have been run from the laptop. Same-account login does not grant the laptop
access to the Ubuntu shell.
