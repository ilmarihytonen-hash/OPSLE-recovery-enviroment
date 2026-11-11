"""Plain-text interactive recovery tools."""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from .health import HealthMonitor, repair_packages, repair_verified_packages, scan_package_integrity

LOGGER = logging.getLogger(__name__)


class RecoveryConsole:
    def __init__(self, monitor: HealthMonitor) -> None:
        self.monitor = monitor

    def run(self) -> None:
        while True:
            print("\nOPSLE DEBIAN RECOVERY")
            print("=====================")
            print("1. Run system diagnostics")
            print("2. Check and repair modified package files")
            print("3. Repair interrupted or broken package installations")
            print("4. Reboot")
            print("5. Reinstall information")
            print("0. Exit")
            try:
                choice = input("Select an option: ").strip()
            except EOFError:
                print()
                return

            if choice == "1":
                self._diagnostics()
            elif choice == "2":
                self._repair_verified_files()
            elif choice == "3":
                self._repair_packages()
            elif choice == "4":
                self._reboot()
            elif choice == "5":
                self._reinstall_information()
            elif choice == "0":
                return
            else:
                print("Invalid selection.")
                continue
            self._pause()

    @staticmethod
    def _pause() -> None:
        print("\nPress Enter to return to the menu...")
        try:
            input()
        except EOFError:
            print()

    def _diagnostics(self) -> None:
        try:
            status = self.monitor.check()
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            LOGGER.exception("System diagnostics failed")
            print(f"Diagnostics failed: {exc}")
            return
        print(f"\nChecked at: {status['checked_at']}")
        print("Health check log:")
        print("  Root filesystem: checked read-only status, free space, and free inodes.")
        print("  systemd: checked failed units and configured critical services.")
        if status["faults"]:
            for fault in status["faults"]:
                print(f"{fault['severity'].upper()}: {fault['summary']}")
                print(f"  {fault['details']}")
        elif status["manual_trigger"]:
            print("No health faults found. Recovery was manually requested.")
        else:
            print("No configured health faults found.")
        if status["critical_fault"]:
            print("A critical fault is present. Automatic rescue-target switching is configured.")

    def _repair_verified_files(self) -> None:
        print("\nChecking files tracked by Debian packages...")
        try:
            report = scan_package_integrity()
        except RuntimeError as exc:
            LOGGER.error("Package integrity scan failed: %s", exc)
            print(f"Integrity scan failed: {exc}")
            return

        if report["output"]:
            print("Verification log:")
            for line in report["output"].splitlines():
                print(f"  {line}")
        if report["errors"]:
            print("Verification messages:")
            for line in report["errors"].splitlines():
                print(f"  {line}")

        if not report["changed_files"]:
            print("dpkg found no modified files with verification metadata.")
            print("This does not verify unowned files or files without stored checksums.")
            return

        print(f"Files differing from package records: {len(report['changed_files'])}")
        for path in report["changed_files"]:
            print(f"  {path}")
        if report["unowned_files"]:
            print("Files without a package owner will not be changed:")
            for path in report["unowned_files"]:
                print(f"  {path}")
        if not report["packages"]:
            print("No owning packages could be identified; no files were changed.")
            return

        print(f"Packages to reinstall: {', '.join(report['packages'])}")
        print("Modified Debian configuration files are preserved when possible.")
        try:
            confirmation = input("Reinstall these packages now? [y/N]: ").strip().lower()
        except EOFError:
            print("\nCancelled.")
            return
        if confirmation != "y":
            print("Cancelled; no packages were changed.")
            return

        print("Reinstalling packages. This can take several minutes...")
        try:
            result = repair_verified_packages(report["packages"])
        except (RuntimeError, ValueError) as exc:
            LOGGER.error("Verified package repair failed: %s", exc)
            print(f"Repair failed: {exc}")
            return
        print("Repair log:")
        if result["output"]:
            print(result["output"])
        print("Result: package reinstall completed.")
        print("Run the integrity check again to verify the result.")

    def _repair_packages(self) -> None:
        print("This runs dpkg --configure -a and apt-get -f install -y.")
        print("APT may install, upgrade, or remove packages.")
        try:
            confirmation = input("Continue? [y/N]: ").strip().lower()
        except EOFError:
            print("\nCancelled.")
            return
        if confirmation != "y":
            print("Cancelled; no packages were changed.")
            return
        try:
            results = repair_packages()
        except RuntimeError as exc:
            LOGGER.error("Package repair failed: %s", exc)
            print(f"Package repair failed: {exc}")
            return
        print("Repair log:")
        for result in results:
            print(f"$ {' '.join(result['command'])}")
            if result["output"]:
                print(result["output"])
        print("Result: package repair completed.")

    @staticmethod
    def _reboot() -> None:
        try:
            confirmation = input("Reboot the computer now? Unsaved work may be lost. [y/N]: ").strip().lower()
        except EOFError:
            print("\nCancelled.")
            return
        if confirmation != "y":
            print("Cancelled.")
            return
        try:
            result = subprocess.run(
                ["systemctl", "--no-block", "reboot"],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"Could not reboot: {exc}")
            return
        if result.returncode:
            print(f"Reboot failed: {result.stderr.strip()}")
        else:
            print("Result: reboot request accepted.")

    @staticmethod
    def _reinstall_information() -> None:
        print("\nA full Debian reinstall requires Debian 13 installation media.")
        print("Back up important data first; reinstalling can erase the selected disks.")
        print("This tool will not partition disks or start an unattended reinstall.")
