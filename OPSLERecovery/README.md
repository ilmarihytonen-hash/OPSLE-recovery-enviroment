# OPSLE Recovery for Debian 13

OPSLE Recovery is a root-owned systemd service that checks system health every 30 seconds and provides a plain-text recovery console. It has no web server or graphical interface. The normal monitor uses Python, but a standalone POSIX-shell console and monitor are installed as a fallback.

## Project files

- `opsle-recovery-shell` is the standalone POSIX-shell recovery menu and fallback monitor.
- `opsle-recovery-launcher` starts the Python monitor, falling back to the shell monitor if Python is unavailable or exits.
- `opsle_recovery/` contains the Python health monitor and interactive console.
- `systemd/opsle-recovery.service` installs the monitor for normal and rescue boots.
- `install.sh` installs and enables the service on Debian 13.
- `config.json` provides default health thresholds and critical units.
- `tests/` contains the Python unit tests.

## What it checks

- Whether the root filesystem is read-only.
- Free space and free inodes on the root filesystem, using configurable warning and critical thresholds.
- Failed systemd units. Failures are warnings unless a unit is listed in `critical_units`.
- Whether each configured critical systemd unit is active.

A critical fault is reported in the system journal and automatically switches systemd to `rescue.target` by default. This stops normal multi-user services and the graphical desktop so recovery can continue in a minimal text environment. Essential kernel drivers for the root disk and console remain loaded. The recovery service stays enabled in the rescue target. Run the text console from its rescue shell:

```sh
/opt/opsle-recovery/opsle-recovery-shell
```

This console uses `/bin/sh` and standard Debian tools only; it does not depend on Python. The systemd service starts through a shell launcher, which starts the Python monitor when available and switches to the independent shell monitor if Python is missing or exits with an error.

For the Python monitor, set `auto_isolate_rescue_target` to `false` in `/etc/opsle-recovery/config.json` to log critical faults without automatically switching targets. The Python-independent shell fallback uses its built-in checks and always requests `rescue.target` on a critical fault.

## Manually enter recovery at startup

The service recognizes a one-boot kernel option, so you can manually request recovery without changing the bootloader configuration:

1. Reboot the computer. If GRUB is hidden, press **Esc** on UEFI systems or hold **Shift** on legacy BIOS systems as it starts to show the GRUB menu.
2. Highlight the normal Debian entry and press **e** to edit it.
3. Find the line beginning with `linux` and append `systemd.unit=rescue.target opsle.recovery=1` to the end of that line. Optionally add `nomodeset` if the graphics driver is suspected of causing boot problems.
4. Press **Ctrl+X** or **F10** to boot the edited entry.
5. Debian boots to its minimal rescue target instead of starting the normal desktop and multi-user services. From the rescue shell, start the recovery console:

   ```sh
   /opt/opsle-recovery/opsle-recovery-shell
   ```

This GRUB edit is temporary and is discarded on the next reboot. `rescue.target` limits started services but still loads the kernel drivers needed to access the root disk, console, and boot process; Linux cannot safely unload all nonessential drivers generically. `nomodeset` asks the kernel to avoid normal GPU mode-setting and use basic display handling where supported, but may reduce display resolution. This does not start a separate live OS or bypass a boot failure that prevents Debian from starting.

## Install

Copy this project to the Debian 13 computer, then run:

```sh
sudo ./install.sh
```

No packages are installed by the script. It installs the service under `/opt/opsle-recovery`, creates `/etc/opsle-recovery/config.json`, and enables the service for normal and rescue boots.

Check service status and logs:

```sh
systemctl status opsle-recovery.service
journalctl -u opsle-recovery.service
```

## Configure

Edit `/etc/opsle-recovery/config.json`; then restart the service. Example:

```json
{
  "check_interval_seconds": 30,
  "warning_free_percent": 10,
  "critical_free_percent": 2,
  "warning_inode_free_percent": 10,
  "critical_inode_free_percent": 2,
  "auto_isolate_rescue_target": true,
  "critical_units": [
    "systemd-journald.service",
    "example-critical.service"
  ]
}
```

The check interval must be 5–3600 seconds. Critical free-space thresholds must be lower than warning thresholds. A failed unit listed in `critical_units` causes recovery-required status. `auto_isolate_rescue_target` must be a JSON boolean.

## Text console actions and safety

- **Run system diagnostics** checks root filesystem read-only status, free disk space and inodes, failed systemd units, and configured critical services.
- **Check and repair modified package files** uses `dpkg --verify` to find changed files for which Debian has verification metadata, identifies their owning packages, then asks before reinstalling those packages. It preserves modified package configuration files where possible.
- **Repair interrupted package installations** asks before running `dpkg --configure -a` and `apt-get -f install -y`. APT may install, upgrade, or remove packages.
- **Reboot** requests a normal systemd reboot after confirmation.
- **Reinstall information** explains how to reinstall using Debian installation media. The tool never repartitions, formats, or automatically reinstalls the OS.

If Python is broken, run `/opt/opsle-recovery/opsle-recovery-shell` directly from the rescue console. The shell fallback monitor checks root filesystem availability/space and `systemd-journald`; it writes to the system journal and requests `rescue.target` on critical faults.

The verification action cannot identify or restore arbitrary unowned files, files without recorded checksums, disk/filesystem damage, or bootloader problems. It reports what Debian can verify and does not overwrite unknown data. For filesystem corruption, boot separate Debian rescue media and run the appropriate filesystem check while the affected filesystem is unmounted.

The console shows diagnostic or package-manager output and results for each operation, then waits for Enter before returning to the menu.
Rescue mode may require the root password at its console. Package reinstalls need working network access or packages already available in the local APT cache; rescue mode intentionally does not start the normal network stack automatically.

## Remove

```sh
sudo systemctl disable --now opsle-recovery.service
sudo rm /etc/systemd/system/opsle-recovery.service
sudo systemctl daemon-reload
sudo rm -r /opt/opsle-recovery
```

The configuration in `/etc/opsle-recovery` is intentionally retained.

## Development tests

Run the Python unit tests from the project directory:

```sh
python3 -m unittest discover -s tests -v
```
