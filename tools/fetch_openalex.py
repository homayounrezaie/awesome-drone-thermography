"""Pull candidate drone-thermography papers from OpenAlex, dedupe, tag, and merge into data/papers.csv.

Usage:
    python tools/fetch_openalex.py                 # full pull
    python tools/fetch_openalex.py --since 2026-01-01   # only works published since a date
    python tools/fetch_openalex.py --max-pages 2   # quick test

Optional environment variables:
    OPENALEX_API_KEY   free key from https://openalex.org (raises the daily limit)
    OPENALEX_MAILTO    your e-mail, sent to OpenAlex as a courtesy identifier

Manual columns in papers.csv (status, tags, sensor, posted, notes, ...) are never overwritten.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(__file__))
from common import (  # noqa: E402
    AUTO_FIELDS, FIELDS, Taxonomy, _preference, dedupe, load_cache, load_db, norm_doi, norm_title,
    save_cache, save_db,
)

API = "https://api.openalex.org/works"

PLATFORM_Q = ('(UAV OR UAVs OR UAS OR drone OR drones OR "unmanned aerial" OR "unmanned aircraft" '
              'OR RPAS OR quadcopter OR multicopter OR multirotor OR "remotely piloted")')
THERMAL_Q = ('(thermal OR thermography OR thermographic OR "thermal infrared" OR TIR OR LWIR '
             'OR "infrared camera" OR "land surface temperature" OR "canopy temperature")')
WORK_TYPES = "article|review|preprint|book-chapter"
SELECT = ("id,doi,title,publication_year,publication_date,type,cited_by_count,primary_location,"
          "open_access,authorships,abstract_inverted_index,keywords")


def abstract_from_index(inv: dict | None) -> str:
    if not inv:
        return ""
    pos = [(i, w) for w, idxs in inv.items() for i in idxs]
    return " ".join(w for _, w in sorted(pos))


def to_record(w: dict) -> dict:
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    oa = w.get("open_access") or {}
    authors = list(dict.fromkeys(  # OpenAlex sometimes lists the same author twice
        a["author"]["display_name"] for a in w.get("authorships") or [] if a.get("author")))
    shown = ", ".join(authors[:6]) + (" et al." if len(authors) > 6 else "")
    return {
        "openalex_id": w["id"].rsplit("/", 1)[-1],
        "doi": norm_doi(w.get("doi")),
        "title": " ".join((w.get("title") or "").split()),
        "year": str(w.get("publication_year") or ""),
        "type": w.get("type") or "",
        "venue": src.get("display_name") or loc.get("raw_source_name") or "",
        "authors": shown,
        "n_authors": str(len(authors)),
        "cited_by": str(w.get("cited_by_count") or 0),
        "is_oa": "yes" if oa.get("is_oa") else "no",
        "oa_url": oa.get("oa_url") or "",
        "license": loc.get("license") or "",
        # cache-only fields (not written to the CSV):
        "abstract": abstract_from_index(w.get("abstract_inverted_index")),
        "keywords": [k.get("display_name", "") for k in w.get("keywords") or []],
    }


def fetch(since: str | None, max_pages: int | None, per_page: int = 200) -> list[dict]:
    filt = [f"title_and_abstract.search:{PLATFORM_Q} AND {THERMAL_Q}",
            "is_retracted:false", f"type:{WORK_TYPES}"]
    if since:
        filt.append(f"from_publication_date:{since}")
    params = {"filter": ",".join(filt), "per-page": per_page, "select": SELECT, "cursor": "*"}
    if os.getenv("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    if os.getenv("OPENALEX_MAILTO"):
        params["mailto"] = os.environ["OPENALEX_MAILTO"]

    out, page, total = [], 0, None
    session = requests.Session()
    while params["cursor"] and (max_pages is None or page < max_pages):
        for attempt in range(6):
            r = session.get(API, params=params, timeout=60)
            if r.status_code == 200:
                break
            if r.status_code == 429 and r.headers.get("x-ratelimit-remaining") == "0":
                print(f"OpenAlex daily limit reached after {page} pages; keeping what was fetched. "
                      "Set OPENALEX_API_KEY or rerun tomorrow to get the rest.")
                return out
            time.sleep(2 ** attempt)  # 429 burst limit or 5xx: back off and retry
        else:
            r.raise_for_status()
        data = r.json()
        total = total or data["meta"]["count"]
        out.extend(to_record(w) for w in data["results"])
        params["cursor"] = data["meta"].get("next_cursor")
        page += 1
        print(f"  page {page}: {len(out)}/{total}  "
              f"(credits left today: {r.headers.get('x-ratelimit-remaining', '?')})", flush=True)
        if not data["results"]:
            break
        time.sleep(0.5)  # stay well under the per-second limit
    return out


def merge(db: list[dict], fresh: list[dict], today: str) -> tuple[list[dict], int]:
    """Update auto fields of existing rows, append new rows. Manual fields are preserved."""
    by_id = {r["openalex_id"]: r for r in db}
    by_doi = {r["doi"]: r for r in db if r["doi"]}
    by_title = {norm_title(r["title"]): r for r in db if len(norm_title(r["title"])) >= 25}
    added = 0
    for rec in fresh:
        row = by_id.get(rec["openalex_id"]) or (by_doi.get(rec["doi"]) if rec["doi"] else None)
        if row is None:
            # Same title within a year = another version of a paper we already have
            # (typically the journal version of a preprint). Keep the better one.
            twin = by_title.get(norm_title(rec["title"]))
            if twin and abs(int(twin["year"] or 0) - int(rec["year"] or 0)) <= 1:
                if _preference(rec) <= _preference(twin):
                    continue
                row = twin
        if row is None:
            row = {k: "" for k in FIELDS}
            row["status"] = "candidate"
            row["first_seen"] = today
            db.append(row)
            by_id[rec["openalex_id"]] = row
            added += 1
        for k in AUTO_FIELDS:
            if k in rec and k != "first_seen":
                row[k] = rec[k]
    return db, added


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="YYYY-MM-DD: only fetch works from this date (incremental update)")
    ap.add_argument("--max-pages", type=int, help="stop after N pages of 200 (for testing)")
    args = ap.parse_args()

    tax = Taxonomy()
    print("Querying OpenAlex ...")
    raw = fetch(args.since, args.max_pages)

    cache = load_cache()
    for rec in raw:
        cache[rec["openalex_id"]] = rec
    save_cache(cache)

    kept, dropped = [], 0
    for rec in raw:
        rec.update(tax.tag(rec))
        if rec["relevance"]:
            kept.append(rec)
        else:
            dropped += 1
    unique, dupes = dedupe(kept)

    today = dt.date.today().isoformat()
    db, added = merge(load_db(), unique, today)
    # A paper already in the DB that is now a duplicate of another (e.g. preprint + journal
    # version) is marked excluded rather than deleted, so your notes on it survive.
    dupe_ids = {r["openalex_id"] for r in kept} - {r["openalex_id"] for r in unique}
    for row in db:
        if row["openalex_id"] in dupe_ids and row["status"] == "candidate":
            row["status"] = "excluded"
            row["notes"] = (row["notes"] + " [auto: duplicate]").strip()
    save_db(db)

    print(f"\nFetched {len(raw)} | not relevant {dropped} | duplicates {dupes} | "
          f"new {added} | database now {len(db)} papers -> data/papers.csv")
    print("Next: python tools/build_readme.py")


if __name__ == "__main__":
    main()
