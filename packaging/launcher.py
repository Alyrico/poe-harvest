"""Entry point for the packaged app (PyInstaller builds 'PoE Harvest Tracker.exe' from this)."""
import sys

from poe_harvest.app import main

if __name__ == "__main__":
    sys.exit(main())
