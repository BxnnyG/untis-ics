#!/usr/bin/env python3
"""Search for schools in WebUntis.

Prints the 'loginName' (that is what goes into config.yaml as `school`) and
the server currently responsible for it.

    python find_schools.py "My School Name"
"""

from __future__ import annotations

import sys

from untis_calendar.school_lookup import search_schools


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    query = " ".join(argv[1:])
    try:
        schools = search_schools(query)
    except Exception as e:
        print(f"Search failed: {e}", file=sys.stderr)
        return 1

    if not schools:
        print(f"No match for '{query}'.")
        print("Tip: try part of the school name, or the town.")
        return 1

    print(f"\n{len(schools)} match(es) for '{query}':\n")
    for s in schools:
        print(f"  {s.get('displayName', '?')}")
        print(f"    school : {s.get('loginName')}")
        print(f"    server : {s.get('server')}")
        print(f"    address: {s.get('address', '-')}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
