# Description: 本部署的手眼步骤——把公共实现（common/steps/handeye.py）绑到产物路径。
#
# 相机固定在顶部：臂回到起始位姿只是为了让操作有个固定起点，画面不影响。
# 起始位姿是 CalibArm 的 prepare 位姿（笛卡尔 [0.2, 0, 0.25]、夹爪朝下），
# 与旧 CLI 工具一致；示教是按住末端 free 按钮手拖。
import paths
from common.steps.handeye import HandeyeStep as _HandeyeStep


class HandeyeStep(_HandeyeStep):
    paths = paths
    desc = ("在画面上点一个桌面上的显眼点 → 按住末端 free 按钮把夹爪尖拖到该点 → "
            "点「记录」→ 松开 free 按钮 → 点「摆到起始位姿」。采够 ≥4 点"
            "（建议 9~16，分散些）后点「拟合单应」。画面已按内参去畸变；"
            "起始位姿是夹爪朝下的观察位，每记录一个点都回这儿。")
