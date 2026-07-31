"""
AETHER FACILITY - Reporting Layer
Generates the STATUS REPORT (markdown) and master CSV index so the facility
has a human-readable front door into everything it holds.
"""

from datetime import datetime, timezone

from db import utcnow


class Reporter:
    def __init__(self, root, db):
        self.root = root
        self.db = db

    def status_report(self):
        stats = self.db.stats()
        sources = self.db.list_sources()
        events = [dict(r) for r in self.db.conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT 40"
        )]
        lines = [
            "# AETHER FACILITY - STATUS REPORT",
            "",
            f"Generated: {utcnow()}",
            "",
            "## Dashboard",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            f"| Total items indexed | {stats['items']} |",
            f"| Videos | {stats['kind_video']} |",
            f"| Documents | {stats['kind_document']} |",
            f"| Images | {stats['kind_image']} |",
            f"| Sources tracked | {stats['sources']} |",
            f"| Sources disabled (bot-walled) | {sum(1 for s in sources if not s['enabled'])} |",
            f"| Status: intake | {stats['status_intake']} |",
            f"| Status: archived | {stats['status_archived']} |",
            f"| Status: quarantine | {stats['status_quarantine']} |",
            f"| Status: verified | {stats['status_verified']} |",
            "",
            "## Source Grid",
            "",
            "| Name | Kind | Category | Status |",
            "| --- | --- | --- | --- |",
        ]
        for s in sources:
            status = s["last_status"] or "never polled"
            if not s["enabled"]:
                status = "disabled (bot-walled)"
            lines.append(f"| {s['name']} | {s['kind']} | {s['category']} | {status} |")

        lines += ["", "## Recent Activity", ""]
        for e in events:
            lines.append(f"- `{e['created_at']}` **{e['action']}** {e['detail'] or ''}")

        out = self.root / "REPORTING" / "STATUS_REPORT.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines), encoding="utf-8")
        return out
