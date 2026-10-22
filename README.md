# robot-jaka-mini_with_linker_hand_o6 — JAKA Mini + LinkerHand O6 deployment

Robonix deployment for the fixed-base **JAKA Mini** 6-DOF arm with a
**LinkerHand O6** six-axis dexterous hand (controller end RS485L). The arm and
the hand are driven by one combined primitive,
[primitive-jaka-mini_with_linker_hand_o6-arm_hand-rbnx](https://github.com/syswonder/primitive-jaka-mini_with_linker_hand_o6-arm_hand-rbnx),
over TCP to the controller (jkrc SDK): it serves `robonix/primitive/arm/*`
always and `robonix/primitive/hand/*` in `end_effector: hand` mode.

The TG-9801 gripper variant of this deployment lives in the original
[robot-jaka-mini](https://github.com/syswonder/robot-jaka-mini) repository.

## Hardware & platform

| item | value |
|---|---|
| arm | JAKA Mini, controller at `10.5.5.100` (config `jaka_arm.ip`) |
| end effector | LinkerHand O6 six-axis dexterous hand, RS485 Modbus RTU via the controller end channel (slave `0x27` right) |
| camera | Orbbec Gemini 336L (USB `2bc5:0807`), overhead/fixed — RGBD, 1280×720 |
| platform | Jetson Orin, JetPack 6 (aarch64), ROS 2 Humble + `rmw_zenoh_cpp` |
| base | fixed (no mobile base, no navigation) |

## Packages

The committed manifest references packages by `url:` — catalog-resolvable for
the package site; `rbnx boot` fetches them into `rbnx-boot/cache/`:

| package | repo | role |
|---|---|---|
| `jaka_arm` | [syswonder/primitive-jaka-mini_with_linker_hand_o6-arm_hand-rbnx](https://github.com/syswonder/primitive-jaka-mini_with_linker_hand_o6-arm_hand-rbnx) | arm + O6 hand primitive — `robonix/primitive/arm/*` always, `robonix/primitive/hand/*` with `end_effector: hand`, one jkrc session |
| `orbbec_camera` | [syswonder/primitive-orbbec-camera-rbnx](https://github.com/syswonder/primitive-orbbec-camera-rbnx) | Orbbec camera primitive — `robonix/primitive/camera/{rgb,depth,camera_info}` (Gemini 336L via `gemini_330_series.launch.py`) |
| `jaka` | [syswonder/skill-jaka-grasp-rbnx](https://github.com/syswonder/skill-jaka-grasp-rbnx) | the same six capabilities as before (grab/release/home/place/pick + `jaka_fixed_grasp`) — unchanged by the hand swap |

On the deploy machine, a gitignored `robonix_manifest.local.yaml` (same
content, `path:` instead of `url:` for the three packages above) makes
`build.sh` / `start.sh` build and boot the sibling checkouts
(`../primitive-jaka-mini_with_linker_hand_o6-arm_hand-rbnx`,
`../primitive-orbbec-camera-rbnx`, `../skill-jaka-grasp-rbnx`) directly — no
GitHub fetch, unpushed local edits take effect. `robot_description` has no
local checkout and stays `url:` (built from `rbnx-boot/cache/`; refresh with
`rbnx update`).

## One primitive, one jkrc session

The O6 hangs on the controller's **end RS485L** channel, the same wire and the
same jkrc connection the arm uses. Two hard constraints make the arm and hand
one primitive rather than two packages:

- the controller permits **one active SDK session** — a second process that
  logs in makes the first process's `get_actual_joint_position` hang
  indefinitely (measured 2026-10-22 on the real controller);
- the controller's **RS485 signal table is global** across SDK sessions and
  misaligns with more than one entry.

So `jaka_arm` owns the single session and the single signal table, and in hand
mode serves both contract families from the same provider id. A standalone hand
primitive was prototyped and backed out for exactly this reason; do not add a
second jkrc client, a TG-9801 tool, or a hand driver on the same controller
while this stack runs.

### Hand interface

In `end_effector: hand` mode the same provider serves:

| capability | transport | what |
|---|---|---|
| `hand/info` | gRPC | six control axes with finger/type/description |
| `hand/move_joint` | gRPC | position by axis name, normalized `[0,1]` (1 = flexed) |
| `hand/set_joint_speed_limits` | gRPC | per-axis speed ceilings |
| `hand/set_joint_torque_limits` | gRPC | per-axis torque ceilings |
| `hand/state_joint` | ROS 2 | `/jaka/hand/state_joint`, normalized positions |

The six axes are `thumb_cmc_pitch`, `thumb_cmc_yaw`, `index_mcp_pitch`,
`middle_mcp_pitch`, `ring_mcp_pitch`, `pinky_mcp_pitch` — the same names the
URDF uses, so the hand joints match by name. The distal joints are mechanically
coupled and are not separate axes. Positions are normalized (0 = extended, 1 =
fully flexed; thumb opposition 0 = neutral, 1 = full); limits are 0 = minimum,
1 = maximum. `move_joint` rejects empty/unknown/duplicate/out-of-range requests
and never clamps; `ok=true` means accepted and dispatched, not reached. At
activation the hand pose is **unknown** — a lost RS485 answer reads as fully
flexed, so a read is not a safe target seed — and the first `move_joint` must
list all six axes; later requests may be partial and hold the unlisted axes.
The skill's `gripper_joint` open/grip is a full six-axis pose and seeds the
same cache, so it needs no special first call.

`joint_states` carries the six arm joints **and** the six hand joints (rad, 0 =
extended) in one message, so `robot_state_publisher` drives the whole body in
RViz. The read path is one single-entry RS485 poll per register (~0.38 s; a
six-axis sweep ~2.3 s), so the hand state runs at roughly one sweep per
`hand_publish_period_s` cycle — slow but continuous, not 50 Hz. The O6 answers
slowly (2~5 s) after a motion and a lost answer reads as 0, so read a verdict
after the motion settles.

### Unchanged skill, `gripper_joint` mapping

The skill still grips the way it did with the TG-9801: it publishes
`sensor_msgs/JointState{name: ["gripper_joint"], position: [m]}` on
`/jaka/joint_command`. In hand mode `jaka_arm` maps `>= 0.049 m` → O6 open
pose, `< 0.049 m` → full five-finger grasp; the skill's five-publish burst is
idempotent (the same pose is re-issued and settles). The skill config declares
the body with the required `end_effector: hand` (`gripper` for the TG-9801
deployment); a missing value is reported as a skill config error. What changes
for the skill is only configuration: the O6 palm is longer, so the
desktop/grasp heights are 0.14 m, and `gripper_settle_s` is raised to 3 s so
the fingers finish closing before the lift. For the grasp verdict the skill reconstructs a width-like
opening from the hand joints in `joint_states` (thumb/index/middle/ring
flexion, the same 0.098 m scale and thresholds as the gripper), so the
empty-grasp/open checks keep working without `gripper_joint`. The O6's
lost-answer clusters can still look like a fully-closed hand — the verdict
waits for two settled samples, but treat a critical grasp with care. The hand
joints are also on `/jaka/hand/state_joint`.

## Pick skill (camera-driven grasp)

`jaka_pick` locates a named object and grasps it: resolve `camera/rgb` → grab a
frame → detect → a calibrated **2D hand-eye homography** maps the bbox centre
to arm `base_link` XY → grasp at `(x, y, desktop_height)` with a vertical-down
orientation. It reuses the same grasp segment as `grab`.

Two detection backends (`detect_scheme` in the skill config):

- **`yolo`** (deploy default): in-process yolo11-obb — the fruit model
  (`carrot`/`potato`/`tomato`) reused from the agilex deploy at
  `robot-agilex-robonix/host-services/yolo/weights/best.pt` (point the
  `YOLO_MODEL_PATH` env var at it — the manifest forwards it to
  `yolo_model_path`; the skill interpreter has CUDA torch + ultralytics).
  Two-frame consistency filters flicker; Chinese phrasing maps to class names
  via `yolo_class_aliases` (胡萝卜→carrot …); the OBB rotation aligns the
  gripper yaw to the object's long edge (`rz = grasp_yaw_offset_deg - yaw`,
  default -90 = this arm's tuned value).
- **`vlm`**: open-vocabulary multimodal detection (pixel bbox, no rotation →
  fixed `grasp_rpy`).

**Calibration is required and on-site.** `pick` is unavailable until you set,
in `robonix_manifest.yaml`'s `skill: - name: jaka` config block:
- `homography_matrix` — 3×3 pixel→base_link, calibrated at 1280×720;
- the scheme's detector: the `YOLO_MODEL_PATH` env var or
  `vlm_base_url` / `vlm_api_key` / `vlm_model` (**multimodal** endpoint);
- `desktop_height` — the table height in `base_link` (grasp z, 0.14 m here).

## Calibration

`jaka_pick`'s `homography_matrix` is measured on site with the in-tree bench
under `tools/calibration/` — a cv2-GUI tool (served as a browser page on
headless machines) for this JAKA Mini + Gemini 336L pair. The arm side reuses
the combined package's `JakaDriver` with `end_effector: hand` (probed from the
sibling checkout or `rbnx-boot/cache/`), so the tool's open/close gesture
drives the O6 open/full-grasp poses over the same jkrc connection.

Run it with robonix **stopped** (the camera and the controller are
single-owner), workspace clear, estop at hand:

```bash
bash tools/calibration/setup_venv.sh
cd tools/calibration && .venv/bin/python calibrate_handeye_2d.py            # default: calibrate
cd tools/calibration && .venv/bin/python calibrate_handeye_2d.py --mode test
```

The tool prints the fitted matrix as ready-to-paste YAML rows; hand-copy it
into `homography_matrix` in **both** `robonix_manifest.yaml` and
`robonix_manifest.local.yaml`. `desktop_height` is *not* produced by the tool;
it must stay 0.14 m (and in sync with `tools/calibration/config.yaml`'s
`default_desktop_height`). The matrix is only valid at 1280×720 on
undistorted frames. Full flow and gotchas: `tools/calibration/README.md`
(Chinese).

## Estop recovery

After the emergency stop is released (pendant knob pulled up; the controller is
usually powered off), `tools/jaka_estop_recover.py` resets it over raw jkrc:
clear error → power on → enable → confirmation → slow move to Home (all-zeros)
→ disable and power off. Run it with robonix **stopped** (it opens its own
jkrc session; the stack must not hold the controller):

```bash
tools/calibration/.venv/bin/python tools/jaka_estop_recover.py
```

## URDF

`urdf/robot.urdf` is the JAKA app export of the real JAKA Mini with the O6 hand
chain grafted on (`meshes/` including `meshes/right_hand/`). It is consumed by
Soma (`urdf.path` in `soma.yaml`), which serves the raw XML to
`robot_description` via `get_urdf`.

The export was post-processed for this deployment:

- The 6 arm joints were renamed `link_002_joint..link_007_joint` →
  `joint_1..joint_6` so `robot_state_publisher` matches the `joint_states`
  names published by the `jaka_arm` provider. If the URDF is re-exported from
  the JAKA app, re-apply this rename (see the note in `soma.yaml`).
- The TG-9801 two-finger chain (`link_008`/`link_009` + prismatic joints) was
  replaced by the O6 hand chain (17 links, 6 actuated `revolute` joints
  `thumb_cmc_pitch`/`thumb_cmc_yaw`/`*_mcp_pitch`, 5 `mimic` distal joints, 5
  fingertip frames; meshes under `urdf/meshes/right_hand/`). The chain comes
  from the standalone O6 URDF in `syswonder/robot-linkerbot-linker_hand_o6`
  (extracted from the BeingBeyond D1 whole-body model); joint names match the
  axis names the primitive reports through `hand/info`.
- The mount joint `link_007_to_hand` **has a placeholder pose**
  (`xyz="0 0 -0.085"`, rpy all-zero): the O6's true mounting offset on the
  flange must be measured on the real arm and corrected there — the pick
  skill's heights depend on it. Do not trust RViz palm placement until it is
  calibrated.

## Prerequisites

- Robonix tooling: `make install` from a cloned `syswonder/robonix` checkout,
  then `rbnx setup <robonix source dir>`.
- `sudo apt install ros-humble-rclpy ros-humble-rmw-zenoh-cpp ros-humble-nav-msgs ros-humble-sensor-msgs`
  (nav-msgs/sensor-msgs feed the Soma runtime reader)
- The combined arm+hand package (ships the JAKA SDK binaries, Apache-2.0;
  needs no extra hardware packages).
- Primitive packages built: `bash build.sh` (builds the sibling checkouts via
  the local manifest; a first build on a fresh clone fetches the url-based
  packages into `rbnx-boot/cache/`).

## Build

```bash
bash build.sh
```

`build.sh` is the canonical entry: it prepares the deploy venv, then builds
every package declared in the manifest (the local `path:` variant when
present, else the committed `url:` one) with `--no-update-check` so builds
stay offline. `robot_description` is fetched from the package catalog into
`rbnx-boot/cache/` when missing.

> **Camera build note (native):** the Orbbec driver
> (`../primitive-orbbec-camera-rbnx`) colcon-builds the vendored
> `OrbbecSDK_ROS2`, which needs ROS 2 C++ deps (`rclcpp`, `image_transport`,
> `cv_bridge`, `camera_info_manager`, `tf2_ros`, …) absent from this Jetson's
> minimal `/opt/ros/humble`. Install them once per machine (needs sudo):
>
> ```bash
> bash ../primitive-orbbec-camera-rbnx/scripts/install_deps.sh
> ```
>
> The arm+hand stack builds fine without them (the camera primitive is only
> needed for `pick`).

## Boot

```bash
# standard entry — committed url manifest, packages from rbnx-boot/cache/
rbnx boot

# offline / pinned cache: skip the pre-boot per-package `git fetch`
rbnx boot --no-update-check

# after estop verification and single-joint tests, enable motion in
# robonix_manifest.yaml: jaka_arm.config.motion_enabled: true
```

`motion_enabled: false` rejects arm and hand motion (safety gate) while
feedback keeps streaming. Boot read-only first, verify `joint_states` (12
joints), `hand/info` and `hand/state_joint`, then enable.

Bare `rbnx boot` resolves every package to its `rbnx-boot/cache/` clone; the
jkrc SDK ships inside the combined package (Apache-2.0), so no manual copy is
needed. Per-machine prerequisites:

- `.vlmkey` vars in the booting shell — `bash start.sh` sources them,
  bare boot cannot (this machine's `~/.zshrc` does).
- on a fresh machine, `bash build.sh` once before the first boot.

The pre-boot freshness check only *reports*; it never pulls. When it marks a
provider behind, run `rbnx update` (needs network) before booting.

The manifest's `env.PATH` expands `${PWD}/.venv/bin` — the venv built by
`build.sh` that gives the `robot_description` provider a new enough grpcio for
its generated bindings; boot from this directory.

## Acceptance record

| stage | command | expected |
|---|---|---|
| static | `rbnx validate ../primitive-jaka-mini_with_linker_hand_o6-arm_hand-rbnx && bash build.sh` | package + all packages built |
| unit | `cd ../primitive-jaka-mini_with_linker_hand_o6-arm_hand-rbnx && .venv/bin/python -m unittest discover -s jaka_arm/tests -t .` | 41/41 OK |
| read-only body | `bash start.sh` (`motion_enabled: false`) | `joint_states` has 6 arm + 6 hand joints; `hand/info` answers 6 axes; `hand/state_joint` streams; no second jkrc client |
| hand move | enable motion, `hand/move_joint` | a partial request is rejected until a full six-axis target is sent; then one axis moves; boundary requests (out-of-range/unknown/empty/dup) rejected with a reason |
| arm + hand health | move a finger, watch `joint_states`/vitals | arm feedback keeps streaming (no `-3` reads), body state NORMAL |
| single joint | small joint delta on `joint_command` | joints follow, limits hold, estop works |
| skill compat | skill `grab` (or a `gripper_joint` publish) | O6 opens before approach and runs the full grasp at the descent; the verdict comes from the reconstructed hand width (two settled samples; O6 link caveat) |
| camera | `bash start.sh`, `rbnx caps -v` | orbbec_camera ACTIVE; `camera/rgb` streams 1280×720 |
| pick (dry) | set VLM + homography, `jaka_pick {object}` before enabling motion | detect + geometry resolve; motion times out with the `motion_enabled` hint |
| pick (live) | motion enabled, calibrated | arm descends on the detected object, hand closes, lifts |
| shutdown | Ctrl-C | hand opens, arm returns to `shutdown_home_joints` (all-zeros), motion stops, arm powered off, logs clean |

Verified on the bench 2026-10-22: the combined provider served one jkrc
session with both contract families, `joint_states` carried 6 arm + 6 hand
joints, `hand/info` returned six axes, the unknown-pose guard rejected a
partial `move_joint` until a full six-axis target was sent, finger moves
executed, and the arm feedback stayed healthy (0 `-3` reads, vitals NORMAL)
while the hand moved. The O6 state stream still shows the documented
lost-answer clusters (brief 1.0 readings) — do not judge a grasp from one
sweep. Re-run the table above end-to-end, including a live pick, before
trusting a live grasp.

Fill in the robot revision, Robonix commit (`git -C <robonix> rev-parse HEAD`)
and the measured grip/open readings here after acceptance.
