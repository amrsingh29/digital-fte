"""Each reader against a tiny hand-made history in that agent's format.
If an agent update changes its format, the matching test should fail."""

import json
import sqlite3

from digital_fte import config, db
from digital_fte.collectors import COLLECTORS
from digital_fte.collectors.base import HOME as H, NO_PROJECT, infer_project, project_of


def load_all(agent, path):
    return [s for src in COLLECTORS[agent].sources(path) for s in src.load()]


def test_claude_code(tmp_path):
    proj = tmp_path / "-Users-me-app"
    proj.mkdir()
    lines = [
        {"type": "user", "timestamp": "2026-10-01T10:00:00Z", "cwd": H + "/Developer/app",
         "message": {"role": "user", "content": "Add a login page"}},
        {"type": "user", "isMeta": True, "timestamp": "2026-10-01T10:00:01Z", "message": {"content": "meta"}},
        # one API response split over two lines that repeat the same usage
        {"type": "assistant", "timestamp": "2026-10-01T10:00:05Z", "message": {
            "id": "m1", "model": "claude-x", "usage": {"input_tokens": 10, "output_tokens": 50, "cache_read_input_tokens": 1000},
            "content": [{"type": "text", "text": "Sure, writing it now."}]}},
        {"type": "assistant", "timestamp": "2026-10-01T10:00:06Z", "message": {
            "id": "m1", "model": "claude-x", "usage": {"input_tokens": 10, "output_tokens": 50, "cache_read_input_tokens": 1000},
            "content": [{"type": "tool_use", "name": "Edit", "input": {
                "file_path": H + "/Developer/app/login.py", "old_string": "a", "new_string": "a\nb\nc"}}]}},
        {"type": "user", "timestamp": "2026-10-01T10:00:07Z", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
        {"type": "user", "timestamp": "2026-10-01T10:01:00Z", "message": {"content": "<local-command-stdout>x</local-command-stdout>"}},
        {"type": "custom-title", "customTitle": "Login page"},
    ]
    (proj / "s1.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
    sub = proj / "s1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a.jsonl").write_text(json.dumps(lines[0]))

    sessions = {s.native_id: s for s in load_all("claude_code", tmp_path)}
    s = sessions["s1"]
    kinds = [e.kind for e in s.events]
    assert kinds == ["prompt", "reply", "action"]
    assert s.title == "Login page" and s.model == "claude-x" and s.project == "app"
    assert sum(e.tokens_out for e in s.events) == 50          # usage counted once
    act = s.events[2]
    assert (act.tool, act.lines_added, act.lines_removed) == ("Edit", 3, 1)
    assert sessions["agent-a"].parent_native_id == "s1"


def test_opencode(tmp_path):
    path = tmp_path / "opencode.db"
    con = sqlite3.connect(path)
    con.execute("create table session_v2 (id, directory, title, parent_id, model, time_updated)")
    con.execute("create table session_message (session_id, type, seq, data)")
    con.execute("insert into session_v2 values ('ses1',?,'Fix bug',null,null,5)", (H + "/Developer/app",))
    msgs = [
        ("user", {"time": {"created": 1_790_000_000_000}, "text": "fix the bug"}),
        ("assistant", {"time": {"created": 1_790_000_005_000}, "model": {"id": "cortex-fast"},
                       "tokens": {"input": 100, "output": 20, "reasoning": 5, "cache": {"read": 7}},
                       "content": [{"type": "reasoning", "text": "hmm"}, {"type": "text", "text": "Done"},
                                   {"type": "tool", "name": "edit", "state": {"input": {
                                       "path": H + "/Developer/app/x.py", "oldString": "a", "newString": "a\nb"}}}]}),
        ("idle", {"time": {"created": 1_790_000_006_000}}),
    ]
    for i, (t, d) in enumerate(msgs):
        con.execute("insert into session_message values ('ses1',?,?,?)", (t, i, json.dumps(d)))
    con.commit()
    con.close()

    [s] = load_all("opencode", path)
    assert [e.kind for e in s.events] == ["prompt", "reply", "action"]
    assert s.model == "cortex-fast" and s.title == "Fix bug" and s.project == "app"
    assert s.events[1].tokens_out == 25 and s.events[2].lines_added == 2


def test_cursor(tmp_path):
    path = tmp_path / "state.vscdb"
    con = sqlite3.connect(path)
    con.execute("create table cursorDiskKV (key text unique on conflict replace, value blob)")
    composer = {"composerId": "c1", "name": "Wizard", "createdAt": 1_790_000_000_000, "lastUpdatedAt": 9,
                "totalLinesAdded": 40, "totalLinesRemoved": 2, "modelConfig": {"modelName": "default"},
                "trackedGitRepos": [{"repoPath": H + "/Developer/wizard"}], "subagentComposerIds": [],
                "fullConversationHeadersOnly": [{"bubbleId": "b1", "type": 1}, {"bubbleId": "b2", "type": 2},
                                                {"bubbleId": "b3", "type": 2}]}
    bubbles = {
        "b1": {"type": 1, "text": "build the wizard", "createdAt": "2026-10-01T10:00:00Z"},
        "b2": {"type": 2, "createdAt": "2026-10-01T10:00:02Z",
               "toolFormerData": {"name": "edit_file_v2", "params": json.dumps({"relativeWorkspacePath": "src/w.ts"})}},
        "b3": {"type": 2, "text": "All done", "createdAt": "2026-10-01T10:00:09Z"},
    }
    con.execute("insert into cursorDiskKV values (?,?)", ("composerData:c1", json.dumps(composer)))
    con.execute("insert into cursorDiskKV values (?,?)", ("composerData:empty-state-draft", "{}"))
    for bid, b in bubbles.items():
        con.execute("insert into cursorDiskKV values (?,?)", (f"bubbleId:c1:{bid}", json.dumps(b)))
    con.commit()
    con.close()

    [s] = load_all("cursor", path)
    assert [e.kind for e in s.events] == ["prompt", "action", "reply"]
    assert s.title == "Wizard" and s.project == "wizard" and s.lines_added == 40
    assert s.events[1].target == "src/w.ts"


def test_cline(tmp_path):
    folder = tmp_path / "123_abc"
    folder.mkdir()
    (folder / "123_abc.json").write_text(json.dumps({"cwd": H + "/Developer/cmdb", "model": "cortex-fast"}))
    (folder / "123_abc.messages.json").write_text(json.dumps({"messages": [
        {"role": "user", "ts": 1_790_000_000_000, "content": [{"type": "text", "text": '<user_input mode="act">Look up a CI</user_input>'}]},
        {"role": "assistant", "ts": 1_790_000_003_000, "metrics": {"inputTokens": 50, "outputTokens": 9},
         "content": [{"type": "thinking", "thinking": "…"},
                     {"type": "tool_use", "name": "editor", "input": {"path": H + "/Developer/cmdb/p.py", "new_text": "x\ny"}}]},
        {"role": "user", "ts": 1_790_000_004_000, "content": [{"type": "tool_result", "content": "ok"}]},
    ]}))
    [s] = load_all("cline", tmp_path)
    assert [e.kind for e in s.events] == ["prompt", "action"]
    assert s.events[0].text == "Look up a CI" and s.events[1].lines_added == 2
    assert s.project == "cmdb" and s.title == "Look up a CI"


def test_omp(tmp_path):
    folder = tmp_path / "-Users-me-Developer-app"
    folder.mkdir()
    head = [
        {"type": "session", "version": 3, "id": "s1", "timestamp": "2026-10-01T10:00:00Z", "cwd": H + "/Developer/app"},
        {"type": "model_change", "model": "cortex/cortex-max"},
        {"type": "message", "timestamp": "2026-10-01T10:00:01Z", "message": {
            "role": "user", "content": [{"type": "text", "text": "Add a login page"}]}},
        # one response with a text block and an edit; usage repeated on a second line of the same response
        {"type": "message", "message": {"role": "assistant", "timestamp": 1790000005000, "model": "cortex-max",
            "responseId": "r1", "usage": {"input": 10, "output": 50, "cacheRead": 1000},
            "content": [{"type": "text", "text": "Sure, writing it now."}]}},
        {"type": "message", "message": {"role": "assistant", "timestamp": 1790000006000,
            "responseId": "r1", "usage": {"input": 10, "output": 50, "cacheRead": 1000},
            "content": [{"type": "toolCall", "name": "edit", "arguments": {
                "i": "Adding form", "input": "[" + H + "/Developer/app/login.py#AC07]\nPUT 3.=3:\n+a\n+b\n-c"}}]}},
        {"type": "message", "message": {"role": "assistant", "timestamp": 1790000007000,
            "content": [{"type": "toolCall", "name": "write", "arguments": {"path": "xd://mcp__eca_about", "content": "{}"}}]}},
        {"type": "message", "message": {"role": "assistant", "timestamp": 1790000008000,
            "content": [{"type": "toolCall", "name": "bash", "arguments": {"command": "ls"}}]}},
        {"type": "title_change", "title": "Login page"},
    ]
    (folder / "s1.jsonl").write_text("\n".join(json.dumps(x) for x in head))
    (folder / ".s1.jsonl.lock.os").write_text("lock")

    sessions = {s.native_id: s for s in load_all("omp", tmp_path)}
    assert set(sessions) == {"s1"}                        # the dot-file lock is skipped
    s = sessions["s1"]
    assert [e.kind for e in s.events] == ["prompt", "reply", "action", "action", "action"]
    assert s.title == "Login page" and s.model == "cortex-max" and s.project == "app"
    assert sum(e.tokens_out for e in s.events) == 50      # usage counted once per response
    edit = s.events[2]
    assert (edit.tool, edit.lines_added, edit.lines_removed) == ("edit", 2, 1)
    assert edit.target == H + "/Developer/app/login.py"
    assert s.events[3].target is None                     # virtual xd:// path is not a file


def test_hermes(tmp_path):
    path = tmp_path / "state.db"
    con = sqlite3.connect(path)
    con.execute("create table sessions (id, parent_session_id, cwd, title, model, input_tokens, output_tokens, "
                "cache_read_tokens, transcript_revision, message_count, last_activity_at)")
    con.execute("create table messages (id integer primary key, session_id, role, content, tool_calls, timestamp, active)")
    con.execute("insert into sessions values ('h1', null, ?, 'Fix login', 'cortex-max', 100, 30, 900, 0, 5, 1790000020)",
                (H + "/Developer/app",))
    calls = [{"id": "c1", "function": {"name": "patch", "arguments": json.dumps(
                {"path": H + "/Developer/app/login.py", "old_string": "a", "new_string": "a\nb\nc"})}}]
    wrapped = [{"id": "c2", "function": {"name": "tool_call", "arguments": json.dumps(
                {"calls": [{"name": "terminal", "arguments": {"command": "pytest"}}]})}}]
    rows = [
        (1, "h1", "user", "Fix the login page", None, 1790000000, 1),
        (2, "h1", "assistant", "Looking at it.", json.dumps(calls), 1790000005, 1),
        (3, "h1", "tool", "ok", None, 1790000006, 1),
        (4, "h1", "assistant", None, json.dumps(wrapped), 1790000010, 1),
        (5, "h1", "assistant", "Done.", None, 1790000020, 1),
        (6, "h1", "user", "old edit", None, 1790000030, 0),   # inactive rows are skipped
    ]
    con.executemany("insert into messages values (?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()

    sessions = {s.native_id: s for s in load_all("hermes", path)}
    s = sessions["h1"]
    assert [e.kind for e in s.events] == ["prompt", "reply", "action", "action", "reply"]
    assert s.model == "cortex-max" and s.project == "app" and s.title == "Fix login"
    assert (sum(e.tokens_in for e in s.events), sum(e.tokens_out for e in s.events)) == (100, 30)
    assert sum(e.cache_read for e in s.events) == 900     # totals split across the 3 assistant turns
    patch = s.events[2]
    assert (patch.tool, patch.lines_added, patch.lines_removed) == ("patch", 3, 1)
    assert patch.target == H + "/Developer/app/login.py"
    assert s.events[3].tool == "terminal" and s.events[3].target == "pytest"


def test_project_names():
    od = H + "/Library/CloudStorage/OneDrive-Company/MyFolder/WorkSpace/Project"
    assert project_of(od + "/Singapore/Acme/deck.pptx") == "Acme (Singapore)"
    assert project_of("/private/var/folders/x/y") is None
    assert infer_project("/tmp/claude-1/scratchpad", [], []) == NO_PROJECT


def test_toggle_keeps_comments(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(config, "HOME_DIR", tmp_path)
    config.ensure()
    config.set_agent_enabled("cursor", False)
    text = config.CONFIG_PATH.read_text()
    assert "# digital-fte settings" in text
    cfg = config.load()
    assert cfg["agents"]["cursor"]["enabled"] is False and cfg["agents"]["cline"]["enabled"] is True


def test_db_roundtrip(tmp_path):
    from digital_fte.collectors.base import Event, Session
    con = db.connect(tmp_path / "t.db")
    s = Session(agent="cline", native_id="x", source="cline:x", project="p",
                events=[Event(ts=0, kind="prompt", text="hi"), Event(ts=60, kind="action", tool="t", lines_added=3),
                        Event(ts=60 + 3600, kind="reply", text="one two three")])
    db.replace_source(con, "cline:x", "cline", "v1", [s])
    r = con.execute("select * from sessions").fetchone()
    assert (r["n_prompts"], r["n_actions"], r["lines_added"], r["words_out"]) == (1, 1, 3, 3)
    assert r["active_min"] == 11  # 1 min + an hour-long gap capped at 10
    # The agent deleted its file: the source is forgotten, the session is kept.
    assert db.forget_missing(con, "cline", set()) == 1
    assert con.execute("select count(*) from sessions").fetchone()[0] == 1
    assert con.execute("select count(*) from sources").fetchone()[0] == 0
