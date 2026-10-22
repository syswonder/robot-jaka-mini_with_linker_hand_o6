# Description: 标定工具的臂适配层——探测并复用合并臂+手包的 JakaDriver，
# 暴露标定脚本使用的调用面（与 AIIT-Roboarm arm/arm_base.Arm 的用法一致）。
#
# 臂驱动不随本工具分发：jkrc/手/运动学代码都在合并包
# primitive-jaka-mini_with_linker_hand_o6-arm_hand-rbnx
# （jaka_arm/driver.py，MIT，与 AIIT 的 jaka_ctrl_by_sdk.py 同源）里维护，
# 这里只做探测 + 薄适配，避免两份实现各自漂移。
import glob
import math
import os
import sys

from scipy.spatial.transform import Rotation as R

from utils.config_getter import get_config_value

TOOL_ROOT = os.path.dirname(os.path.abspath(__file__))
DEPLOY_ROOT = os.path.dirname(os.path.dirname(TOOL_ROOT))

# AIIT 同款：夹爪朝下、RZ 默认 0(+offset) 的抓取姿态（度，ZYX 序）
DEFAULT_DOWN_EULER_DEG_ZYX = [0.0, 180.0, 0.0]


def _locate_jaka_package() -> str:
    """返回含 jaka_arm/driver.py 的包根目录（sys.path.insert 后即可 import jaka_arm）。"""
    candidates = []
    configured = get_config_value("jaka_arm_package_path", "", raise_if_missing=False)
    if configured:
        candidates.append(configured)
    for name in (
        "primitive-jaka-mini_with_linker_hand_o6-arm_hand-rbnx",
        # TG-9801-only legacy arm package
        "primitive-jaka-mini_with_tg9801_gripper-arm_gripper-rbnx",
    ):
        candidates.append(os.path.join(os.path.dirname(DEPLOY_ROOT), name))
    candidates.extend(
        sorted(glob.glob(os.path.join(DEPLOY_ROOT, "rbnx-boot", "cache", "*")))
    )
    candidates.extend(sorted(glob.glob(os.path.join(os.path.dirname(DEPLOY_ROOT), "*"))))
    for candidate in candidates:
        if os.path.exists(os.path.join(candidate, "jaka_arm", "driver.py")):
            return candidate
    raise ImportError(
        "找不到臂包（需要合并包/旧臂包的 jaka_arm/driver.py）。已尝试:\n  "
        + "\n  ".join(candidates)
        + "\n请先 `bash build.sh` 或 `rbnx boot` 让臂包进入 rbnx-boot/cache/，"
        "或在 config.yaml 里设置 jaka_arm_package_path。"
    )


_PACKAGE_ROOT = _locate_jaka_package()
sys.path.insert(0, _PACKAGE_ROOT)
from jaka_arm.driver import GRIPPER_STROKE_M, JakaDriver  # noqa: E402

print(f"[calib_arm] 臂包: {_PACKAGE_ROOT}", flush=True)


class CalibArm:
    """把标定脚本用到的臂调用面映射到 JakaDriver。

    与 AIIT-Roboarm 的 JakaBySDK 行为对齐（见各方法注释）。差异：连接后不
    回零——AIIT 在构造函数里 reset，这里首个动作就是 prepare_for_manual_teach
    的侧面待拖位姿。
    """

    def __init__(self):
        self._catch_rot_offset_deg = get_config_value("jaka_catch_rot_offset_deg", 0)
        # 末端与部署清单同键：gripper = TG-9801；hand = LinkerHand O6。
        # 标定脚本的"开爪/合爪"手势经同一条 jkrc 连接驱动对应末端（hand 模式
        # 下是 O6 的张开/全握姿势）。
        self._end_effector = get_config_value("end_effector", "gripper")
        self._driver = JakaDriver(
            get_config_value("arm_port"),
            joint_speed_rad_s=get_config_value("joint_speed_rad_s", 1.0),
            linear_speed_mm_s=get_config_value("linear_speed_mm_s", 100.0),
            end_effector=self._end_effector,
            hand_type=get_config_value("hand_type", "right"),
        )
        self._driver.connect()

    def get_arm_pose(self) -> tuple[list[float] | None, list[float] | None]:
        """返回 (pos[m], [RZ, RY, RX] 度)；读失败返回 (None, None)。"""
        pose = self._driver.get_tcp_pose()
        if pose is None:
            return None, None
        pos, rpy_rad = pose
        euler_zyx_deg = R.from_euler("xyz", rpy_rad).as_euler("zyx", degrees=True)
        return list(pos), euler_zyx_deg.tolist()

    def prepare_for_manual_teach(self) -> bool:
        """标定拖动采点前，先摆到夹爪朝下的工作区上方姿态，缩短拖动距离。"""
        return self.move_to([0.2, 0.0, 0.25])

    def disable_torque(self):
        # 与 AIIT 相同的故意空操作：采点用末端 free 按钮拖拽（按住=拖拽、
        # 松开=锁定），软件 drag_mode_enable 会超时自锁、采点间隙反复掉出
        # 拖拽，故不用。机械臂保持使能即可，free 按钮在使能态下释放抱闸。
        print("JAKA: 请按住末端 free 按钮拖拽机械臂采点（松开即锁定）。")

    def move_to(
        self,
        pos: list[float],
        gripper_open_0to1: float | None = None,
        rot_rad: float | int | None = None,
    ) -> bool:
        """笛卡尔移动，朝向 = 夹爪朝下（ZYX [RZ, 180, 0]，AIIT 同款）。

        rot_rad 覆盖 RZ 并叠加 jaka_catch_rot_offset_deg（与运行时抓取朝向
        同源）。直线/IK 回退策略由 JakaDriver.move_pose 负责。
        """
        if len(pos) != 3:
            raise ValueError("位置参数格式错误，应该是[x, y, z]")

        target_euler = list(DEFAULT_DOWN_EULER_DEG_ZYX)
        if rot_rad is not None:
            target_euler[0] = math.degrees(rot_rad) + self._catch_rot_offset_deg
        rpy_rad = R.from_euler("zyx", target_euler, degrees=True).as_euler("xyz")

        if gripper_open_0to1 is not None:
            if not 0 <= gripper_open_0to1 <= 1:
                raise ValueError("gripper_open_0to1 must in [0, 1]")
            # TG-9801 自适应模式为二值动作：>= 半行程张开（release），否则夹取
            self._driver.set_gripper_open_m(
                GRIPPER_STROKE_M if gripper_open_0to1 >= 0.5 else 0.0
            )
        return self._driver.move_pose(list(pos), [float(a) for a in rpy_rad])

    def move_to_home(self) -> bool:
        """全零位（与 AIIT 的 move_to_home 一致）。"""
        return self._driver.move_joints_rad([0.0] * 6)

    def disconnect_arm(self):
        """AIIT 同款收尾：回零（夹爪张开）→ 失能断电。"""
        print("Resetting JAKA arm to initial state.")
        try:
            self._driver.set_gripper_open_m(GRIPPER_STROKE_M)
            self._driver.move_joints_rad([0.0] * 6)
        except Exception as e:
            print(f"Error during reset: {e}")
        self._driver.disconnect()
        print("Arm disconnected")
