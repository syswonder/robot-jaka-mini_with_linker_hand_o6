# 灵巧手接线：从控制器末端改为 USB

把 LinkerHand O6 从 JAKA 控制器末端 RS485L 改接到 NX 的 USB 口。软件侧只改配置
（`hand_bus: usb`），控制器侧的那套 RS485 路径原样保留。

## 1. 改接线

O6 的 RS485（A/B 两线）接到 CH340 转接板的 RS485 端，转接板 USB 端插 NX。
转接板芯片确认是 CH340：

```bash
lsusb | grep 7523        # 1a86:7523 QinHeng Electronics CH340
```

## 2. 装内核驱动（只做一次）

L4T 内核没有编进 `ch341`，必须自己编：

```bash
./tools/ch340-driver/build.sh                 # 编译 + 自签名
sudo ./tools/ch340-driver/build.sh --install  # 安装到 /lib/modules 并加载
```

如果机器里还有 `brltty`（它的 udev 规则会抢 CH340），卸掉：

```bash
sudo apt-get remove -y brltty
```

**内核更新后要重跑** `build.sh --install`（vermagic 变了旧 `.ko` 不加载）。

## 3. 设权限（只做一次）

```bash
sudo bash setup-native-boot.sh
```

这会装一条 udev 规则：设备组给到 `plugdev`（登录用户默认就在这个组），并创建
固定软链 `/dev/ttyO6`，免得每次重新登录才拿到 `dialout` 权限。

确认：

```bash
lsusb -t | grep 7523           # 接口应显示 Driver=ch341
ls -l /dev/ttyO6               # -> ttyUSB0，组为 plugdev
```

## 4. 改 deployment 配置

`robonix_manifest.yaml` 里 `jaka_arm.config` 加两项：

```yaml
      end_effector: hand
      hand_bus: usb
      hand_port: /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
```

`hand_port` 用 by-id 路径（`ls /dev/serial/by-id/`），避免 ttyUSB 编号变化。

原语仓需为含 `hand_bus` 的版本（`main` 已包含）。

## 5. 验证

```bash
# 只读：读自由度/方向/角度，不动手指
# 用原语包的 venv（系统 python3 没有 pymodbus）
cd rbnx-boot/cache/primitive-jaka-mini_with_linker_hand_o6-arm_hand-rbnx
.venv/bin/python - <<'EOF'
from jaka_arm.hand_usb import JakaHandUsb
h = JakaHandUsb("/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0")
h.power_on()   # 打开串口并等手就绪
print("dof", h.read_dof(), "direction", h.read_direction(), "angles", h.read_angles())
h.close()
EOF
```

期望：`dof 6`、`direction 82`（右手）、六个角度值。

然后 boot，日志里应看到：

```
[jaka_arm] O6 hand on usb /dev/serial/by-id/...: slave 0x27 (configured right, read right)
```

首次 boot 建议先把 `motion_enabled` 设为 `false`，确认 `hand/info` 和
`hand/state_joint` 有数据后再打开。

## 回退

改回 `hand_bus: controller`（或删掉这一行，默认就是 `controller`），把 A/B 线接回
控制器末端 RS485L 即可，软件不用改其他东西。
