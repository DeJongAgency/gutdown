#!/usr/bin/env python3
"""
gutenberg_downloader.py — Polite Gutenberg plain-text corpus downloader.

Different shape from corpus_downloader.py: Gutenberg is ~70k individual
books, not a few giant dumps. This script:
  1. Fetches the master index (GUTINDEX.ALL) once.
  2. Parses book IDs from it.
  3. Downloads each book's plain text from the canonical cache path.
  4. Skips already-downloaded files (resume-friendly).
  5. Rate-limits to be polite to gutenberg.org (3 sec default — generous,
     not merely compliant; this is a multi-day job, their gear is not
     ours to strain).

Configuration:
    Download directory: set GUTENBERG_DOWNLOAD_DIR, or pass --dir <path>.
    Default: ./gutenberg_corpus next to this script. CHANGE THIS for your
    machine — never hardcode a personal path in a shared script.

Usage:
    python gutenberg_downloader.py --limit 300        # validation run
    python gutenberg_downloader.py                    # full corpus (3s between)
    python gutenberg_downloader.py --rate 1.0         # faster (use mirrors)
    python gutenberg_downloader.py --dir /data/books  # custom target dir

Disposition of the logs when the run is done:
    _missing.tsv is a genuine archive-integrity artifact — a record of
    every stale cache path, 404, and transient server refusal we hit.
    Intended: send to gutenberg.org as a cycle-count audit of their
    archive (volunteer-run project; this is our contribution back).
"""

import os
import re
import sys
import time
import urllib.request
import urllib.error

# ── CONFIGURATION ──────────────────────────────────────────────────────────
# Where the corpus lands. Override with --dir <path> or the
# GUTENBERG_DOWNLOAD_DIR environment variable.
# ⚠️  THIS IS THE VALUE YOU MUST CHANGE. The default below is a neutral
#     fallback; point it at a real download location on your machine.
#     Do NOT hardcode personal paths in this file if you fork/distribute it.
TARGET = os.environ.get(
    "GUTENBERG_DOWNLOAD_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "gutenberg_corpus"),
)
INDEX_URL = "https://gutenberg.pglaf.org/GUTINDEX.ALL"
BOOK_URL = "https://gutenberg.pglaf.org/cache/epub/{id}/pg{id}.txt"
# Polite, identifying User-Agent. Public repo URL only — no personal
# contact info (no email addresses).
UA = "Polite Gutenberg Downloader/0.2 (+https://github.com/DeJongAgency/gutdown)"
# NOTE: the log path is resolved at call time inside log() so that
# --dir / GUTENBERG_DOWNLOAD_DIR overrides take effect.

# Trap: if this many consecutive downloads fail, stop the run and wait for
# a human to look. Prevents grinding through a network block or catalog
# problem for hours while logging one error after another.
MAX_CONSECUTIVE_FAILURES = 10


def log(name, status, size, note=""):
    log_path = os.path.join(TARGET, "_gutenberg_log.tsv")
    os.makedirs(TARGET, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{name}\t{status}\t{size}\t{note}\n")


def fetch_index():
    dest = os.path.join(TARGET, "GUTINDEX.ALL")
    if os.path.exists(dest) and os.path.getsize(dest) > 9_000_000:
        print(f"[SKIP] index already present ({os.path.getsize(dest)} bytes)")
        return dest
    os.makedirs(TARGET, exist_ok=True)
    req = urllib.request.Request(INDEX_URL, headers={"User-Agent": UA})
    print("[GO  ] fetching GUTINDEX.ALL ...", flush=True)
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        data = resp.read()
        f.write(data)
    print(f"[DONE] index: {len(data)} bytes", flush=True)
    return dest


def parse_ids(index_path, limit=None):
    ids = []
    with open(index_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            # Skip reserved/empty slots — the index marks them "Not Used".
            # Trying to download these is guaranteed 404 waste.
            if "not used" in line.lower():
                continue
            # Pattern A: indented number-only line ("    1342")
            m = re.match(r"^\s*(\d{1,6})\s*$", line)
            if m:
                ids.append(int(m.group(1)))
                continue
            # Pattern B: non-indented line ending in a numeric ID
            m = re.match(r"^(\S.*?)\s+(\d{1,6})\s*$", line)
            if m:
                ids.append(int(m.group(2)))
    # de-dup preserving order
    seen = set()
    out = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out[:limit] if limit else out


# Transient server-side errors: worth one retry after a backoff.
TRANSIENT_CODES = {429, 500, 502, 503, 504}
RETRY_BACKOFF = 60  # seconds


def log_missing(book_id, category, note):
    path = os.path.join(TARGET, "_missing.tsv")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\tpg{book_id}\t{category}\t{note}\n")


def load_dead_ids():
    """Load permanently-missing IDs from _missing.tsv so restarts don't
    re-attempt known-dead entries (index-listed books with no file at the
    mirror cache path — e.g. withdrawn titles). Transient entries are NOT
    dead; they deserve another try on a later run."""
    dead = set()
    path = os.path.join(TARGET, "_missing.tsv")
    if not os.path.exists(path):
        return dead
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3 and parts[2] == "permanent":
                m = re.match(r"^pg(\d+)$", parts[1])
                if m:
                    dead.add(int(m.group(1)))
    return dead


def download_book(book_id, rate):
    dest = os.path.join(TARGET, f"pg{book_id}.txt")
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return "skip", os.path.getsize(dest)
    url = BOOK_URL.format(id=book_id)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            f.write(resp.read())
        size = os.path.getsize(dest)
        log(f"pg{book_id}", "OK", size)
        return "ok", size
    except urllib.error.HTTPError as e:
        code = e.code
        if code == 404:
            # Permanently gone (stale/removed ID). Retrying cannot help.
            log(f"pg{book_id}", f"HTTP {code}", 0)
            log_missing(book_id, "permanent",
                        "404 not found in cache path — ID stale/removed; no retry")
            return "permanent", 0
        if code in TRANSIENT_CODES:
            # Server refusing. One retry after a backoff; then catalog it.
            log(f"pg{book_id}", f"HTTP {code}", 0, "retrying once after 60s")
            time.sleep(RETRY_BACKOFF)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
                    f.write(resp.read())
                size = os.path.getsize(dest)
                log(f"pg{book_id}", "OK", size, "after retry")
                return "ok", size
            except urllib.error.HTTPError as e2:
                log(f"pg{book_id}", f"HTTP {e2.code}", 0)
                log_missing(book_id, "transient",
                            f"HTTP {e2.code} after retry — server refused; try again on a later run")
                return "transient", 0
            except Exception as e2:
                log(f"pg{book_id}", "ERROR", 0, str(e2))
                log_missing(book_id, "transient",
                            f"{e2} after retry — try again on a later run")
                return "transient", 0
        log(f"pg{book_id}", f"HTTP {code}", 0)
        log_missing(book_id, "http", f"HTTP {code} — unexpected; manual review")
        return "transient", 0
    except Exception as e:
        log(f"pg{book_id}", "ERROR", 0, str(e))
        log_missing(book_id, "transient", str(e))
        return "transient", 0


def main():
    global TARGET
    args = sys.argv[1:]
    limit = None
    rate = 3.0
    if "--dir" in args:
        TARGET = os.path.abspath(args[args.index("--dir") + 1])
        print(f"[INFO] download directory: {TARGET}", flush=True)
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])
    if "--rate" in args:
        rate = float(args[args.index("--rate") + 1])

    index = fetch_index()
    ids = parse_ids(index, limit)
    # Filter to books not already on disk — the resume set. Skip-checking
    # in the loop is free, but the per-iteration sleep is not: at 3s each,
    # sleeping through ~16k already-owned books would waste ~13 hours.
    before = len(ids)
    ids = [b for b in ids if not (
        os.path.exists(os.path.join(TARGET, f"pg{b}.txt"))
        and os.path.getsize(os.path.join(TARGET, f"pg{b}.txt")) > 0
    )]
    dead = load_dead_ids()
    if dead:
        ids = [b for b in ids if b not in dead]
    print(f"[INFO] {len(ids)} books to fetch ({before - len(ids)} already on disk + {len(dead)} known-dead, filtered)", flush=True)

    ok = skip = fail = 0
    consec_fail = 0
    for i, book_id in enumerate(ids, 1):
        status, size = download_book(book_id, rate)
        if status == "ok":
            ok += 1
            consec_fail = 0
        elif status == "skip":
            skip += 1
        elif status == "permanent":
            fail += 1  # cataloged in _missing.tsv; not a server problem, no trap
        else:  # transient / http / error
            fail += 1
            consec_fail += 1
        if consec_fail > MAX_CONSECUTIVE_FAILURES:
            note = f"TRAP: {consec_fail} consecutive transient failures (last: pg{book_id}) — stopping; manual review required"
            print(f"[TRAP ] {note}", flush=True)
            log(f"pg{book_id}", "TRAP", 0, note)
            break
        if i % 25 == 0:
            print(f"  [{i}/{len(ids)}] ok={ok} skip={skip} fail={fail}", flush=True)
        time.sleep(rate)

    print(f"[DONE] ok={ok} skip={skip} fail={fail} total={len(ids)}", flush=True)
    print("All tasks finished.", flush=True)


if __name__ == "__main__":
    main()
