# JAKA Mini 2D 手眼标定台（tools/calibration）

宿主直连硬件的标定小工具：**点画面 → 按住末端 free 键把夹爪尖拖到该点 → 空格记录**，
采够点后 RANSAC 拟合"像素 → 臂基座平面"的单应矩阵，即 `jaka_pick` 消费的
`homography_matrix`。无头机器上画面走浏览器（Flask MJPEG，`:8079`）。

- **臂**：直连控制器 `10.5.5.100`（jkrc）；驱动不随工具分发，复用臂包
  `primitive-jaka-mini_with_tg9801_gripper-arm_gripper-rbnx` 的 `JakaDriver`。
- **相机**：宿主 `pyorbbecsdk` 直连 Gemini 336L，**钉死 1280×720**（与运行时相机原语
  同口径；退默认档会大声警告——那意味着标定作废）。
- **内参**：`intrinsics-data/` 不入库（值与清单 `camera_matrix`/`dist_coeffs` 相同）；
  首次克隆或换相机后，按清单数值存成 `intrinsics-data/{camera_matrix,dist_coeffs}.npy`
  （numpy 数组，形状 3×3 / `(8,)`）即可。本工具**不标内参**。

## 跑起来

标定期间 **robonix 必须停机**（相机/控制器独占；直连 jkrc，`motion_enabled` 门不适用），
急停在手、工作区清空。

```bash
bash tools/calibration/setup_venv.sh    # 必须 Python 3.10：jkrc.so 是 3.10 扩展
cd tools/calibration
.venv/bin/python calibrate_handeye_2d.py                # 默认 = 标定
.venv/bin/python calibrate_handeye_2d.py --mode test    # 仅验证
.venv/bin/python calibrate_handeye_2d.py --mode calibrate_board   # 棋盘格
# 浏览器打开 http://<本机IP>:8079
```

## 三个模式

| 模式 | 干什么 | 产物（`artifacts/`） |
|---|---|---|
| `calibrate`（默认） | 点画面 → 拖臂对点 → 空格记录（≥4 点，建议 ≥6、散布开）→ ESC 拟合 → 打误差 + 自动验证 | `2d_image_points.npy`、`2d_end_poses.npy`、`2d_homography.npy` |
| `calibrate_board` | 棋盘格回车锁定，按提示示教 9 个角点；`--capture-count` 多轮拼接（`--pattern-cols/rows` 填**内角点数**） | 上述 + `2d_robot_points.npy` 等 3 个 |
| `test` | 载入现有矩阵，点图 → 臂移到映射点（z = `default_desktop_height`） | 无 |

画面是**去畸变后**的；拖臂时夹爪保持垂直桌面（倾斜是主要误差源）。

## 产物怎么进部署清单

工具打印**可直贴的 YAML 行**，手工抄进 `skill: name: jaka` 的 `homography_matrix`——
**`robonix_manifest.yaml` 和 `robonix_manifest.local.yaml` 两个都要改**；误差打印留档即可。
`camera_matrix` / `dist_coeffs`（随附数据）与 `desktop_height`（手工测量，必须等于
config 的 `default_desktop_height`）本工具**不产出**。

## 臂包依赖

需要含 `jaka_arm/driver.py` 的包根，按序探测：config `jaka_arm_package_path` →
`../primitive-jaka-rbnx`（部署根同级）→ `rbnx-boot/cache/*/` → 部署根同级其它目录；
找不到会列出尝试过的路径（先跑 `bash build.sh` 或 `rbnx boot` 把包拉进 cache）。

## 行为说明（有意为之）

启动**不回零**：连接后第一个动作是 prepare 位姿 `[0.2, 0, 0.25]`，别当故障处理。

**保真保留，勿"顺手清理"**：死代码 `make_board_index_points` / `test_homography`；
board 模式 ESC 抛未捕获 KeyboardInterrupt（清扫靠 `finally`，traceback 属预期）；
`2d_reference_robot_points.npy` 重复写（历史行为）。

## 现场注意

- 采点手拖**必须按住末端 free 按钮**；软件拖拽模式会超时自锁，不用。
- **别用 kill 结束工具**（UVC 不释放 → "能开但不出帧"）；用 ESC 退出，噎住了拔插 USB。
- 夹爪读一次约 1.3s（RS485 半双工），移动偏慢属正常；Orbbec SDK 在 cwd 写 `Log/`（已 ignore）。
- 别用仓库根 `.venv`（numpy 1.23/grpcio）或 pyenv 3.11 跑工具——jkrc.so 只认 3.10。

## 实机情况

2026-09-23 的 10 点数据（即当前清单现值）于本臂实机采得；重投影 1.1–6.9mm、
均值 4.5mm。2026-10-13 本工具预检通过：相机 1280×720@30；CalibArm 全链路
（connect / prepare 毫米级到位 / 回零 / 断电）；`--mode test` 点击 (629, 219) →
`(0.1858, 0.3229, 0.125)`，落点目检准确。未现场重标（清单未变）；`--mode
calibrate` 的采集环待下次现场重标时验证并补记录。

第三方代码归属见 `NOTICE`。
