# Description: 网页标定台的产物路径——与旧 CLI 工具共用 tools/calibration/ 下的
# intrinsics-data/ 与 artifacts/，两套入口互操作（旧工具 --mode test 直接复用新矩阵）。
#
# 属性名与 agilex 的 paths 约定对齐（公共代码 common/ 认的就是这些名字），另加
# read_manifest_intrinsics() 供“从清单导入内参”按钮使用。
from pathlib import Path
import re

import numpy as np
import yaml

TOOL_ROOT = Path(__file__).resolve().parent.parent      # tools/calibration
DEPLOY_ROOT = TOOL_ROOT.parent.parent                   # 部署根

# 内参：沿用旧工具的 intrinsics-data/（旧 Camera(undistort=True) 读的也是这里）
INTRINSICS_DIR = TOOL_ROOT / "intrinsics-data"
CAMERA_MATRIX = INTRINSICS_DIR / "camera_matrix.npy"
DIST_COEFFS = INTRINSICS_DIR / "dist_coeffs.npy"

# 内参标定的采集图
ARTIFACTS = TOOL_ROOT / "artifacts"
INTRINSICS_IMAGES = ARTIFACTS / "intrinsics" / "images"

# 手眼标定（沿用旧工具的文件名：calibrate_handeye_2d.py 的产物就是这几个）
HANDEYE_DIR = ARTIFACTS
HOMOGRAPHY = HANDEYE_DIR / "2d_homography.npy"
HANDEYE_IMAGE_POINTS = HANDEYE_DIR / "2d_image_points.npy"
HANDEYE_ROBOT_POINTS = HANDEYE_DIR / "2d_robot_points.npy"

# 测高（station/steps/measure_z.py 产出）：指尖碰桌面时的 TCP z 记录
HEIGHTS = ARTIFACTS / "heights.json"

# 验证（station/steps/verify.py 产出）：最近一次「移过去」的记录。
# 首页卡片靠它判断"验证过了"——验证本身没有产物文件，只有这一条运行记录。
VERIFY_LAST = ARTIFACTS / "verify_last.json"

# 工具自己的 config.yaml（旧 CLI 与验证页共用；写 default_desktop_height）
CONFIG_YAML = TOOL_ROOT / "config.yaml"


def _manifest_candidates() -> list[Path]:
    """清单查找顺序：本地覆盖优先，其次入库版本（与 build.sh/start.sh 的选择一致）。"""
    return [DEPLOY_ROOT / "robonix_manifest.local.yaml",
            DEPLOY_ROOT / "robonix_manifest.yaml"]


def read_manifest_intrinsics() -> tuple[np.ndarray, np.ndarray, str]:
    """读清单 skill: name: jaka 下的 camera_matrix / dist_coeffs。

    Returns:
        (K 3x3, D, 清单相对路径字符串)

    Raises:
        FileNotFoundError / KeyError / ValueError: 清单或字段缺失、形状不对。
    """
    for path in _manifest_candidates():
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for skill in data.get("skill") or []:
            if skill.get("name") != "jaka":
                continue
            cfg = skill.get("config") or {}
            if "camera_matrix" not in cfg or "dist_coeffs" not in cfg:
                raise KeyError(f"{path.name}: skill jaka 缺少 camera_matrix / dist_coeffs")
            K = np.array(cfg["camera_matrix"], dtype=np.float64)
            D = np.array(cfg["dist_coeffs"], dtype=np.float64)
            if K.shape != (3, 3):
                raise ValueError(f"camera_matrix 形状是 {K.shape}，应为 (3, 3)")
            if D.ndim != 1:
                raise ValueError(f"dist_coeffs 应为 1 维，收到形状 {D.shape}")
            return K, D, path.name
    raise FileNotFoundError("找不到 robonix_manifest.yaml / robonix_manifest.local.yaml")


def read_manifest_jaka_config() -> tuple[dict, str]:
    """读清单 skill: name: jaka 的 config 块（测高页要显示 grasp_pose 等高度）。"""
    for path in _manifest_candidates():
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for skill in data.get("skill") or []:
            if skill.get("name") == "jaka":
                return (skill.get("config") or {}), path.name
    raise FileNotFoundError("找不到含 skill: jaka 的清单")


def _height_line(key: str) -> re.Pattern:
    """匹配单行 `key: 数值`（保留缩进；数值后可带行尾注释）。"""
    return re.compile(
        rf"^([ \t]*{re.escape(key)}:[ \t]*)([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)(.*)$",
        re.M,
    )


def _rewrite_line(path: Path, key: str, value: float) -> bool:
    """把单行 `key: 数值` 原地替换（保留缩进与行尾注释）。

    只做行级替换、不 dump 整份 YAML——清单里大量人工注释必须原样保留。
    找不到该键或文件不存在时返回 False。
    """
    if not path.exists():
        return False
    pattern = _height_line(key)
    text = path.read_text(encoding="utf-8")
    new_text, count = pattern.subn(
        lambda m: f"{m.group(1)}{round(float(value), 6)!r}{m.group(3)}", text)
    if count == 0:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def _rewrite_pose_z(path: Path, key: str, z: float) -> bool:
    """把单行 `key: [x, y, z, rx, ry, rz]` 的第三个数（z）替换，其余原样保留。

    只改 z：姿态的 x/y/rpy 是各自标定的，不能跟着桌面值一起动。
    """
    if not path.exists():
        return False
    pattern = re.compile(
        rf"^([ \t]*{re.escape(key)}:[ \t]*\[)([^]]*)(\])(.*)$", re.M)

    def repl(m: re.Match) -> str:
        parts = [p.strip() for p in m.group(2).split(",")]
        if len(parts) != 6:
            return m.group(0)
        parts[2] = repr(round(float(z), 6))
        return m.group(1) + ", ".join(parts) + m.group(3) + m.group(4)

    text = path.read_text(encoding="utf-8")
    new_text, count = pattern.subn(repl, text)
    if count == 0:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def _write_manifests(edits: list[tuple[str, str, float]]) -> list[str]:
    """对两份清单（存在的那份/那几份）执行 [(kind, key, value)] 行级改写。

    kind: "scalar" = `key: 数值`；"pose_z" = 六元状态列表只换 z。
    返回实际改写的文件清单；一处都没改到时抛 RuntimeError。
    """
    written: list[str] = []
    for path in (DEPLOY_ROOT / "robonix_manifest.local.yaml",
                 DEPLOY_ROOT / "robonix_manifest.yaml"):
        if not path.exists():
            continue
        changed = False
        for kind, key, value in edits:
            if kind == "pose_z":
                changed = _rewrite_pose_z(path, key, value) or changed
            else:
                changed = _rewrite_line(path, key, value) or changed
        if changed:
            written.append(path.name)
    if not written:
        raise RuntimeError("两份清单里都没找到要改的行")
    return written


def write_desktop_height(desktop_z: float, tool_z: float | None = None,
                         grasp_z: float | None = None,
                         hover_z: float | None = None) -> list[str]:
    """把桌面/抓取/悬停高度写进消费它们的地方，返回实际改写的文件清单。

    1. 两份清单的 skill: jaka → desktop_height = 六指 0.2 手型下指尖碰桌面的 z
    2. grasp_z 给定时 → grasp_pose[2]（只改 z，x/y/rpy 不动）
    3. hover_z 给定时 → hover_pose[2]（同理）
    4. tools/calibration/config.yaml → default_desktop_height = tool_z（给定时）

    grasp/hover 不自动从 desktop 推导：抓取时手是张开的，指尖位置与测高
    手型（0.2）不同，具体值由操作员在测高页填写。
    """
    edits: list[tuple[str, str, float]] = [("scalar", "desktop_height", float(desktop_z))]
    if grasp_z is not None:
        edits.append(("pose_z", "grasp_pose", float(grasp_z)))
    if hover_z is not None:
        edits.append(("pose_z", "hover_pose", float(hover_z)))
    written = _write_manifests(edits)
    if tool_z is not None and _rewrite_line(CONFIG_YAML, "default_desktop_height", float(tool_z)):
        written.append(str(CONFIG_YAML.relative_to(DEPLOY_ROOT)))
    return written
