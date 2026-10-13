"""Issue #3: roles from the catalog ("virtual team"): AI first, category/purpose fallback."""

import json
import tomllib

from digital_fte import ai, config, refine

from test_refine import CFG, add_ai, final, setup

NAMES = [r["name"] for r in CFG["roles"]]


def test_default_catalog():
    roles = config.roles(CFG["roles"], 9000)
    assert len(roles) == 10 and all(r["monthly_cost"] == 9000 for r in roles)
    assert {"Full-stack developer", "Presales consultant", "Researcher / advisor"} <= {r["name"] for r in roles}


def test_catalog_cleanup():
    raw = [{"name": "Dev", "monthly_cost": 5000}, {"name": "dev"}, {"name": " "}, "junk", {"name": "Writer"}]
    roles = config.roles(raw, 9000)
    assert [(r["name"], r["monthly_cost"], r["fallback"]) for r in roles] == [("Dev", 5000, []), ("Writer", 9000, [])]


def test_clean_roles():
    assert ai.clean_roles([{"role": "presales consultant", "share": 3}, {"role": "Full-stack developer", "share": 1}], NAMES) \
        == [{"role": "Presales consultant", "share": 0.75}, {"role": "Full-stack developer", "share": 0.25}]
    # unknown roles dropped, at most two kept, shares renormalised
    got = ai.clean_roles([{"role": "Wizard", "share": 0.5}, {"role": "UX designer", "share": 0.2},
                          {"role": "Business analyst", "share": 0.3}, {"role": "Technical writer", "share": 0.1}], NAMES)
    assert got == [{"role": "Business analyst", "share": 0.6}, {"role": "UX designer", "share": 0.4}]
    assert ai.clean_roles([{"role": "Wizard", "share": 1}], NAMES) is None
    assert ai.clean_roles("nonsense", NAMES) is None


def test_prompt_lists_catalog_and_limits_choices():
    system, schema = ai.prompt(config.roles(CFG["roles"], 9000))
    assert "- Presales consultant: prepares customer demos" in system
    assert schema["properties"]["roles"]["items"]["properties"]["role"]["enum"] == NAMES
    assert "enum" not in ai.SCHEMA["properties"]["roles"]["items"]["properties"]["role"]   # template untouched


def test_fallback_by_category_and_purpose():
    roles = config.roles(CFG["roles"], 9000)
    assert refine.fallback_role(roles, "code", "project") == "Full-stack developer"
    assert refine.fallback_role(roles, "demo prep", "project") == "Presales consultant"
    assert refine.fallback_role(roles, "code", "admin") == "DevOps / IT admin"
    assert refine.fallback_role(roles, "code", "learning") == "Researcher / advisor"
    assert refine.fallback_role([{"name": "Only", "fallback": []}], "code", "project") == "Only"
    assert refine.fallback_role([], "code", "project") is None


def test_task_roles_ai_first_then_fallback(tmp_path):
    con, [tid] = setup(tmp_path)
    t, _ = final(con, tid)                       # no AI: a documentation task → presales consultant
    assert t["roles"] == [{"role": "Presales consultant", "share": 1.0}] and t["roles_basis"] == "rules"
    add_ai(con, tid, h=4, roles=[{"role": "Solution architect", "share": 0.7}, {"role": "Technical writer", "share": 0.3}])
    t, _ = final(con, tid)
    assert t["roles_basis"] == "ai" and t["role"] == "Solution architect" and t["roles"][1]["share"] == 0.3


def test_role_removed_from_catalog_falls_back(tmp_path):
    con, [tid] = setup(tmp_path)
    add_ai(con, tid, h=4, roles=[{"role": "Retired role", "share": 1}])
    t, _ = final(con, tid)
    assert t["roles_basis"] == "rules" and t["role"] == "Presales consultant"


def test_ai_reruns_estimates_without_roles(tmp_path):
    con, [tid] = setup(tmp_path)
    add_ai(con, tid, h=4)
    con.execute("update ai_estimates set roles = null")
    assert [r["id"] for r in ai.pending(con, "cortex", "cortex-fast", 10)] == [tid]
    con.execute("update ai_estimates set roles = ?", (json.dumps([]),))   # answered, but no usable role
    assert ai.pending(con, "cortex", "cortex-fast", 10) == []


def test_config_gains_roles(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text("[dfte]\nhours_per_fte_month = 140\n")
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "HOME_DIR", tmp_path)
    cfg = config.load()
    assert len(cfg["roles"]) == 10 and len(tomllib.loads(path.read_text())["roles"]) == 10
    config.load()                                # appended once only
    assert path.read_text().count("[[roles]]") == 10


# ---------- step 2: your role corrections, and learning from them ----------

def test_your_roles_win(tmp_path):
    con, [tid] = setup(tmp_path)
    add_ai(con, tid, h=4, roles=[{"role": "Technical writer", "share": 1}])
    refine.save_correction(con, tid, roles=[{"role": "Presales consultant", "share": 0.7},
                                           {"role": "Full-stack developer", "share": 0.3}])
    t, _ = final(con, tid)
    assert t["roles_basis"] == "you" and t["role"] == "Presales consultant" and t["correction"]["roles"] == t["roles"]
    refine.clear_correction(con, tid)
    assert final(con, tid)[0]["roles_basis"] == "ai"


def _cfg(**ai_overrides):
    return dict(CFG, roles=config.roles(CFG["roles"], 9000), ai=dict(CFG["ai"], **ai_overrides))


def test_examples_from_corrections(tmp_path):
    con, ids = setup(tmp_path, n_tasks=3)
    add_ai(con, ids[0], h=4, title="Demo dashboard for customer meeting")
    refine.save_correction(con, ids[0], purpose="project", category="demo prep",
                           roles=[{"role": "Presales consultant", "share": 0.7}, {"role": "Full-stack developer", "share": 0.3}])
    refine.save_correction(con, ids[1], h=12)                      # hours only: not an example
    refine.save_correction(con, ids[2], purpose="learning", excluded=True)   # not real work: not an example
    got = ai.examples(con, _cfg())
    assert got == [(ids[0], '"Demo dashboard for customer meeting" → project work · demo prep · '
                            '70% Presales consultant, 30% Full-stack developer')]
    assert ai.examples(con, _cfg(learn_from=["roles"]))[0][1].endswith('→ 70% Presales consultant, 30% Full-stack developer')
    assert ai.examples(con, _cfg(learn_from=[])) == []


def test_examples_spread_across_purposes(tmp_path):
    con, ids = setup(tmp_path, n_tasks=14)
    for i, tid in enumerate(ids):                 # 12 project corrections, then 2 learning ones (newest)
        refine.save_correction(con, tid, purpose="learning" if i >= 12 else "project", category="research")
    got = [x for _, x in ai.examples(con, _cfg())]
    assert len(got) == ai.N_EXAMPLES + 1 and sum("general & learning" in x for x in got) == 2


def test_prompt_includes_examples_but_not_hours():
    system, _ = ai.prompt(config.roles(CFG["roles"], 9000), ['"Sizing sheet" → presales consultant'])
    assert system.endswith('- "Sizing sheet" → presales consultant') and "judge the hours on their own" in system
    assert "corrected" not in ai.prompt(config.roles(CFG["roles"], 9000))[0]


def test_api_checks_roles_against_catalog(tmp_path, monkeypatch):
    import pytest
    from fastapi import HTTPException
    from digital_fte import app as app_mod
    monkeypatch.setattr(config, "HOME_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    setup(tmp_path)

    def post(roles):
        return app_mod.correct("x@1", app_mod.Correction(roles=roles))
    with pytest.raises(HTTPException):
        post([{"role": "Wizard", "share": 1}])
    with pytest.raises(HTTPException):
        post([{"role": "UX designer", "share": 0.5}] * 3)
    assert post([{"role": "UX designer", "share": 0.5}, {"role": "Solution architect", "share": 0.5}]) == {"ok": True}


def test_existing_ai_section_gains_learn_from(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('[ai]\nuse = "off"\nmax_tasks_per_run = 10\n\n[ai.local]\napi = "openai"\n')
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "HOME_DIR", tmp_path)
    cfg = config.load()
    assert cfg["ai"]["learn_from"] == ["roles", "purpose", "category"] and cfg["ai"]["use"] == "off"
    assert path.read_text().count("learn_from") == 1


def test_redo_before_keeps_old_estimates_until_replaced(tmp_path):
    import time
    con, [tid] = setup(tmp_path)
    add_ai(con, tid, h=4, roles=[{"role": "UX designer", "share": 1}])
    assert ai.pending(con, "cortex", "cortex-fast", 10) == []
    assert [r["id"] for r in ai.pending(con, "cortex", "cortex-fast", 10, redo_before=time.time() + 1)] == [tid]
    assert final(con, tid)[0]["role"] == "UX designer"     # still shown while waiting
