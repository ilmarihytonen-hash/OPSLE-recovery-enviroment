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
STAGING_DIR=$(mktemp -d /opt/opsle-recovery.new.XXXXXX)
BACKUP_DIR="${STAGING_DIR}.old"

cleanup_install() {
    if [ -d "$STAGING_DIR" ]; then
        rm -rf "$STAGING_DIR"
    fi
    if [ -d "$BACKUP_DIR" ] && [ ! -e "$INSTALL_DIR" ]; then
        mv "$BACKUP_DIR" "$INSTALL_DIR"
    elif [ -d "$BACKUP_DIR" ]; then
        rm -rf "$BACKUP_DIR"
    fi
}
trap cleanup_install 0
trap 'exit 1' HUP INT TERM

install -d -o root -g root -m 0755 "$STAGING_DIR/opsle_recovery"
cp -R "$SOURCE_DIR/opsle_recovery/." "$STAGING_DIR/opsle_recovery/"
install -o root -g root -m 0755 "$SOURCE_DIR/opsle-recovery-shell" "$STAGING_DIR/opsle-recovery-shell"
install -o root -g root -m 0755 "$SOURCE_DIR/opsle-recovery-launcher" "$STAGING_DIR/opsle-recovery-launcher"
chown -R root:root "$STAGING_DIR"
find "$STAGING_DIR" -type d -exec chmod 0755 {} \;
find "$STAGING_DIR" -type f -exec chmod 0644 {} \;
chmod 0755 "$STAGING_DIR/opsle-recovery-shell" "$STAGING_DIR/opsle-recovery-launcher"

install -d -o root -g root -m 0755 "$CONFIG_DIR"
if [ ! -e "$CONFIG_DIR/config.json" ]; then
    install -o root -g root -m 0644 "$SOURCE_DIR/config.json" "$CONFIG_DIR/config.json"
fi
install -o root -g root -m 0644 "$SOURCE_DIR/systemd/opsle-recovery.service" \
    /etc/systemd/system/opsle-recovery.service

if systemctl is-active --quiet opsle-recovery.service; then
    systemctl stop opsle-recovery.service
fi

if [ -e "$INSTALL_DIR" ]; then
    mv "$INSTALL_DIR" "$BACKUP_DIR"
fi
if ! mv "$STAGING_DIR" "$INSTALL_DIR"; then
    if [ -d "$BACKUP_DIR" ]; then
        mv "$BACKUP_DIR" "$INSTALL_DIR"
    fi
    echo "Could not install the new recovery files; restored the previous installation." >&2
    exit 1
fi
if [ -d "$BACKUP_DIR" ]; then
    rm -rf "$BACKUP_DIR"
fi

systemctl daemon-reload
systemctl enable --now opsle-recovery.service

trap - 0 HUP INT TERM
echo "OPSLE Recovery is installed and enabled."
echo "The previous /opt/opsle-recovery installation was replaced."
echo "Configuration in /etc/opsle-recovery was preserved."
echo "For a text recovery console, run:"
echo "  sudo /opt/opsle-recovery/opsle-recovery-shell"
