#!/usr/bin/env bash
# Copy the Zero Portal boot payload onto an archiso airootfs or installed root.
set -euo pipefail

usage() {
  echo "Usage: install-into.sh [--activate] AIROOTFS" >&2
}

activate=0
dest=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --activate)
      activate=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
    *)
      if [[ -n "${dest}" ]]; then
        echo "Unexpected argument: $1" >&2
        usage
        exit 1
      fi
      dest="$1"
      shift
      ;;
  esac
done

if [[ -z "${dest}" || ! -d "${dest}" ]]; then
  usage
  exit 1
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
src="${root}/airootfs"
if [[ ! -d "${src}/etc/plymouth" || ! -d "${src}/usr/share/plymouth/themes/02-zero-portal" || ! -d "${src}/usr/share/gnome-shell/extensions/02-zero-portal@02os" ]]; then
  echo "Error: invalid payload layout under ${src}" >&2
  exit 1
fi

dest_real="$(cd "${dest}" && pwd -P)"
if [[ "${dest_real}" == "${root}" || "${dest_real}" == "${src}" ]]; then
  echo "Error: destination ${dest} cannot be the source repository" >&2
  exit 1
fi

if [[ ! -d "${dest_real}/etc" || ! -d "${dest_real}/usr" ]]; then
  echo "Error: ${dest} does not look like a valid AIROOTFS/root layout (expected etc/ and usr/)" >&2
  exit 1
fi

cp -a "${src}/." "${dest_real}/"
echo "Installed 02 boot animation into ${dest_real}"

if [[ "${activate}" -eq 1 ]]; then
  if [[ "${dest_real}" == "/" ]]; then
    for cmd in plymouth-set-default-theme mkinitcpio systemd-tmpfiles dconf; do
      if ! command -v "${cmd}" >/dev/null 2>&1; then
        echo "Error: --activate requires '${cmd}' in PATH" >&2
        exit 1
      fi
    done
    plymouth-set-default-theme 02-zero-portal
    mkinitcpio -P
    systemd-tmpfiles --create /usr/lib/tmpfiles.d/02os-zero-portal.conf
    dconf update
    echo "Activated 02-zero-portal theme, initramfs, tmpfiles, and dconf"
  elif command -v arch-chroot >/dev/null 2>&1 && [[ -e "${dest_real}/usr/bin/sh" || -L "${dest_real}/usr/bin/sh" ]]; then
    arch-chroot "${dest_real}" plymouth-set-default-theme 02-zero-portal
    arch-chroot "${dest_real}" mkinitcpio -P
    systemd-tmpfiles --root="${dest_real}" --create /usr/lib/tmpfiles.d/02os-zero-portal.conf
    arch-chroot "${dest_real}" dconf update
    echo "Activated 02-zero-portal inside ${dest_real}"
  else
    echo "Error: --activate requires either '/' or a runnable rootfs with arch-chroot" >&2
    exit 1
  fi
else
  cat <<'EOF'
Post-install steps inside the target/installed system:
  plymouth-set-default-theme 02-zero-portal
  mkinitcpio -P
  systemd-tmpfiles --create /usr/lib/tmpfiles.d/02os-zero-portal.conf
  dconf update
EOF
fi
