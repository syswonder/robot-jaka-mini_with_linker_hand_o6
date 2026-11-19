# Description: 本部署的内参步骤——把公共实现（common/steps/intrinsics.py）绑到产物路径。
import paths
from common.steps.intrinsics import IntrinsicsStep as _IntrinsicsStep


class IntrinsicsStep(_IntrinsicsStep):
    paths = paths
