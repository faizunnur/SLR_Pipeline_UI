"""
Desktop-app service tests.

The desktop app must be a second interface to the SAME method, not a second
method. These tests drive a whole review through slr_desktop.service with no
window and no network (the search output is seeded from the bundled example,
every later stage runs the real stage script), and assert the things that make
a run auditable: checkpoints in state.json, decisions in decisions.jsonl,
events in provenance.jsonl, and the refusal cases the terminal app enforces.
"""
import json
import shutil
import time
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("rich")

from slr_desktop.jobs import JobManager, redact_cmd
from slr_desktop.service import ReviewService, ServiceError

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "examples" / "candidates_sample.csv"

FORM = {
    "topic": "ML intrusion detection", "mailto": "reviewer@university.example",
    "keyword_blocks": [["intrusion detection", "IDS"], ["machine learning"]],
    "inclusion_criteria": "empirical study", "exclusion_criteria": "not in English",
    "research_field": "software_engineering", "year_from": 2020, "year_to": 2024,
    "sources": ["openalex", "crossref"], "max_per_source": 20, "n_phases": 2,
    "title_threshold": 92, "reviewer": "FN", "assist_tool_name": "TestBot",
}


def wait(svc, job, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = svc.jobs.status(job.id)
        if status["status"] != "running":
            return status
        time.sleep(0.1)
    raise AssertionError("job did not finish: " + "\n".join(svc.jobs.status(job.id)["lines"]))


def events(svc, run_id):
    path = svc.runs_dir / run_id / "provenance.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def svc(tmp_path, monkeypatch):
    for var in ("S2_API_KEY", "IEEE_API_KEY", "SCOPUS_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return ReviewService(tmp_path / "workspace")


@pytest.fixture
def searched(svc):
    """A review whose phase-1 search output is already on disk; the job then
    runs the real dedup.py and screen.py."""
    run_id = svc.create_run(FORM)["run_id"]
    pdir = svc.runs_dir / run_id / "phase_1"
    pdir.mkdir()
    shutil.copy(SAMPLE, pdir / "candidates.csv")
    state, _, _ = svc._load(run_id)
    state.mark_stage(1, "search", counts={"n_hits": len(pd.read_csv(SAMPLE))})
    status = wait(svc, svc.start_search(run_id, 1))
    assert status["status"] == "done", "\n".join(status["lines"])
    return run_id


class TestCreate:
    def test_creates_run_with_block_query_and_no_secrets(self, svc):
        out = svc.create_run({**FORM, "ieee_api_key": "SECRET"})
        assert out["query"] == '("intrusion detection" OR IDS) AND "machine learning"'
        config = json.loads((svc.runs_dir / out["run_id"] / "config.json").read_text(encoding="utf-8"))
        assert config["n_phases"] == 2 and config["reviewer"] == "FN"
        assert not any("api_key" in k for k in config)
        names = [e["event"] for e in events(svc, out["run_id"])]
        assert names[:2] == ["review_created", "tool_version"]

    def test_default_grey_literature_reason_is_recorded(self, svc):
        run_id = svc.create_run(FORM)["run_id"]
        cfg = svc.get_run(run_id)["config"]
        assert cfg["grey_literature_included"] is False
        assert "peer-reviewed" in cfg["grey_literature_justification"]
        assert "narratively" in cfg["reporting_bias_assessment"]

    @pytest.mark.parametrize("patch,needle", [
        ({"topic": " "}, "topic"), ({"mailto": "nobody"}, "email"),
        ({"year_from": 2025, "year_to": 2020}, "Year from"), ({"n_phases": 0}, "n_phases"),
    ])
    def test_rejects_bad_input(self, svc, patch, needle):
        with pytest.raises(ServiceError, match=needle):
            svc.create_run({**FORM, **patch})

    def test_same_topic_gets_a_distinct_folder(self, svc):
        a, b = svc.create_run(FORM)["run_id"], svc.create_run(FORM)["run_id"]
        assert a != b and len(svc.list_runs()) == 2


class TestPhase:
    def test_search_job_marks_dedup_and_prescreen(self, svc, searched):
        info = svc.get_run(searched)["phases"][0]
        assert info["searched"] and info["n_rows"] > 0 and info["n_undecided"] == info["n_rows"]
        assert info["stages"]["dedup"]["counts"]["n_in"] == len(pd.read_csv(SAMPLE))
        assert svc.get_run(searched)["progress"]["target"] == {"tab": "screening", "phase": 1}

    def test_phase_two_is_locked_until_expansion(self, svc, searched):
        with pytest.raises(ServiceError, match="Phase 2 opens after"):
            svc.start_search(searched, 2)

    def test_manual_decision_is_written_and_audited(self, svc, searched):
        rows = svc.get_screening(searched, 1)["rows"]
        out = svc.set_ta_decision(searched, 1, rows[0]["id"], "exclude", "off topic")
        assert out["remaining"] == len(rows) - 1
        again = svc.get_screening(searched, 1)["rows"][0]
        assert again["ta_decision"] == "exclude" and again["reviewer"] == "FN"
        log = (svc.runs_dir / searched / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
        last = json.loads(log[-1])
        assert last["decision"] == "exclude" and last["source"] == "human-review" and last["stage"] == "ta"

    def test_bad_decision_value_is_refused(self, svc, searched):
        with pytest.raises(ServiceError, match="include, exclude or maybe"):
            svc.set_ta_decision(searched, 1, "1", "perhaps")

    def test_ai_assist_round_trip_only_touches_its_batch(self, svc, searched):
        batch = svc.assist_batch(searched, 1)
        assert not batch["done"] and len(batch["batch_ids"]) == 20
        assert Path(batch["prompt_file"]).exists() and "empirical study" in batch["prompt"]
        reply = "\n".join(f"{i} | {'INCLUDE' if n % 2 else 'EXCLUDE'} | reason {i}"
                          for n, i in enumerate(batch["batch_ids"])) + "\n9999 | INCLUDE | invented id"
        out = svc.assist_apply(searched, 1, batch["start"], batch["batch_ids"], reply)
        assert out["n_applied"] == 20 and out["counts"] == {"include": 10, "exclude": 10}
        assert any("not in this batch" in p for p in out["problems"])
        assert (svc.runs_dir / searched / "phase_1" / f"reply_ta_{batch['start']}.txt").exists()
        assert svc.assist_finish(searched, 1)["complete"] is False  # rows remain undecided
        nxt = svc.assist_batch(searched, 1)
        assert nxt["start"] == 20 and not set(nxt["batch_ids"]) & set(batch["batch_ids"])

    def test_gate_refuses_to_complete_with_undecided_rows(self, svc, searched):
        out = svc.apply_gate(searched, 1, [], [])
        assert out["complete"] is False and out["n_undecided"] > 0
        assert not svc.get_run(searched)["phases"][0]["gated"]
        assert events(svc, searched)[-1]["event"] == "review_gate_incomplete"


def screen_all(svc, run_id, include_first=3):
    rows = svc.get_screening(run_id, 1)["rows"]
    for n, row in enumerate(rows):
        decision = "include" if n < include_first else "exclude"
        svc.set_ta_decision(run_id, 1, row["id"], decision, "" if decision == "include" else "out of scope")
    return rows


class TestGateAndBeyond:
    def test_gate_flips_both_directions_and_advances(self, svc, searched):
        rows = screen_all(svc, searched)
        gate = svc.get_gate(searched, 1)
        assert len(gate["included"]) == 3 and gate["n_undecided"] == 0
        out = svc.apply_gate(searched, 1, [rows[0]["id"]], [rows[5]["id"], rows[6]["id"]])
        assert out == {"complete": True, "n_included": 4, "n_to_exclude": 1, "n_to_include": 2}
        sheet = {r["id"]: r for r in svc.get_screening(searched, 1)["rows"]}
        assert sheet[rows[0]["id"]]["ta_decision"] == "exclude"
        assert "[reviewer override]" in sheet[rows[5]["id"]]["ta_reason"]
        # phase 2 of 2 still needs the expansion step before it opens
        assert svc.get_run(searched)["current_phase"] == 1

    def test_expansion_opens_next_phase_with_expanded_query(self, svc, searched):
        screen_all(svc, searched)
        with pytest.raises(ServiceError, match="review gate first"):
            svc.apply_expansion(searched, 1, ["federated"])
        svc.apply_gate(searched, 1, [], [])
        assert isinstance(svc.expansion_terms(searched, 1)["terms"], list)
        out = svc.apply_expansion(searched, 1, ["federated learning"])
        assert out["next_query"].endswith('AND "federated learning"')
        run = svc.get_run(searched)
        assert run["current_phase"] == 2 and run["phases"][1]["unlocked"]
        assert run["phases"][1]["query"] == out["next_query"]

    def test_full_text_needs_reason_and_completes(self, svc, searched):
        screen_all(svc, searched)
        svc.apply_gate(searched, 1, [], [])
        assert svc.merge(searched)["n_merged"] == 3
        rows = svc.get_fulltext(searched)["rows"]
        assert [r["id"] for r in rows][0].startswith("p1_")
        with pytest.raises(ServiceError, match="16b"):
            svc.set_ft_decision(searched, rows[0]["id"], "exclude", "")
        with pytest.raises(ServiceError, match="no maybe"):
            svc.set_ft_decision(searched, rows[0]["id"], "maybe", "")
        svc.set_ft_decision(searched, rows[0]["id"], "exclude", "wrong outcome")
        svc.set_ft_decision(searched, rows[1]["id"], "include")
        assert svc.finish_fulltext(searched)["complete"] is False
        out = svc.set_ft_decision(searched, rows[2]["id"], "include")
        assert out == {"n_included": 2, "n_excluded": 1, "n_left": 0, "complete": True}
        run = svc.get_run(searched)
        assert run["fulltext_done"] and run["prisma"]["included"] == 2
        assert run["prisma"]["ft_reasons"] == {"wrong outcome": 1}

    def test_extraction_reports_and_exports(self, svc, searched):
        screen_all(svc, searched)
        svc.apply_gate(searched, 1, [], [])
        for row in svc.get_fulltext(searched)["rows"]:
            svc.set_ft_decision(searched, row["id"], "include")
        status = wait(svc, svc.start_extract(searched))
        assert status["status"] == "done", "\n".join(status["lines"])
        sheet = svc.get_extraction(searched)
        assert len(sheet["rows"]) == 3 and "quality_tier" in sheet["columns"]
        rid = sheet["rows"][0]["id"]

        with pytest.raises(ServiceError, match="one of"):
            svc.update_extraction(searched, rid, "R", "X")
        for field in ("R", "A", "T", "C"):
            out = svc.update_extraction(searched, rid, field, "H")
        assert out["row"]["quality_tier"] in ("A", "B", "C")
        assert out["row"]["extraction_reviewer"] == "FN"
        computed = out["row"]["quality_tier"]
        other = next(t for t in ("A", "B", "C") if t != computed)
        assert "manual override" in svc.update_extraction(searched, rid, "quality_tier", other)["note"]
        assert svc.update_extraction(searched, rid, "notes", "")["changed"] is False

        assert svc.export_references(searched)["n_records"] == 3
        assert "search" in svc.write_methods(searched)["paragraph"].lower()
        assert Path(svc.write_provenance(searched)["path"]).exists()
        assert Path(svc.write_self_appraisal(searched)["path"]).name == "review_self_appraisal_dare.md"
        with pytest.raises(ServiceError, match="No full-text exclusions"):
            svc.export_exclusions(searched)

        names = {e["event"] for e in events(svc, searched)}
        assert {"build_extraction_sheet", "extraction_field_edited", "quality_tier_recomputed",
                "quality_tier_manual_override", "export_references", "methods_report_drafted"} <= names
        present = {f["name"] for f in svc.outputs(searched)["files"] if f["exists"]}
        assert {"extraction.csv", "references.bib", "references.ris", "PROVENANCE.md"} <= present
        assert svc.read_text(searched, "search_methods.md").startswith("# Search methods")

    def test_figures_are_drawn_from_the_run(self, svc, searched):
        screen_all(svc, searched)
        svc.apply_gate(searched, 1, [], [])
        for row in svc.get_fulltext(searched)["rows"]:
            svc.set_ft_decision(searched, row["id"], "include")
        assert svc.figures_check(searched)["warnings"] == []
        status = wait(svc, svc.start_figures(searched))
        assert status["status"] == "done", "\n".join(status["lines"])
        figs = svc.figures(searched)
        assert any(f["name"].startswith("prisma") for f in figs)
        assert all(f["src"].startswith("data:image/png;base64,") for f in figs)

    def test_kappa_between_two_sheets(self, svc, searched, tmp_path):
        screen_all(svc, searched)
        sheet_a = svc.runs_dir / searched / "phase_1" / "screening.csv"
        df = pd.read_csv(sheet_a)
        df.loc[0, "ta_decision"] = "exclude"
        sheet_b = tmp_path / "reviewer_b.csv"
        df.to_csv(sheet_b, index=False)
        out = svc.kappa(searched, str(sheet_a), str(sheet_b), "ta_decision")
        assert out["n_compared"] == len(df) and len(out["conflicts"]) == 1
        assert Path(out["conflicts_path"]).exists()

    def test_diagnose_flags_a_missing_stage_file(self, svc, searched):
        (svc.runs_dir / searched / "phase_1" / "candidates_dedup.csv").unlink()
        out = svc.diagnose(searched)
        assert any("candidates_dedup.csv" in m for m in out["mismatches"])


class TestSafety:
    def test_read_text_stays_inside_the_run_folder(self, svc, searched):
        (svc.workspace / "secret.txt").write_text("x", encoding="utf-8")
        with pytest.raises(ServiceError, match="not part of this review"):
            svc.read_text(searched, "../../secret.txt")

    def test_api_keys_go_to_workspace_env_and_never_to_config(self, svc):
        run_id = svc.create_run({**FORM, "sources": ["openalex", "ieee"]})["run_id"]
        assert svc.get_run(run_id)["missing_keys"] == ["ieee"]
        keys = svc.set_api_key("IEEE_API_KEY", "abc123")
        assert next(k for k in keys["keys"] if k["var"] == "IEEE_API_KEY")["set"] is True
        assert "IEEE_API_KEY=abc123" in svc.env_path.read_text(encoding="utf-8")
        assert svc.get_run(run_id)["missing_keys"] == []
        assert "abc123" not in (svc.runs_dir / run_id / "config.json").read_text(encoding="utf-8")
        svc.delete_api_key("IEEE_API_KEY")
        assert svc.get_run(run_id)["missing_keys"] == ["ieee"]
        with pytest.raises(ServiceError, match="Unknown key"):
            svc.set_api_key("PATH", "x")

    def test_delete_run_only_removes_a_review_folder(self, svc):
        run_id = svc.create_run(FORM)["run_id"]
        with pytest.raises(ServiceError, match="Not a review folder"):
            svc.delete_run("..")
        svc.delete_run(run_id)
        assert svc.list_runs() == []

    def test_secret_flag_values_are_masked_in_the_log(self):
        shown = redact_cmd(["python", "search.py", "--scopus-insttoken", "TOKEN", "--out", "a b.csv"])
        assert "TOKEN" not in shown and "****" in shown and '"a b.csv"' in shown

    def test_only_one_job_runs_at_a_time(self):
        import threading
        gate = threading.Event()
        jobs = JobManager()
        first = jobs.start("first", lambda ctx: gate.wait(5) and {})
        with pytest.raises(RuntimeError, match="still running"):
            jobs.start("second", lambda ctx: {})
        gate.set()
        deadline = time.time() + 5
        while jobs.status(first.id)["status"] == "running" and time.time() < deadline:
            time.sleep(0.02)
        assert jobs.status(first.id)["status"] == "done"
        assert jobs.status(jobs.start("third", lambda ctx: {"n": 1}).id)["status"] in ("running", "done")


class TestNothingIsSilentlyLost:
    """Where one click could discard recorded work, the desktop app keeps it."""

    def test_rerun_search_starts_a_blank_sheet_and_keeps_the_old_one(self, svc, searched, tmp_path,
                                                                     monkeypatch):
        import slr_desktop.service as service_module

        # A stand-in for search.py (no network): writes the sample, REVERSED, so
        # the same row ids now belong to different papers.
        stub = tmp_path / "search.py"
        stub.write_text(
            "import argparse, pandas as pd\n"
            "ap = argparse.ArgumentParser()\n"
            "ap.add_argument('--out'); ap.add_argument('--strategy-log')\n"
            "args, _ = ap.parse_known_args()\n"
            f"df = pd.read_csv(r'{SAMPLE}').iloc[::-1].reset_index(drop=True)\n"
            "df['id'] = range(1, len(df) + 1)\n"
            "df.to_csv(args.out, index=False)\n", encoding="utf-8")
        real = service_module._script
        monkeypatch.setattr(service_module, "_script",
                            lambda name: str(stub) if name == "search.py" else real(name))

        before = svc.get_screening(searched, 1)["rows"]
        svc.set_ta_decision(searched, 1, before[0]["id"], "include", "relevant")
        status = wait(svc, svc.start_search(searched, 1, True))
        assert status["status"] == "done", "\n".join(status["lines"])

        after = svc.get_screening(searched, 1)["rows"]
        assert all(r["ta_decision"] == "" for r in after), "an old decision landed on a new record"
        backups = list((svc.runs_dir / searched / "phase_1").glob("screening.before-rerun-*.csv"))
        assert len(backups) == 1
        assert "include" in pd.read_csv(backups[0])["ta_decision"].astype(str).tolist()
        assert events(svc, searched)[-1]["event"] == "rerun_search"

    def test_reconfirming_the_gate_keeps_full_text_decisions(self, svc, searched):
        rows = screen_all(svc, searched)
        svc.apply_gate(searched, 1, [], [])
        ft = svc.get_fulltext(searched)["rows"]
        svc.set_ft_decision(searched, ft[0]["id"], "exclude", "wrong population")
        svc.set_ft_decision(searched, ft[1]["id"], "include")

        # rescue one more record at the gate: the merged set is rebuilt
        time.sleep(1.1)  # the staleness check compares file modification times
        svc.apply_gate(searched, 1, [], [rows[7]["id"]])
        again = {r["id"]: r for r in svc.get_fulltext(searched)["rows"]}
        assert len(again) == 4
        assert again[ft[0]["id"]]["ft_decision"] == "exclude"
        assert again[ft[0]["id"]]["ft_reason"] == "wrong population"
        assert again[ft[1]["id"]]["ft_decision"] == "include"
        assert again[f"p1_{rows[7]['id']}"]["ft_decision"] == ""

    def test_explicit_merge_also_keeps_full_text_decisions(self, svc, searched):
        screen_all(svc, searched)
        svc.apply_gate(searched, 1, [], [])
        ft = svc.get_fulltext(searched)["rows"]
        svc.set_ft_decision(searched, ft[0]["id"], "include")
        assert svc.merge(searched)["n_merged"] == 3
        assert svc.get_fulltext(searched)["rows"][0]["ft_decision"] == "include"

    def test_rebuilding_the_extraction_sheet_keeps_typed_values(self, svc, searched):
        screen_all(svc, searched)
        svc.apply_gate(searched, 1, [], [])
        for row in svc.get_fulltext(searched)["rows"]:
            svc.set_ft_decision(searched, row["id"], "include")
        wait(svc, svc.start_extract(searched))
        rid = svc.get_extraction(searched)["rows"][0]["id"]
        svc.update_extraction(searched, rid, "key_findings", "F1 of 0.97 on CIC-IDS2017")
        svc.update_extraction(searched, rid, "venue_tier", "T1")

        status = wait(svc, svc.start_extract(searched))
        assert status["status"] == "done" and status["result"]["n_kept"] == 1
        row = next(r for r in svc.get_extraction(searched)["rows"] if r["id"] == rid)
        assert row["key_findings"] == "F1 of 0.97 on CIC-IDS2017" and row["venue_tier"] == "T1"


class TestEasyStart:
    """The things that remove set-up work for a first-time user."""

    def test_example_review_needs_no_setup_and_no_network(self, svc):
        job, run_id = svc.start_example()
        status = wait(svc, job)
        assert status["status"] == "done", "\n".join(status["lines"])
        phase = svc.get_run(run_id)["phases"][0]
        assert phase["searched"] and phase["n_rows"] > 10 and phase["n_undecided"] == phase["n_rows"]
        names = [e["event"] for e in events(svc, run_id)]
        # bundled records must not be passed off as a search this review ran
        assert "example_data_loaded" in names and "search_run" not in names
        assert svc.get_screening(run_id, 1)["criteria"]["inclusion"]

    def test_example_review_cannot_contact_services_without_an_email(self, svc):
        job, run_id = svc.start_example()
        wait(svc, job)
        for call in (lambda: svc.start_download(run_id), lambda: svc.start_snowball(run_id),
                     lambda: svc.start_search(run_id, 1, True)):
            with pytest.raises(ServiceError, match="contact email"):
                call()
        svc.update_search_settings(run_id, {"mailto": "reviewer@university.example"})
        assert svc.get_run(run_id)["config"]["mailto"] == "reviewer@university.example"

    def test_shortcut_points_at_something_that_exists(self):
        from slr_desktop import shortcut
        cmd = shortcut.launch_command()
        assert Path(cmd[0]).exists()
        assert cmd[0].lower().endswith(("slr-desktop", "slr-desktop.exe")) or cmd[1:] == ["-m", "slr_desktop"]

    def test_shortcut_is_created_in_the_given_folder(self, tmp_path):
        from slr_desktop import shortcut
        paths = shortcut.create([tmp_path])
        assert len(paths) == 1 and paths[0].exists() and paths[0].parent == tmp_path
        assert paths[0].stat().st_size > 0

    def test_linux_launcher_is_a_valid_desktop_entry(self, tmp_path, monkeypatch):
        from slr_desktop import shortcut
        monkeypatch.setattr(shortcut.sys, "platform", "linux")
        path = shortcut.create([tmp_path / "apps"])[0]
        body = path.read_text(encoding="utf-8")
        assert body.startswith("[Desktop Entry]") and "Terminal=false" in body
        assert "Exec=" in body and "slr" in body
