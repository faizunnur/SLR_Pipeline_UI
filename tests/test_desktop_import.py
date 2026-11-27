"""
Importing exported files, reading aids and undo in the desktop app.

The import is the path for databases that give no API key to an individual
researcher (Scopus, Web of Science, IEEE Xplore). What must hold: a file is read
correctly whatever the export format, imported records behave exactly like
search results (de-duplicated, counted, screened), and no decision a reviewer
already made is lost or moved to a different paper.
"""
import json
import shutil
import time
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("rich")

from slr_desktop import importer
from slr_desktop.service import DEFAULT_REASONS, ReviewService, ServiceError

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "examples" / "candidates_sample.csv"

FORM = {
    "topic": "ML intrusion detection", "mailto": "reviewer@university.example",
    "keyword_blocks": [["intrusion detection", "IDS"], ["machine learn*"]],
    "inclusion_criteria": "empirical study", "exclusion_criteria": "not in English",
    "research_field": "software_engineering", "year_from": 2020, "year_to": 2024,
    "sources": ["openalex", "crossref"], "max_per_source": 20, "n_phases": 2,
    "title_threshold": 92, "reviewer": "FN", "assist_tool_name": "TestBot",
}

RIS = """TY  - JOUR
TI  - A wholly new study of import pipelines
AU  - Rahman, Ayesha
AU  - Chowdhury, Tanvir
PY  - 2023/05/01
JO  - Journal of Imported Things
DO  - https://doi.org/10.9999/IMPORT.001
UR  - https://example.org/a
AB  - First line of the abstract
      continues on a second line.
ER  -

TY  - CONF
T1  - Another imported record without a DOI
A1  - Islam, Nadia
Y1  - 2022
T2  - Proceedings of Examples
N2  - Short abstract.
ER  -
TY  - JOUR
AU  - Nobody, Has No Title
PY  - 2021
ER  -
"""

BIB = r"""@comment{exported by a database}
@article{rahman2023,
  title = {A {BibTeX} Record with Nested {Braces} and an Accent: Caf{\'e}},
  author = {Rahman, Ayesha and Chowdhury, Tanvir},
  journal = "Journal of Imported Things",
  year = 2023,
  doi = {10.9999/bib.001},
  abstract = {An abstract, with a comma and a closing brace \} inside.}
}
@inproceedings{islam2022, author = {Islam, Nadia}, title = "Second entry", booktitle = {Proc. of Examples}, year = {2022}}
"""

NBIB = """PMID- 12345678
TI  - A PubMed record about imports
      that wraps onto a second line.
AB  - The abstract.
FAU - Rahman, Ayesha
AU  - Rahman A
FAU - Islam, Nadia
DP  - 2021 Mar
JT  - The Journal of PubMed Examples
LID - 10.9999/pm.001 [doi]

PMID- 22222222
TI  - A second PubMed record
DP  - 2020
"""

SCOPUS_CSV = (
    '﻿"Authors","Title","Year","Source title","DOI","Link","Abstract"\n'
    '"Rahman A.; Islam N.","A Scopus CSV record, with a comma",2022,"Journal X","10.9999/SC.001",'
    '"https://www.scopus.com/x","Abstract text."\n'
    '"Chowdhury T.","Second Scopus record",2021,"Journal Y","","",""\n'
)


def wait(svc, job, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = svc.jobs.status(job.id)
        if status["status"] != "running":
            return status
        time.sleep(0.1)
    raise AssertionError("job did not finish: " + "\n".join(svc.jobs.status(job.id)["lines"]))


def events(svc, run_id, name):
    path = svc.runs_dir / run_id / "provenance.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r["event"] == name]


def sheet(svc, run_id, phase=1):
    return pd.read_csv(svc.runs_dir / run_id / f"phase_{phase}" / "screening.csv")


@pytest.fixture
def svc(tmp_path, monkeypatch):
    for var in ("S2_API_KEY", "IEEE_API_KEY", "SCOPUS_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return ReviewService(tmp_path / "workspace")


@pytest.fixture
def searched(svc):
    run_id = svc.create_run(FORM)["run_id"]
    pdir = svc.runs_dir / run_id / "phase_1"
    pdir.mkdir()
    shutil.copy(SAMPLE, pdir / "candidates.csv")
    state, _, _ = svc._load(run_id)
    state.mark_stage(1, "search", counts={"n_hits": len(pd.read_csv(SAMPLE))})
    status = wait(svc, svc.start_search(run_id, 1))
    assert status["status"] == "done", "\n".join(status["lines"])
    return run_id


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestParsers:
    def test_ris(self, tmp_path):
        out = importer.parse_file(write(tmp_path, "scopus.ris", RIS))
        assert out["format"] == "RIS" and out["n_no_title"] == 1
        first, second = out["records"]
        assert first == {
            "title": "A wholly new study of import pipelines",
            "authors": "Rahman, Ayesha; Chowdhury, Tanvir", "year": "2023",
            "venue": "Journal of Imported Things", "doi": "10.9999/import.001",
            "url": "https://example.org/a",
            "abstract": "First line of the abstract continues on a second line."}
        assert second["title"] == "Another imported record without a DOI"
        assert second["venue"] == "Proceedings of Examples" and second["abstract"] == "Short abstract."
        assert second["doi"] == "" and second["year"] == "2022"

    def test_ris_saved_as_txt_is_recognised_by_content(self, tmp_path):
        assert importer.parse_file(write(tmp_path, "savedrecs.txt", RIS))["format"] == "RIS"

    def test_bibtex(self, tmp_path):
        out = importer.parse_file(write(tmp_path, "export.bib", BIB))
        assert out["format"] == "BibTeX" and len(out["records"]) == 2
        first, second = out["records"]
        assert first["title"] == "A BibTeX Record with Nested Braces and an Accent: Cafe"
        assert first["authors"] == "Rahman, Ayesha; Chowdhury, Tanvir"
        assert first["venue"] == "Journal of Imported Things" and first["year"] == "2023"
        assert first["doi"] == "10.9999/bib.001"
        assert first["abstract"].startswith("An abstract, with a comma")
        assert second["title"] == "Second entry" and second["venue"] == "Proc. of Examples"

    def test_pubmed_nbib(self, tmp_path):
        out = importer.parse_file(write(tmp_path, "pubmed-set.nbib", NBIB))
        assert out["format"] == "MEDLINE" and len(out["records"]) == 2
        first = out["records"][0]
        assert first["title"] == "A PubMed record about imports that wraps onto a second line"
        assert first["authors"] == "Rahman, Ayesha; Islam, Nadia"
        assert first["year"] == "2021" and first["doi"] == "10.9999/pm.001"
        assert first["url"] == "https://pubmed.ncbi.nlm.nih.gov/12345678/"

    def test_scopus_csv_with_bom_and_quoted_commas(self, tmp_path):
        out = importer.parse_file(write(tmp_path, "scopus.csv", SCOPUS_CSV))
        assert out["format"] == "CSV"
        first, second = out["records"]
        assert first["title"] == "A Scopus CSV record, with a comma"
        assert first["authors"] == "Rahman A.; Islam N." and first["year"] == "2022"
        assert first["venue"] == "Journal X" and first["doi"] == "10.9999/sc.001"
        assert second["doi"] == "" and second["abstract"] == ""

    def test_ieee_csv_column_names(self, tmp_path):
        text = ('"Document Title",Authors,"Publication Title","Publication Year",Abstract,DOI,"PDF Link"\n'
                '"An IEEE record","A. Rahman; N. Islam","IEEE Access",2024,"Text.","10.1109/X.1","https://ieeexplore.ieee.org/x"\n')
        rec = importer.parse_file(write(tmp_path, "export2026.csv", text))["records"][0]
        assert rec["title"] == "An IEEE record" and rec["venue"] == "IEEE Access"
        assert rec["year"] == "2024" and rec["doi"] == "10.1109/x.1"

    @pytest.mark.parametrize("name,text,match", [
        ("notes.csv", "a,b\n1,2\n", "no title column"),
        ("thesis.docx", "PK binary", "does not look like a reference export"),
        ("empty.ris", "TY  - JOUR\nAU  - X\nER  -\n", "No records with a title"),
    ])
    def test_unusable_files_say_why(self, tmp_path, name, text, match):
        with pytest.raises(importer.ImportProblem, match=match):
            importer.parse_file(write(tmp_path, name, text))

    def test_doi_and_year_cleaning(self):
        assert importer.clean_doi("doi:10.1000/ABC.") == "10.1000/abc"
        assert importer.clean_doi("https://dx.doi.org/10.1000/a(b)c [doi]") == "10.1000/a(b)c"
        assert importer.clean_doi("not a doi") == ""
        assert importer.clean_year("2023/05/01") == "2023" and importer.clean_year("Mar 1999") == "1999"
        assert importer.clean_year("12345") == "" and importer.clean_year("") == ""


class TestImportIntoAReview:
    def test_preview_changes_nothing(self, svc, searched, tmp_path):
        before = sheet(svc, searched)
        info = svc.preview_import(str(write(tmp_path, "scopus.ris", RIS)))
        assert info["n_records"] == 2 and info["n_no_title"] == 1 and info["format"] == "RIS"
        assert info["n_abstract"] == 2 and info["n_doi"] == 1 and info["source_guess"] == "Scopus"
        assert sheet(svc, searched).equals(before)
        with pytest.raises(ServiceError, match="no title column"):
            svc.preview_import(str(write(tmp_path, "bad.csv", "a,b\n1,2\n")))

    def test_import_adds_records_and_keeps_every_decision(self, svc, searched, tmp_path):
        before = sheet(svc, searched)
        ids = [str(v) for v in before["id"].tolist()]
        svc.set_ta_decision(searched, 1, ids[0], "include", "on topic")
        svc.set_ta_decision(searched, 1, ids[1], "exclude", "Wrong topic")
        state, _, _ = svc._load(searched)
        state.mark_stage(1, "review_gate", counts={"n_included": 1})

        job = svc.start_import(searched, 1, str(write(tmp_path, "scopus.ris", RIS)), "Scopus",
                               'TITLE-ABS-KEY("intrusion detection")', "2026-11-20")
        status = wait(svc, job)
        assert status["status"] == "done", "\n".join(status["lines"])
        assert status["result"] == {"n_records": 2, "n_new": 2, "n_duplicates": 0}

        after = sheet(svc, searched)
        assert len(after) == len(before) + 2
        by_id = after.set_index(after["id"].astype(str))
        assert by_id.loc[ids[0], "ta_decision"] == "include" and by_id.loc[ids[0], "ta_reason"] == "on topic"
        assert by_id.loc[ids[1], "ta_decision"] == "exclude"
        # ids of records already there never change; the new ones continue the numbering
        assert set(ids) <= set(by_id.index)
        new = after[after["title"].str.contains("import", case=False, na=False)]
        assert len(new) == 2 and new["ta_decision"].isna().all()
        assert int(new["id"].min()) > max(int(i) for i in ids)
        assert set(new["round"]) == {"Scopus"}

        # reported like any other source (PRISMA items 6 and 7)
        strategy = svc.search_strategy(searched, 1)
        row = next(r for r in strategy if r["source"] == "Scopus")
        assert row["status"] == "imported" and row["n_retrieved"] == 2
        assert row["query_sent"] == 'TITLE-ABS-KEY("intrusion detection")'
        assert row["retrieved_at"] == "2026-11-20"
        logged = events(svc, searched, "records_imported")[-1]
        assert logged["file"] == "scopus.ris" and logged["n_new_to_screen"] == 2

        run = svc.get_run(searched)
        assert run["prisma"]["identified"] == len(pd.read_csv(SAMPLE)) + 2
        # new undecided records reopen the gate: it no longer covers the whole phase
        assert run["phases"][0]["gated"] is False and run["phases"][0]["searched"] is True

    def test_same_file_twice_is_refused(self, svc, searched, tmp_path):
        path = str(write(tmp_path, "scopus.ris", RIS))
        assert wait(svc, svc.start_import(searched, 1, path, "Scopus"))["status"] == "done"
        with pytest.raises(ServiceError, match="already imported"):
            svc.start_import(searched, 1, path, "Scopus")
        with pytest.raises(ServiceError, match="which database"):
            svc.start_import(searched, 1, str(write(tmp_path, "other.ris", RIS + "\n")), "  ")

    def test_records_already_in_the_review_are_removed_as_duplicates(self, svc, searched, tmp_path):
        before = sheet(svc, searched)
        have = pd.read_csv(svc.runs_dir / searched / "phase_1" / "candidates_dedup.csv")
        have = have[have["duplicate_of"].isna() & have["doi"].notna()].iloc[0]
        text = (f"TY  - JOUR\nTI  - {have['title']}\nAU  - Someone, Else\nDO  - {have['doi']}\nER  - \n" + RIS)
        status = wait(svc, svc.start_import(searched, 1, str(write(tmp_path, "wos.ris", text)), "Web of Science"))
        assert status["result"] == {"n_records": 3, "n_new": 2, "n_duplicates": 1}
        assert len(sheet(svc, searched)) == len(before) + 2

    def test_a_decision_follows_the_record_when_the_imported_copy_is_better(self, svc, tmp_path):
        """dedup keeps the copy with a DOI. If the reviewer already decided the
        DOI-less copy, the decision must move to the surviving row, not vanish."""
        run_id = svc.create_run(FORM)["run_id"]
        first = ("TY  - JOUR\nTI  - Federated anomaly detection for industrial control networks\n"
                 "AU  - Rahman, Ayesha\nPY  - 2022\nER  - \n"
                 "TY  - JOUR\nTI  - An unrelated second record about something else\nAU  - Islam, Nadia\nER  - \n")
        assert wait(svc, svc.start_import(run_id, 1, str(write(tmp_path, "a.ris", first)), "Scopus"))["status"] == "done"
        rows = sheet(svc, run_id)
        rid = str(rows[rows["title"].str.startswith("Federated")]["id"].iloc[0])
        svc.set_ta_decision(run_id, 1, rid, "include", "relevant")

        better = ("TY  - JOUR\nTI  - Federated anomaly detection for industrial control networks\n"
                  "AU  - Rahman, Ayesha\nPY  - 2022\nDO  - 10.9999/fed.1\nAB  - Now with an abstract.\nER  - \n")
        status = wait(svc, svc.start_import(run_id, 1, str(write(tmp_path, "b.ris", better)), "Web of Science"))
        assert status["status"] == "done", "\n".join(status["lines"])
        rows = sheet(svc, run_id)
        kept = rows[rows["title"].str.startswith("Federated")]
        assert len(kept) == 1 and str(kept["id"].iloc[0]) != rid
        assert kept["doi"].iloc[0] == "10.9999/fed.1"
        assert kept["ta_decision"].iloc[0] == "include" and kept["ta_reason"].iloc[0] == "relevant"
        assert any("moved to the better copy" in line for line in status["lines"])

    def test_a_review_can_start_from_an_import_alone(self, svc, tmp_path):
        run_id = svc.create_run(FORM)["run_id"]
        status = wait(svc, svc.start_import(run_id, 1, str(write(tmp_path, "scopus.csv", SCOPUS_CSV)), "Scopus"))
        assert status["status"] == "done", "\n".join(status["lines"])
        run = svc.get_run(run_id)
        phase = run["phases"][0]
        assert phase["searched"] and phase["n_rows"] == 2
        assert phase["stages"]["search"]["counts"] == {"n_hits": 2, "n_imported": 2, "imported_only": True}
        assert [r["id"] for r in svc.get_screening(run_id, 1)["rows"]] == ["1", "2"]
        assert svc.list_imports(run_id, 1)[0]["file"] == "scopus.csv"

    def test_a_later_search_keeps_the_imported_records(self, svc, searched, tmp_path):
        """search.py rewrites candidates.csv; the import must be folded back in."""
        assert wait(svc, svc.start_import(searched, 1, str(write(tmp_path, "scopus.ris", RIS)), "Scopus"))["status"] == "done"
        state, _, _ = svc._load(searched)
        pdir = state.phase_dir(1)
        shutil.copy(SAMPLE, pdir / "candidates.csv")           # what a fresh search leaves behind
        (pdir / "search_strategy.csv").unlink()
        assert svc._fold_imports(state, 1, pdir) == 2
        cands = pd.read_csv(pdir / "candidates.csv")
        assert len(cands) == len(pd.read_csv(SAMPLE)) + 2 and cands["id"].is_unique
        assert (cands["source"] == "Scopus").sum() == 2
        assert pd.read_csv(pdir / "search_strategy.csv")["status"].tolist() == ["imported"]


class TestReadingAids:
    def test_defaults_come_from_the_search_string(self, svc, searched):
        aids = svc.get_screening(searched, 1)["aids"]
        assert aids["keywords"] == ["intrusion detection", "IDS", "machine learn*"]
        assert aids["use_keywords"] is True and aids["include"] == [] and aids["exclude"] == []
        assert aids["reasons"] == DEFAULT_REASONS and len(DEFAULT_REASONS) <= 9

    def test_saved_words_and_reasons_come_back_cleaned(self, svc, searched):
        out = svc.set_screening_aids(searched, {
            "use_keywords": False, "include": "cohort,  Randomized ,cohort,\n", "exclude": ["editorial", ""],
            "reasons": ["Wrong population", " ", "Not a study", "Wrong population"] + [f"r{i}" for i in range(12)]})
        assert out["include"] == ["cohort", "Randomized"] and out["exclude"] == ["editorial"]
        assert out["use_keywords"] is False
        assert out["reasons"][:2] == ["Wrong population", "Not a study"] and len(out["reasons"]) == 9
        assert svc.get_fulltext(searched)["aids"]["include"] == ["cohort", "Randomized"]
        assert events(svc, searched, "screening_aids_edited")
        # a reading aid never touches a decision or the protocol
        assert sheet(svc, searched)["ta_decision"].isna().all()


class TestUndo:
    def test_undo_returns_a_record_to_no_decision_and_is_audited(self, svc, searched):
        rid = svc.get_screening(searched, 1)["rows"][0]["id"]
        svc.set_ta_decision(searched, 1, rid, "exclude", "Wrong topic")
        out = svc.restore_decision(searched, "ta", 1, rid, "", "", "")
        assert out["decision"] == ""
        row = next(r for r in svc.get_screening(searched, 1)["rows"] if r["id"] == rid)
        assert row["ta_decision"] == "" and row["ta_reason"] == "" and row["reviewer"] == ""
        state, _, _ = svc._load(searched)
        trail = [d for d in state.decisions() if str(d["id"]) == rid]
        assert [d["decision"] for d in trail] == ["exclude", ""]
        assert trail[-1]["source"] == "human-review/undo"

    def test_undo_restores_the_earlier_decision_reason_and_reviewer(self, svc, searched):
        rid = svc.get_screening(searched, 1)["rows"][0]["id"]
        svc.set_ta_decision(searched, 1, rid, "maybe", "unclear design")
        svc.set_ta_decision(searched, 1, rid, "exclude", "Wrong topic")
        svc.restore_decision(searched, "ta", 1, rid, "maybe", "unclear design", "FN")
        row = next(r for r in svc.get_screening(searched, 1)["rows"] if r["id"] == rid)
        assert (row["ta_decision"], row["ta_reason"], row["reviewer"]) == ("maybe", "unclear design", "FN")

    def test_undo_at_full_text_reopens_a_completed_stage(self, svc, searched):
        rows = svc.get_screening(searched, 1)["rows"]
        for r in rows:
            svc.set_ta_decision(searched, 1, r["id"], "include" if r is rows[0] else "exclude", "x")
        svc.apply_gate(searched, 1, [], [])
        ft = svc.get_fulltext(searched)["rows"]
        assert len(ft) == 1
        assert svc.set_ft_decision(searched, ft[0]["id"], "include")["complete"] is True
        assert svc.get_run(searched)["fulltext_done"] is True
        svc.restore_decision(searched, "ft", 0, ft[0]["id"], "", "", "")
        assert svc.get_run(searched)["fulltext_done"] is False
        assert svc.get_fulltext(searched)["rows"][0]["ft_decision"] == ""
        assert events(svc, searched, "full_text_screening_reopened")

    def test_undo_refuses_nonsense(self, svc, searched):
        rid = svc.get_screening(searched, 1)["rows"][0]["id"]
        with pytest.raises(ServiceError, match="cannot be restored"):
            svc.restore_decision(searched, "ta", 1, rid, "banana")
        with pytest.raises(ServiceError, match="cannot be restored"):
            svc.restore_decision(searched, "ft", 0, rid, "maybe")
        with pytest.raises(ServiceError, match="No record with id"):
            svc.restore_decision(searched, "ta", 1, "99999", "include")
