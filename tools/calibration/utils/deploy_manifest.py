# Description: 从部署清单读机械臂的权威数值（控制器 IP、回零关节角、悬停位姿）——
# 清单是唯一出处，标定工具与急停复位脚本都从这里取，不再各存一份。

from pathlib import Path

import yaml

DEPLOY_ROOT = Path(__file__).resolve().parents[3]  # utils → calibration → tools → 部署根


def _section_config(section: str, entry: str) -> dict:
    """返回清单 `section` 下首个别名为 `entry` 的条目 → config 块（本地覆盖清单优先）。"""
    for fname in ("robonix_manifest.local.yaml", "robonix_manifest.yaml"):
        path = DEPLOY_ROOT / fname
        if not path.exists():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for item in data.get(section) or []:
            if item.get("name") == entry:
                return item.get("config") or {}
    return {}


def read_arm_ip() -> str:
    """返回 jaka_arm 原语配置的控制器 IP。

    查找顺序与 build.sh/start.sh 一致：本地覆盖清单优先，其次入库版本。

    Raises:
        FileNotFoundError: 两份清单都不存在，或都不含 primitive: jaka_arm。
        KeyError: 清单里的 jaka_arm 缺少 config.ip。
    """
    for name in ("robonix_manifest.local.yaml", "robonix_manifest.yaml"):
        path = DEPLOY_ROOT / name
        if not path.exists():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for primitive in data.get("primitive") or []:
            if primitive.get("name") != "jaka_arm":
                continue
            ip = (primitive.get("config") or {}).get("ip")
            if not ip:
                raise KeyError(f"{name}: primitive jaka_arm 缺少 config.ip")
            return str(ip)
    raise FileNotFoundError(
        f"{DEPLOY_ROOT} 下找不到含 primitive: jaka_arm 的清单"
    )


def read_shutdown_home_joints() -> list[float]:
    """返回 jaka_arm 回零关节角（弧度，6 个）：primitive: jaka_arm → config: shutdown_home_joints。

    与运行时原语退出时的归位目标、skill 的 `home_joints` 同一来源。
    缺省/格式不对回退全零（JAKA 参考位）。
    """
    raw = _section_config("primitive", "jaka_arm").get("shutdown_home_joints")
    if isinstance(raw, (list, tuple)) and len(raw) == 6:
        try:
            return [float(v) for v in raw]
        except (TypeError, ValueError):
            pass
    return [0.0] * 6


def read_hover_pose() -> list[float] | None:
    """返回 skill: jaka → config: hover_pose（[x, y, z, rx, ry, rz]），缺省 None。"""
    raw = _section_config("skill", "jaka").get("hover_pose")
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        try:
            return [float(v) for v in raw]
        except (TypeError, ValueError):
            return None
    return None
