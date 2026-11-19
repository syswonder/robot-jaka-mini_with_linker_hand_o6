# Description: 测高——记录"手指尖碰到桌面"时的末端 z，即 pick/place 用的 desktop_height。
#
# 流程：摆到起始位姿 → 用 free 按钮把指尖拖到桌面附近（或输入 xyz 点「移到该位置」微调）
# → 指尖接触桌面时点「记录当前 z」→ 点「写入清单」把值写进两份清单与工具 config.yaml。
#
# 与 agilex s10/steps/measure_z.py 的区别：那边记录后还要手动算 +2cm 余量；
# 本部署的 desktop_height 就是指尖触桌面这个 z（O6 手掌较长，0.125 → 0.14 的原因），
# 所以记录值原样写入，不做补偿。
import json
import time

from flask import jsonify, request

import paths
from common.ui import Step, stream_response
from utils.config_getter import get_config_value


class MeasureZStep(Step):
    name = "measure_z"
    title = "测高"
    desc = ("测高统一用六指开合度 0.2 的手型：先把六指设为 0.2 → 摆到起始位姿 → 按住"
            "free 按钮把指尖拖到桌面附近（或输入 xyz 点「移到该位置」低速微调）→ "
            "指尖刚好接触桌面时点「记录当前 z」→ 点「写入清单」。记录值原样作为桌面高度，"
            "不再另加偏移。")
    needs = ("camera", "arm")
    paths = paths
    # 测高时的手型：六指统一开合度（手闭合需要空间；0.2 是"指尖伸得足够低"的基准手型）
    MEASURE_HAND = 0.2

    def __init__(self):
        super().__init__()
        self.pos = None
        self.recorded = None
        self.hint = "先把六指设为 0.2，再摆到起始位姿、把指尖拖到桌面。"
        self._load()

    # ---------- 工具 ----------

    def _default_speed(self) -> float:
        return float(get_config_value("measure_z_linear_speed_mm_s", 10.0, raise_if_missing=False))

    def _max_speed(self) -> float:
        return float(get_config_value("linear_speed_mm_s", 100.0, raise_if_missing=False))

    def _load(self) -> None:
        if self.paths.HEIGHTS.exists():
            try:
                with open(self.paths.HEIGHTS, encoding="utf-8") as f:
                    self.recorded = json.load(f)
            except Exception:  # noqa: BLE001
                self.recorded = None

    def _frame(self):
        try:
            frame = self.rig.camera().get_frames()["color"]
        except Exception:  # noqa: BLE001
            return None
        return frame

    def _manifest_heights(self) -> dict:
        """清单里其它与高度相关的 z（只读显示，不自动改）。"""
        try:
            cfg, src = self.paths.read_manifest_jaka_config()
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        out = {"source": src}
        for key in ("grasp_pose", "hover_pose"):
            pose = cfg.get(key)
            if isinstance(pose, list) and len(pose) >= 3:
                out[key] = float(pose[2])
        if isinstance(cfg.get("desktop_height"), (int, float)):
            out["desktop_height"] = float(cfg["desktop_height"])
        return out

    # ---------- 页面 ----------

    def _height_inputs(self) -> dict:
        """页面里抓取/悬停输入框的初值：取清单现值。

        绝不默认 0 —— 输入框为空或 0 时用户随手点「写入清单」就会把 0 写进
        清单，臂会直接扎向基座。清单读不到时分别用 grasp 0.14 / hover 0.29
        作占位（值来自本部署历史配置），并在页面注明来源。
        """
        try:
            cfg, src = self.paths.read_manifest_jaka_config()
        except Exception:  # noqa: BLE001
            cfg, src = {}, ""
        grasp = cfg.get("grasp_pose")
        hover = cfg.get("hover_pose")
        g = float(grasp[2]) if isinstance(grasp, list) and len(grasp) >= 3 else 0.14
        h = float(hover[2]) if isinstance(hover, list) and len(hover) >= 3 else 0.29
        return {"grasp": g, "hover": h, "source": src or "（读不到清单）"}

    def html(self) -> str:
        tool_z = float(get_config_value("default_desktop_height", 0.14, raise_if_missing=False))
        hand = self.MEASURE_HAND
        heights = self._height_inputs()
        return f"""
<div class="row">
  <div class="col"><img class="stream" src="/{self.name}/stream" alt="camera"></div>
  <div class="col">
    <div id="msg" class="status" style="display:none"></div>
    <div id="hint" class="status">{self.hint}</div>
    <div class="kv">当前末端：<b id="pos">-</b>　·　拖动中：<b id="drag">-</b>　·　急停：<b id="estop">-</b></div>
    <p class="kv">测高固定用六指开合度 <b>{hand}</b> 的手型，「摆到起始位姿」会自动先写这个手型。</p>
    <p>
      <button class="primary" onclick="act('pose')">摆到起始位姿</button>
      <button class="primary" onclick="act('record')">记录当前 z（指尖碰桌面）</button>
    </p>
    <h2>微调到刚好接触桌面</h2>
    <p>
      x <input id="x" type="number" step="0.005" style="width:6.5em">
      y <input id="y" type="number" step="0.005" style="width:6.5em">
      z <input id="z" type="number" step="0.005" style="width:6.5em">
    </p>
    <p>
      速度 <input id="speed" type="number" step="1" min="1" max="{self._max_speed():.0f}"
                 value="{self._default_speed():.0f}" style="width:5.5em"> mm/s
      <button onclick="act('move')">移到该位置</button>
      <button onclick="fillCurrent()">填入机械臂当前末端位置</button>
    </p>
    <div class="kv">「移到该位置」保持当前末端朝向、只动 xyz，按上面的速度移动（默认
      {self._default_speed():.0f} mm/s，上限 {self._max_speed():.0f}）。速度只对这一次移动生效，
      结束后恢复标定配置的常规速度——桌面附近毫米级微调请用小值。</div>
    <p><button class="primary" onclick="act('write_manifest')">写入清单（桌面 / 抓取 / 悬停）</button></p>
    <h2>抓取与悬停高度</h2>
    <p class="kv">grab 用的两个 z，已按清单现值（{heights["source"]}）预填。
      抓取 = <code>grasp_pose[2]</code>，悬停 = <code>hover_pose[2]</code>（只改 z，x/y/rpy 不动）。</p>
    <p>
      抓取 z <input id="grasp_z" type="number" step="0.001" min="0.001"
                    value="{heights["grasp"]!r}" style="width:7em"> m
      悬停 z <input id="hover_z" type="number" step="0.001" min="0.001"
                    value="{heights["hover"]!r}" style="width:7em"> m
      <button onclick="fillHeights()">填入清单现值</button>
    </p>
    <h2>记录值</h2>
    <div class="mono" id="rec">-</div>
    <h2>清单当前高度（对照）</h2>
    <div class="mono" id="heights">-</div>
  </div>
</div>
<script>
async function act(what) {{
  const num = (id) => {{ const v = document.getElementById(id).value.trim(); return v === "" ? null : +v; }};
  const body = JSON.stringify({{
    x: +document.getElementById("x").value, y: +document.getElementById("y").value,
    z: +document.getElementById("z").value,
    speed: +document.getElementById("speed").value,
    grasp_z: num("grasp_z"),
    hover_z: num("hover_z")}});
  const j = await (await fetch("/{self.name}/" + what,
      {{method: "POST", headers: {{"Content-Type": "application/json"}}, body}})).json();
  const el = document.getElementById("msg");
  el.style.display = "block";
  el.textContent = (j.hint || j.error || "") + (j.written ? "（已改: " + j.written.join(", ") + "）" : "");
  el.className = "status " + (j.ok ? "ok" : "err");
  tick();
}}
function fillCurrent() {{
  const p = document.getElementById("pos").textContent;
  const m = p.match(/\\[([-\\d.]+), ([-\\d.]+), ([-\\d.]+)\\]/);
  if (m) {{
    document.getElementById("x").value = m[1];
    document.getElementById("y").value = m[2];
    document.getElementById("z").value = m[3];
  }}
}}
function fillHeights(manifest) {{
  if (manifest) {{
    if (manifest.grasp_pose !== undefined) document.getElementById("grasp_z").value = manifest.grasp_pose.toFixed(6);
    if (manifest.hover_pose !== undefined) document.getElementById("hover_z").value = manifest.hover_pose.toFixed(6);
    return;
  }}
  fetch("/{self.name}/state").then(r => r.json()).then(s => {{ if (s.manifest) fillHeights(s.manifest); }});
}}
async function tick() {{
  try {{
    const s = await (await fetch("/{self.name}/state")).json();
    document.getElementById("pos").textContent = s.pos || "-";
    document.getElementById("drag").textContent = s.dragging === null ? "?" : (s.dragging ? "是" : "否");
    document.getElementById("estop").textContent = s.estop === null ? "?" : (s.estop ? "已按下" : "正常");
    document.getElementById("rec").textContent = s.rec_text || "还没记录。";
    document.getElementById("heights").textContent = s.heights || "-";
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
            # 只读轮询（与内参页同一条教训：轮询 acquire 会把已结束的步骤重新占上）。
            owner = self.rig.owner()
            if owner and owner != self.name:
                return jsonify({"ok": False, "hint": f"「{owner}」正在用相机/臂。",
                                "pos": self.pos, "dragging": None, "estop": None,
                                "recorded": self.recorded, "heights": self._heights_text(),
                                "manifest": self._manifest_heights()})
            dragging = estop = None
            try:
                arm = self.rig.arm()
                self.pos = [round(v, 4) for v in arm.end_pos()]
                st = arm.status()
                dragging, estop = st.get("dragging"), st.get("estop")
            except Exception as e:  # noqa: BLE001
                if "已按「结束本步骤」释放" in str(e):
                    return jsonify({"ok": False, "hint": str(e), "pos": self.pos,
                                    "dragging": None, "estop": None,
                                    "recorded": self.recorded, "heights": self._heights_text(),
                                "manifest": self._manifest_heights()})
                # 臂连不上不影响记录展示；把原因给人话
                self.hint = f"读臂失败：{e}"
            hint = self.hint
            ok = self.recorded is not None
            if dragging:
                hint = "拖动中：指尖接触桌面后点「记录当前 z」。"
                ok = True
            return jsonify({"ok": ok, "hint": hint,
                            "pos": "[%.4f, %.4f, %.4f]" % tuple(self.pos) if self.pos else None,
                            "dragging": dragging, "estop": estop,
                            "recorded": self.recorded, "rec_text": self._rec_text(),
                            "heights": self._heights_text(),
                            "manifest": self._manifest_heights()})


        @app.post(f"/{self.name}/pose")
        def _pose():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            try:
                arm = self.rig.arm()
                if arm.status().get("dragging"):
                    return jsonify({"ok": False, "error": "仍在拖动中（free 按钮未松开）"})
                if not arm.take_over():
                    return jsonify({"ok": False, "error": "接管臂失败（急停？）"})
                # 测高固定手型：先写六指 = MEASURE_HAND，再摆臂，保证指尖伸出的长度可复现
                if not arm.set_hand_joint_positions_normalized([self.MEASURE_HAND] * 6):
                    return jsonify({"ok": False, "error": "写手部失败（手链路不可用？end_effector 是 hand 吗）"})
                if not arm.start_pose():
                    return jsonify({"ok": False, "error": "自动回起始位姿失败——用 free 按钮手动摆位即可"})
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": f"动臂失败：{e}"})
            self.hint = ("六指已设为 %.2f 并到起始位姿。按住 free 按钮把指尖拖到桌面附近。"
                         % self.MEASURE_HAND)
            return jsonify({"ok": True, "hint": self.hint})

        @app.post(f"/{self.name}/move")
        def _move():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            data = request.get_json(silent=True) or {}
            try:
                target = [float(data["x"]), float(data["y"]), float(data["z"])]
            except (KeyError, TypeError, ValueError):
                return jsonify({"ok": False, "error": "x/y/z 三个数都要填（先点「填入机械臂当前末端位置」再改）"})
            try:
                speed = float(data.get("speed", self._default_speed()))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "速度要填数值（mm/s）"})
            if not 1.0 <= speed <= self._max_speed():
                return jsonify({"ok": False, "error": "速度必须在 1~%.0f mm/s 之间" % self._max_speed()})
            try:
                arm = self.rig.arm()
                if arm.status().get("dragging"):
                    return jsonify({"ok": False, "error": "仍在拖动中（free 按钮未松开）"})
                if not arm.take_over():
                    return jsonify({"ok": False, "error": "接管臂失败（急停？）"})
                if not arm.move_to_keep_orientation(target, slow_mm_s=speed):
                    return jsonify({"ok": False, "error": "移动失败（无解或未到位），改小步长再试"})
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": f"动臂失败：{e}"})
            self.hint = ("已以 %.0f mm/s 移到 [%.4f, %.4f, %.4f]。指尖接触桌面后点「记录当前 z」。"
                         % (speed, target[0], target[1], target[2]))
            return jsonify({"ok": True, "hint": self.hint})

        @app.post(f"/{self.name}/record")
        def _record():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            data = request.get_json(silent=True) or {}
            try:
                pos = [float(v) for v in self.rig.arm().end_pos()]
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": f"读末端位姿失败：{e}"})
            # 指尖碰桌面的 z 直接作为桌面高度；不再加偏移（手型固定 0.2 已含闭合空间）
            record = {"desktop_z": pos[2], "hand": [self.MEASURE_HAND] * 6,
                      "pos": pos, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            self.paths.ARTIFACTS.mkdir(parents=True, exist_ok=True)
            with open(self.paths.HEIGHTS, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
            self.recorded = record
            self.hint = ("已记录桌面 z = %.4f m（手型 %.2f）。点「写入清单」应用。"
                         % (pos[2], self.MEASURE_HAND))
            print("已记录 desktop_z=%.4f（手型 %.2f）-> %s"
                  % (pos[2], self.MEASURE_HAND, self.paths.HEIGHTS), flush=True)
            return jsonify({"ok": True, "hint": self.hint, "recorded": record,
                            "rec_text": self._rec_text()})

        @app.post(f"/{self.name}/write_manifest")
        def _write_manifest():
            if self.recorded is None:
                return jsonify({"ok": False, "error": "还没有记录值——先点「记录当前 z」"})
            try:
                desktop_z = float(self.recorded["desktop_z"])
            except (KeyError, TypeError, ValueError):
                return jsonify({"ok": False, "error": "记录里没有可用的 desktop_z——重新记录一次"})
            data = request.get_json(silent=True) or {}
            # 整格删除时 JS 送 null / 空串 → 当作"没填"跳过，绝不能把 0 写进
            # 清单（悬停/抓取为 0 会让臂直接扎向基座）。
            heights: dict[str, float | None] = {}
            for key in ("grasp_z", "hover_z"):
                raw = data.get(key)
                if raw is None or raw == "":
                    heights[key] = None
                    continue
                try:
                    val = float(raw)
                except (TypeError, ValueError):
                    return jsonify({"ok": False, "error": f"{key} 要填数值"})
                if not val > 0.0:
                    return jsonify({"ok": False, "error":
                                    f"{key} 必须大于 0（为 0 会把臂扎向基座）；"
                                    "不想改的项点「填入清单现值」恢复"})
                if val > 2.0:
                    return jsonify({"ok": False, "error": f"{key} 超出合理范围（0~2 m）"})
                heights[key] = val
            try:
                written = self.paths.write_desktop_height(
                    desktop_z, tool_z=desktop_z,
                    grasp_z=heights["grasp_z"], hover_z=heights["hover_z"])
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": f"写入失败：{e}"})
            parts = [f"desktop_height = {desktop_z:.4f}"]
            if heights["grasp_z"] is not None:
                parts.append(f"grasp_pose z = {heights['grasp_z']:.4f}")
            if heights["hover_z"] is not None:
                parts.append(f"hover_pose z = {heights['hover_z']:.4f}")
            self.hint = ("已写入 %s（改到 %s）；重启 start.sh 生效。"
                         % ("，".join(parts), "、".join(written)))
            print(self.hint, flush=True)
            return jsonify({"ok": True, "hint": self.hint, "written": written})

    # ---------- 摘要 ----------

    def _rec_text(self) -> str:
        rec = self.recorded
        if not rec:
            return "还没记录。"
        pos = rec.get("pos") or []
        text = f"桌面 z = {rec['desktop_z']:.4f} m（{rec.get('ts', '')}）"
        hand = rec.get("hand")
        if hand:
            text += "\n测高时手型 = [" + ", ".join("%.2f" % v for v in hand) + "]"
        if pos:
            text += "\n记录时末端 = [" + ", ".join("%.4f" % v for v in pos) + "]"
        return text

    def _heights_text(self) -> str:
        heights = self._manifest_heights()
        if "error" in heights:
            return "读清单失败：" + heights["error"]
        rec = self.recorded or {}
        desktop_z = rec.get("desktop_z")
        lines = [f"清单来源：{heights.pop('source')}"]

        rows = [
            ("desktop_height", "desktop_height", desktop_z),
            ("grasp_pose[2] 抓取", "grasp_pose", None),
            ("hover_pose[2] 悬停", "hover_pose", None),
        ]
        for label, key, want in rows:
            if key not in heights:
                continue
            z = heights[key]
            if want is None:
                flag = "　（不随测高改动）" if key != "desktop_height" else ""
            else:
                flag = "✓" if abs(z - want) < 1e-6 else f"　（应为 {want:.4f}）"
            lines.append(f"{label:<22} {z:.4f}{flag}")
        if not rec:
            lines.append("（先记录一次，才能显示与记录值的对照）")
        return "\n".join(lines)

    def summary(self) -> dict:
        if not self.recorded:
            return {"ok": False, "lines": ["还没测过桌面高度（六指 0.2 手型下指尖碰桌面的 z）。"]}
        rec = self.recorded
        try:
            cfg, src = self.paths.read_manifest_jaka_config()
            cur = cfg.get("desktop_height")
            ok = isinstance(cur, (int, float)) and abs(cur - rec["desktop_z"]) < 1e-6
            lines = [
                f"桌面 z = {rec['desktop_z']:.4f} m（手型 {self.MEASURE_HAND}）",
                f"清单 desktop_height = {cur}（{src}）：{'一致' if ok else '不一致，需写入'}",
            ]
            return {"ok": ok, "lines": lines}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "lines": [f"记录：桌面 {rec['desktop_z']:.4f} m", f"读清单失败：{e}"]}
