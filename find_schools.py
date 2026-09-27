#!/usr/bin/env python3
"""Search for schools in WebUntis.

Prints the 'loginName' (that is what goes into config.yaml as `school`) and
the server currently responsible for it.

    python find_schools.py "My School Name"

Same as ``untis-ics find-school``, which also works inside the container.
"""

from __future__ import annotations

import sys

from untis_calendar.__main__ import main

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(["find-school", *sys.argv[1:]]))
