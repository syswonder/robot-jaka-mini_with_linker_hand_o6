# Description: 验证——点画面上的一个点，让臂把夹爪尖移到那个点，人看对不对。
#
# 标定本身只能验"点对得上"（重投影误差）；这一步是拿真臂走一遍：像素 → 单应 → 臂平面
# (x, y) → 移到 (x, y, z)。不经过 robonix 栈（原语那套是运行时的事，标定时不必起）。
# 与 agilex 版的差异：JAKA 用控制器自带 IK（JakaDriver.move_pose 的直线 + IK 回退），
# 不需要 SLSQP/URDF 那套本地 IK。
import json
import threading
import time

import cv2
import numpy as np
from flask import jsonify, request

import paths
from common.homography import image_to_robot_xy
from common.ui import Step, stream_response


class VerifyStep(Step):
    name = "verify"
    title = "验证"
    desc = ("点画面上的一个点 → 臂把夹爪尖移到该点（像素经单应映射成臂坐标）。"
            "人看一眼夹爪尖是不是正好落在点的那件东西上。会动臂。")
    needs = ("camera", "arm")
    paths = paths

    def __init__(self):
        super().__init__()
        self.selected = None
        self.last = None          # 最近一次「移过去」的展示串（从 VERIFY_LAST 恢复）
        self.hint = "先在画面里点一个点，再点「移过去」。"
        self._lock = threading.Lock()
        self._load_last()

    # ---------- 验证记录 ----------

    def _load_last(self) -> None:
        rec = self._last_record()
        if rec:
            self.last = self._format_record(rec)

    def _last_record(self) -> dict | None:
        """上次验证记录；文件缺失/损坏时返回 None。"""
        try:
            with open(self.paths.VERIFY_LAST, encoding="utf-8") as f:
                rec = json.load(f)
            float(rec["pixel"][0]), float(rec["xyz"][0])
            return rec
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            return None

    @staticmethod
    def _format_record(rec: dict) -> str:
        px = rec["pixel"]
        xyz = rec["xyz"]
        return (f"像素 ({px[0]:.0f}, {px[1]:.0f}) → 臂 ({xyz[0]:.4f}, {xyz[1]:.4f}, "
                f"{xyz[2]:.4f})，已移过去；看夹爪尖对不对得上（{rec.get('ts', '')}）")

    def _save_record(self, sel, xyz) -> dict:
        rec = {"pixel": [float(sel[0]), float(sel[1])],
               "xyz": [float(v) for v in xyz],
               "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.paths.ARTIFACTS.mkdir(parents=True, exist_ok=True)
        with open(self.paths.VERIFY_LAST, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
        return rec

    def _homography_mtime(self) -> float | None:
        try:
            return self.paths.HOMOGRAPHY.stat().st_mtime
        except OSError:
            return None

    def _measured_record(self) -> dict | None:
        """测高记录（含可用的 desktop_z）；文件缺失/损坏时返回 None。"""
        try:
            with open(self.paths.HEIGHTS, encoding="utf-8") as f:
                rec = json.load(f)
            float(rec["desktop_z"])
            return rec
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _measured_z(self) -> float | None:
        """测高页记录的桌面 z（六指 0.2 手型下指尖碰桌面）；无效记录返回 None。"""
        rec = self._measured_record()
        if rec is None:
            return None
        try:
            return float(rec["desktop_z"])
        except (TypeError, ValueError):
            return None

    def guard(self) -> str | None:
        """验证会让臂移动到 (x, y, z)：内参、单应、测高缺一样都不许进。

        测高记录必须**能读出 z**，而不只是文件存在——损坏的记录会让 z 静默
        回退到配置默认值，臂按错误高度下降就是撞机。
        """
        missing = [name for name, p in (("内参", self.paths.CAMERA_MATRIX),
                                        ("单应矩阵", self.paths.HOMOGRAPHY),
                                        ("测高", self.paths.HEIGHTS)) if not p.exists()]
        if missing:
            return "缺" + "、".join(missing) + "，先把前几步做完（验证用的 z 来自测高页的记录）。"
        if self._measured_z() is None:
            return ("测高记录读不出桌面高度（artifacts/heights.json 损坏或不完整）——"
                    "先去测高页重新记录，验证才能动臂。")
        return None

    # ---------- 工具 ----------

    def _frame(self):
        """相机不可用时返回 None（MJPEG 生成器里抛异常会断流）。"""
        try:
            frame = self.rig.camera().get_frames()["color"]
        except Exception:  # noqa: BLE001
            return None
        if frame is None:
            return None
        if self.selected is not None:
            cv2.circle(frame, tuple(self.selected), 8, (0, 0, 255), -1)
        return frame

    def _default_z(self) -> float:
        # guard() 已保证记录有效；这里不做"回退到配置默认值"——那会让臂按
        # 未标定的高度下降（撞机风险）。
        measured = self._measured_z()
        if measured is None:
            raise RuntimeError("测高记录无效，拒绝以默认 z 动臂")
        return measured

    # ---------- 页面 ----------

    def html(self) -> str:
        default_z = self._default_z()
        return f"""
<div class="row">
  <div class="col"><img class="stream" src="/{self.name}/stream" alt="camera"></div>
  <div class="col">
    <!-- 两块分开：msg=按钮动作的结果（一直留到下次动作），hint=每秒轮询的状态 -->
    <div id="msg" class="status" style="display:none"></div>
    <div id="hint" class="status">{self.hint}</div>
    <div class="kv">已选点：<b id="sel">-</b>　·　映射到的臂坐标：<b id="xy">-</b>　·
      内参：<b id="intr">-</b></div>
    <p>
      目标 z（臂基座系，米）<input id="z" type="number" step="0.005" value="{default_z}" style="width:7em">
      <button class="primary" onclick="act('move')">移过去</button>
      <button onclick="act('pose')">回起始位姿</button>
    </p>
    <div class="kv">做的是：像素 → 单应 → 臂平面 (x, y) → 末端移到 (x, y, z)，末端垂直朝下。
      臂会动，确认周围没人再点。</div>
    <h2>最近一次</h2>
    <div class="mono" id="last">-</div>
  </div>
</div>
<script>
const img = document.querySelector("img.stream");
img.addEventListener("click", (e) => {{
  const rect = img.getBoundingClientRect();
  const x = Math.round((e.clientX - rect.left) * img.naturalWidth / rect.width);
  const y = Math.round((e.clientY - rect.top) * img.naturalHeight / rect.height);
  fetch("/{self.name}/click", {{method: "POST", headers: {{"Content-Type": "application/json"}},
                                body: JSON.stringify({{x, y}})}}).then(tick);
}});
async function act(what) {{
  const body = JSON.stringify({{z: +document.getElementById("z").value}});
  const j = await (await fetch("/{self.name}/" + what,
      {{method: "POST", headers: {{"Content-Type": "application/json"}}, body}})).json();
  const el = document.getElementById("msg");
  el.style.display = "block";
  el.textContent = j.hint || j.error || "";
  el.className = "status " + (j.ok ? "ok" : "err");
  tick();
}}
async function tick() {{
  try {{
    const s = await (await fetch("/{self.name}/state")).json();
    document.getElementById("sel").textContent = s.selected ? s.selected.join(", ") : "-";
    document.getElementById("xy").textContent = s.xy ? s.xy.map(v => v.toFixed(4)).join(", ") : "-";
    document.getElementById("intr").textContent = s.has_intrinsics ? "已标" : "未标（先去内参页）";
    document.getElementById("last").textContent = s.last || "-";
    if (s.hint) {{
      const el = document.getElementById("hint");
      el.textContent = s.hint;
      el.className = "status " + (s.ok ? "ok" : "err");
    }}
  }} catch (e) {{}}
}}
setInterval(tick, 900); tick();
</script>"""

    # ---------- 动作 ----------

    def register(self) -> None:
        app = self.app

        @app.get(f"/{self.name}/stream")
        def _stream():
            return stream_response(lambda: self._frame())

        @app.get(f"/{self.name}/state")
        def _state():
            xy = None
            if self.selected is not None and self.paths.HOMOGRAPHY.exists():
                xy = [float(v) for v in image_to_robot_xy(
                    np.load(self.paths.HOMOGRAPHY), self.selected)]
            return jsonify({"selected": list(self.selected) if self.selected else None, "xy": xy,
                            "has_intrinsics": self.rig.has_intrinsics(),
                            "last": self.last, "hint": self.hint,
                            "ok": self.last is not None})

        @app.post(f"/{self.name}/click")
        def _click():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            data = request.get_json(silent=True) or {}
            with self._lock:
                self.selected = (int(data.get("x", 0)), int(data.get("y", 0)))
            self.hint = "已选点 %s，点「移过去」让臂尖去那儿。" % (self.selected,)
            return jsonify({"ok": True, "hint": self.hint})

        @app.post(f"/{self.name}/move")
        def _move():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            missing = [name for name, p in (("内参", self.paths.CAMERA_MATRIX),
                                            ("单应矩阵", self.paths.HOMOGRAPHY)) if not p.exists()]
            if missing:
                return jsonify({"ok": False, "error": "还缺：" + "、".join(missing)})
            with self._lock:
                sel = self.selected
            if sel is None:
                return jsonify({"ok": False, "error": "先在画面里点一个点"})
            z = float((request.get_json(silent=True) or {}).get("z", self._default_z()))
            x, y = [float(v) for v in image_to_robot_xy(np.load(self.paths.HOMOGRAPHY), sel)]
            try:
                arm = self.rig.arm()
                if not arm.take_over():
                    return jsonify({"ok": False, "error": "接管臂失败（急停？）"})
                ok = arm.move_to([x, y, z], gripper_open_0to1=1)
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": f"动臂失败：{e}"})
            if not ok:
                self.hint = "移过去失败（无解或未到位），换个点或改 z 再试。"
                return jsonify({"ok": False, "error": self.hint})
            self.last = self._format_record(self._save_record(sel, [x, y, z]))
            self.hint = self.last
            print(self.last, flush=True)
            return jsonify({"ok": True, "hint": self.hint})

        @app.post(f"/{self.name}/pose")
        def _pose():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            try:
                arm = self.rig.arm()
                if not arm.take_over():
                    return jsonify({"ok": False, "error": "接管臂失败（急停？）"})
                if not arm.start_pose():
                    return jsonify({"ok": False, "error": "自动回起始位姿失败——用 free 按钮拖回观察位"})
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": f"回起始位姿失败：{e}"})
            self.hint = "已回起始位姿。"
            return jsonify({"ok": True, "hint": self.hint})

    # ---------- 摘要 ----------

    def summary(self) -> dict:
        """首页卡片。

        ok 表示"验证过了"，而不只是"前置条件齐备"：必须有一条成功的「移过去」
        记录，且它晚于当前单应矩阵——重新标定手眼后，旧验证就作废了。
        """
        rec = self._last_record()
        h_mtime = self._homography_mtime()
        try:
            rec_ts = time.mktime(time.strptime(rec["ts"], "%Y-%m-%d %H:%M:%S")) if rec else None
        except (KeyError, TypeError, ValueError):
            rec_ts = None
        stale = rec_ts is not None and h_mtime is not None and rec_ts < h_mtime
        ok = rec is not None and not stale
        lines = ["标定完点一个桌面上的点，让臂尖去那儿，肉眼看对不对。"]
        if rec is None:
            lines.append("还没验证过。")
        elif stale:
            lines.append(f"上次验证 {rec['ts']} 早于当前单应矩阵（{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(h_mtime))}）"
                         "——重新标定后需要再验证一次。")
        else:
            lines.append(self._format_record(rec))
        return {"ok": ok, "lines": lines}
