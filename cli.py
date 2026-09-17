#!/usr/bin/env python3
"""Compatibility wrapper.

The CLI lives in ``untis_calendar/__main__.py`` so it ships with the package
and is reachable as ``untis-ics`` or ``python -m untis_calendar``. This file
stays so existing deployments (systemd units, cron entries) keep working.
"""

from __future__ import annotations

import sys

from untis_calendar.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
