"""SkillPulse command line: run | report --date | send-test | cache stats."""

from __future__ import annotations

import argparse
import sys

from skillpulse.config import ROOT, load_settings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="skillpulse")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("config", help="validate and print the loaded config")
    args = p.parse_args(argv)
    if args.cmd == "config":
        s = load_settings(ROOT / "config.yaml")
        print(s.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
