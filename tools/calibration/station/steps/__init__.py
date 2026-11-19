# Description: 步骤清单——app.py 按这个顺序生成导航与首页卡片（也就是建议的执行顺序）。
#
# 内参 → 手眼 → 测高 → 验证：先标内参（手眼要用去畸变后的画面），再标手眼；
# 测高记录指尖碰桌面的 z（= 清单 desktop_height）；最后让臂真走一遍验证。
# 内参和测高都是独立入口：清单里已有内参时在内参页点「从清单导入内参」即可。
from steps.handeye import HandeyeStep
from steps.intrinsics import IntrinsicsStep
from steps.measure_z import MeasureZStep
from steps.verify import VerifyStep

STEP_CLASSES = [
    IntrinsicsStep,   # 1. 内参 K/D
    HandeyeStep,      # 2. 手眼单应（画面已去畸变）
    MeasureZStep,     # 3. 测高：指尖碰桌面的 z → desktop_height
    VerifyStep,       # 4. 验证：点一个点，臂尖去那儿
]
