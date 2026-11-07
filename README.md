# 02 boot animation

Self-contained Plymouth theme and GDM handoff for the 02 Zero Portal. Drop the `airootfs/` tree into an archiso profile, or run `install-into.sh`.

The sequence is the cyan 02 mark, the 2 slipping behind the 0, then the tall oval 0 expanding in place. Plymouth theme `02-zero-portal` plays in the initramfs. The `02-zero-portal@02os` GNOME Shell extension finishes the same motion over the real GDM login. It does not turn the 0 into a ring.

## Layout

```
02-logo.svg                  approved mark
generate_assets.py           regenerates logo.png and the motion.js paths
render_lock_field.py         greeter field from a wallpaper
check_native.c               native-link probe for the asset tools
install-into.sh              copy airootfs/ onto a profile root
airootfs/                    archiso payload
```

## Install

```bash
./install-into.sh /path/to/profile/airootfs
python3 render_lock_field.py \
  --wallpaper /path/to/profile/airootfs/usr/share/backgrounds/02os/desktop.jpg \
  --output /path/to/profile/airootfs/usr/share/backgrounds/02os/portal-halo.png
```

`render_lock_field.py` needs Pillow and numpy. The ISO can ship the committed `portal-halo.png` when those libraries are absent. `generate_assets.py` needs pycairo and Pillow, and is only for editing the mark.

Plymouth must be in the initramfs after `kms` and before any encrypt hook. The installed system selects the theme with `plymouth-set-default-theme 02-zero-portal`.
