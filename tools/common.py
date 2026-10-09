"""Shared helpers: the paper database (CSV), the abstract cache, dedupe and tagging."""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "papers.csv"
CACHE_PATH = ROOT / "data" / "cache" / "works.jsonl"  # raw-ish OpenAlex records, git-ignored
TAXONOMY_PATH = ROOT / "taxonomy.yaml"

# Columns filled by the pipeline. They are overwritten on every fetch/retag.
AUTO_FIELDS = [
    "openalex_id", "doi", "title", "year", "type", "venue", "authors", "n_authors",
    "cited_by", "is_oa", "oa_url", "license", "relevance", "auto_tags", "auto_methods",
    "auto_sensors", "first_seen",
]
# Columns you edit by hand. The pipeline never touches them once set.
MANUAL_FIELDS = [
    "status",    # candidate | included | excluded   (default: candidate)
    "tags",      # override auto_tags, ';'-separated slugs
    "methods",   # override auto_methods
    "sensor",    # override auto_sensors (free text, e.g. "DJI Zenmuse H20T")
    "platform",  # free text, e.g. "DJI M300 RTK"
    "altitude_m",
    "posted",    # date of LinkedIn post, e.g. 2026-10-14
    "post_url",
    "notes",
]
FIELDS = AUTO_FIELDS + MANUAL_FIELDS
STATUSES = ("candidate", "included", "excluded")


# ---------------------------------------------------------------------------
# Database I/O
# ---------------------------------------------------------------------------

def load_db(path: Path = DB_PATH) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in FIELDS:
            r.setdefault(k, "")
    return rows


def save_db(rows: list[dict], path: Path = DB_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (-int(r["year"] or 0), -int(r["cited_by"] or 0), r["title"]))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def load_cache(path: Path = CACHE_PATH) -> dict[str, dict]:
    if not path.exists():
        return {}
    out = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                out[rec["openalex_id"]] = rec
    return out


def save_cache(cache: dict[str, dict], path: Path = CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for rec in cache.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Normalization & dedupe
# ---------------------------------------------------------------------------

def norm_doi(doi: str | None) -> str:
    if not doi:
        return ""
    doi = doi.strip().lower()
    return re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi)


def norm_title(title: str | None) -> str:
    if not title:
        return ""
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    t = re.sub(r"<[^>]+>", " ", t)  # stray HTML like <i>
    return re.sub(r"[^a-z0-9]+", "", t.lower())


def _preference(rec: dict) -> tuple:
    """Higher is better when two records describe the same paper."""
    type_rank = {"article": 3, "review": 3, "book-chapter": 2, "preprint": 1}
    return (type_rank.get(rec.get("type", ""), 0), bool(rec.get("doi")), int(rec.get("cited_by") or 0))


def dedupe(records: list[dict]) -> tuple[list[dict], int]:
    """Collapse records sharing an OpenAlex ID, DOI, or normalized title (+/- 1 year).

    Keeps the published version over a preprint, then the most cited one.
    Returns (unique_records, number_removed).
    """
    best: dict[str, dict] = {}       # canonical key -> record
    alias: dict[str, str] = {}       # any key -> canonical key

    def keys(rec):
        ks = ["id:" + rec["openalex_id"]]
        if rec.get("doi"):
            ks.append("doi:" + norm_doi(rec["doi"]))
        nt = norm_title(rec.get("title"))
        if len(nt) >= 25:  # very short titles ("Editorial") are not safe to match on
            ks.append("t:" + nt)
        return ks

    for rec in records:
        ks = keys(rec)
        matched = next((k for k in ks if k in alias), None)
        hit = alias[matched] if matched else None
        if hit is not None:
            other = best[hit]
            years_ok = abs(int(other.get("year") or 0) - int(rec.get("year") or 0)) <= 1
            # A title-only match years apart (e.g. "Thermal UAV survey of X" 2016 vs 2023)
            # is a different paper; an ID/DOI match is always the same paper.
            if not matched.startswith("t:") or years_ok:
                if _preference(rec) > _preference(other):
                    best[hit] = rec
                for k in ks:
                    alias[k] = hit
                continue
        canon = ks[0]
        best[canon] = rec
        for k in ks:
            alias.setdefault(k, canon)
    unique = list(best.values())
    return unique, len(records) - len(unique)


# ---------------------------------------------------------------------------
# Taxonomy & tagging
# ---------------------------------------------------------------------------

def _compile(patterns: list[str]) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(f"(?:{p})" for p in patterns) + r")\b", re.IGNORECASE)


@dataclass
class Category:
    slug: str
    name: str
    group: str
    regex: re.Pattern
    title_only: bool = False


class Taxonomy:
    def __init__(self, path: Path = TAXONOMY_PATH):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        rel = raw["relevance"]
        self.platform = _compile(rel["platform"])
        self.thermal = _compile(rel["thermal"])
        self.thermal_weak = _compile(rel.get("thermal_weak") or ["(?!x)x"])
        self.exclude_if_weak = _compile(rel.get("exclude_if_weak") or ["(?!x)x"])
        self.applications = [
            Category(c["slug"], c["name"], c.get("group", "Other"), _compile(c["patterns"]))
            for c in raw["applications"]
        ]
        self.fallback = {c["slug"] for c in raw["applications"] if c.get("fallback")}
        self.methods = [
            Category(c["slug"], c["name"], "", _compile(c["patterns"]), c.get("fields") == "title")
            for c in raw["methods"]
        ]
        self.sensors = {name: re.compile(r"\b(?:" + p + r")", re.IGNORECASE)
                        for name, p in raw["sensors"].items()}
        self.groups = list(dict.fromkeys(c.group for c in self.applications))

    def app_by_slug(self) -> dict[str, Category]:
        return {c.slug: c for c in self.applications}

    def method_by_slug(self) -> dict[str, Category]:
        return {c.slug: c for c in self.methods}

    def relevance(self, text: str) -> str:
        """'strong', 'weak' or '' (drop)."""
        if not self.platform.search(text):
            return ""
        if self.thermal.search(text):
            return "strong"
        if self.thermal_weak.search(text) and not self.exclude_if_weak.search(text):
            return "weak"
        return ""

    def tag(self, rec: dict) -> dict:
        title = rec.get("title") or ""
        text = " ".join([title, rec.get("abstract") or "", " ".join(rec.get("keywords") or [])])
        # Title hits count double: rank tags so the primary application comes first.
        scored = []
        for c in self.applications:
            n = len(c.regex.findall(text)) + 2 * len(c.regex.findall(title))
            if n:
                scored.append((n, c.slug))
        scored = [x for x in scored if x[1] not in self.fallback] or scored[:1]  # fallback only if nothing else
        scored.sort(key=lambda x: -x[0])
        # The top area always counts; others need a title hit or repeated mentions,
        # and must be at least a third as strong as the top one. This cuts noise like
        # a wildlife paper being tagged "forestry" because it mentions trees once.
        top = scored[0][0] if scored else 0
        apps = [s for i, (n, s) in enumerate(scored) if i == 0 or (n >= 3 and n >= top / 3)]
        methods = [c.slug for c in self.methods
                   if c.regex.search(title if c.title_only else text)]
        if rec.get("type") == "review" and "review" not in methods:
            methods.append("review")
        sensors = [name for name, rx in self.sensors.items() if rx.search(text)]
        if "DJI Zenmuse XT/XT2 (unspecified)" in sensors and any(s.startswith("DJI Zenmuse") for s in sensors if "unspecified" not in s):
            sensors.remove("DJI Zenmuse XT/XT2 (unspecified)")
        return {
            "relevance": self.relevance(text),
            "auto_tags": ";".join(apps),
            "auto_methods": ";".join(methods),
            "auto_sensors": ";".join(sensors),
        }


def effective(row: dict, field: str) -> list[str]:
    """Manual override if set, otherwise the auto value; as a list of slugs."""
    auto = {"tags": "auto_tags", "methods": "auto_methods", "sensor": "auto_sensors"}[field]
    val = row.get(field) or row.get(auto) or ""
    return [v.strip() for v in val.split(";") if v.strip()]
