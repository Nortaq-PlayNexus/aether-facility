"""
AETHER FACILITY - Vault Layer
Storage integrity: SHA-256 fingerprinting, deduplication, quarantine of
corrupt/duplicate intake, and master manifest generation.
"""

import csv
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from db import Database, utcnow


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


class Vault:
    def __init__(self, root, db: Database):
        self.root = Path(root)
        self.db = db
        self.intake = self.root / "INTAKE"
        self.archive = self.root / "ARCHIVE"
        self.quarantine = self.root / "INTAKE" / "quarantine"
        self.manifest = self.root / "_DATA" / "manifest.json"

    # ---- intake scanning ----
    def scan_intake(self):
        """Scan INTAKE/* for any unregistered files and register them."""
        found = []
        for folder in self.intake.iterdir():
            if folder.name in ("quarantine",):
                continue
            if folder.is_dir():
                for fp in sorted(folder.rglob("*")):
                    if fp.is_file():
                        found.append(fp)
        for folder in ("raw_video", "raw_docs", "unsorted"):
            d = self.intake / folder
            if d.is_dir():
                for fp in sorted(d.rglob("*")):
                    if fp.is_file() and fp not in found:
                        found.append(fp)
        return found

    def register_file(self, path, title=None, kind=None, source_url=None, origin=None, category=None):
        path = Path(path)
        sha = sha256_file(path)
        existing = self.db.item_by_sha(sha)
        if existing and Path(existing["file_path"]).exists() and existing["file_path"] != str(path):
            self.quarantine_file(path, reason="duplicate of " + existing["file_path"])
            return None, "duplicate-quarantined"
        ext = path.suffix.lower().lstrip(".")
        if kind is None:
            kind = self.guess_kind(ext, path)
        item_id, created = self.db.upsert_item(
            sha256=sha,
            title=title or path.stem,
            kind=kind,
            status="intake",
            category=category,
            source_url=source_url,
            origin=origin,
            file_path=str(path),
            file_size=path.stat().st_size,
            ext=ext,
        )
        self.db.log("register", item_id, f"kind={kind} created={created}")
        return item_id, "new" if created else "updated"

    def guess_kind(self, ext, path):
        video = {"mp4", "mov", "mkv", "avi", "webm", "wmv", "m4v", "mpeg", "mpg", "mts", "m2ts", "flv"}
        image = {"jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff", "webp", "heic", "raw"}
        audio = {"mp3", "wav", "flac", "aac", "ogg", "m4a"}
        doc = {"pdf", "doc", "docx", "txt", "md", "rtf", "odt", "xls", "xlsx", "csv", "json", "html", "htm"}
        transcript = {"srt", "vtt", "sub", "txt"}
        if ext in video:
            return "video"
        if ext in image:
            return "image"
        if ext in audio:
            return "audio"
        if ext in doc:
            return "document"
        return "document"

    # ---- archival routing ----
    def archive_to(self, item_id, category, dest_subdir):
        item = self.db.conn.execute(
            "SELECT * FROM items WHERE id=?", (item_id,)
        ).fetchone()
        if not item:
            return None
        src = Path(item["file_path"])
        if not src.exists():
            return None
        plural = {"video": "video", "document": "documents", "image": "images",
                  "audio": "audio", "transcript": "transcripts"}.get(item["kind"], item["kind"] + "s")
        base = self.archive / plural / dest_subdir
        base.mkdir(parents=True, exist_ok=True)
        safe = self._safe_name(src.stem) + src.suffix
        dest = base / safe
        src_sha = sha256_file(src)
        n = 1
        while dest.exists() and sha256_file(dest) != src_sha:
            dest = base / f"{self._safe_name(src.stem)}__{n}{src.suffix}"
            n += 1
        if not dest.exists():
            shutil.move(str(src), str(dest))
        else:
            src.unlink(missing_ok=True)
        self.db.conn.execute(
            "UPDATE items SET file_path=?, category=?, status=? WHERE id=?",
            (str(dest), category, "archived", item_id),
        )
        self.db.conn.commit()
        self.db.log("archive", item_id, f"-> {dest_subdir}/{safe}")
        return str(dest)

    def quarantine_file(self, path, reason=""):
        path = Path(path)
        if not path.exists():
            return None
        self.quarantine.mkdir(parents=True, exist_ok=True)
        dest = self.quarantine / path.name
        n = 1
        while dest.exists():
            dest = self.quarantine / f"{path.stem}__{n}{path.suffix}"
            n += 1
        shutil.move(str(path), str(dest))
        self.db.log("quarantine", detail=f"{reason} :: {path.name} -> {dest.name}")
        return dest

    @staticmethod
    def _safe_name(name):
        keep = []
        for ch in name:
            if ch.isalnum() or ch in "-_ .()[]":
                keep.append(ch)
        out = "".join(keep).strip().rstrip(".")
        return out or "item"

    # ---- manifest ----
    def write_manifest(self):
        items = [dict(r) for r in self.db.conn.execute(
            "SELECT * FROM items ORDER BY created_at"
        )]
        stats = self.db.stats()
        manifest = {
            "generated": utcnow(),
            "facility": "AETHER FACILITY",
            "stats": stats,
            "count": len(items),
            "items": items,
        }
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        self.manifest.write_text(
            json.dumps(manifest, indent=2, default=str), encoding="utf-8"
        )
        return self.manifest

    # ---- CSV export ----
    def export_csv(self, out_path=None):
        out_path = out_path or (self.root / "REPORTING" / "master_index.csv")
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        items = [dict(r) for r in self.db.conn.execute(
            "SELECT * FROM items ORDER BY created_at"
        )]
        cols = ["id", "sha256", "title", "kind", "status", "category", "source_id",
                "source_url", "origin", "recorded_at", "file_path", "file_size",
                "ext", "notes", "verified", "created_at"]
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for it in items:
                w.writerow({c: it.get(c, "") for c in cols})
        return out_path
