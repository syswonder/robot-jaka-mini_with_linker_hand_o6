# CH340 driver for the USB LinkerHand O6 (`hand_bus: usb`)

The O6 is wired to the NX through a CH340 serial adapter (`lsusb` shows
`1a86:7523`). Two host-side gaps stop it from showing up as `/dev/ttyUSB*`:

1. **No kernel module.** The NVIDIA L4T 5.15 kernel is built with
   `# CONFIG_USB_SERIAL_CH341 is not set`, so `ch341.ko` does not exist and
   the adapter stays unbound (`lsusb -t` shows no `Driver=` on its interface).
2. **A fake braille device.** Ubuntu's brltty rule matches `1a86/7523` and
   starts `brltty`, which claims the USB interface in userspace and blocks the
   serial driver (`sudo apt-get remove -y brltty`).

## Build and install

```bash
# build + self-sign (module signature enforcement is off on this kernel)
./tools/ch340-driver/build.sh

# install, depmod, load
sudo ./tools/ch340-driver/build.sh --install

# and drop brltty if this host still has it
sudo apt-get remove -y brltty
```

`build.sh` fetches the stock `ch341.c` for the running kernel version, builds
it against `/lib/modules/$(uname -r)/build`, and signs it with a local
throwaway key. Re-run it **after every kernel update** — the vermagic changes
and the old `.ko` stops loading.

After install, replug the hand and check:

```bash
lsusb -t | grep 7523     # interface should show Driver=ch341
ls -l /dev/ttyO6         # udev symlink from setup-native-boot.sh
```

`setup-native-boot.sh` installs the udev rule that gives the port to
`plugdev` (the login user is already in it) and adds the `/dev/ttyO6` symlink,
so the provider can open it without a `dialout` re-login.
