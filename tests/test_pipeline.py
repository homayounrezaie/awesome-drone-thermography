import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from build_readme import md  # noqa: E402
from common import Taxonomy, dedupe, norm_doi, norm_title  # noqa: E402
from fetch_openalex import abstract_from_index, merge  # noqa: E402

TAX = Taxonomy()


def rec(i, title, year="2020", doi="", type_="article", cited="0", abstract=""):
    return {"openalex_id": f"W{i}", "title": title, "year": year, "doi": doi,
            "type": type_, "cited_by": cited, "abstract": abstract}


def test_norm():
    assert norm_doi("https://doi.org/10.1/ABC") == "10.1/abc"
    assert norm_title("Thermal <i>UAV</i> mapping!") == norm_title("thermal uav  mapping")


def test_dedupe_prefers_journal_over_preprint():
    t = "UAV thermal infrared mapping of groundwater discharge into the coastal zone"
    out, removed = dedupe([rec(1, t, "2015", type_="preprint", cited="50"), rec(2, t, "2016", doi="10.1/x")])
    assert removed == 1 and out[0]["openalex_id"] == "W2"


def test_dedupe_same_doi():
    out, removed = dedupe([rec(1, "A long enough title about drones", doi="10.1/x"),
                           rec(2, "Totally different title text here", doi="https://doi.org/10.1/X")])
    assert removed == 1


def test_dedupe_same_title_years_apart_kept():
    t = "Thermal UAV survey of building envelopes in cold climates"
    _, removed = dedupe([rec(1, t, "2016"), rec(2, t, "2023")])
    assert removed == 0


def test_relevance():
    assert TAX.relevance("UAV thermal infrared imagery of crops") == "strong"
    assert TAX.relevance("Thermal management of drone batteries") == ""
    assert TAX.relevance("A drone study of thermal effects") == "weak"
    assert TAX.relevance("Thermal infrared satellite imagery") == ""  # no platform


def test_tagging():
    tag = lambda title, abstract="": TAX.tag({"title": title, "abstract": abstract})
    assert tag("Quality Control of PV-Modules using a drone thermal camera")["auto_tags"].startswith("solar-pv")
    # "unmanned aerial vehicle" must not trigger vehicle detection
    assert "surveillance-detection" not in tag("Unmanned aerial vehicle thermal imaging of roofs")["auto_tags"]
    assert tag("Calibration of uncooled thermal cameras for UAV")["auto_tags"] == "methods-general"
    assert "review" in tag("A review of UAV thermography", "We review roofs.")["auto_methods"]
    assert "DJI Zenmuse H20T/N" in tag("x", "flown with a DJI Zenmuse H20T on an M300")["auto_sensors"]


def test_merge_preserves_manual_fields():
    db = [{"openalex_id": "W1", "doi": "", "title": "Old title of a drone thermal paper",
           "year": "2020", "type": "article", "cited_by": "1", "status": "included",
           "tags": "wildlife", "notes": "great paper", "first_seen": "2026-01-01"}]
    fresh = [{"openalex_id": "W1", "doi": "", "title": "New title", "year": "2020", "cited_by": "9"}]
    db, added = merge(db, fresh, "2026-10-09")
    assert added == 0
    assert db[0]["cited_by"] == "9" and db[0]["status"] == "included"
    assert db[0]["notes"] == "great paper" and db[0]["first_seen"] == "2026-01-01"


def test_abstract_and_md():
    assert abstract_from_index({"thermal": [1], "UAV": [0]}) == "UAV thermal"
    assert md("a | b <i>c</i>") == "a \\| b c"
