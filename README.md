# Awesome Drone Thermography

A database of research papers that use **thermal infrared cameras on drones (UAV / UAS / RPAS)**. Papers are collected automatically from [OpenAlex](https://openalex.org) every day and tagged by application area.

**The data is in [`data/papers.csv`](data/papers.csv).** It opens in Excel, Google Sheets, or pandas.

## Columns

| Column | Meaning |
|---|---|
| `title`, `authors`, `year`, `venue`, `doi`, `type` | Bibliographic data |
| `cited_by` | Citation count (refreshed weekly) |
| `is_oa`, `oa_url`, `license` | Open access status and link |
| `relevance` | `strong`: drone + thermal imaging in title/abstract · `fulltext`: mentioned only in the body · `weak`: says only "thermal", needs a check · empty: not relevant |
| `auto_tags` | Application areas, most relevant first (see [`taxonomy.yaml`](taxonomy.yaml)) |
| `auto_methods`, `auto_sensors` | Methods (deep learning, calibration, …) and thermal cameras mentioned |
| `found_by` | Which search found it (`core` = title/abstract, `fulltext` = body text) |
| `first_seen` | Date the paper was added |
| `status`, `tags`, `methods`, `sensor`, `platform`, `altitude_m`, `posted`, `post_url`, `notes` | Filled in by hand. Never overwritten by the automatic updates |

## How it's collected

- [`tools/fetch_openalex.py`](tools/fetch_openalex.py) searches OpenAlex for drone terms AND thermal terms. It covers articles, conference papers, reviews, preprints, book chapters, books, dissertations, reports, datasets and software.
- Duplicates (same DOI, OpenAlex ID or title) are merged, keeping the published version over a preprint.
- A [GitHub Action](.github/workflows/update.yml) runs daily and resumes where the last run stopped ([`data/fetch_state.json`](data/fetch_state.json)).

Run it yourself:

```bash
pip install -r requirements.txt
python tools/fetch_openalex.py                    # title/abstract search
python tools/fetch_openalex.py --query fulltext   # full-text search (large; resumes across runs)
```

## License

Database: [CC BY 4.0](LICENSE-DATA.md). Please credit the repository if you reuse it. Code: [MIT](LICENSE). Metadata comes from OpenAlex (CC0), and the papers belong to their authors and publishers.

Maintained by [Homayoun Rezaie](https://homayounrezaie.github.io/).
