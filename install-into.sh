#!/usr/bin/env bash
# Copy the Zero Portal boot payload onto an archiso airootfs.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest="${1:-}"
if [[ -z "${dest}" || ! -d "${dest}" ]]; then
  echo "Usage: install-into.sh AEROOTFS" >&2
  exit 1
fi

mkdir -p "${dest}"
cp -a "${root}/airootfs/." "${dest}/"
echo "Installed 02 boot animation into ${dest}"
