"""Generate README.md and categories/*.md from data/papers.csv + taxonomy.yaml.

    python tools/build_readme.py

Shown publicly: rows with status 'included', plus 'candidate' rows with strong relevance.
Rows with status 'excluded' are hidden. Weak/untagged candidates go to REVIEW_QUEUE.md.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from common import ROOT, Taxonomy, effective, load_db  # noqa: E402

TEMPLATE = ROOT / "templates" / "README.template.md"
CAT_DIR = ROOT / "categories"
TOP_N = 10  # papers per category shown in the README


def is_public(r: dict) -> bool:
    return r["status"] == "included" or (r["status"] in ("", "candidate") and r["relevance"] == "strong")


def md(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return text.replace("|", "\\|").replace("\n", " ").strip()


def link(r: dict) -> str:
    url = f"https://doi.org/{r['doi']}" if r["doi"] else f"https://openalex.org/{r['openalex_id']}"
    return f"[{md(r['title'])}]({url})"


def badges(r: dict) -> str:
    b = []
    if r["status"] == "included":
        b.append("✅")
    if r["posted"]:
        b.append(f"[📣]({r['post_url']})" if r["post_url"] else "📣")
    if r["is_oa"] == "yes":
        b.append("🔓")
    return " ".join(b)


def short_authors(r: dict) -> str:
    names = [a.strip() for a in r["authors"].replace(" et al.", "").split(",") if a.strip()]
    if not names:
        return ""
    return md(names[0]) + (" et al." if int(r["n_authors"] or 1) > 1 else "")


def table(rows: list[dict], methods: dict, full: bool) -> str:
    head = "| Year | Paper | Authors | Venue | Cites | Methods | Sensor | |\n|---|---|---|---|--:|---|---|---|\n"
    if not full:
        head = "| Year | Paper | Authors | Cites | Methods | |\n|---|---|---|--:|---|---|\n"
    lines = []
    for r in rows:
        m = ", ".join(methods[s].name for s in effective(r, "methods") if s in methods)
        if full:
            lines.append(f"| {r['year']} | {link(r)} | {short_authors(r)} | {md(r['venue'])} | "
                         f"{r['cited_by']} | {m} | {md(', '.join(effective(r, 'sensor')))} | {badges(r)} |")
        else:
            lines.append(f"| {r['year']} | {link(r)} | {short_authors(r)} | {r['cited_by']} | {m} | {badges(r)} |")
    return head + "\n".join(lines) + "\n"


def by_cites(rows):
    return sorted(rows, key=lambda r: (-int(r["cited_by"] or 0), -int(r["year"] or 0)))


def by_year(rows):
    return sorted(rows, key=lambda r: (-int(r["year"] or 0), -int(r["cited_by"] or 0)))


def year_chart(rows: list[dict], first: int = 2010) -> str:
    counts = Counter(int(r["year"]) for r in rows if r["year"] and int(r["year"]) >= first)
    if not counts:
        return ""
    peak = max(counts.values())
    out = ["```text"]
    for y in range(first, max(counts) + 1):
        n = counts.get(y, 0)
        out.append(f"{y}  {'█' * max(1 if n else 0, round(40 * n / peak)):<40} {n}")
    out.append("```")
    return "\n".join(out)


def main() -> None:
    tax = Taxonomy()
    apps, methods = tax.app_by_slug(), tax.method_by_slug()
    db = load_db()
    public = [r for r in db if is_public(r)]

    by_cat: dict[str, list[dict]] = {s: [] for s in apps}
    untagged = []
    for r in public:
        tags = [t for t in effective(r, "tags") if t in apps]
        if not tags:
            untagged.append(r)
        for t in tags:
            by_cat[t].append(r)

    # ---- per-category pages ------------------------------------------------
    CAT_DIR.mkdir(exist_ok=True)
    for old in CAT_DIR.glob("*.md"):
        old.unlink()
    for slug, cat in apps.items():
        rows = by_year(by_cat[slug])
        (CAT_DIR / f"{slug}.md").write_text(
            f"# {cat.name}\n\n[← back to overview](../README.md) · {len(rows)} papers · "
            f"newest first · ✅ reviewed · 📣 featured on LinkedIn · 🔓 open access\n\n"
            + table(rows, methods, full=True), encoding="utf-8")

    # ---- review queue (private triage, not linked from README) --------------
    weak = [r for r in db if r["status"] in ("", "candidate") and r["relevance"] == "weak"]
    queue = ["# Review queue\n",
             "Papers that need a human decision. Set `status` to `included` or `excluded` "
             "in `data/papers.csv`, or set `tags` by hand.\n",
             f"## No application tag ({len(untagged)})\n", table(by_cites(untagged), methods, full=True),
             f"\n## Weak relevance: only says \"thermal\" ({len(weak)})\n", table(by_cites(weak), methods, full=True)]
    (ROOT / "REVIEW_QUEUE.md").write_text("\n".join(queue), encoding="utf-8")

    # ---- README --------------------------------------------------------------
    overview = ["| Area | Application | Papers | Since | Most cited |", "|---|---|--:|--:|---|"]
    for group in tax.groups:
        for slug, cat in apps.items():
            if cat.group != group:
                continue
            rows = by_cat[slug]
            since = min((int(r["year"]) for r in rows if r["year"]), default="")
            top = by_cites(rows)[0] if rows else None
            overview.append(f"| {group} | [{cat.name}](categories/{slug}.md) | {len(rows)} | {since} | "
                            f"{link(top) if top else ''} |")

    sections = []
    for group in tax.groups:
        sections.append(f"### {group}\n")
        for slug, cat in apps.items():
            if cat.group != group:
                continue
            rows = by_cat[slug]
            sections.append(f"#### {cat.name}\n\nTop {min(TOP_N, len(rows))} most cited of "
                            f"**{len(rows)}** · [see all →](categories/{slug}.md)\n")
            if rows:
                sections.append(table(by_cites(rows)[:TOP_N], methods, full=False))

    sensor_counts = Counter(s for r in public for s in effective(r, "sensor"))
    sensors = ["| Sensor | Papers mentioning it |", "|---|--:|"] + [
        f"| {s} | {n} |" for s, n in sensor_counts.most_common(15)]

    method_counts = Counter(m for r in public for m in effective(r, "methods"))
    method_tbl = ["| Method | Papers |", "|---|--:|"] + [
        f"| {methods[m].name} | {n} |" for m, n in method_counts.most_common() if m in methods]

    recent = sorted(public, key=lambda r: (r["first_seen"], r["year"], r["cited_by"]), reverse=True)[:15]

    fill = {
        "{{TOTAL}}": str(len(public)),
        "{{REVIEWED}}": str(sum(r["status"] == "included" for r in public)),
        "{{POSTED}}": str(sum(bool(r["posted"]) for r in public)),
        "{{N_CATEGORIES}}": str(len(apps)),
        "{{UPDATED}}": dt.date.today().isoformat(),
        "{{OVERVIEW}}": "\n".join(overview),
        "{{YEAR_CHART}}": year_chart(public),
        "{{SENSORS}}": "\n".join(sensors),
        "{{METHODS}}": "\n".join(method_tbl),
        "{{RECENT}}": table(recent, methods, full=False),
        "{{SECTIONS}}": "\n".join(sections),
    }
    text = TEMPLATE.read_text(encoding="utf-8")
    for k, v in fill.items():
        text = text.replace(k, v)
    (ROOT / "README.md").write_text(text, encoding="utf-8")
    print(f"README.md: {len(public)} papers in {len(apps)} categories "
          f"({len(untagged)} untagged, {len(weak)} weak -> REVIEW_QUEUE.md)")


if __name__ == "__main__":
    main()
