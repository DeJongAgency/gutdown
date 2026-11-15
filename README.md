# Polite Gutenberg Downloader

A polite, resumable, **dependency-free** downloader for the Project Gutenberg plain-text corpus (~70,000 individual books).

Written for a multi-day archival pull of the whole corpus. It is deliberately gentle on gutenberg.org's servers and engineered to survive being killed and restarted at any point without losing progress.

## Features

- **No dependencies** — pure Python 3 standard library. No `pip install` needed.
- **Resumable** — already-downloaded files are skipped on re-run. Kill it mid-run, restart it, it picks up where it left off.
- **Known-dead tracking** — permanent 404s (stale/removed IDs) are remembered in `_missing.tsv` so restarts don't waste time re-attempting them.
- **Transient retry with backoff** — 429/5xx responses get one retry after 60s before being cataloged as transient.
- **Failure trap** — 10 consecutive transient failures stops the run for manual review instead of grinding through a network block logging errors for hours.
- **Rate-limited by default** — 3 seconds between downloads. This is a feature, not a bug.
- **Full logging** — every download attempt goes to `_gutenberg_log.tsv` with timestamp, ID, status, and size.

## Requirements

- Python 3.8+
- Nothing else. No third-party packages.

## Getting the code

```bash
git clone https://github.com/DeJongAgency/gutdown
cd gutdown
```

## Usage

```bash
# Validation run — grab 300 books, make sure everything works
python gutenberg_downloader.py --limit 300

# Full corpus (default: 3 seconds between downloads — ~58 hours for 70k books)
python gutenberg_downloader.py

# Faster (only if you're using a mirror — see Politeness below)
python gutenberg_downloader.py --rate 1.0

# Custom download location
python gutenberg_downloader.py --dir /path/to/your/books
```

## Configuration

| Setting | How to set | Default |
|---|---|---|
| Download directory | `--dir <path>` flag **or** `GUTENBERG_DOWNLOAD_DIR` environment variable | `./gutenberg_corpus` (next to the script) |
| Rate (seconds between downloads) | `--rate <seconds>` | `3.0` |

> **⚠️ IMPORTANT:** The download directory is a *configuration value*, not a constant. Do not hardcode a personal path into the script — use `--dir` or the environment variable. The default is a neutral fallback, not a recommendation.

## Output artifacts (in the download directory)

| File | What it is |
|---|---|
| `GUTINDEX.ALL` | The master index, fetched once (~9 MB). Book IDs are parsed from this. |
| `pgNNNNN.txt` | The books themselves. |
| `_gutenberg_log.tsv` | Per-file download log: timestamp, ID, status, size. |
| `_missing.tsv` | Archive-integrity record — every permanent 404 and transient refusal, with timestamps and reasons. |

## Politeness

- The default 3-second rate is deliberate. Project Gutenberg is volunteer-run; a full-corpus pull is a multi-day job and **their gear is not yours to strain**.
- If you need speed, point the script at a mirror and use `--rate 1.0` or lower — never hammer the primary site.
- The `_missing.tsv` file is a genuine contribution back: it's a cycle-count audit of the archive's stale cache paths and removed IDs. Consider sending it to the gutenberg.org maintainers when a run completes.

## Resuming

Just run the script again. It:
1. Skips files already on disk (`pgNNNNN.txt` exists and is non-empty).
2. Loads known-dead IDs from `_missing.tsv` and skips them.
3. Picks up where the previous run left off.

## Notes

- The index is only re-fetched if missing or under 9 MB — a present, valid index is reused.
- A `TRAP` stop (10 consecutive transient failures) is the script telling you something is wrong at the network or catalog level. Check the log, fix the cause, re-run.
- Book IDs marked "Not Used" in the index are skipped — they're reserved slots, not real books.
