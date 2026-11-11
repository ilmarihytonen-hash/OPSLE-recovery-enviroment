#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root (for example: sudo ./install.sh)." >&2
    exit 1
fi

if [ ! -r /etc/os-release ]; then
    echo "Cannot identify this operating system." >&2
    exit 1
fi
. /etc/os-release
if [ "${ID:-}" != "debian" ] || [ "${VERSION_ID:-}" != "13" ]; then
    echo "This installer supports Debian 13 only; detected ${PRETTY_NAME:-unknown}." >&2
    exit 1
fi

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INSTALL_DIR=/opt/opsle-recovery
CONFIG_DIR=/etc/opsle-recovery

install -d -o root -g root -m 0755 "$INSTALL_DIR"
install -d -o root -g root -m 0755 "$CONFIG_DIR"
rm -rf "$INSTALL_DIR/opsle_recovery"
cp -R "$SOURCE_DIR/opsle_recovery" "$INSTALL_DIR/"
install -o root -g root -m 0755 "$SOURCE_DIR/opsle-recovery-shell" "$INSTALL_DIR/opsle-recovery-shell"
install -o root -g root -m 0755 "$SOURCE_DIR/opsle-recovery-launcher" "$INSTALL_DIR/opsle-recovery-launcher"
chown -R root:root "$INSTALL_DIR"
find "$INSTALL_DIR" -type d -exec chmod 0755 {} \;
find "$INSTALL_DIR" -type f -exec chmod 0644 {} \;
chmod 0755 "$INSTALL_DIR/opsle-recovery-shell" "$INSTALL_DIR/opsle-recovery-launcher"

if [ ! -e "$CONFIG_DIR/config.json" ]; then
    install -o root -g root -m 0644 "$SOURCE_DIR/config.json" "$CONFIG_DIR/config.json"
fi
install -o root -g root -m 0644 "$SOURCE_DIR/systemd/opsle-recovery.service" \
    /etc/systemd/system/opsle-recovery.service
systemctl daemon-reload
systemctl enable --now opsle-recovery.service

echo "OPSLE Recovery is installed and enabled."
echo "For a text recovery console, run:"
echo "  sudo /opt/opsle-recovery/opsle-recovery-shell"
