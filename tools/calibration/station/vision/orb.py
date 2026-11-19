# Description: OrbCamera —— 把 camera/orb_camera.py（pyorbbecsdk 直连 Gemini 336L，
# 钉死 1280x720，与运行时相机原语同口径）包装成标定台要的相机接口。
#
# 与 agilex piper/vision/camera.py 的 OrbCamera 职责相同，差异只有实现来源：
# 这里复用本部署旧 CLI 工具已经验证过的取流代码，不再复制一份 SDK 调用。
#
# 为什么多一层：MJPEG 流（Flask 长连接线程）与 /state 轮询会并发取图，而
# pipeline.wait_for_frames 不是重入安全的。所以内部加锁；页面绘制用 peek()
# 读最近一帧，不额外消耗 SDK 帧。
import threading

from camera import orb_camera


class OrbCamera:
    """满足 FrameSource 协议（get_frames/close），错误记在 last_error 供页面展示。"""

    def __init__(self):
        self.last_error: str | None = None
        self._lock = threading.Lock()
        self._latest = None
        self._pipeline = None
        try:
            self._pipeline = orb_camera.open_camera(color=True, depth=False)
        except Exception as e:  # noqa: BLE001
            # 与 agilex 的语境一致：打开失败把原因留在这里，页面显示而不是挂死。
            # （设备被 udev 权限挡住/被别的进程占着时，这里就是那句人话。）
            self.last_error = f"打开相机失败：{type(e).__name__}: {e}"

    def get_frames(self) -> dict:
        if self._pipeline is None:
            return {"color": None, "depth": None}
        try:
            with self._lock:
                frames = orb_camera.get_frames(self._pipeline)
        except Exception as e:  # noqa: BLE001
            self.last_error = f"取帧失败：{type(e).__name__}: {e}"
            return {"color": None, "depth": None}
        if frames.get("color") is not None:
            self.last_error = None
            self._latest = frames["color"]
        return frames

    def peek(self) -> object | None:
        """最近一次成功取到的彩色帧（不触发 SDK 取图）。"""
        return self._latest

    def close(self) -> None:
        if self._pipeline is not None:
            try:
                orb_camera.close_camera(self._pipeline)
            finally:
                self._pipeline = None
