#!/usr/bin/env bash
# Deploy firmware/ to the CIRCUITPY drive and shared/km_*.py into its lib/.
set -euo pipefail
cd "$(dirname "$0")/.."
MP=$(findmnt -n -o TARGET LABEL=CIRCUITPY || true)
if [ -z "$MP" ]; then
    udisksctl mount -b /dev/disk/by-label/CIRCUITPY >/dev/null
    MP=$(findmnt -n -o TARGET LABEL=CIRCUITPY)
fi
# 'P km_*.py' protects the shared modules (copied below) from --delete
rsync -r --delete --filter='P km_*.py' --exclude backup-factory firmware/ "$MP"/
cp shared/km_*.py "$MP/lib/" 2>/dev/null || true
sync
echo "deployed to $MP"
