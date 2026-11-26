# Description: 从部署清单读机械臂控制器 IP——清单 `primitive: name: jaka_arm →
# config: ip` 是唯一出处，标定工具与急停复位脚本都从这里取，不再各存一份。

from pathlib import Path

import yaml

DEPLOY_ROOT = Path(__file__).resolve().parents[3]  # utils → calibration → tools → 部署根


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
