from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from untis_calendar.config import Config
from untis_calendar.ics import events_to_ics
from untis_calendar.logging_config import setup_logging
from untis_calendar.untis_client import UntisClient

logger = logging.getLogger("cli")


def _select_accounts(cfg: Config, only: str | None):
    accounts = cfg.active_accounts
    if only:
        wanted = {k.strip() for k in only.split(",") if k.strip()}
        unknown = wanted - {a.key for a in cfg.accounts}
        if unknown:
            raise SystemExit(f"Unbekannte Account-Keys: {sorted(unknown)}")
        accounts = [a for a in cfg.accounts if a.key in wanted]
    return accounts


def cmd_generate(args) -> int:
    setup_logging()
    cfg = Config.load(args.config)
    client = UntisClient(cfg.app)
    out_dir = Path(cfg.app.output_dir)

    failures = 0
    for acc in _select_accounts(cfg, args.only):
        out_file = out_dir / acc.calendar.file_name
        try:
            events = client.fetch_events(acc)
        except Exception as e:
            failures += 1
            # Bewusst NICHT schreiben: eine bestehende, gute ICS-Datei darf
            # durch einen Fehlversuch nicht zu einem leeren Kalender werden.
            logger.error("Abruf für '%s' fehlgeschlagen: %s", acc.key, e)
            logger.debug("Details:", exc_info=True)
            if out_file.exists():
                logger.warning("Behalte bisherige Datei %s unverändert.", out_file)
            continue

        if not events:
            logger.warning("Keine Termine für '%s' im Zeitfenster.", acc.key)
            if out_file.exists() and out_file.stat().st_size > 200 and not args.allow_empty:
                logger.warning(
                    "Schreibe NICHT: %s enthält bereits Daten. "
                    "Mit --allow-empty erzwingen.", out_file
                )
                continue

        ics_bytes = events_to_ics(
            events,
            calendar_name=acc.calendar.display_name or f"Stundenplan {acc.key}",
            refresh_minutes=cfg.app.refresh_interval_minutes or 60,
            subject_style=cfg.app.subject_style,
            cancelled_style=cfg.app.cancelled_style,
        )
        out_file.write_bytes(ics_bytes)
        logger.info("geschrieben: %s (%d Termine, %d bytes)",
                    out_file, len(events), len(ics_bytes))

    if failures:
        logger.error("%d Account(s) fehlgeschlagen.", failures)
    return 1 if failures else 0


def cmd_check(args) -> int:
    """Prüft jeden Account: Server, Login, Stundenplan-Abruf."""
    setup_logging(level=logging.WARNING if not args.verbose else logging.INFO)
    cfg = Config.load(args.config)
    client = UntisClient(cfg.app)

    failures = 0
    for acc in _select_accounts(cfg, args.only):
        print(f"\n=== Account '{acc.key}' ({acc.school}, User {acc.username}) ===")
        from untis_calendar.school_lookup import resolve_server
        resolved = resolve_server(acc.school)
        if resolved:
            print(f"  Schulsuche  : {acc.school} -> {resolved}")
            if acc.server and acc.server != resolved:
                print(f"  ! Config-Server '{acc.server}' weicht ab (umgezogen?)")
        else:
            print(f"  Schulsuche  : keine Treffer für '{acc.school}'")

        try:
            events = client.fetch_events(acc)
        except Exception as e:
            failures += 1
            print(f"  Status      : FEHLER - {e}")
            continue

        print(f"  Status      : OK - {len(events)} Termine")
        for ev in events[:4]:
            lehrer = ", ".join(ev.teacher_display()) or "-"
            fach = ev.subject_display(cfg.app.subject_style)
            print(f"    {ev.start:%d.%m. %H:%M}-{ev.end:%H:%M}  "
                  f"{ev.room or '-':<6}  {fach}  ({lehrer})")
    print()
    return 1 if failures else 0


def cmd_serve(args) -> int:
    import uvicorn

    from untis_calendar.server import create_app

    app = create_app(args.config)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser("untis-calendar")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_gen = sub.add_parser("generate", help="ICS-Dateien erzeugen")
    p_gen.add_argument("--config", required=True)
    p_gen.add_argument("--only", help="Nur diese Account-Keys (kommagetrennt)")
    p_gen.add_argument("--allow-empty", action="store_true",
                       help="Leeren Kalender auch über vorhandene Daten schreiben")
    p_gen.set_defaults(func=cmd_generate)

    p_chk = sub.add_parser("check", help="Accounts testen (Server, Login, Abruf)")
    p_chk.add_argument("--config", required=True)
    p_chk.add_argument("--only", help="Nur diese Account-Keys (kommagetrennt)")
    p_chk.add_argument("--verbose", action="store_true")
    p_chk.set_defaults(func=cmd_check)

    p_srv = sub.add_parser("serve", help="Webserver starten")
    p_srv.add_argument("--config", required=True)
    p_srv.add_argument("--host", default="0.0.0.0")
    p_srv.add_argument("--port", type=int, default=8080)
    p_srv.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
