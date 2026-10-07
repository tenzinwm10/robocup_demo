# Native T2 compatibility investigation

This records the earlier native-body investigation. The current implementation
scope is the existing supported RoboCup demo simulation; VisualKick is absent
from the firmware and excluded from acceptance. Walking, velocity and head
control are the supported motion baseline. Follow
[WORKSTATION_PLAN.md](WORKSTATION_PLAN.md) for current completion criteria.

Tested 2026-10-07 using Studio 1.12.7, its native simulator source and built-in
`football_pitch_T2.bscene`. Upstream `sandbox/support_T2` still resolves to
`2d56d3622ac6dbfdbda96d27a2042eb543f199ae`.

## Installed virtual robot image: 0.8.4-beta-humble

The native scene compiled with 31 actuators, 45 qpos, 43 qvel and one camera.
The physics WebSocket served 7,451 snapshots during a 15-second observation;
simulation time advanced from 61.714 to 76.834 seconds. This proves live physics,
not successful locomotion.

Studio selected `BoosterT2Config`, generated a T2 Lua controller configuration,
and launched the stock `booster-motion` executable. Its log reports:

```
Cannot load module 'pos_track' of type 'rsm::PosTrackBodyControlT2'.
Segmentation fault
```

The T2 configuration references policies absent from `/opt/booster/Gait/lib/T2`:

- `model_2026-06-25_22-05-06_v1p2_foot_impact_0p01`
- `model_2026-06-30_08-54-54_pos_track`

The direct canonical `/LocoApiTopicReq` probe found zero subscribers and no
correlated replies to GetMode (2017), zero velocity (2001), or stop VisualKick
(2038). Walking and kicking could not be tested because the controller crashed.
No head-pose messages were observed on `/head_pose_stamped`; RGB/depth were not
tested because this headless probe did not run Studio's native camera renderer.

The RoboCup branch supplies the brain, perception, GameController integration,
SDK clients and tests. It does not contain the failing controller module or the
two required policy files. Installing/building that branch cannot repair the
bundled low-level binary by itself.

Reproduction scripts: `native_t2_probe_start.sh`, `native_t2_probe.py`.
Evidence: `logs/native-t2-launch.log`, `logs/native-t2-controller.log`,
`logs/native-t2-validation.json`. The probe is isolated from the existing T1
container and uses its own loopback port 18788.

## Updated virtual robot image: 0.9.5-alpha-humble

The official registry lists images through `0.9.5-alpha-humble` and
`0.9.5-alpha-kilted`. The installed Studio config pins `0.8.4-beta-humble` and
`0.8.5-alpha-kilted`.

The September 24 `0.9.5-alpha-humble` image was downloaded and launched with the
same Studio simulator source and stock T2 scene. It contains the required
`model_2026-07-16_20-11-22_v1p2_walk_full_01` policy and an updated graph without
the old missing `pos_track` module. The stock controller stayed running.

- 6,074 physics snapshots and 1,156 `/head_pose_stamped` messages were observed
  in the 15-second sensor/RPC probe.
- Canonical GetMode, ChangeMode, RotateHead and Move requests returned status 0
  in WALK mode (2).
- Ten 0.15 m/s velocity requests followed by zero velocity moved the robot
  0.250538 m over 2.902 simulation seconds. Its root height was 0.960044 m
  before and 0.967000 m afterward. This is a short walking test, not a complete
  match or long-duration stability validation.
- Both valid VisualKick versions (kV1=0 and kV2=1) returned status 501 for the
  stop command even in the active gait. No kick success is claimed.
- A requested soccer mode (4) returned status 0 but GetMode still returned 2.
  The controller log explicitly states `Robocup mode is disabled, use
  BoosterAgent mode instead`.
- Its T2 config sets `robocup_enable=false` and has no registered visual-kick
  module in the T2 graph. Changing that flag alone has not been validated as a
  way to supply the missing soccer behavior.

Evidence: `logs/native-t2-095-humble-validation.json`,
`logs/native-t2-095-humble-motion.json`, `logs/native-t2-095-humble-launch.log`,
`logs/native-t2-095-humble-controller.log`.

Image index digest:
`sha256:aa40857c62a4b894f2ca415a0cec1842ce1f935bd32747697b8800d98b0e935f`.

## Updated virtual robot image: 0.9.5-alpha-kilted

The same stock T2 scene and canonical RPC motion probe also passed on the
September 24 Kilted image. GetMode, walking-mode selection, head rotation and
velocity commands returned status 0. The robot moved approximately 0.198 m
over 3.088 simulation seconds and stayed upright (root height 0.963 to 0.969 m).

Both valid stop-VisualKick versions returned 501, and a soccer-mode request
again left GetMode at 2. The Kilted image therefore does not resolve the soccer
controller gap. Its index digest is
`sha256:1ce13d9d860c9b10ba7feb2a1b152613e70c29433c83e9f662173b6b5157449a`.
Evidence: `logs/native-t2-095-kilted-motion.json`,
`logs/native-t2-095-kilted-launch.log`, `logs/native-t2-095-kilted-controller.log`.

## Public GitHub controller resources

Checked public branches, including `robocup_demo/sandbox/support_T2`,
`sandbox/support_sim` and `sim_stable`, plus Booster's asset, deployment,
training, SDK and example repositories.

Booster **does** publish a T2 walking controller/policy:
[`booster_deploy/tasks/locomotion/robots/t2`](https://github.com/BoosterRobotics/booster_deploy/tree/main/tasks/locomotion/robots/t2)
contains `t2_walk.pt` and the 31-DOF controller configuration. The deploy
framework runs policy output through low-level `joint_ctrl` in custom mode.
It does not provide the factory RoboCup firmware services just by loading that
policy. T2 training configs and model geometry are public as well.

The current [SDK capability table](https://github.com/BoosterRobotics/booster_robotics_sdk/blob/main/include/booster/robot/b1/b1_loco_api.hpp)
documents factory T2 soccer mode as disabled and VisualKick as dependent on the
installed motion graph. This describes those published/default configurations,
not the capabilities of the user's already-verified physical T2 soccer firmware.

Equivalent future factory soccer motions would require a compatible simulator
controller bundle with those policies. That is outside the current supported
simulation scope and does not block this setup. No substitute kick policy or
invented success response has been added.
