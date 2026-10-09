"""Re-run relevance + tagging on every paper using the local cache (no API calls).

Run this after editing taxonomy.yaml:
    python tools/retag.py && python tools/build_readme.py
"""

from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
from common import Taxonomy, load_cache, load_db, save_db  # noqa: E402


def main() -> None:
    tax, cache, db = Taxonomy(), load_cache(), load_db()
    if not cache:
        sys.exit("No cache found (data/cache/works.jsonl). Run tools/fetch_openalex.py first.")
    missing = 0
    for row in db:
        rec = cache.get(row["openalex_id"])
        if rec is None:  # e.g. a paper you added by hand: tag from the title alone
            rec, missing = {"title": row["title"], "type": row["type"]}, missing + 1
        row.update(tax.tag(rec))
    save_db(db)
    rel = Counter(r["relevance"] or "dropped" for r in db)
    print(f"Retagged {len(db)} papers ({missing} without cached abstract). Relevance: {dict(rel)}")


if __name__ == "__main__":
    main()
