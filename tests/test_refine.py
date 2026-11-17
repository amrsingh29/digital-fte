"""Phase 4: AI estimates on top of the rules, guard rails, corrections and learning."""

import json
import time
import tomllib

import pytest

from digital_fte import ai, config, db, estimate, refine
from digital_fte.collectors.base import Event, Session

CFG = tomllib.loads(config.DEFAULT)
CFG["ai"]["use"] = "cortex"          # the AI tests need a provider on; the shipped default is off


def setup(tmp_path, n_tasks=1):
    con = db.connect(tmp_path / "t.db")
    for i in range(n_tasks):
        s = Session(agent="cline", native_id=f"s{i}", source=f"cline:s{i}", project="p", events=[
            Event(ts=i * 10_000, kind="prompt", text="write a report"),
            Event(ts=i * 10_000 + 60, kind="action", tool="editor", target=f"/nowhere/r{i}.md", lines_added=400),
            Event(ts=i * 10_000 + 90, kind="reply", text="done")])
        db.replace_source(con, s.source, s.agent, "v", [s])
    estimate.rebuild(con, CFG)
    ai.ensure_tables(con)
    return con, [r["id"] for r in con.execute("select id from tasks order by started")]


def add_ai(con, task_id, h, k=0.9, title="AI title", category="documentation", service="cortex", model="cortex-fast",
           purpose="project", roles=None):
    con.execute("insert or replace into ai_estimates values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, service, model, 3, title, category, h * 0.7, h, h * 1.3, k, "why", None, time.time(), purpose,
                 json.dumps(roles or [])))


def final(con, task_id):
    tasks, summary = refine.finalize(con, CFG)
    return next(t for t in tasks if t["id"] == task_id), summary


def test_rules_only_without_ai(tmp_path):
    con, [tid] = setup(tmp_path)
    t, summary = final(con, tid)
    assert t["ai"] is None and t["h"] == t["rules"]["h"] and summary["estimated"] == 0


def test_ai_estimate_replaces_h_and_title(tmp_path):
    con, [tid] = setup(tmp_path)
    rules_h = con.execute("select h from tasks").fetchone()[0]          # 400 doc lines / 40 = 10h
    add_ai(con, tid, h=rules_h * 0.5)
    t, summary = final(con, tid)
    assert t["h"] == pytest.approx(rules_h * 0.5) and t["title"] == "AI title" and summary["estimated"] == 1
    assert t["rules"]["h"] == pytest.approx(rules_h)


@pytest.mark.parametrize("factor,expected", [(10, refine.GUARD_HIGH), (0.01, refine.GUARD_LOW)])
def test_guard_rails(tmp_path, factor, expected):
    con, [tid] = setup(tmp_path)
    rules_h = con.execute("select h from tasks").fetchone()[0]
    add_ai(con, tid, h=rules_h * factor)
    t, _ = final(con, tid)
    assert t["h"] == pytest.approx(rules_h * expected, abs=0.01) and t["ai"]["clamped"]


def test_ai_k_only_when_files_unchecked(tmp_path):
    con, [tid] = setup(tmp_path)
    add_ai(con, tid, h=5, k=0.2)
    t, _ = final(con, tid)
    assert t["k"] == 0.3          # the edited file is gone: the file check wins over the AI's 0.2
    con.execute("update tasks set k_basis = 'default: no edited files to check', k = 0.7")
    t, _ = final(con, tid)
    assert t["k"] == 0.2 and "judged" in t["k_basis"]


def test_other_services_estimates_are_ignored(tmp_path):
    con, [tid] = setup(tmp_path)
    add_ai(con, tid, h=1, service="claude", model="claude-haiku-5-5")
    t, _ = final(con, tid)
    assert t["ai"] is None


def test_correction_wins_and_exclusion_zeroes_net(tmp_path):
    con, [tid] = setup(tmp_path)
    add_ai(con, tid, h=6)
    refine.save_correction(con, tid, h=2.5, k=1.0)
    t, summary = final(con, tid)
    assert (t["h"], t["k"], t["k_basis"]) == (2.5, 1.0, "set by you") and summary["corrected"] == 1
    refine.save_correction(con, tid, excluded=True)
    t, _ = final(con, tid)
    assert t["net"] == 0 and t["excluded"]
    refine.clear_correction(con, tid)
    t, _ = final(con, tid)
    assert t["h"] == 6 and t["correction"] is None


def test_learning_from_three_corrections(tmp_path):
    con, ids = setup(tmp_path, n_tasks=5)
    for tid in ids:
        add_ai(con, tid, h=8)
    for tid in ids[:3]:
        refine.save_correction(con, tid, h=4)          # you halve the estimate three times
    tasks, summary = refine.finalize(con, CFG)
    assert summary["learned"] == {"documentation": 0.5}
    untouched = [t for t in tasks if t["id"] in ids[3:]]
    assert all(t["h"] == 4 and t["learned"] == 0.5 for t in untouched)


def test_json_from_tolerates_fences_and_prose():
    assert ai._json_from('Sure!\n```json\n{"h": 2, "title": "x"}\n```') == {"h": 2, "title": "x"}
    with pytest.raises(ai.AIError):
        ai._json_from("no json here")


def test_status_explains_what_is_missing(monkeypatch):
    monkeypatch.delenv("NOPE_KEY", raising=False)
    assert ai.status("x", {"api": "openai", "model": ""}) == (False, "Set a model in config.toml")
    ok, why = ai.status("x", {"api": "openai", "model": "m", "key_env": "NOPE_KEY"})
    assert not ok and "NOPE_KEY" in why
    assert ai.status("local", {"api": "openai", "model": "m"}) == (True, "Ready")


def test_config_migrates_old_ai_block(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(config, "HOME_DIR", tmp_path)
    old = config.DEFAULT.split("# AI that refines")[0] + (
        '[ai]\nprovider = "cortex"\nmodel = "cortex-fast"\n')
    config.CONFIG_PATH.write_text(old)
    cfg = config.load()
    assert cfg["ai"]["use"] == "cortex" and "provider" not in cfg["ai"]
    assert set(ai.services(cfg)) == {"cortex", "claude", "openai", "gemini", "local"}
    config.set_ai_use("claude")
    assert config.load()["ai"]["use"] == "claude"


def test_purpose_ai_then_correction_and_weights(tmp_path):
    con, [tid] = setup(tmp_path)
    t, _ = final(con, tid)
    assert t["purpose"] == "project" and t["group"] == "p"          # rules: an edited report is project work
    add_ai(con, tid, h=4, k=1.0, purpose="learning")
    t, _ = final(con, tid)
    assert (t["purpose"], t["group"]) == ("learning", "General & learning")
    full = t["net"]
    cfg = dict(CFG, purposes={"learning_weight": 0.5, "admin_weight": 1.0})
    half = next(x for x in refine.finalize(con, cfg)[0] if x["id"] == tid)
    assert half["net"] == pytest.approx(full * 0.5, abs=0.01)
    refine.save_correction(con, tid, purpose="admin")
    t, _ = final(con, tid)
    assert (t["purpose"], t["group"]) == ("admin", "Setup & admin")


def test_ai_reruns_estimates_without_purpose(tmp_path):
    con, [tid] = setup(tmp_path)
    add_ai(con, tid, h=4, purpose=None)
    assert [r["id"] for r in ai.pending(con, "cortex", "cortex-fast", 10)] == [tid]
    add_ai(con, tid, h=4, purpose="learning")
    assert ai.pending(con, "cortex", "cortex-fast", 10) == []
