#!/usr/bin/env bash
# One-time host setup for the dockerless deployment:
#   1. ROS 2 runtime deps (rmw-zenoh-cpp, robot-state-publisher + nav/sensor msgs)
#   2. systemd-owned Zenoh router (rmw_zenohd) on tcp/127.0.0.1:7447
# After this, boot with plain `rbnx boot` (from a shell that has sourced
# .vlmkey) or `bash start.sh` — both reuse the systemd router.
#
# Run with sudo in a real terminal:   sudo bash setup-native-boot.sh
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "must run with sudo" >&2; exit 1; }
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[setup] installing ROS 2 runtime deps (rmw-zenoh-cpp, robot-state-publisher, nav/sensor msgs)"
apt-get install -y ros-humble-rmw-zenoh-cpp ros-humble-robot-state-publisher ros-humble-nav-msgs ros-humble-sensor-msgs

echo "[setup] installing rbnx-zenoh-router systemd service"
# The unit's User= is a placeholder; bind it to the sudo invoker.
sed "s/^User=.*/User=${SUDO_USER:-nvidia}/" \
  "$DEPLOY_DIR/rbnx-zenoh-router.service" > /etc/systemd/system/rbnx-zenoh-router.service
systemctl daemon-reload
systemctl enable rbnx-zenoh-router
# If a start.sh-owned router currently holds 7447, the unit keeps retrying
# (StartLimitIntervalSec=0) and binds as soon as the port frees.
systemctl restart rbnx-zenoh-router || true
sleep 1
systemctl --no-pager --lines=5 status rbnx-zenoh-router || true

echo "[setup] installing udev rule for the CH340 USB hand adapter"
# The O6 hand (hand_bus: usb) is a CH340 serial adapter (1a86:7523). The
# stock rule leaves it root:dialout 0660, which needs a dialout re-login to
# take effect. Give the device to `plugdev` instead — the login user is
# normally already in it, and the provider opens the port at every boot.
cat > /etc/udev/rules.d/70-ch340-o6-hand.rules <<'EOF'
# LinkerHand O6 USB serial adapter (CH340)
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", GROUP="plugdev", MODE="0660", SYMLINK+="ttyO6"
EOF
udevadm control --reload-rules
udevadm trigger --subsystem-match=tty --action=change || true

echo
echo "[setup] done. Reboot the stack with either:"
echo "  bash start.sh                     # from any shell"
echo "  source .vlmkey && rbnx boot       # bare boot (or add .vlmkey to ~/.zshrc)"
