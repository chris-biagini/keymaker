#!/usr/bin/env bash
# Install keymaker on this machine: firmware to the pad, daemon to systemd.
set -euo pipefail
cd "$(dirname "$0")/.."

./system/deploy-firmware.sh

mkdir -p ~/.config/systemd/user
cp system/keymaker.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now keymaker.service
systemctl --user --no-pager status keymaker.service || true

if [ ! -e /etc/udev/rules.d/62-keymaker.rules ]; then
    cat <<'EOF'

MANUAL STEP (root): install the udev rule, then replug the pad:
  sudo cp system/62-keymaker.rules /etc/udev/rules.d/
  sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=tty
EOF
fi
