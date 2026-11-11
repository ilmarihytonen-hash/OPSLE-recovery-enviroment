import json
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from opsle_recovery.health import (
    HealthMonitor,
    load_config,
    manual_recovery_requested,
    repair_verified_packages,
    scan_package_integrity,
)
from opsle_recovery.console import RecoveryConsole


class HealthMonitorTests(unittest.TestCase):
    def test_critical_disk_fault_requires_recovery(self):
        monitor = HealthMonitor({
            "warning_free_percent": 10,
            "critical_free_percent": 2,
            "warning_inode_free_percent": 10,
            "critical_inode_free_percent": 2,
            "critical_units": [],
        })
        with patch("opsle_recovery.health.os.statvfs", create=True) as statvfs, patch(
            "opsle_recovery.health.subprocess.run"
        ) as run:
            statvfs.return_value.f_blocks = 100
            statvfs.return_value.f_bavail = 1
            statvfs.return_value.f_files = 100
            statvfs.return_value.f_favail = 50
            statvfs.return_value.f_flag = 0
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            result = monitor.check()
        self.assertTrue(result["recovery_required"])
        self.assertEqual(result["faults"][0]["severity"], "critical")
        self.assertEqual(result["faults"][0]["check"], "root-disk-space")

    def test_read_only_root_is_critical(self):
        monitor = HealthMonitor({
            "warning_free_percent": 10,
            "critical_free_percent": 2,
            "warning_inode_free_percent": 10,
            "critical_inode_free_percent": 2,
            "critical_units": [],
        })
        with patch("opsle_recovery.health.os.statvfs", create=True) as statvfs, patch(
            "opsle_recovery.health.subprocess.run"
        ) as run:
            statvfs.return_value.f_blocks = 100
            statvfs.return_value.f_bavail = 50
            statvfs.return_value.f_files = 100
            statvfs.return_value.f_favail = 50
            statvfs.return_value.f_flag = getattr(os, "ST_RDONLY", 1)
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            result = monitor.check()
        self.assertTrue(result["recovery_required"])
        self.assertEqual(result["faults"][0]["check"], "root-filesystem")

    def test_failed_noncritical_unit_is_warning(self):
        monitor = HealthMonitor({
            "warning_free_percent": 10,
            "critical_free_percent": 2,
            "warning_inode_free_percent": 10,
            "critical_inode_free_percent": 2,
            "critical_units": [],
        })
        with patch("opsle_recovery.health.os.statvfs", create=True) as statvfs, patch(
            "opsle_recovery.health.subprocess.run"
        ) as run:
            statvfs.return_value.f_blocks = 100
            statvfs.return_value.f_bavail = 50
            statvfs.return_value.f_files = 100
            statvfs.return_value.f_favail = 50
            statvfs.return_value.f_flag = 0
            run.return_value.returncode = 0
            run.return_value.stdout = "example.service loaded failed failed Example"
            run.return_value.stderr = ""
            result = monitor.check()
        self.assertFalse(result["recovery_required"])
        self.assertEqual(result["faults"][0]["severity"], "warning")

    def test_acknowledgement_does_not_hide_critical_fault(self):
        monitor = HealthMonitor({
            "warning_free_percent": 10,
            "critical_free_percent": 2,
            "warning_inode_free_percent": 10,
            "critical_inode_free_percent": 2,
            "critical_units": [],
        })
        with patch("opsle_recovery.health.os.statvfs", create=True) as statvfs, patch(
            "opsle_recovery.health.subprocess.run"
        ) as run:
            statvfs.return_value.f_blocks = 100
            statvfs.return_value.f_bavail = 1
            statvfs.return_value.f_files = 100
            statvfs.return_value.f_favail = 50
            statvfs.return_value.f_flag = 0
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            monitor.check()
        acknowledged = monitor.acknowledge()
        self.assertTrue(acknowledged["recovery_required"])
        self.assertTrue(acknowledged["acknowledged"])

    def test_config_rejects_bad_threshold_order(self):
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as config_file:
            json.dump({"warning_free_percent": 1, "critical_free_percent": 2}, config_file)
            config_path = config_file.name
        try:
            with self.assertRaises(ValueError):
                load_config(config_path)
        finally:
            os.unlink(config_path)

    def test_manual_boot_option_enables_recovery_without_faults(self):
        monitor = HealthMonitor({
            "warning_free_percent": 10,
            "critical_free_percent": 2,
            "warning_inode_free_percent": 10,
            "critical_inode_free_percent": 2,
            "critical_units": [],
        }, manual_trigger=True)
        with patch("opsle_recovery.health.os.statvfs", create=True) as statvfs, patch(
            "opsle_recovery.health.subprocess.run"
        ) as run:
            statvfs.return_value.f_blocks = 100
            statvfs.return_value.f_bavail = 50
            statvfs.return_value.f_files = 100
            statvfs.return_value.f_favail = 50
            statvfs.return_value.f_flag = 0
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            result = monitor.check()
        self.assertTrue(result["recovery_required"])
        self.assertTrue(result["manual_trigger"])
        self.assertFalse(result["critical_fault"])

    def test_manual_recovery_kernel_option_is_recognized(self):
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as cmdline:
            cmdline.write("root=UUID=abc ro opsle.recovery=1 quiet")
            cmdline_path = cmdline.name
        try:
            self.assertTrue(manual_recovery_requested(cmdline_path))
        finally:
            os.unlink(cmdline_path)

    def test_integrity_scan_identifies_changed_files_and_owners(self):
        verify_result = type("Result", (), {
            "returncode": 0,
            "stdout": "??5??????   /usr/bin/example\n",
            "stderr": "",
        })()
        owner_result = type("Result", (), {
            "returncode": 0,
            "stdout": "example-package:amd64: /usr/bin/example\n",
            "stderr": "",
        })()
        with patch("opsle_recovery.health.subprocess.run", side_effect=[verify_result, owner_result]) as run:
            report = scan_package_integrity()
        self.assertEqual(report["changed_files"], ["/usr/bin/example"])
        self.assertEqual(report["packages"], ["example-package:amd64"])
        self.assertEqual(report["unowned_files"], [])
        self.assertEqual(run.call_count, 2)

    def test_verified_package_repair_preserves_configuration(self):
        result = type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
        with patch("opsle_recovery.health.subprocess.run", return_value=result) as run:
            repair_result = repair_verified_packages(["example-package"])
        self.assertIn("Dpkg::Options::=--force-confold", run.call_args.args[0])
        self.assertEqual(repair_result["output"], "ok")

    def test_console_waits_for_enter_after_operation(self):
        monitor = type("Monitor", (), {
            "check": lambda _: {
                "checked_at": "now",
                "faults": [],
                "manual_trigger": False,
                "critical_fault": False,
            }
        })()
        console = RecoveryConsole(monitor)
        output = io.StringIO()
        with patch("builtins.input", side_effect=["1", "", "0"]), redirect_stdout(output):
            console.run()
        self.assertIn("Press Enter to return to the menu...", output.getvalue())


if __name__ == "__main__":
    unittest.main()
