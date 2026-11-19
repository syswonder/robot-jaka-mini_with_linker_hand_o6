#!/usr/bin/env python3
"""JAKA 网页标定台：一个 Flask 服务，把标定流程的每一步挂成一页（宿主上跑，不用 ROS）。

    bash tools/calibration/setup_venv.sh        # 必须 Python 3.10（jkrc.so）
    cd tools/calibration
    .venv/bin/python station/app.py --port 8080
    # 浏览器打开 http://<本机IP>:8080

页面：
    /            总览：每一步的产物状态，从这里进各个步骤
    /intrinsics  相机内参（棋盘格；画面是原始的，不去畸变；也可从清单导入）
    /handeye     手眼标定（2D 单应；画面已按内参去畸变）
    /verify      验证：点一个点，让臂尖移过去，肉眼看对不对

标定期间 **robonix 必须停机**（相机/控制器独占；直连 jkrc），急停在手、工作区清空。
退出用 Ctrl-C（别 kill：UVC 不释放会"能开但不出帧"）。
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # tools/calibration/station
TOOL = ROOT.parent                              # tools/calibration（calib_arm / config.yaml / camera 在这）
sys.path.insert(0, str(ROOT))                   # common/ paths/ steps/ vision/
sys.path.insert(0, str(TOOL))                   # calib_arm / utils / camera

from flask import Flask, jsonify, redirect, request  # noqa: E402

import paths  # noqa: E402
from common.rig import Rig  # noqa: E402
from common.robot.jaka import JakaArm  # noqa: E402
from common.ui import card_html, page  # noqa: E402
from steps import STEP_CLASSES  # noqa: E402
from vision.orb import OrbCamera  # noqa: E402

app = Flask(__name__)
app.site_name = "JAKA 标定台"
rig = Rig(camera_factory=OrbCamera, arm_factory=JakaArm, paths=paths)
STEPS = [cls() for cls in STEP_CLASSES]

for _step in STEPS:
    _step.mount(app, rig, STEPS)


def _step_by_owner(owner: str):
    return next((s for s in STEPS if s.name == owner), None)


@app.get("/")
def home():
    rig_status = rig.status()
    cards = [card_html(step) for step in STEPS]
    head = (
        f'<h1>JAKA 标定台</h1><div class="desc">三步：内参 → 手眼 → 验证。'
        f"标定期间 robonix 必须停机（相机/控制器独占），急停在手、工作区清空。"
        f"标定完把数值抄进两份清单（robonix_manifest.yaml 与 .local.yaml）。"
        f"手眼那步用的画面一定是去畸变后的，与运行时一致。</div>"
        f'<div class="status">硬件占用：<b>{rig_status["owner"] or "空闲"}</b>　'
        f'相机：<b>{"已打开" if rig_status["camera_open"] else "未打开"}</b>　'
        f'内参：<b>{"已标" if rig_status["has_intrinsics"] else "未标"}</b>'
        + ('　（已结束本步骤，相机/臂已释放；进任意步骤做动作即重新开始）'
           if rig_status["released"] else "") +
        '</div>'
    )
    return page("总览", f'{head}<div class="grid">{"".join(cards)}</div>', STEPS, active="")


@app.get("/api/rig")
def api_rig():
    return jsonify(rig.status())


@app.post("/rig/take")
def rig_take():
    """强制接管硬件（页面上那个按钮）：把别的步骤挤掉，自己占上。"""
    owner = (request.get_json(silent=True) or {}).get("owner", "")
    step = _step_by_owner(owner)
    if step is None:
        return jsonify({"ok": False, "error": f"未知步骤: {owner}"}), 400
    try:
        rig.acquire(owner, force=True)
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "owner": owner})


@app.post("/rig/release")
def rig_release():
    owner = (request.get_json(silent=True) or {}).get("owner", "")
    try:
        rig.release(owner)
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True})


@app.get("/favicon.ico")
def favicon():
    return redirect("/", code=302)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="JAKA 网页标定台（Flask 一体服务，宿主上跑）")
    ap.add_argument("--host", default="0.0.0.0", help="绑定地址；默认全网卡（局域网/手机可开）")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--debug", action="store_true", help="Flask 调试模式（会重启进程）")
    return ap.parse_args()


def main():
    args = parse_args()
    print("=" * 56, flush=True)
    print("JAKA 标定台: http://<本机IP>:%d（本机打开用 http://127.0.0.1:%d）"
          % (args.port, args.port), flush=True)
    print("产物目录: %s" % os.path.relpath(TOOL, os.getcwd()), flush=True)
    print("提醒: robonix 必须停机（相机/控制器独占）；退出用 Ctrl-C，别 kill。", flush=True)
    print("=" * 56, flush=True)
    app.run(host=args.host, port=args.port, threaded=True, debug=args.debug, use_reloader=False)


if __name__ == "__main__":
    main()
