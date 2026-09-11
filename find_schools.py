#!/usr/bin/env python3
"""Schulen in WebUntis suchen.

Liefert den 'loginName' (gehoert als `school` in die config.yaml) und den
aktuell zustaendigen Server.

    python find_schools.py "Muster-Berufskolleg"
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
        print(f"Suche fehlgeschlagen: {e}", file=sys.stderr)
        return 1

    if not schools:
        print(f"Keine Treffer für '{query}'.")
        print("Tipp: Teil des Schulnamens oder des Ortes probieren.")
        return 1

    print(f"\n{len(schools)} Treffer für '{query}':\n")
    for s in schools:
        print(f"  {s.get('displayName', '?')}")
        print(f"    school : {s.get('loginName')}")
        print(f"    server : {s.get('server')}")
        print(f"    Adresse: {s.get('address', '-')}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
