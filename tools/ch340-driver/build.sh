#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build ch341.ko for the running JetPack kernel.
#
# The NVIDIA L4T kernel is built with CONFIG_USB_SERIAL_CH341 unset, so a
# CH340-based serial adapter (LinkerHand O6 over USB, lsusb 1a86:7523) never
# gets a /dev/ttyUSB* node. This builds the stock 5.15 ch341 driver
# out-of-tree against the running kernel's headers and signs it with a
# throwaway key (CONFIG_MODULE_SIG_FORCE is off, no lockdown), so it can be
# `insmod`ed without a kernel rebuild.
#
# Re-run this AFTER a kernel update: the vermagic changes and the old .ko
# stops loading.
#
#   ./build.sh                      # build + sign -> ch341.ko
#   sudo ./build.sh --install       # + copy under /lib/modules, depmod, modprobe
set -euo pipefail
KREL="$(uname -r)"
KDIR="/lib/modules/$KREL/build"
OUT="$(cd "$(dirname "$0")" && pwd)"
KVER="v${KREL%%-*}"   # 5.15.148-tegra -> v5.15.148
URL="https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/plain/drivers/usb/serial/ch341.c?h=$KVER"

[[ -d "$KDIR" ]] || { echo "kernel headers missing: $KDIR" >&2; exit 1; }

if [[ ! -f "$OUT/ch341.c" ]]; then
    echo "[ch341] fetching ch341.c @ $KVER"
    curl -fsSL "$URL" -o "$OUT/ch341.c"
fi
printf 'obj-m := ch341.o\n' > "$OUT/Makefile"

echo "[ch341] building against $KDIR"
make -C "$KDIR" M="$OUT" modules

# Sign with a local key if the headers ship the helper (stock L4T modules are
# signed by NVIDIA's build key; this key is NOT in the kernel keyring, which
# is fine because signature enforcement is off).
SIGN="$KDIR/scripts/sign-file"
if [[ -x "$SIGN" ]] && command -v openssl >/dev/null; then
    [[ -f "$OUT/key.pem" ]] || openssl req -new -x509 -newkey rsa:2048 \
        -keyout "$OUT/key.pem" -out "$OUT/cert.pem" -days 36500 -nodes \
        -subj "/CN=ch341-build/" >/dev/null 2>&1
    "$SIGN" sha512 "$OUT/key.pem" "$OUT/cert.pem" "$OUT/ch341.ko"
    echo "[ch341] signed with $OUT/key.pem"
fi
modinfo "$OUT/ch341.ko" | grep -E '^(vermagic|signer):'

if [[ "${1:-}" == "--install" ]]; then
    [[ $EUID -eq 0 ]] || { echo "--install needs root (sudo)" >&2; exit 1; }
    DEST="$KDIR/kernel/drivers/usb/serial"
    install -m 644 "$OUT/ch341.ko" "$DEST/ch341.ko"
    depmod -a
    modprobe ch341
    echo "[ch341] installed; /dev/ttyUSB* should appear on replug"
fi
