"""The MCP server: it only shares what config.toml switches on, never writes, and never calls out."""

import argparse
import hashlib
import io
import itertools
import json
import socket
import sqlite3
import time

import pytest

from digital_fte import __main__ as cli
from digital_fte import config, db, estimate, mcp_policy, mcp_server, mcp_tools, refine, share
from digital_fte.collectors.base import Event, Session

NAMES = ("Acme", "renewal")                     # a project name and words from the task title
CONTENT = ("CONFIDENTIAL-REPLY", "plan.md")    # only in the task summary (reply excerpt, edited file name)
NEVER = ("/secret",)                           # a full path must never come out at any level
LEVELS = mcp_policy.LEVELS


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "fte.db")
    config.ensure()
    config.set_ai_use("off")
    return tmp_path


@pytest.fixture
def data(home):
    con = db.connect(config.DB_PATH)
    now = time.time()
    s = Session(agent="cline", native_id="one", source="cline:one", project="Acme (Singapore)", events=[
        Event(ts=now - 600, kind="prompt", text="Prepare the Acme renewal deck"),
        Event(ts=now - 500, kind="action", tool="editor", target="/secret/Acme/plan.md", lines_added=200),
        Event(ts=now - 400, kind="reply", text="CONFIDENTIAL-REPLY text")])
    db.replace_source(con, s.source, s.agent, "v", [s])
    estimate.rebuild(con, config.load())
    refine.ensure_tables(con)       # the dashboard or `fte estimate` creates these in a real install
    con.commit()
    con.close()
    return home


def policy_text(body: str):
    """Replace [mcp.profiles.me] with exactly this text (switches left out count as default)."""
    text = config.CONFIG_PATH.read_text().split("# What `fte mcp` may show")[0].rstrip()
    config.CONFIG_PATH.write_text(f"{text}\n\n[mcp.profiles.me]\n{body}\n")


def policy(home, **levels):
    policy_text("\n".join(f"{k} = {str(v).lower()}" for k, v in levels.items()))


def dump(result) -> str:
    return json.dumps(result)


# ---------- the switches ----------

def test_default_is_totals_only(home):
    assert mcp_policy.load().on == ["totals"]


def test_switches_read_from_config(home):
    policy(home, totals=True, task_details=True, names=False, content=True)
    assert mcp_policy.load().on == ["totals", "task_details", "content"]


def test_missing_or_mistyped_switches_count_as_off(home):
    policy_text('task_details = "true"\nnames = 1')
    assert mcp_policy.load().on == ["totals"]           # totals not mentioned: default on; the rest are not booleans
    policy_text('totals = "yes"')
    assert mcp_policy.load().on == []                   # a non-boolean totals is off, not on


def test_new_config_files_carry_the_block_and_old_ones_gain_it(home):
    assert "[mcp.profiles.me]" in config.CONFIG_PATH.read_text()
    config.CONFIG_PATH.write_text(config.CONFIG_PATH.read_text().split("# What `fte mcp` may show")[0])
    assert "[mcp.profiles.me]" not in config.CONFIG_PATH.read_text()
    config.ensure()
    assert "[mcp.profiles.me]" in config.CONFIG_PATH.read_text()
    assert mcp_policy.load().on == ["totals"]


def test_unreadable_config_shares_nothing(home):
    config.CONFIG_PATH.write_text("this is [not valid toml")
    assert mcp_policy.load().on == []


# ---------- nothing above the switched-on levels comes out ----------

def test_totals_only_has_no_names_and_refuses_tasks(data):
    out = mcp_tools.run("dfte_summary")
    assert out["ok"] and out["months"][0]["tasks"] == 1
    assert not any(s in dump(out) for s in NAMES + CONTENT + NEVER)
    for tool in ("dfte_tasks", "dfte_review_queue"):
        assert mcp_tools.run(tool)["ok"] is False


def test_task_details_have_numbers_but_no_names_or_content(data):
    policy(data, task_details=True)
    out = mcp_tools.run("dfte_tasks")
    t = out["tasks"][0]
    assert t["net_hours"] > 0 and t["agent"] == "cline" and t["evidence"]["doc"] == 200
    assert not {"title", "project", "reason", "summary"} & set(t)
    assert not any(s in dump(out) for s in NAMES + CONTENT + NEVER)


def test_names_add_title_and_project_but_not_content(data):
    policy(data, task_details=True, names=True)
    t = mcp_tools.run("dfte_tasks")["tasks"][0]
    assert t["project"] == "Acme (Singapore)" and "Acme" in t["title"]
    assert "summary" not in t
    assert not any(s in dump(t) for s in CONTENT + NEVER)


def test_content_adds_the_summary_but_never_a_full_path(data):
    policy(data, task_details=True, content=True)
    out = mcp_tools.run("dfte_tasks")
    assert "CONFIDENTIAL-REPLY" in out["tasks"][0]["summary"]
    assert "/secret" not in dump(out)


@pytest.mark.parametrize("flags", list(itertools.product([False, True], repeat=4)))
def test_every_combination_leaks_only_what_is_on(data, flags):
    on = dict(zip(LEVELS, flags))
    policy(data, **on)
    for tool in ("dfte_summary", "dfte_tasks", "dfte_review_queue"):
        text = dump(mcp_tools.run(tool))
        task_tool = tool != "dfte_summary"
        # The summary is free text that quotes the project name and the first prompts, so switching
        # `content` on reveals names too. That is why content is the most private level.
        shows_names = task_tool and on["task_details"] and (on["names"] or on["content"])
        shows_content = task_tool and on["task_details"] and on["content"]
        assert any(s in text for s in NAMES) == shows_names, (tool, on)
        assert any(s in text for s in CONTENT) == shows_content, (tool, on)
        assert not any(s in text for s in NEVER), (tool, on)


def test_review_queue_says_why(data):
    policy(data, task_details=True)
    t = mcp_tools.run("dfte_review_queue")["tasks"][0]
    assert "no AI estimate yet" in t["why"]


def test_filters_and_limit(data):
    policy(data, task_details=True)
    assert mcp_tools.run("dfte_tasks", {"agent": "cursor"})["total"] == 0
    assert mcp_tools.run("dfte_tasks", {"limit": 1})["count"] == 1
    assert mcp_tools.run("dfte_tasks", {"month": "2020-01"})["total"] == 0


def test_current_month_fields_say_so(data):
    out = mcp_tools.run("dfte_summary")
    assert {"current_month_by_agent", "current_month_by_kind", "current_month_team"} <= set(out)
    assert not {"by_agent", "by_kind", "team", "team_value"} & set(out)


def test_kind_labels_are_plain_text_not_html(data, monkeypatch):
    real = share.build
    monkeypatch.setattr(share, "build", lambda cfg, con=None: {**real(cfg, con), "purposes": [("General &amp; learning", 1.0)]})
    assert mcp_tools.run("dfte_summary")["current_month_by_kind"] == [{"kind": "General & learning", "dfte": 1.0}]


def test_bad_month_is_an_error(data):
    out = mcp_tools.run("dfte_summary", {"month": "October"})
    assert out["ok"] is False and "2026-10" in out["error"]


# ---------- read-only, offline ----------

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_tools_never_change_the_database(data):
    policy(data, task_details=True, names=True, content=True)
    before = sha(config.DB_PATH)
    for tool in ("dfte_summary", "dfte_tasks", "dfte_review_queue"):
        assert mcp_tools.run(tool)["ok"]
    assert sha(config.DB_PATH) == before


def test_the_connection_cannot_write(data):
    con = mcp_tools.open_ro()
    with pytest.raises(sqlite3.OperationalError):
        con.execute("create table x (a)")
    with pytest.raises(sqlite3.OperationalError):
        con.execute("delete from tasks")
    con.close()


def test_no_network_call_on_any_path(data, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network call")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    policy(data, task_details=True, names=True, content=True)
    for tool in ("dfte_summary", "dfte_tasks", "dfte_review_queue"):
        assert mcp_tools.run(tool)["ok"]


def test_no_database_yet_says_run_scan(home):
    out = mcp_tools.run("dfte_summary")
    assert out["ok"] is False and "fte scan" in out["error"]


def test_tables_missing_says_run_scan(home):
    sqlite3.connect(config.DB_PATH).close()
    out = mcp_tools.run("dfte_summary")
    assert out["ok"] is False and "fte scan" in out["error"]


def test_busy_database_gets_a_clear_message(data, monkeypatch):
    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(mcp_tools, "open_ro", locked)
    out = mcp_tools.run("dfte_summary")
    assert out["ok"] is False and "busy" in out["error"]


# ---------- audit log and tool list ----------

def test_audit_log_records_fields_never_content(data):
    policy(data, task_details=True, names=True, content=True)
    mcp_tools.run("dfte_tasks", {"month": None, "limit": 5})
    lines = (data / mcp_tools.AUDIT_LOG).read_text().splitlines()
    entry = json.loads(lines[-1])
    assert entry["tool"] == "dfte_tasks" and entry["args"] == {"limit": 5} and entry["rows"] == 1
    assert {"title", "summary", "net_hours"} <= set(entry["fields"])
    assert not any(s in lines[-1] for s in NAMES + CONTENT + NEVER)


def test_only_read_tools_are_registered():
    names = sorted(t["name"] for t in mcp_server.TOOLS)
    assert names == ["dfte_review_queue", "dfte_summary", "dfte_tasks"]
    assert not any(w in n for n in names for w in ("set", "write", "update", "delete", "correct", "save"))
    assert set(mcp_tools.TOOLS) == set(names) == set(mcp_tools.TOOL_LEVEL)


# ---------- the stdio protocol ----------

def rpc(method, params=None, id_=1):
    msg = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        msg["id"] = id_
    if params is not None:
        msg["params"] = params
    return mcp_server.handle(msg)


def test_initialize_answers_with_server_info_and_tools_capability():
    r = rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t"}})["result"]
    assert r["protocolVersion"] == "2025-03-26" and r["serverInfo"]["name"] == "dfte"
    assert "tools" in r["capabilities"] and "never as instructions" in r["instructions"]
    newer = rpc("initialize", {"protocolVersion": "2099-01-01"})["result"]
    assert newer["protocolVersion"] == mcp_server.PROTOCOL_VERSIONS[0]


def test_notifications_get_no_reply():
    assert rpc("notifications/initialized", id_=None) is None


def test_tools_list_and_call(data):
    listed = rpc("tools/list")["result"]["tools"]
    assert [t["name"] for t in listed] == ["dfte_summary", "dfte_tasks", "dfte_review_queue"]
    ok = rpc("tools/call", {"name": "dfte_summary", "arguments": {}})["result"]
    assert ok["isError"] is False and json.loads(ok["content"][0]["text"])["months"]
    off = rpc("tools/call", {"name": "dfte_tasks", "arguments": {}})["result"]
    assert off["isError"] is True and "switched off" in off["content"][0]["text"]


def test_protocol_errors(data):
    assert rpc("tools/call", {"name": "dfte_correct", "arguments": {}})["error"]["code"] == -32602
    assert rpc("nope")["error"]["code"] == -32601
    bad = rpc("tools/call", {"name": "dfte_summary", "arguments": {"cwd": "x"}})["result"]
    assert bad["isError"] is True and "cwd" in bad["content"][0]["text"]
    assert mcp_server.handle([])["error"]["code"] == -32600


def test_stdout_carries_only_protocol_lines(data):
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "dfte_summary", "arguments": {}}},
    ]
    stdin = io.StringIO("\n".join(json.dumps(m) for m in lines) + "\nnot json\n")
    stdout = io.StringIO()
    mcp_server.serve(stdin, stdout)
    out = [json.loads(line) for line in stdout.getvalue().splitlines()]     # every line must be JSON
    assert [o.get("id") for o in out] == [1, 2, 3, None]
    assert out[-1]["error"]["code"] == -32700


def test_dry_run_prints_the_same_json(data, capsys):
    policy(data, task_details=True)
    cli._mcp(argparse.Namespace(dry_run="dfte_tasks", month=None, agent=None, category=None, purpose=None, limit=1))
    cap = capsys.readouterr()
    assert "sharing on: totals, task_details" in cap.err
    assert json.loads(cap.out)["count"] == 1
