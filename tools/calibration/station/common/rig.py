# Description: 相机与臂的唯一持有者——一个进程里只该有一条相机 pipeline 和一条控制器会话。
#
# 从 agilex common/rig.py 移植；差异只有一个：臂类也由部署注入（arm_factory），
# 因为本部署的臂是 JAKA（jkrc 直连），不是 Piper 的 CAN 会话。相机/臂都是独占硬件：
# 步骤要用就先 acquire()，别的步骤进来会拿到一句明确的报错，用完 release()。
import threading

from common.camera import UndistortCamera


class Rig:
    def __init__(self, camera_factory, arm_factory, paths):
        """
        Args:
            camera_factory: 无参可调用，返回一个相机对象（有 get_frames/close）。
            arm_factory: 无参可调用，返回一个臂会话（见 common/robot/jaka.py 的接口面）。
            paths: 这个部署的产物路径模块（去畸变要读里面的内参文件）。
        """
        self._camera_factory = camera_factory
        self._arm_factory = arm_factory
        self._paths = paths
        self._lock = threading.Lock()
        self._owner: str | None = None
        self._released = False   # 点过「结束本步骤」：在下次 acquire 之前不碰硬件
        self._cam = None
        self._cam_error: str | None = None   # 打开相机失败的原因（页面上要看得到）
        self._cam_undist: UndistortCamera | None = None
        self._arm = None

    # ---------- 占用 ----------

    def acquire(self, owner: str, force: bool = False) -> None:
        """声明占用；被别的步骤占着就抛 RuntimeError（页面把这句话显示出来）。

        Args:
            owner: 步骤名。
            force: `True` = 页面上点了「强制接管」，把别的步骤挤掉。
        """
        with self._lock:
            if self._owner and self._owner != owner and not force:
                raise RuntimeError(f"「{self._owner}」正在用相机/臂，先到那个页面点「结束本步骤」。")
            self._owner = owner
            self._released = False

    def release(self, owner: str) -> None:
        """释放硬件：关相机、断臂，并保持关闭直到下次 acquire。

        `_released` 是必需的：MJPEG 流和各页轮询会持续取图，不拦住的话相机
        几毫秒内就被重新打开，「结束本步骤」形同虚设（boot 前腾不出相机）。
        """
        with self._lock:
            if self._owner not in (None, owner):
                raise RuntimeError(f"「{self._owner}」正占着，不能由「{owner}」释放。")
            self._owner = None
            self._released = True
            self._cam_error = None   # 重新开始：下次 acquire 后允许再试一次
            self._close_hardware()

    def released(self) -> bool:
        return self._released

    def owner(self) -> str | None:
        return self._owner

    # ---------- 硬件（懒建） ----------

    def has_intrinsics(self) -> bool:
        return self._paths.CAMERA_MATRIX.exists() and self._paths.DIST_COEFFS.exists()

    def camera(self, raw: bool = False):
        """取相机。

        Args:
            raw: `True` = 原始画面（内参标定用——去畸变就看不出畸变了）；
                `False` = 有内参就去畸变（手眼标定、验证要与运行时一致）。
        """
        with self._lock:
            if self._released:
                raise RuntimeError("已按「结束本步骤」释放相机/臂——在页面上做任意动作可重新开始")
            if self._cam is None:
                # 打开失败后不逐帧重试：Orbbec 的 UVC 管线反复创建会让设备卡住
                # （"能开但不出帧"）。失败原因留在 camera_error，重启本步骤即可重试。
                if self._cam_error:
                    raise RuntimeError(self._cam_error)
                try:
                    self._cam = self._camera_factory()
                    self._cam_error = None
                except Exception as e:  # noqa: BLE001
                    self._cam_error = f"打开相机失败：{type(e).__name__}: {e}"
                    raise
            if raw:
                return self._cam
            if self._cam_undist is None and self.has_intrinsics():
                self._cam_undist = UndistortCamera(self._cam, self._paths.CAMERA_MATRIX,
                                                   self._paths.DIST_COEFFS)
            return self._cam_undist or self._cam

    def arm(self):
        """取臂会话（JAKA 控制器直连）。"""
        with self._lock:
            if self._released:
                raise RuntimeError("已按「结束本步骤」释放相机/臂——在页面上做任意动作可重新开始")
            if self._arm is None:
                self._arm = self._arm_factory()
            return self._arm

    def drop_arm(self) -> None:
        """断开臂会话（相机不动）。"""
        with self._lock:
            if self._arm is not None:
                self._arm.close()
                self._arm = None

    def _close_hardware(self) -> None:
        if self._cam is not None:
            self._cam.close()
            self._cam = None
            self._cam_undist = None
        if self._arm is not None:
            self._arm.close()
            self._arm = None

    # ---------- 状态 ----------

    def status(self) -> dict:
        return {
            "owner": self._owner,
            "released": self._released,
            "camera_open": self._cam is not None,
            "arm_open": self._arm is not None,
            "arm_port": "JAKA 控制器直连",
            "has_intrinsics": self.has_intrinsics(),
            "camera_error": self.camera_error(),
        }

    def camera_error(self) -> str | None:
        """相机最近一次出错的原因（打开失败、或取帧一直失败）；正常时为 None。

        页面靠它把"画面出不来"变成一句能读懂的话。
        """
        return self._cam_error or getattr(self._cam, "last_error", None)
