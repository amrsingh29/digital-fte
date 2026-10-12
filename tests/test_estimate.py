"""Task splitting and the rules-based H, K, Y."""

import json
import subprocess
import tomllib

import pytest

from digital_fte import config, db, estimate
from digital_fte.collectors.base import HOME, Event, Session

CFG = tomllib.loads(config.DEFAULT)
MIN = 60


def store(tmp_path, *sessions):
    con = db.connect(tmp_path / "t.db")
    for s in sessions:
        db.replace_source(con, s.source, s.agent, "v", [s])
    estimate.rebuild(con, CFG)
    return con, [dict(r) | {"evidence": json.loads(r["evidence"])} for r in con.execute("select * from tasks order by started")]


def sess(native_id, events, parent=None, cwd=None, agent="claude_code"):
    return Session(agent=agent, native_id=native_id, source=f"{agent}:{native_id}", project="p",
                   cwd=cwd, parent_native_id=parent, events=events)


def work(t0, n_actions=3, words=100):
    """An agent turn: a reply plus some reads."""
    return [Event(ts=t0 + 5, kind="reply", text="word " * words)] + \
           [Event(ts=t0 + 10 + i, kind="action", tool="Read", target=f"/x/{i}") for i in range(n_actions)]


def test_break_starts_new_task(tmp_path):
    events = [Event(ts=0, kind="prompt", text="first job"), *work(0),
              Event(ts=45 * MIN, kind="prompt", text="second job"), *work(45 * MIN)]
    _, tasks = store(tmp_path, sess("s", events))
    assert [t["title"] for t in tasks] == ["first job", "second job"]


def test_short_follow_up_stays_in_task(tmp_path):
    events = [Event(ts=0, kind="prompt", text="job"), *work(0),
              Event(ts=5 * MIN, kind="prompt", text="and also this"), *work(5 * MIN)]
    _, tasks = store(tmp_path, sess("s", events))
    assert len(tasks) == 1 and tasks[0]["n_prompts"] == 2


def test_tiny_task_merges_into_neighbour(tmp_path):
    events = [Event(ts=0, kind="prompt", text="big job"), *work(0, n_actions=5),
              Event(ts=60 * MIN, kind="prompt", text="thanks!"), Event(ts=60 * MIN + 5, kind="reply", text="You're welcome")]
    _, tasks = store(tmp_path, sess("s", events))
    assert len(tasks) == 1 and tasks[0]["title"] == "big job"


def test_subagent_work_joins_parent_task(tmp_path):
    parent = sess("p", [Event(ts=0, kind="prompt", text="research this"),
                        Event(ts=10, kind="action", tool="Task"), *work(20)])
    child = sess("c", [Event(ts=11, kind="instruction", text="look into X"), *work(12, n_actions=10)], parent="p")
    _, tasks = store(tmp_path, parent, child)
    assert len(tasks) == 1
    t = tasks[0]
    assert t["n_prompts"] == 1                # the parent's instruction is not your prompt
    assert t["evidence"]["reads"] == 13       # subagent reads counted in the parent's task


def test_h_from_evidence(tmp_path):
    events = [Event(ts=0, kind="prompt", text="build it"),
              Event(ts=10, kind="action", tool="Write", target="/tmp/x/app.py", lines_added=100),
              Event(ts=20, kind="action", tool="Write", target="/tmp/x/README.md", lines_added=40),
              Event(ts=30, kind="action", tool="Write", target="/tmp/x/data.json", lines_added=5000),   # capped
              Event(ts=40, kind="action", tool="Bash", target="pytest"),
              Event(ts=50, kind="reply", text="word " * 800)]
    _, [t] = store(tmp_path, sess("s", events))
    r = CFG["rates"]
    assert t["evidence"]["code"] == 100 + r["max_lines_per_edit"] and t["evidence"]["doc"] == 40
    expected = (400 / r["code_lines_per_hour"] + 40 / r["doc_lines_per_hour"] + 800 / r["answer_words_per_hour"]
                + r["command_minutes"] / 60)
    assert t["h"] == pytest.approx(expected, abs=0.01)
    assert t["h_low"] == pytest.approx(t["h"] * r["range_low"], abs=0.01)
    assert t["category"] == "code" and t["role"] == "developer"


def test_y_counts_typing_review_and_reading(tmp_path):
    events = [Event(ts=0, kind="prompt", text="w " * 40),               # 1 min typing
              *work(0),
              Event(ts=29 * 60, kind="prompt", text="w " * 40),       # 29 min later: review capped at 10, +1 min typing
              Event(ts=29 * 60 + 10, kind="reply", text="w " * 250)]  # 1 min reading the final answer
    _, [t] = store(tmp_path, sess("s", events))
    assert t["y"] == pytest.approx((1 + 10 + 1 + 1) / 60, abs=0.01)


def test_keep_factor(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "kept.py").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "kept.py"], check=True)
    (repo / "loose.py").write_text("x")
    k, basis = estimate.keep_factor([str(repo / "kept.py"), str(repo / "loose.py"), str(repo / "gone.py")], 0.7)
    assert k == pytest.approx((1.0 + 0.9 + 0.3) / 3, abs=0.01)
    assert "1 in git" in basis and "1 gone" in basis
    assert estimate.keep_factor([], 0.7) == (0.7, "default: no edited files to check")


def test_session_level_lines_are_shared_by_edits(tmp_path):
    s = sess("c", [Event(ts=0, kind="prompt", text="go"),
                   Event(ts=5, kind="action", tool="edit_file_v2", target="a.ts"),
                   Event(ts=6, kind="action", tool="edit_file_v2", target="b.ts"), *work(7)], agent="cursor")
    s.lines_added = 120
    _, [t] = store(tmp_path, s)
    assert t["evidence"]["code"] == 120


@pytest.mark.parametrize("tool,cls", [
    ("Edit", "edit"), ("edit_file_v2", "edit"), ("editor", "edit"), ("Bash", "command"), ("run_commands", "command"),
    ("Read", "read"), ("ripgrep_raw_search", "read"), ("WebSearch", "web"), ("websearch", "web"),
    ("mcp__Claude_Browser__computer", "command"), ("mcp__Claude_Browser__get_page_text", "web"), ("TodoWrite", "other"),
])
def test_action_classes(tool, cls):
    assert estimate.action_class(tool) == cls


@pytest.mark.parametrize("ev,files,project,text,expected", [
    (dict(edits=0, commands=0, web=2, reads=0, words=400), [], "(no project)", "how do vector databases work?", "learning"),
    (dict(edits=0, commands=0, web=0, reads=0, words=300), [], "(no project)", "explain RAG chunking", "learning"),
    (dict(edits=0, commands=0, web=0, reads=12, words=300), [], "billing-api", "what does this module do?", "project"),
    (dict(edits=2, commands=1, web=0, reads=1, words=50), [HOME + "/.zshrc"], "zshrc config", "fix my prompt", "admin"),
    (dict(edits=0, commands=4, web=0, reads=0, words=80), [], "(no project)", "install and configure the mcp server", "admin"),
    (dict(edits=5, commands=2, web=0, reads=3, words=90), ["/repo/app.py"], "app", "add login", "project"),
])
def test_purpose_rules(ev, files, project, text, expected):
    assert estimate.purpose_of(ev, files, project, text) == expected
