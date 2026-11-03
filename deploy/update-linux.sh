#!/usr/bin/env sh
# Author: FubukiHanasaki. Check by default; pass --apply --confirm-stopped to update.
set -eu
pcc_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
exec python3 "$pcc_root/scripts/update.py" "$@"
