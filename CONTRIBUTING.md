# Contributing to AETHER FACILITY

Thanks for your interest! This project is intentionally **stdlib-only** — that's a
design constraint, not an oversight.

## Ground rules

- **No new runtime dependencies.** If a solution needs a pip package, it needs a very
  good reason.
- **Fingerprint everything.** Any intake or download path must go through SHA-256 in
  `_CORE/vault.py` so dedup and integrity stay automatic.
- **Be polite to the web.** New sources go through `Scout` with a rate limit; bot-walled
  sources are recorded as disabled, not hammered.
- Keep the CLI surface stable (`facility.py` commands) and the directory map documented
  in the README.

## Setup

```bat
git clone https://github.com/Nortaq-PlayNexus/aether-facility.git
cd aether-facility
python _SCRIPTS\facility.py init
```

No venv, no `pip install`. If `python` isn't on your PATH, use the full interpreter path.

## Workflow

1. Fork and create a feature branch.
2. Make changes with tests in mind — a smoke test is `facility.py run_once` on a
   scratch copy of the folder.
3. Run the quick checks:
   ```bat
   python _SCRIPTS\facility.py init
   python _SCRIPTS\facility.py report
   ```
   Both should exit 0 and print `OK`.
4. Open a pull request describing the change and what you verified.

## Adding a source

Edit `_DATA/sources.json` with an entry like:

```json
{
  "name": "My Source",
  "kind": "web",
  "mode": "rss",
  "url": "https://example.com/feed",
  "category": "government_reports",
  "priority": 3,
  "item_kind": "document",
  "verified": true
}
```

`mode` is one of `rss | json | page | file`. Prefer `page`/`file` modes only when a
source has no structured feed. Never set `"enabled": true` on a site that blocks bots.

## Building the exe

See [docs/BUILDING.md](docs/BUILDING.md).

## Code of conduct

Be constructive. This project organizes evidence about a contested topic — respectful
disagreement is welcome, badgering is not.
