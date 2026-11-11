"""System health checks used by the recovery service."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
RECOVERY_BOOT_OPTION = "opsle.recovery=1"


@dataclass(frozen=True)
class Fault:
    check: str
    severity: str
    summary: str
    details: str


DEFAULT_CONFIG: dict[str, Any] = {
    "check_interval_seconds": 30,
    "warning_free_percent": 10,
    "critical_free_percent": 2,
    "warning_inode_free_percent": 10,
    "critical_inode_free_percent": 2,
    "critical_units": ["systemd-journald.service"],
    "auto_isolate_rescue_target": True,
}


def load_config(path: str) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    config_path = Path(path)
    if not config_path.exists():
        return config

    try:
        supplied = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read configuration {config_path}: {exc}") from exc
    if not isinstance(supplied, dict):
        raise ValueError("Configuration must be a JSON object")
    config.update(supplied)

    for key in (
        "check_interval_seconds",
        "warning_free_percent",
        "critical_free_percent",
        "warning_inode_free_percent",
        "critical_inode_free_percent",
    ):
        value = config[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{key} must be an integer")
    if not 5 <= config["check_interval_seconds"] <= 3600:
        raise ValueError("check_interval_seconds must be between 5 and 3600")
    for warning_key, critical_key in (
        ("warning_free_percent", "critical_free_percent"),
        ("warning_inode_free_percent", "critical_inode_free_percent"),
    ):
        warning = config[warning_key]
        critical = config[critical_key]
        if not 0 <= critical < warning <= 100:
            raise ValueError(f"{warning_key} must be greater than {critical_key}, and both must be 0-100")
    units = config["critical_units"]
    if not isinstance(units, list) or not all(isinstance(unit, str) and unit for unit in units):
        raise ValueError("critical_units must be a list of non-empty systemd unit names")
    if not isinstance(config["auto_isolate_rescue_target"], bool):
        raise ValueError("auto_isolate_rescue_target must be a boolean")
    return config


def manual_recovery_requested(cmdline_path: str = "/proc/cmdline") -> bool:
    try:
        options = Path(cmdline_path).read_text(encoding="utf-8").split()
    except OSError as exc:
        LOGGER.warning("Could not read kernel command line for manual recovery option: %s", exc)
        return False
    return RECOVERY_BOOT_OPTION in options


class HealthMonitor:
    """Collects health checks and retains the latest recovery state."""

    def __init__(self, config: dict[str, Any], manual_trigger: bool = False) -> None:
        self.config = config
        self.manual_trigger = manual_trigger
        self._lock = threading.Lock()
        self._faults: list[Fault] = []
        self._checked_at: str | None = None
        self._acknowledged = False
        self._rescue_target_requested = False

    def check(self) -> dict[str, Any]:
        faults = self._collect_faults()
        with self._lock:
            self._faults = faults
            self._checked_at = datetime.now(timezone.utc).isoformat()
            if not any(fault.severity == "critical" for fault in faults):
                self._acknowledged = False
            snapshot = self._snapshot_locked()

        if snapshot["critical_fault"]:
            LOGGER.critical("Critical system fault detected; run the OPSLE recovery console from a text terminal")
            self._enter_rescue_target()
        elif self.manual_trigger:
            LOGGER.info("Manual recovery boot option detected; text recovery console is available")
        elif faults:
            LOGGER.warning("System health warnings detected: %s", "; ".join(f.summary for f in faults))
        else:
            LOGGER.info("System health checks passed")
        return snapshot

    def _enter_rescue_target(self) -> None:
        if self.manual_trigger or not self.config.get("auto_isolate_rescue_target", True) or self._rescue_target_requested:
            return
        try:
            result = subprocess.run(
                ["systemctl", "isolate", "rescue.target"],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            LOGGER.error("Could not switch to rescue.target: %s", exc)
            return
        if result.returncode:
            LOGGER.error("Could not switch to rescue.target: %s", result.stderr.strip())
            return
        self._rescue_target_requested = True
        LOGGER.critical("System switched to rescue.target after a critical health fault")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def acknowledge(self) -> dict[str, Any]:
        with self._lock:
            if self.manual_trigger or any(fault.severity == "critical" for fault in self._faults):
                self._acknowledged = True
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
        critical = any(fault.severity == "critical" for fault in self._faults)
        return {
            "checked_at": self._checked_at,
            "recovery_required": critical or self.manual_trigger,
            "critical_fault": critical,
            "manual_trigger": self.manual_trigger,
            "acknowledged": self._acknowledged,
            "faults": [asdict(fault) for fault in self._faults],
            "checks": {
                "root_free_warning_percent": self.config["warning_free_percent"],
                "root_free_critical_percent": self.config["critical_free_percent"],
                "inode_free_warning_percent": self.config["warning_inode_free_percent"],
                "inode_free_critical_percent": self.config["critical_inode_free_percent"],
            },
        }

    def _collect_faults(self) -> list[Fault]:
        faults: list[Fault] = []
        try:
            stats = os.statvfs("/")
            free_percent = 100.0 * stats.f_bavail / stats.f_blocks if stats.f_blocks else 0.0
            inode_free_percent = 100.0 * stats.f_favail / stats.f_files if stats.f_files else 0.0
            read_only = bool(stats.f_flag & getattr(os, "ST_RDONLY", 1))
            if read_only:
                faults.append(Fault("root-filesystem", "critical", "The root filesystem is read-only", "Writes to / are unavailable."))
            self._add_capacity_fault(
                faults, "root-disk-space", "Root filesystem free space", free_percent,
                self.config["critical_free_percent"], self.config["warning_free_percent"],
            )
            self._add_capacity_fault(
                faults, "root-inodes", "Root filesystem free inodes", inode_free_percent,
                self.config["critical_inode_free_percent"], self.config["warning_inode_free_percent"],
            )
        except OSError as exc:
            faults.append(Fault("root-filesystem", "critical", "Unable to inspect the root filesystem", str(exc)))

        critical_units = set(self.config["critical_units"])
        try:
            result = subprocess.run(
                ["systemctl", "--failed", "--no-legend", "--plain", "--no-pager"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if result.returncode not in (0, 1):
                faults.append(Fault("systemd-units", "warning", "Could not query failed systemd units", result.stderr.strip()))
            else:
                for line in result.stdout.splitlines():
                    unit = line.split(maxsplit=1)[0] if line.split() else ""
                    if unit:
                        severity = "critical" if unit in critical_units else "warning"
                        faults.append(Fault(
                            f"unit:{unit}", severity, f"Systemd unit failed: {unit}",
                            line.strip(),
                        ))
        except (OSError, subprocess.TimeoutExpired) as exc:
            faults.append(Fault("systemd-units", "warning", "Could not query failed systemd units", str(exc)))

        for unit in critical_units:
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", "--quiet", unit],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                if result.returncode != 0 and not any(f.check == f"unit:{unit}" for f in faults):
                    faults.append(Fault(
                        f"unit:{unit}", "critical", f"Critical systemd unit is not active: {unit}",
                        result.stderr.strip() or "systemctl reported the unit as inactive.",
                    ))
            except (OSError, subprocess.TimeoutExpired) as exc:
                faults.append(Fault(f"unit:{unit}", "critical", f"Could not check critical unit: {unit}", str(exc)))
        return faults

    @staticmethod
    def _add_capacity_fault(
        faults: list[Fault], check: str, label: str, free_percent: float,
        critical_percent: int, warning_percent: int,
    ) -> None:
        if free_percent <= critical_percent:
            faults.append(Fault(
                check, "critical", f"{label} critically low ({free_percent:.1f}% free)",
                f"Critical threshold: {critical_percent}% free.",
            ))
        elif free_percent <= warning_percent:
            faults.append(Fault(
                check, "warning", f"{label} low ({free_percent:.1f}% free)",
                f"Warning threshold: {warning_percent}% free.",
            ))


def repair_packages() -> list[dict[str, Any]]:
    """Run Debian's supported package-repair commands, with bounded execution."""
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    commands = [
        ["dpkg", "--configure", "-a"],
        ["apt-get", "-f", "install", "-y"],
    ]
    results: list[dict[str, Any]] = []
    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=900,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{' '.join(command)} timed out after 15 minutes") from exc
        except OSError as exc:
            raise RuntimeError(f"Could not run {' '.join(command)}: {exc}") from exc
        results.append({
            "command": command,
            "returncode": result.returncode,
            "output": (result.stdout + "\n" + result.stderr)[-12000:].strip(),
        })
        if result.returncode != 0:
            raise RuntimeError(f"{' '.join(command)} failed with exit code {result.returncode}")
    return results


def scan_package_integrity() -> dict[str, list[str]]:
    """Find dpkg-verified files that differ and the packages that own them."""
    try:
        result = subprocess.run(
            ["dpkg", "--verify"],
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("dpkg file verification timed out after 15 minutes") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not run dpkg --verify: {exc}") from exc
    if result.returncode:
        raise RuntimeError(f"dpkg --verify failed: {result.stderr.strip()}")

    changed_files: list[str] = []
    packages: set[str] = set()
    unowned_files: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        path = parts[1].strip()
        if not path.startswith("/"):
            continue
        changed_files.append(path)
        try:
            owner_result = subprocess.run(
                ["dpkg-query", "-S", path],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not find the package owning {path}: {exc}") from exc
        owners: set[str] = set()
        if owner_result.returncode == 0:
            for owner_line in owner_result.stdout.splitlines():
                owner, separator, _ = owner_line.partition(": ")
                if separator:
                    owners.add(owner)
        if owners:
            packages.update(owners)
        else:
            unowned_files.append(path)

    return {
        "changed_files": changed_files,
        "packages": sorted(packages),
        "unowned_files": unowned_files,
        "output": result.stdout.strip(),
        "errors": result.stderr.strip(),
    }


def repair_verified_packages(packages: list[str]) -> dict[str, Any]:
    """Reinstall packages identified by dpkg verification, preserving local configs."""
    if not packages:
        raise ValueError("No package names were provided for repair")
    for package in packages:
        if not package or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+.-:" for char in package):
            raise ValueError(f"Invalid package name: {package!r}")

    command = [
        "apt-get",
        "-o", "Dpkg::Options::=--force-confold",
        "--reinstall",
        "install",
        "-y",
        *packages,
    ]
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=1800,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Package file repair timed out after 30 minutes") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not reinstall verified packages: {exc}") from exc
    if result.returncode:
        raise RuntimeError(
            f"Package file repair failed with exit code {result.returncode}: "
            f"{(result.stdout + chr(10) + result.stderr)[-12000:].strip()}"
        )
    return {
        "command": command,
        "output": (result.stdout + "\n" + result.stderr)[-12000:].strip(),
    }
