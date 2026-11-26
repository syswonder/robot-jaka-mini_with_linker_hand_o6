# Description: 急停后的机械臂复位。急停释放（旋钮拔起）后运行本脚本：
# 清除错误 -> 上电 -> 使能 -> 人工确认后慢速回 Home。
#
# 与 AIIT-Roboarm/tools/jaka_estop_recover.py 同源，差异只在取依赖：直连控制器
# 裸 jkrc（不经 robonix，motion_enabled 门不适用）——复位期间 robonix 应停机。
# 臂驱动不随工具分发：臂包探测复用 tools/calibration/calib_arm（jkrc SDK 随之经
# 包内平台加载器就绪），控制器 IP 复用清单 primitive: name: jaka_arm → config: ip
# （utils.deploy_manifest.read_arm_ip）。
# 用标定 venv 跑（Python 3.10，jkrc.so 只认 3.10）：
#   tools/calibration/.venv/bin/python tools/jaka_estop_recover.py
import os
import sys
import time

CALIB_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "calibration"
)
sys.path.insert(0, CALIB_ROOT)

# 导入 calib_arm 即完成臂包探测、sys.path 注入与包内 jkrc 平台加载器的执行，
# jkrc 随之就绪；找不到臂包会给出补救指引（jkrc 二进制在臂包里，不随本工具分发）。
import calib_arm  # noqa: F401
import jkrc
from utils.deploy_manifest import read_arm_ip

ABS = 0  # jkrc 绝对运动
HOME_SPEED_RAD_S = 0.3  # 回零速度，慢速防止急停后姿态异常时扫到物体


def main():
    arm_ip = read_arm_ip()
    robot = jkrc.RC(arm_ip)
    ret = robot.login()
    if ret[0] != 0:
        raise ConnectionError(f"JAKA 控制器登录失败 ({arm_ip}): {ret}")
    print(f"已连接控制器 {arm_ip}")

    print("清除错误状态...")
    robot.clear_error()
    time.sleep(1)

    # 急停后通常整体断电；若已上电，重复调用无副作用（仅返回非 0 错误码）
    print("上电...")
    ret = robot.power_on()
    while ret[0] != 0:
        print(f"power_on 返回码: {ret[0]}（非 0）")
        ans = input(
            "请检查急停按钮是否已释放（旋钮顺时针拔起），"
            "释放后按回车重试；确认急停已释放则输入 s 跳过: "
        )
        if ans.strip().lower() == "s":
            break
        robot.clear_error()
        time.sleep(1)
        print("重试上电...")
        ret = robot.power_on()
    time.sleep(1)

    print("使能...")
    ret = robot.enable_robot()
    while ret[0] != 0:
        print(f"enable_robot 返回码: {ret[0]}（非 0）")
        ans = input(
            "请确认急停已释放、上电已成功，"
            "处理后按回车重试；确认无问题则输入 s 跳过: "
        )
        if ans.strip().lower() == "s":
            break
        robot.clear_error()
        time.sleep(1)
        print("重试使能...")
        ret = robot.enable_robot()
    time.sleep(1)

    input("\n确认机械臂回 Home 路径无障碍后按回车（将慢速回零位）...")
    ret = robot.joint_move([0.0] * 6, ABS, True, HOME_SPEED_RAD_S)
    if ret[0] != 0:
        print(f"回 Home 失败，错误码 {ret[0]}，运动状态: {robot.get_motion_status()}")
    else:
        print("已回到 Home 位。")

    print("断使能并断电...")
    robot.disable_robot()
    time.sleep(0.5)
    robot.power_off()
    robot.logout()
    print("复位完成，机械臂已断使能断电。")


if __name__ == "__main__":
    main()
