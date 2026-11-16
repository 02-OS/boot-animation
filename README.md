# 02 boot animation

Self-contained Plymouth theme and GDM handoff for the 02 Zero Portal. Drop the `airootfs/` tree into an archiso profile, or run `install-into.sh`.

The sequence is the cyan 02 mark, the 2 slipping behind the 0, then the tall oval 0 expanding in place. Plymouth theme `02-zero-portal` plays in the initramfs. The `02-zero-portal@02os` GNOME Shell extension finishes the same motion over the real GDM login. It does not turn the 0 into a ring.

## Layout

```
02-logo.svg                  approved vector mark
portal-spec.json             canonical portal geometry, backdrop, and timing spec
generate_assets.py           regenerates logo.png and motion.js constants/paths
render_lock_field.py         renders quantized greeter field from a wallpaper
check_native.c               native Plymouth script plugin probe
install-into.sh              copy airootfs/ onto an AIROOTFS / root filesystem
airootfs/                    archiso payload
```

## Install

Copy the payload into an archiso `airootfs/` (must contain `etc/` and `usr/`):

```bash
./install-into.sh /path/to/profile/airootfs
python3 render_lock_field.py \
  --wallpaper /path/to/profile/airootfs/usr/share/backgrounds/02os/desktop.jpg \
  --output /path/to/profile/airootfs/usr/share/backgrounds/02os/portal-halo.png
```

Or install and activate directly on a live system root (or chrootable installed root with `arch-chroot`):

```bash
./install-into.sh --activate /
```

`render_lock_field.py` needs Pillow and numpy (and supports `--width` / `--height`, defaulting to `portal-spec.json`). The ISO can ship the committed `portal-halo.png` when those libraries are absent. `generate_assets.py` needs pycairo and Pillow, and is only for editing the mark or `portal-spec.json`.

### Post-install configuration

Plymouth must be in `/etc/mkinitcpio.conf` `HOOKS` after `kms` and before any `encrypt` / `sd-encrypt` hook. On the target or installed system, run:

```bash
plymouth-set-default-theme 02-zero-portal
mkinitcpio -P
systemd-tmpfiles --create /usr/lib/tmpfiles.d/02os-zero-portal.conf
dconf update
```

## Verify

- Check GNOME Shell extension JS syntax:
  ```bash
  node --check airootfs/usr/share/gnome-shell/extensions/02-zero-portal@02os/extension.js
  node --check airootfs/usr/share/gnome-shell/extensions/02-zero-portal@02os/motion.js
  ```
- Regenerate assets deterministically from `02-logo.svg` and `portal-spec.json`:
  ```bash
  python3 generate_assets.py
  python3 render_lock_field.py --wallpaper /path/to/desktop.jpg --output airootfs/usr/share/backgrounds/02os/portal-halo.png
  ```
- Compile and run `check_native.c` against matching Plymouth headers and `script.so`:
  ```bash
  cc -O2 -Wall -Wextra -Werror check_native.c -o check_native \
    -I"$PLYMOUTH_SRC/src/plugins/splash/script" \
    -I"$PLYMOUTH_SRC/src/libply" \
    -I"$PLYMOUTH_SRC/src/libply-splash-core" \
    -I"$PLYMOUTH_SRC/src/libply-splash-graphics" \
    /usr/lib/x86_64-linux-gnu/plymouth/script.so \
    -l:libply.so.5 -l:libply-splash-core.so.5 -l:libply-splash-graphics.so.5 \
    $(pkg-config --cflags --libs cairo) -lm
  ./check_native airootfs/usr/share/plymouth/themes/02-zero-portal 0
  ./check_native airootfs/usr/share/plymouth/themes/02-zero-portal 1
  ```
