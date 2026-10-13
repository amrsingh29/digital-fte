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
