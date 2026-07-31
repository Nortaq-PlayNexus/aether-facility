"""
AETHER FACILITY - Scout Layer
Polite web intelligence collection. Polls vetted public sources (RSS feeds,
JSON APIs, curated pages) for UAP/UFO media and documents, records what is
found into the database, and optionally queues downloads.

Stdlib-only. Respects a global user-agent, per-source rate limit, and optional
max bytes per download.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from db import Database, utcnow
from vault import sha256_file

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "close",
}

TAG_RE = re.compile(r"<[^>]+>")
MEDIA_RE = re.compile(r'<(?:a|img|source|video|audio)\b[^>]*?(?:href|src)\s*=\s*"([^"]+)"', re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


class Scout:
    def __init__(self, root, db: Database, sources, cfg: dict):
        self.root = Path(root)
        self.db = db
        self.sources = sources or []
        self.cfg = cfg or {}
        self.rate = float(self.cfg.get("rate_limit_seconds", 1.5))
        self.timeout = int(self.cfg.get("http_timeout", 30))
        self.max_bytes = int(self.cfg.get("max_download_bytes", 200 * 1024 * 1024))
        self.retries = int(self.cfg.get("retries", 2))
        self.tmp = self.root / "_TEMP"
        self.tmp.mkdir(parents=True, exist_ok=True)

    # ---- http ----
    def _open(self, url, timeout=None):
        req = urllib.request.Request(url, headers=dict(HEADERS))
        return urllib.request.urlopen(req, timeout=timeout or self.timeout)

    def fetch_bytes(self, url, max_bytes=None, timeout=None):
        last_err = None
        for attempt in range(self.retries + 1):
            try:
                resp = self._open(url, timeout)
                max_bytes = max_bytes or self.max_bytes
                data = bytearray()
                while len(data) < max_bytes:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    data += chunk
                return bytes(data), resp.headers.get("Content-Type", "")
            except urllib.error.HTTPError as ex:
                if ex.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    last_err = ex
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as ex:
                if attempt < self.retries:
                    last_err = ex
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
        raise last_err

    def fetch_text(self, url, max_chars=4_000_000):
        data, _ = self.fetch_bytes(url, max_bytes=max_chars)
        return data.decode("utf-8", errors="replace")

    # ---- parsers ----
    def parse_rss(self, text):
        entries = []
        for m in re.finditer(r"<item[^>]*>(.*?)</item>", text, re.I | re.S):
            body = m.group(1)
            title = TAG_RE.sub("", re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S).group(1)) if re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S) else "untitled"
            link = ""
            lm = re.search(r"<link[^>]*>(.*?)</link>", body, re.I | re.S)
            if not lm:
                lm = re.search(r'<link[^>]*href="([^"]+)"', body, re.I)
            if lm:
                link = lm.group(1).strip()
            pub = ""
            pm = re.search(r"<pubDate[^>]*>(.*?)</pubDate>", body, re.I | re.S)
            if pm:
                pub = TAG_RE.sub("", pm.group(1)).strip()
            entries.append({"title": TAG_RE.sub("", title).strip(), "url": link, "date": pub})
        return entries

    def parse_json_feed(self, text):
        entries = []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return entries
        nodes = data.get("items", data) if isinstance(data, dict) else data
        if isinstance(nodes, dict):
            nodes = nodes.get("items", [])
        if not isinstance(nodes, list):
            return entries
        for node in nodes[:500]:
            if not isinstance(node, dict):
                continue
            title = node.get("title") or node.get("name") or node.get("headline") or "untitled"
            url = node.get("url") or node.get("link") or node.get("web_url") or node.get("html_url") or ""
            date = node.get("date_published") or node.get("published") or node.get("date") or node.get("created") or ""
            if url:
                entries.append({"title": str(title).strip(), "url": str(url), "date": str(date)})
        return entries

    def parse_page_media(self, text, base_url):
        found = []
        junk = ("wikipedia/static", "wikimedia.org/static", "/logo", "tagline",
                "wordmark", "ajax-loader", "spinner", "loading.gif", "spacer",
                "dump_1", "1x1", "mediawiki", "favicon", "icon-128", "sharethis")
        for url in MEDIA_RE.findall(text):
            absolute = urllib.parse.urljoin(base_url, url)
            if not absolute.startswith(("http://", "https://")):
                continue
            if any(j in absolute.lower() for j in junk):
                continue
            found.append(absolute)
        return sorted(set(found))

    # ---- collection ----
    def run(self, download=False):
        results = {"sources": [], "found": 0, "downloaded": 0, "errors": [],
                   "disabled": []}
        for src in self.sources:
            enabled = bool(src.get("enabled", True))
            sid = self.db.upsert_source(src.get("name", "?"), src.get("url", "?"),
                                        kind=src.get("kind", "web"),
                                        category=src.get("category", "general"),
                                        priority=int(src.get("priority", 5)),
                                        enabled=1 if enabled else 0)
            if not enabled:
                results["disabled"].append(src.get("name", "?"))
                continue
            mode = src.get("mode", "rss")
            src_name = src.get("name", "?")
            print(f"\n[SCOUT] polling: {src_name}", flush=True)
            try:
                entries = []
                if mode == "rss":
                    text = self.fetch_text(src["url"])
                    entries = self.parse_rss(text)
                elif mode == "json":
                    text = self.fetch_text(src["url"])
                    entries = self.parse_json_feed(text)
                elif mode == "page":
                    text = self.fetch_text(src["url"])
                    title_m = TITLE_RE.search(text)
                    page_title = TAG_RE.sub("", title_m.group(1)).strip() if title_m else src["name"]
                    found = self.parse_page_media(text, src["url"])
                    for u in found:
                        entries.append({"title": f"{page_title} :: {u.split('/')[-1]}", "url": u, "date": ""})
                elif mode == "file":
                    entries = [{"title": src_name, "url": src["url"], "date": ""}]

                registered = 0
                for e in entries:
                    if not e.get("url"):
                        continue
                    existing = self.db.item_by_url(e["url"])
                    if existing:
                        item_id = existing["id"]
                        created = False
                        self.db.conn.execute(
                            "UPDATE items SET title=?, status=?, category=?, source_id=?, "
                            "origin=?, recorded_at=?, verified=? WHERE id=?",
                            (e.get("title", "untitled"), "intake", src.get("category", "general"),
                             sid, src.get("name", "?"), e.get("date") or None,
                             1 if src.get("verified") else 0, item_id))
                        self.db.conn.commit()
                    else:
                        item_id, created = self.db.upsert_item(
                            sha256=self._url_fingerprint(e["url"]),
                            title=e.get("title", "untitled"),
                            kind=src.get("item_kind", "document"),
                            status="intake",
                            category=src.get("category", "general"),
                            source_id=sid,
                            source_url=e["url"],
                            origin=src.get("name", "?"),
                            recorded_at=e.get("date") or None,
                            verified=1 if src.get("verified") else 0,
                        )
                    registered += 1
                    results["found"] += 1
                    has_local = False
                    row = self.db.conn.execute(
                        "SELECT file_path FROM items WHERE id=?", (item_id,)
                    ).fetchone()
                    if row and row["file_path"]:
                        has_local = Path(row["file_path"]).exists()
                    if download and (created or not has_local):
                        ok = self.download(e["url"], item_id, sid)
                        if ok:
                            results["downloaded"] += 1
                            print(f"   [OK] {e['url']}", flush=True)
                        else:
                            print(f"   [SKIP] {e['url']}", flush=True)
                self.db.touch_source(sid, f"ok::{len(entries)} entries")
                print(f"   [DONE] {src_name}: {len(entries)} entries, {registered} registered",
                      flush=True)
                results["sources"].append({"name": src["name"], "entries": len(entries), "registered": registered})
                time.sleep(self.rate)
            except urllib.error.HTTPError as ex:
                self.db.touch_source(sid, f"http_{ex.code}")
                self.db.log("scout_error", detail=f"{src_name} :: HTTP {ex.code}")
                print(f"   [ERR] {src_name}: HTTP {ex.code}", flush=True)
                results["errors"].append(f"{src['name']}: HTTP {ex.code}")
            except Exception as ex:  # noqa: BLE001
                self.db.touch_source(sid, "error")
                self.db.log("scout_error", detail=f"{src_name} :: {type(ex).__name__}: {ex}")
                print(f"   [ERR] {src_name}: {type(ex).__name__}: {ex}", flush=True)
                results["errors"].append(f"{src['name']}: {type(ex).__name__}: {ex}")
        return results

    @staticmethod
    def _url_fingerprint(url):
        import hashlib
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def download(self, url, item_id, source_id):
        """Download an item into INTAKE/raw_* and re-fingerprint by content."""
        try:
            data, ctype = self.fetch_bytes(url)
        except Exception:  # noqa: BLE001
            return False
        if not data or "text/html" in ctype or "application/xhtml" in ctype or "text/xml" in ctype:
            self.db.log("download_skip", item_id, f"non-file response ({ctype or 'empty'}) :: {url}")
            return False
        kind = self.db.conn.execute("SELECT kind FROM items WHERE id=?", (item_id,)).fetchone()
        kind = kind["kind"] if kind else "document"
        folder = "raw_video" if kind == "video" else "raw_docs"
        dest = self.root / "INTAKE" / folder
        dest.mkdir(parents=True, exist_ok=True)
        sniffed = self._sniff_ext(data, url)
        if sniffed == ".html":
            self.db.log("download_skip", item_id, f"html-looking content :: {url}")
            return False
        ext = sniffed or self._ext_from_url(url, ctype) or ".bin"
        path = dest / (f"{item_id}_{self._safe(url)}" + ext)
        path.write_bytes(data)
        sha = sha256_file(path)
        dup = self.db.item_by_sha(sha)
        if dup and dup["id"] != item_id:
            path.unlink(missing_ok=True)
            self.db.conn.execute(
                "UPDATE items SET file_path=?, file_size=?, ext=? WHERE id=?",
                (dup["file_path"], dup["file_size"], dup["ext"], item_id),
            )
            self.db.conn.commit()
            self.db.log("download_dup", item_id,
                        f"content already in item {dup['id']} :: {url}")
            return True
        self.db.conn.execute(
            "UPDATE items SET sha256=?, file_path=?, file_size=?, ext=? WHERE id=?",
            (sha, str(path), path.stat().st_size, ext.lstrip("."), item_id),
        )
        self.db.conn.commit()
        self.db.log("download", item_id, url)
        return True

    @staticmethod
    def _safe(url):
        name = urllib.parse.urlparse(url).path.split("/")[-1] or "item"
        return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:60]

    @staticmethod
    def _sniff_ext(data, url=""):
        """Detect real file type from magic bytes when URL/ctype is ambiguous."""
        if data[:4] == b"\x89PNG":
            return ".png"
        if data[:3] == b"GIF":
            return ".gif"
        if data[:3] == b"\xff\xd8\xff":
            return ".jpg"
        if data[:4] == b"%PDF":
            return ".pdf"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return ".webp"
        if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            return ".wav"
        if data[4:8] == b"ftyp":
            return ".mp4"
        if data[:4] == b"\x1aE\xdf\xa3":
            return ".webm"
        if data[:3] == b"ID3" or data[:2] == b"\xff\xfb":
            return ".mp3"
        if data[:4] == b"OggS":
            return ".ogg"
        head = data[:64].lstrip().lower()
        if head.startswith(b"<svg") or head.startswith(b"<?xml") and b"<svg" in head:
            return ".svg"
        if head.startswith(b"{") or head.startswith(b"["):
            return ".json"
        if head.startswith(b"<!doctype") or head.startswith(b"<html"):
            return ".html"
        return None

    @staticmethod
    def _ext_from_url(url, ctype):
        p = urllib.parse.urlparse(url).path
        ext = Path(p).suffix.lower()
        if ext in {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".pdf", ".doc",
                   ".docx", ".txt", ".md", ".jpg", ".jpeg", ".png", ".srt", ".vtt", ".csv", ".json"}:
            return ext
        if "video" in ctype:
            return ".mp4"
        if "pdf" in ctype:
            return ".pdf"
        return ".bin"
