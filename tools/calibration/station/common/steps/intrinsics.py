# Description: 相机内参标定（棋盘格）——公共实现，产物路径由部署绑定（station/steps/intrinsics.py）。
#
# 画面是**原始**的（不能去畸变，不然标不出畸变来）。采到的图与解出来的 K/D 存哪儿，
# 由子类绑定的 `paths`（部署的 station/paths.py）决定，本文件不写死路径。
import cv2
import numpy as np
from flask import jsonify, request

from common.chessboard import (calibrate_from_images, compute_per_view_errors,
                               detect_chessboard_points, focal_uncertainty_warning)
from common.ui import Step, stream_response

# 默认值对着 tools/calibration/boards/ 里那两张 A4/A3 板子（12×8 格 → 11×7 个内角点）
DEFAULTS = {"cols": 11, "rows": 7, "min_images": 10}


class IntrinsicsStep(Step):
    name = "intrinsics"
    title = "内参"
    desc = ("手持棋盘格在相机前变换位姿（倾斜、旋转、平移，覆盖画面各个区域），"
            "检测到完整棋盘格时点「采集」。建议采 10~20 张，然后点「解算」。"
            "也可以直接用「从清单导入内参」沿用清单里的 AIIT 内参。")
    needs = ("camera", "arm")
    # 子类绑定：这个部署的 paths 模块（CAMERA_MATRIX / DIST_COEFFS / INTRINSICS_IMAGES）
    paths = None

    def __init__(self):
        super().__init__()
        self.result = None       # {"rms": float, "per_view": [(name, err)], "K":..., "D":..., "warning": str|None}
        self.hint = "画面里要能看到完整棋盘格，才点「采集」。"
        self._grab_error = None  # 最近一次取图失败原因（流里不抛，留给人话提示）

    # ---------- 页面 ----------

    def html(self) -> str:
        return f"""
<div class="row">
  <div class="col"><img class="stream" src="/{self.name}/stream" alt="camera"></div>
  <div class="col">
    <!-- 两块分开：msg=按钮动作的结果（一直留到下次动作），hint=每秒轮询的状态 -->
    <div id="msg" class="status" style="display:none"></div>
    <div id="hint" class="status">{self.hint}</div>
    <div class="kv">已采：<b id="count">0</b> 张　·　当前画面：<b id="det">-</b>　·
      内参文件：<b id="has">{'有' if self._has_files() else '无'}</b></div>
    <p>
      <input id="cols" type="number" value="{DEFAULTS['cols']}" style="width:5em" title="列（内角点数）">
      <input id="rows" type="number" value="{DEFAULTS['rows']}" style="width:5em" title="行（内角点数）">
      <span class="kv">列 × 行（内角点数）</span>
    </p>
    <p class="kv">列 × 行填的是<strong>内角点数</strong>，不是格子数——12×8 格的板子填 <b>11 × 7</b>（20×14 格的填 19 × 13）。
      填成格子数会一个角点都找不到。格子边长不用填：它只决定解出来的棋盘位姿有多大，解 K/D 用不到。</p>
    <p>
      <button class="primary" onclick="act('capture')">采集当前画面</button>
      <button onclick="act('solve')">解算 K/D</button>
      <button onclick="act('import_manifest')">从清单导入内参</button>
      <button onclick="act('write_manifest')">写入清单（K / D）</button>
      <button class="danger" onclick="if(confirm('删掉已采的所有图片？')) act('reset')">清空图片</button>
    </p>
    <h2>解算结果</h2>
    <div class="mono" id="res">-</div>
  </div>
</div>
<script>
function params() {{
  return {{ cols: +document.getElementById("cols").value,
            rows: +document.getElementById("rows").value,
            }};
}}
async function act(what) {{
  const j = await (await fetch("/{self.name}/" + what,
      {{method: "POST", headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify(params())}})).json();
  const el = document.getElementById("msg");
  el.style.display = "block";
  el.textContent = j.hint || j.error || "";
  el.className = "status " + (j.ok ? "ok" : "err");
  tick();
}}
async function tick() {{
  try {{
    // 带上输入框里的列×行：不带的话服务端按默认值判，页面永远显示"没检测到"
    const q = new URLSearchParams(params()).toString();
    const s = await (await fetch("/{self.name}/state?" + q)).json();
    document.getElementById("count").textContent = s.count;
    document.getElementById("det").textContent = s.detected ? "检测到完整棋盘格 ✓" : "没检测到完整棋盘格";
    document.getElementById("has").textContent = s.has_files ? "有" : "无";
    document.getElementById("res").textContent = s.result_text || "-";
    if (s.hint) {{
      const el = document.getElementById("hint");
      el.textContent = s.hint;
      el.className = "status " + (s.ok ? "ok" : "err");
    }}
  }} catch (e) {{}}
}}
setInterval(tick, 900); tick();
</script>"""

    # ---------- 工具 ----------

    def _pattern(self, params: dict):
        return (int(params.get("cols", DEFAULTS["cols"])), int(params.get("rows", DEFAULTS["rows"])))

    def _images(self) -> list[str]:
        d = self.paths.INTRINSICS_IMAGES
        return sorted(str(p) for p in d.glob("calib_*.png")) if d.exists() else []

    def _has_files(self) -> bool:
        return self.paths.CAMERA_MATRIX.exists() and self.paths.DIST_COEFFS.exists()

    def _grab(self):
        """取原始帧；相机不可用（未占用/已释放/打开失败）时返回 None。

        MJPEG 流在生成器里跑，抛异常会直接断流；所以统一吞成 None。
        """
        try:
            return self.rig.camera(raw=True).get_frames()["color"]
        except Exception as e:  # noqa: BLE001
            self._grab_error = str(e)
            return None

    # ---------- 动作 ----------

    def register(self) -> None:
        app = self.app

        @app.get(f"/{self.name}/stream")
        def _stream():
            return stream_response(lambda: self._grab())

        @app.get(f"/{self.name}/state")
        def _state():
            # 轮询只读不占用：占用了会在「结束本步骤」后 900ms 内把自己重新占上，
            # 别的页面永远看到这里占着（2026-11-18 现场踩到）。真正占用发生在
            # 点击类动作（capture/solve/import_manifest）里。
            owner = self.rig.owner()
            if owner and owner != self.name:
                return jsonify({"ok": False, "hint": f"「{owner}」正在用相机/臂。",
                                "count": len(self._images()), "detected": False,
                                "has_files": self._has_files(),
                                "result_text": self._result_text()})
            if self.rig.released():
                return jsonify({"ok": False, "hint": "已结束本步骤（相机/臂已释放）。点「采集当前画面」重新开始。",
                                "count": len(self._images()), "detected": False,
                                "has_files": self._has_files(),
                                "result_text": self._result_text()})
            params = {"cols": request.args.get("cols", DEFAULTS["cols"]),
                      "rows": request.args.get("rows", DEFAULTS["rows"])}
            try:
                frame = self._grab()
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "hint": f"取图失败：{e}", "count": len(self._images()),
                                "detected": False, "has_files": self._has_files(),
                                "result_text": self._result_text()})
            pattern = self._pattern(params)
            detected = frame is not None and detect_chessboard_points(frame, pattern) is not None
            hint = ("已检测到完整棋盘格，可以采集。" if detected
                    else "画面里没检测到完整棋盘格（别截边、别太斜、光照均匀一点）。")
            return jsonify({"ok": detected, "hint": hint, "count": len(self._images()),
                            "detected": detected, "has_files": self._has_files(),
                            "result_text": self._result_text()})

        @app.post(f"/{self.name}/capture")
        def _capture():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            params = request.get_json(silent=True) or {}
            pattern = self._pattern(params)
            frame = self._grab()
            if frame is None:
                return jsonify({"ok": False, "error": "没取到画面"})
            if detect_chessboard_points(frame, pattern) is None:
                return jsonify({"ok": False, "error": "没检测到完整棋盘格，这张没收"})
            self.paths.INTRINSICS_IMAGES.mkdir(parents=True, exist_ok=True)
            path = self.paths.INTRINSICS_IMAGES / f"calib_{len(self._images()):03d}.png"
            cv2.imwrite(str(path), frame)
            print(f"已采集: {path}", flush=True)
            return jsonify({"ok": True, "hint": f"已采集第 {len(self._images())} 张：{path.name}",
                            "count": len(self._images())})

        @app.post(f"/{self.name}/solve")
        def _solve():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            params = request.get_json(silent=True) or {}
            pattern = self._pattern(params)
            images = self._images()
            min_images = int(params.get("min_images", DEFAULTS["min_images"]))
            if len(images) < min_images:
                return jsonify({"ok": False, "error": f"图片太少（{len(images)} < {min_images}）"})
            try:
                K, D, rms = calibrate_from_images(images, pattern)
                self.paths.INTRINSICS_DIR.mkdir(parents=True, exist_ok=True)
                np.save(self.paths.CAMERA_MATRIX, K)
                np.save(self.paths.DIST_COEFFS, D)
                per_view = list(zip([p.rsplit("/", 1)[-1] for p in images],
                                    compute_per_view_errors(images, pattern, K, D)))
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": f"解算失败：{e}"})
            # 顺手量一下这组照片有没有把焦距约束住（慢，只在解算时做一次）
            warning = focal_uncertainty_warning(images, pattern)
            self.result = {"rms": rms, "per_view": per_view, "K": K.tolist(),
                           "D": D.ravel().tolist(), "warning": warning}
            print("内参标定完成：RMS=%.6f px -> %s" % (rms, self.paths.CAMERA_MATRIX), flush=True)
            return jsonify({"ok": True, "hint": f"解算完成：RMS={rms:.4f} px",
                            "result_text": self._result_text()})

        @app.post(f"/{self.name}/import_manifest")
        def _import_manifest():
            """本部署增强：清单里已有权威内参（AIIT 数据）时，一键写成 .npy。

            只读清单、只写已 gitignore 的 intrinsics-data/——不抢硬件、不改清单。
            """
            try:
                K, D, src = self.paths.read_manifest_intrinsics()
                self.paths.INTRINSICS_DIR.mkdir(parents=True, exist_ok=True)
                np.save(self.paths.CAMERA_MATRIX, K)
                np.save(self.paths.DIST_COEFFS, D)
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": f"导入失败：{e}"})
            self.hint = f"已从 {src} 导入内参（fx={K[0, 0]:.2f} fy={K[1, 1]:.2f}）。"
            return jsonify({"ok": True, "hint": self.hint, "result_text": self._result_text()})

        @app.post(f"/{self.name}/write_manifest")
        def _write_manifest():
            """把已有内参（刚解算的或从清单导入的 .npy）写回两份清单。"""
            if not self._has_files():
                return jsonify({"ok": False, "error": "还没有内参——先「解算 K/D」或「从清单导入内参」"})
            K = np.load(self.paths.CAMERA_MATRIX)
            D = np.load(self.paths.DIST_COEFFS)
            warn = self._implausible_warning(K)
            if warn:
                return jsonify({"ok": False, "error": "这组内参不可信，未写入清单：\n" + warn})
            try:
                written = self.paths.write_manifest_intrinsics(K, D)
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": f"写入失败：{e}"})
            self.hint = (f"已写入 camera_matrix / dist_coeffs（改到 {'、'.join(written)}）；"
                         "重启 start.sh 生效。")
            print(self.hint, flush=True)
            return jsonify({"ok": True, "hint": self.hint, "written": written})

        @app.post(f"/{self.name}/reset")
        def _reset():
            # 不抢硬件：只是删掉采集的图片，不碰相机
            removed = 0
            for p in self.paths.INTRINSICS_IMAGES.glob("calib_*.png") if self.paths.INTRINSICS_IMAGES.exists() else []:
                p.unlink()
                removed += 1
            self.result = None
            print(f"已清空 {removed} 张标定图片", flush=True)
            return jsonify({"ok": True, "hint": f"已清空 {removed} 张图片。", "count": 0})

    # ---------- 摘要 ----------

    def _result_text(self) -> str:
        if not self.result:
            return ("还没解算。\n" if not self._has_files()
                    else "artifacts 里已有内参（上次标定的或从清单导入的）；这次还没解算。")
        lines = [f"总体重投影误差 RMS: {self.result['rms']:.6f} px"]
        for warn in (self._implausible_warning(), self.result.get("warning")):
            if warn:
                lines += ["", warn]
        lines += ["", "K =", str(self.result["K"]),
                  "", "畸变系数 [k1,k2,p1,p2,k3] =", str(self.result["D"]), "", "每张图误差(px):"]
        lines += [f"  {name}: {err:.4f}" for name, err in self.result["per_view"]]
        return "\n".join(lines)

    def _implausible_warning(self, K: np.ndarray | None = None) -> str | None:
        """解出来的焦距得和画面宽度对得上。

        重投影误差小不等于参数对：照片拍得太单调（板子一直正对相机、位置/距离变化小）时，
        焦距和畸变没有约束，解算器能用一组离谱参数把点贴得很准。这个检查专抓这种解。
        """
        if K is None:
            if not self.result:
                return None
            K = np.array(self.result["K"])
        images = self._images()
        if not images:
            return None
        frame = cv2.imread(images[0])
        if frame is None:
            return None
        w = frame.shape[1]
        fx = float(K[0][0])
        lo, hi = 0.3 * w, 1.5 * w          # 视场约 37°~118°，常见相机都在这区间
        if lo <= fx <= hi:
            return None
        return (f"⚠️ 这组内参不可信：fx={fx:.0f}px，而 {w}px 宽的画面对应的常见焦距是 "
                f"{lo:.0f}~{hi:.0f}px；重投影误差小也可能出现在这种解里（模型自由度把点硬贴上了）。"
                "多半是照片拍得太单调——重拍时把板子倾斜 ±20~30°、放到画面四角和四边附近、远近也变一变，"
                "采 15 张以上再解。")

    def summary(self) -> dict:
        if self._has_files():
            K = np.load(self.paths.CAMERA_MATRIX)
            lines = [
                f"已有内参：fx={K[0, 0]:.2f} fy={K[1, 1]:.2f} cx={K[0, 2]:.2f} cy={K[1, 2]:.2f}",
                f"采过的图片：{len(self._images())} 张",
                "点「写入清单」同步 camera_matrix / dist_coeffs 到两份清单",
            ]
            warn = self._implausible_warning(K)
            if warn:
                lines.append(warn)
            return {"ok": not warn, "lines": lines}
        return {"ok": False, "lines": ["还没标内参。手眼标定必须先有内参（画面要去畸变）。"
                                       "清单里已有内参时可直接「从清单导入内参」。"]}
