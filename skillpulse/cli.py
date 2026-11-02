"""SkillPulse command line: run | report --date | send-test | cache stats | backup | config."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from skillpulse.config import ROOT, Paths, load_secrets, load_settings


def _today(tz: str) -> str:
    return datetime.now(ZoneInfo(tz)).date().isoformat()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="skillpulse", description=__doc__)
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    p.add_argument("--log-level", default="INFO")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="the daily run")
    r.add_argument("--dry-run", action="store_true", help="fixtures only; no network, no LLM calls, no email")
    r.add_argument("--date", help="override the run date (YYYY-MM-DD); default: today in schedule.timezone")
    r.add_argument("--no-email", action="store_true")

    rp = sub.add_parser("report", help="re-render a day's report from the DB")
    rp.add_argument("--date", required=True)
    rp.add_argument("--email", action="store_true", help="also send it")
    rp.add_argument("--dry-run", action="store_true", help="use the dry-run DB")

    sub.add_parser("send-test", help="email a sample report built from fixtures")

    c = sub.add_parser("cache", help="cache commands")
    c.add_argument("action", choices=["stats"])
    c.add_argument("--dry-run", action="store_true")

    b = sub.add_parser("backup", help="snapshot the DB to data/backups (keeps 14)")
    b.add_argument("--keep", type=int, default=14)

    sub.add_parser("config", help="validate and print the loaded config")

    args = p.parse_args(argv)
    settings = load_settings(__import__("pathlib").Path(args.config))
    from skillpulse.util.logging_setup import setup_logging

    setup_logging(ROOT / "logs", args.log_level)
    secrets = load_secrets(ROOT)

    if args.cmd == "config":
        print(settings.model_dump_json(indent=2))
        return 0

    if args.cmd == "run":
        from skillpulse.pipeline import run_daily
        from skillpulse.util.lock import LockHeld, run_lock

        paths = Paths.at(ROOT, dry_run=args.dry_run)
        paths.ensure()
        day = args.date or _today(settings.schedule.timezone)
        try:
            with run_lock(paths.lock):
                res = run_daily(paths, settings, secrets, day=day, dry_run=args.dry_run,
                                send_email=not args.no_email,
                                memory_out=(paths.data if args.dry_run else None))
        except LockHeld as e:
            print(f"another run is in progress: {e}", file=sys.stderr)
            return 2
        except Exception as e:  # noqa: BLE001 — already logged + failure email sent
            print(f"run failed: {e}", file=sys.stderr)
            return 1
        print(json.dumps({"status": res.status, "date": day, "emailed": res.emailed,
                          "files": {k: str(v) for k, v in res.files.items()}}, indent=2))
        return 0

    if args.cmd == "report":
        from skillpulse.extract.aliases import Aliases
        from skillpulse.pipeline import build_report, write_report_files
        from skillpulse.store.sqlite_store import SqliteStore

        paths = Paths.at(ROOT, dry_run=args.dry_run)
        store = SqliteStore(paths.db)
        aliases = Aliases.load(paths.memory / "skill_aliases.yaml", settings.focus_list())
        prev = paths.reports / f"{args.date}.json"
        health = json.loads(prev.read_text())["health"] if prev.exists() else {"sources": []}
        data = build_report(args.date, store, settings, aliases, None, health)
        files = write_report_files(data, paths.reports)
        if args.email:
            from skillpulse.deliver import email as mail

            sender, _, to = mail.check_configured(secrets)
            mail.send(mail.build_report_message(data, sender, to, settings.delivery.email, files.get("docx")),
                      settings.delivery.email, secrets)
        print(json.dumps({k: str(v) for k, v in files.items()}, indent=2))
        return 0

    if args.cmd == "send-test":
        from skillpulse.pipeline import send_test

        res = send_test(ROOT, settings, secrets, _today(settings.schedule.timezone))
        print(f"test email sent to {secrets.report_to} ({res.data['glance']['total_postings']} sample postings)")
        return 0

    if args.cmd == "cache":
        from skillpulse.store.sqlite_store import SqliteStore

        store = SqliteStore(Paths.at(ROOT, dry_run=args.dry_run).db)
        stats = store.stats()
        stats["recent_runs"] = [
            {k: r[k] for k in ("run_date", "status", "postings_fetched", "postings_new", "postings_cached",
                               "extracted", "cost_usd")} for r in store.recent_runs(5)]
        print(json.dumps(stats, indent=2))
        return 0

    if args.cmd == "backup":
        from skillpulse.store.sqlite_store import SqliteStore

        paths = Paths.at(ROOT)
        store = SqliteStore(paths.db)
        print(store.backup(paths.data / "backups", keep=args.keep))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
