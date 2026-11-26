# Description: JAKA 版"臂会话"——把标定台需要的臂接口面映射到本部署已有的
# CalibArm（薄适配层，内部是臂包的 JakaDriver/jkrc，见 tools/calibration/calib_arm.py）。
#
# 与 agilex common/robot/direct.py 的接口面保持一致，供三个步骤共用：
#   end_pos() / status() / take_over() / enable_and_take_control() / move_joints(deg) / close()
#
# 差异（有意为之）：
#   * 示教不是"进/出示教模式"，而是操作员按住末端 free 按钮（硬件）。所以 status()
#     返回的 teach_status 只是 is_in_drag_mode() 的折射——手眼页用它做"还在拖动中"
#     的提示，不用它做硬性状态机。
#   * 起始位姿不是关节角文件，而是 CalibArm.prepare_for_manual_teach()（xyz 读清单
#     skill.jaka → hover_pose、夹爪朝下）——与旧 CLI 工具行为一致，标定记录点从这里出发。
#   * 每次动臂前 best-effort 查急停/拖动状态（jkrc 的返回值形状不保证，解析失败
#     只记日志不阻断），急停时拒绝移动。
import numpy as np

from calib_arm import CalibArm


class JakaArm:
    """JAKA 标定会话。构造即连接控制器（登录/上电/使能/末端就绪），与旧工具一致。"""

    def __init__(self):
        self._calib = CalibArm()
        self._driver = self._calib._driver

    # ---------- 反馈 ----------

    def end_pos(self) -> list[float]:
        """末端位置 [x, y, z]（米）；读失败抛 RuntimeError。"""
        pose = self._driver.get_tcp_pose()
        if pose is None:
            raise RuntimeError("读末端位姿失败（控制器无响应）")
        return [float(v) for v in pose[0]]

    def is_estop(self) -> bool | None:
        """急停是否按下；读不到时返回 None（不当作急停，只记日志）。"""
        return self._rc_flag("is_in_estop")

    def is_in_drag_mode(self) -> bool | None:
        """是否正被 free 按钮拖动；读不到时返回 None。"""
        return self._rc_flag("is_in_drag_mode")

    def status(self) -> dict:
        drag = self.is_in_drag_mode()
        return {
            # 旧代码的示教状态 0x01 ≡ 正在拖动
            "teach_status": 0x01 if drag else 0x00,
            "dragging": drag,
            "estop": self.is_estop(),
        }

    def _rc_flag(self, name: str) -> bool | None:
        rc = getattr(self._driver, "_rc", None)
        fn = getattr(rc, name, None) if rc is not None else None
        if fn is None:
            return None
        try:
            ret = fn()
        except Exception as e:  # noqa: BLE001
            print(f"[jaka_arm] {name} 读取失败: {e}", flush=True)
            return None
        if isinstance(ret, tuple):
            ret = ret[0]
        if isinstance(ret, (int, float, bool)):
            return bool(ret)
        return None

    # ---------- 运动 ----------

    def enable_and_take_control(self) -> bool:
        """驱动连接时已上电使能；这里只做急停检查。"""
        return self._check_motion_allowed() is None

    def take_over(self) -> bool:
        """退出示教（松开 free 按钮）后接管——JAKA 侧只需确认不在拖动/急停。"""
        return self._check_motion_allowed() is None

    def _check_motion_allowed(self) -> str | None:
        if self.is_estop():
            return "急停已按下——松开急停并复位后再动臂"
        return None

    def move_joints(self, target_deg: list[float]) -> bool:
        """关节角移动（度）。急停/异常时抛 RuntimeError，让页面把原因显示出来。"""
        err = self._check_motion_allowed()
        if err:
            raise RuntimeError(err)
        if len(target_deg) != 6:
            raise ValueError(f"期望 6 个关节角，收到 {len(target_deg)}")
        ok = self._driver.move_joints_rad([float(np.radians(a)) for a in target_deg])
        if not ok:
            raise RuntimeError("关节移动失败（超限、无解或未到位）")
        return True

    def start_pose(self) -> bool:
        """摆到起始/观察位姿（夹爪朝下、工作区上方），与旧工具 prepare 位姿一致。

        注意：它是笛卡尔移动，驱动内部可能回退到控制器 IK；失败时返回 False，
        调用方把"用 free 按钮手拖到观察位"的人话提示给操作员。
        """
        err = self._check_motion_allowed()
        if err:
            raise RuntimeError(err)
        return bool(self._calib.prepare_for_manual_teach())

    def move_to(self, pos: list[float], gripper_open_0to1: float | None = None) -> bool:
        """笛卡尔移动（验证页/测高页用）：夹爪朝下、z 由调用方给。"""
        err = self._check_motion_allowed()
        if err:
            raise RuntimeError(err)
        return bool(self._calib.move_to(pos, gripper_open_0to1=gripper_open_0to1))

    def move_to_keep_orientation(self, pos: list[float],
                                  slow_mm_s: float | None = None) -> bool:
        """保持当前末端姿态、只动位置（测高微调用）。

        微调高度时不该顺带把夹爪转正——操作员摆的朝向要保持，只换 xyz。
        朝向取当前 TCP 的 rpy 原样传给 move_pose；直线无解时驱动内部回退控制器 IK。

        slow_mm_s 给定时临时用这个直线速度（mm/s），结束后无论成败都恢复原速——
        指尖贴桌面时默认 100 mm/s 太危险。None = 用驱动配置的速度。
        """
        err = self._check_motion_allowed()
        if err:
            raise RuntimeError(err)
        pose = self._driver.get_tcp_pose()
        if pose is None:
            raise RuntimeError("读末端位姿失败（控制器无响应）")
        _pos, rpy_rad = pose
        if slow_mm_s is None:
            return bool(self._driver.move_pose([float(v) for v in pos],
                                               [float(v) for v in rpy_rad]))
        driver = self._driver
        original = driver._linear_speed_mm_s
        driver._linear_speed_mm_s = min(original, float(slow_mm_s))
        try:
            return bool(driver.move_pose([float(v) for v in pos],
                                         [float(v) for v in rpy_rad]))
        finally:
            driver._linear_speed_mm_s = original

    def set_hand_joint_positions_normalized(self, values: list[float]) -> bool:
        """写六指开合度 [0,1]（0 = 张开，1 = 完全弯曲/闭合）。

        测高页固定用 [0.2]*6；超界由驱动拒绝（不截断），调用方先校验。
        """
        err = self._check_motion_allowed()
        if err:
            raise RuntimeError(err)
        return bool(self._driver.set_hand_joint_positions_normalized(
            [float(v) for v in values]))

    # ---------- 收尾 ----------

    def close(self) -> None:
        """断开控制器（回位 + 失能断电，见 CalibArm.disconnect_arm）。幂等。"""
        try:
            self._calib.disconnect_arm()
        except Exception as e:  # noqa: BLE001
            print(f"[jaka_arm] 断开臂失败: {e}", flush=True)
