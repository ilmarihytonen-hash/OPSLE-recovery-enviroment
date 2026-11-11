"""OPSLE Recovery service entry point."""

from __future__ import annotations

import argparse
import logging
import time

from .console import RecoveryConsole
from .health import HealthMonitor, load_config, manual_recovery_requested


def main() -> None:
    parser = argparse.ArgumentParser(description="OPSLE Debian recovery monitor")
    parser.add_argument("--config", default="/etc/opsle-recovery/config.json")
    parser.add_argument("--console", action="store_true", help="run the text-based recovery console")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    monitor = HealthMonitor(config, manual_trigger=manual_recovery_requested())
    if args.console:
        monitor.check()
        RecoveryConsole(monitor).run()
        return

    while True:
        try:
            monitor.check()
        except Exception:
            logging.getLogger(__name__).exception("Health monitor check failed")
        time.sleep(config["check_interval_seconds"])


if __name__ == "__main__":
    main()
