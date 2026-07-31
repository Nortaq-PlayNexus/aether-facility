"""
AETHER FACILITY - Command Center
One entry point for the whole research scaffold.

Commands:
  init      - bootstrap facility: create dirs, database, sources, manifest
  scout     - poll all vetted sources (metadata index; --download pulls files)
  ingest    - register everything found in INTAKE/ into the database
  archive   - route verified items from INTAKE into ARCHIVE tree
  report    - regenerate STATUS_REPORT.md + master_index.csv
  run_once  - init + ingest + scout + report in one sweep
  menu      - interactive control panel
"""

import argparse
import json
import sys
import time
from pathlib import Path

if getattr(sys, "frozen", False):
    FACILITY_ROOT = Path(sys.executable).resolve().parent
else:
    FACILITY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FACILITY_ROOT / "_CORE"))

from db import Database  # noqa: E402
from vault import Vault  # noqa: E402
from scout import Scout  # noqa: E402
from report import Reporter  # noqa: E402


def load_config(root):
    cfg_path = root / "CONFIG.json"
    with open(cfg_path, encoding="utf-8-sig") as f:
        return json.load(f)


def load_sources(root, cfg):
    src_path = root / cfg.get("sources_file", "_DATA/sources.json")
    if src_path.exists():
        with open(src_path, encoding="utf-8-sig") as f:
            return json.load(f)
    return []


def build(root, cfg):
    db = Database(root / cfg["storage"]["database"])
    vault = Vault(root, db)
    reporter = Reporter(root, db)
    return db, vault, reporter


def cmd_init(root, cfg):
    for sub in ("INTAKE/raw_video", "INTAKE/raw_docs", "INTAKE/unsorted",
                "INTAKE/quarantine", "ARCHIVE", "PROCESSED", "REPORTING", "_DATA", "_TEMP"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    db, vault, reporter = build(root, cfg)
    sources = load_sources(root, cfg)
    n = 0
    for src in sources:
        sid = db.upsert_source(src.get("name", "?"), src.get("url", "?"),
                               kind=src.get("kind", "web"),
                               category=src.get("category", "general"),
                               priority=int(src.get("priority", 5)))
        n += 1
    db.log("init", detail=f"facility initialized with {n} sources")
    vault.write_manifest()
    reporter.status_report()
    db.close()
    print(f"[init] OK - {n} sources registered")


def cmd_ingest(root, cfg, db, vault):
    files = vault.scan_intake()
    new = updated = dupes = 0
    for fp in files:
        folder = fp.parent.name
        default_cat = {"raw_video": "unconfirmed", "raw_docs": "news_articles"}.get(folder)
        item_id, outcome = vault.register_file(fp, category=default_cat)
        if outcome == "new":
            new += 1
        elif outcome == "updated":
            updated += 1
        else:
            dupes += 1
    db.log("ingest", detail=f"new={new} updated={updated} quarantined={dupes}")
    print(f"[ingest] OK - new={new} updated={updated} quarantined={dupes}")
    return new


def cmd_scout(root, cfg, db):
    cfg_net = cfg.get("network", {})
    sources = load_sources(root, cfg)
    scout = Scout(root, db, sources, cfg_net)
    res = scout.run(download=bool(cfg_net.get("download", False)))
    print(f"[scout] OK - sources={len(res['sources'])} found={res['found']} "
          f"downloaded={res['downloaded']} disabled={len(res['disabled'])} "
          f"errors={len(res['errors'])}")
    for err in res["errors"]:
        print(f"   ! {err}")
    return res


def cmd_archive(root, cfg, db, vault):
    items = [dict(r) for r in db.conn.execute(
        "SELECT * FROM items WHERE status='intake' AND file_path IS NOT NULL"
    )]
    routing = cfg.get("routing", {})
    moved = 0
    for it in items:
        path = Path(it["file_path"])
        if not path.exists():
            continue
        dest_sub = routing.get(it["kind"], {}).get(it["category"], "")
        if dest_sub:
            vault.archive_to(it["id"], it["category"], dest_sub)
            moved += 1
    print(f"[archive] OK - moved {moved} items into ARCHIVE")
    return moved


def cmd_report(root, cfg, db, vault):
    m = vault.write_manifest()
    csv = vault.export_csv()
    sr = Reporter(root, db).status_report()
    print(f"[report] OK - {m.name} | {csv.name} | {sr.name}")


def cmd_run_once(root, cfg):
    db, vault, reporter = build(root, cfg)
    try:
        cmd_init(root, cfg)
        cmd_ingest(root, cfg, db, vault)
        cmd_scout(root, cfg, db)
        cmd_archive(root, cfg, db, vault)
        cmd_report(root, cfg, db, vault)
        print("\n[run_once] full sweep complete")
    finally:
        db.close()


def cmd_menu(root, cfg):
    db, vault, reporter = build(root, cfg)
    try:
        while True:
            print("\n" + "=" * 56)
            print("  AETHER FACILITY - Command Center")
            print("=" * 56)
            print("  [1] Init/Reset infrastructure")
            print("  [2] Ingest INTAKE files")
            print("  [3] Scout web sources (metadata)")
            print("  [4] Scout + download media")
            print("  [5] Archive routed items")
            print("  [6] Generate report + manifest")
            print("  [7] FULL SWEEP (2+3+5+6)")
            print("  [8] Show database stats")
            print("  [0] Exit")
            choice = input("  select> ").strip()
            if choice == "1":
                cmd_init(root, cfg)
            elif choice == "2":
                cmd_ingest(root, cfg, db, vault)
            elif choice == "3":
                cmd_scout(root, cfg, db)
            elif choice == "4":
                cfg["network"]["download"] = True
                cmd_scout(root, cfg, db)
                cfg["network"]["download"] = False
            elif choice == "5":
                cmd_archive(root, cfg, db, vault)
            elif choice == "6":
                cmd_report(root, cfg, db, vault)
            elif choice == "7":
                cmd_ingest(root, cfg, db, vault)
                cmd_scout(root, cfg, db)
                cmd_archive(root, cfg, db, vault)
                cmd_report(root, cfg, db, vault)
            elif choice == "8":
                for k, v in db.stats().items():
                    print(f"    {k}: {v}")
            elif choice == "0":
                break
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser(prog="aether", description="AETHER FACILITY command center")
    ap.add_argument("command", nargs="?", default="menu",
                    choices=["init", "ingest", "scout", "archive", "report",
                             "run_once", "menu"])
    ap.add_argument("--root", default=str(FACILITY_ROOT))
    args = ap.parse_args()

    root = Path(args.root)
    cfg = load_config(root)

    if args.command == "init":
        cmd_init(root, cfg)
    elif args.command == "ingest":
        db, vault, _ = build(root, cfg)
        try:
            cmd_ingest(root, cfg, db, vault)
        finally:
            db.close()
    elif args.command == "scout":
        db, _, _ = build(root, cfg)
        try:
            cmd_scout(root, cfg, db)
        finally:
            db.close()
    elif args.command == "archive":
        db, vault, _ = build(root, cfg)
        try:
            cmd_archive(root, cfg, db, vault)
        finally:
            db.close()
    elif args.command == "report":
        db, vault, _ = build(root, cfg)
        try:
            cmd_report(root, cfg, db, vault)
        finally:
            db.close()
    elif args.command == "run_once":
        cmd_run_once(root, cfg)
    else:
        cmd_menu(root, cfg)


if __name__ == "__main__":
    main()
