"""Phase 4: an AI reviews each task's short summary and re-estimates it.

Two connectors cover every service in config.toml:
  - api = "openai":    OpenAI-compatible chat completions (Cortex, OpenAI, Gemini, Ollama…), plain HTTP
  - api = "anthropic": Claude through the official `anthropic` SDK (optional extra: pip install -e ".[claude]")

Keys come from the macOS Keychain or an environment variable, never from the config file.
Results are stored per task and per service/model, so each task is sent once.
"""

from __future__ import annotations

import json
import os
import random
import re
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

CATEGORIES = ["code", "documentation", "demo prep", "research", "analysis"]
PURPOSES = ["project", "learning", "admin"]

SYSTEM = """You review one piece of work an AI coding agent did for a person, and estimate how long
a competent professional would have needed to produce the same result WITHOUT AI help.

Pick the professional by the kind of work: a mid-level developer for code, a presales consultant
for documents, decks and demo preparation, an analyst for research and analysis.

Judge the difficulty of the outcome, not the volume of text. AI writes verbose code and prose:
generated boilerplate, sample data, repeated config and long explanations take a person far less
time than their line count suggests, while a hard bug fix or a tricky design decision can take
hours with very few lines changed. Include the time to understand the problem, look things up,
write, test and polish. Do not include waiting or meetings.

Also judge the keep factor k (0 to 1): how much of the output the person likely used. Use 1 when
the conversation shows they built on it, lower when they abandoned, redid or questioned it.

Set `purpose`:
- "project": the work produced or advanced something for a specific project, customer, product or deliverable.
- "learning": the person was asking to understand a topic, concept, tool or idea for themselves, the way they
  would ask a senior colleague or a teacher. For these, h is the time they would have spent reading,
  searching and asking around to reach the same understanding without AI.
- "admin": setting up, configuring or fixing their own tools, machine or accounts.

Return only JSON matching the schema. `title` is a short plain name for the work (max 70
characters). `reason` is one sentence on what drove the estimate."""

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "category": {"type": "string", "enum": CATEGORIES},
        "purpose": {"type": "string", "enum": PURPOSES},
        "h_low": {"type": "number"},
        "h": {"type": "number"},
        "h_high": {"type": "number"},
        "k": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["title", "category", "purpose", "h_low", "h", "h_high", "k", "reason"],
    "additionalProperties": False,
}

TABLE = """
create table if not exists ai_estimates (
  task_id text not null, service text not null, model text not null, n_events int,
  title text, category text, h_low real, h real, h_high real, k real, reason text,
  error text, created_at real, purpose text,
  primary key (task_id, service, model)
);
"""


def ensure_tables(con: sqlite3.Connection) -> None:
    con.executescript(TABLE)
    cols = {r[1] for r in con.execute("pragma table_info(ai_estimates)")}
    if "purpose" not in cols:   # added after the first release of this table
        con.execute("alter table ai_estimates add column purpose text")


class AIError(Exception):
    pass


# ---------- services and keys ----------

_key_cache: dict[tuple, tuple[float, str | None]] = {}


def get_key(svc: dict) -> str | None:
    """Keychain first (cached for a minute), then the environment variable."""
    if svc.get("key_keychain"):
        ck = ("kc", svc["key_keychain"])
        hit = _key_cache.get(ck)
        if hit and time.time() - hit[0] < 60:
            return hit[1]
        try:
            out = subprocess.run(["security", "find-generic-password", "-s", svc["key_keychain"], "-w"],
                                 capture_output=True, text=True, timeout=10)
            key = out.stdout.strip() or None if out.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            key = None
        _key_cache[ck] = (time.time(), key)
        if key:
            return key
    if svc.get("key_env"):
        return os.environ.get(svc["key_env"]) or None
    return None


def services(cfg: dict) -> dict[str, dict]:
    return {k: v for k, v in cfg["ai"].items() if isinstance(v, dict)}


def status(name: str, svc: dict) -> tuple[bool, str]:
    """Is this service usable right now, and if not, why."""
    if not svc.get("model"):
        return False, "Set a model in config.toml"
    if svc.get("api") == "anthropic":
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, 'Install the Claude extra: pip install -e ".[claude]"'
    needs_key = bool(svc.get("key_keychain") or svc.get("key_env"))
    if needs_key and not get_key(svc):
        where = f"Keychain entry '{svc['key_keychain']}'" if svc.get("key_keychain") else f"${svc.get('key_env')}"
        return False, f"No key found in {where}"
    return True, "Ready"


def active(cfg: dict) -> tuple[str, dict] | None:
    name = cfg["ai"].get("use", "off")
    svc = services(cfg).get(name)
    return (name, svc) if svc else None


# ---------- one call ----------

def _json_from(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise AIError(f"no JSON in reply: {text[:120]!r}")
    return json.loads(m.group(0))


def _openai(svc: dict, user: str) -> dict:
    url = svc["base_url"].rstrip("/") + "/chat/completions"
    # An explicit User-Agent: Cloudflare-fronted endpoints (Cortex) reject Python's default one.
    headers = {"content-type": "application/json", "accept": "application/json", "user-agent": "digital-fte/0.1"}
    key = get_key(svc)
    if key:
        headers["authorization"] = f"Bearer {key}"
    body = {"model": svc["model"], "temperature": 0,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": user + "\n\nJSON schema:\n" + json.dumps(SCHEMA)}],
            "response_format": {"type": "json_object"}}
    for attempt in range(5):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            return _json_from(data["choices"][0]["message"]["content"] or "")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:200]
            if e.code == 400 and "response_format" in body:
                body.pop("response_format")   # some servers don't support JSON mode; the prompt still asks for JSON
                continue
            if e.code == 429 and attempt < 4:
                # Busy: wait as long as the server asks (or 30s), with jitter so workers don't collide.
                wait = float(e.headers.get("retry-after") or 30)
                time.sleep(wait + random.uniform(0, 5))
                continue
            if e.code in (500, 502, 503, 504) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise AIError(f"HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise AIError(f"connection failed: {e}") from None
    raise AIError("gave up after retries")


def _anthropic(svc: dict, user: str) -> dict:
    import anthropic

    key = get_key(svc)
    client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=svc["model"],
            max_tokens=8000,
            system=SYSTEM,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
    except anthropic.RateLimitError as e:
        raise AIError(f"rate limited: {e.message}") from None
    except anthropic.APIStatusError as e:
        raise AIError(f"HTTP {e.status_code}: {e.message}") from None
    except anthropic.APIConnectionError as e:
        raise AIError(f"connection failed: {e}") from None
    if response.stop_reason == "refusal":
        raise AIError("the model declined this task")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text)


def estimate_one(svc: dict, summary: str) -> dict:
    out = (_anthropic if svc.get("api") == "anthropic" else _openai)(svc, summary)
    try:
        h = float(out["h"])
        lo, hi = sorted([float(out.get("h_low", h)), float(out.get("h_high", h))])
        cat = out.get("category") if out.get("category") in CATEGORIES else None
        purpose = out.get("purpose") if out.get("purpose") in PURPOSES else None
        return {"title": str(out.get("title", ""))[:90], "category": cat, "purpose": purpose, "h": h, "h_low": min(lo, h),
                "h_high": max(hi, h), "k": float(out.get("k", 0.7)), "reason": str(out.get("reason", ""))[:300]}
    except (KeyError, TypeError, ValueError) as e:
        raise AIError(f"unexpected reply shape: {e}") from None


# ---------- running over tasks ----------

progress = {"running": False, "done": 0, "total": 0, "errors": 0, "last_error": None, "service": None}
_lock = threading.Lock()


def pending(con: sqlite3.Connection, name: str, model: str, limit: int) -> list[sqlite3.Row]:
    """Tasks with no estimate from this service/model yet, whose task grew since, or
    whose estimate predates the purpose field."""
    ensure_tables(con)
    return con.execute(
        "select t.id, t.summary, t.n_events from tasks t left join ai_estimates a "
        "on a.task_id = t.id and a.service = ? and a.model = ? "
        "where a.task_id is null or a.n_events != t.n_events or a.error is not null or a.purpose is null "
        "order by t.started desc limit ?", (name, model, limit)).fetchall()


def run(db_path, cfg: dict, limit: int | None = None) -> dict:
    """Estimate every pending task with the active service. Safe to call from a
    background thread; only one run happens at a time."""
    act = active(cfg)
    if not act:
        return {"skipped": "AI is off"}
    name, svc = act
    ok, why = status(name, svc)
    if not ok:
        return {"skipped": f"{name}: {why}"}
    if not _lock.acquire(blocking=False):
        return {"skipped": "already running"}
    from .jobs import file_lock
    with file_lock("ai", wait=False) as got:
        if not got:
            _lock.release()
            return {"skipped": "already running in another process"}
        return _run(db_path, cfg, name, svc, limit)


def _run(db_path, cfg: dict, name: str, svc: dict, limit: int | None) -> dict:
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        todo = pending(con, name, svc["model"], limit or cfg["ai"].get("max_tasks_per_run", 400))
        progress.update(running=True, done=0, total=len(todo), errors=0, last_error=None, service=f"{name} · {svc['model']}")
        with ThreadPoolExecutor(max_workers=max(1, int(svc.get("parallel", 2)))) as pool:
            pause = float(svc.get("pause_seconds", 0))

            def one(summary):
                try:
                    return estimate_one(svc, summary)
                finally:
                    time.sleep(pause)   # leave room for the person's own agents on a shared quota

            futures = {pool.submit(one, t["summary"]): t for t in todo}
            for f in as_completed(futures):
                t = futures[f]
                try:
                    r, err = f.result(), None
                except Exception as e:  # keep going; the error is stored and retried next run
                    r, err = {}, str(e)[:300]
                    progress["errors"] += 1
                    progress["last_error"] = err
                con.execute("insert or replace into ai_estimates values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (t["id"], name, svc["model"], t["n_events"], r.get("title"), r.get("category"),
                             r.get("h_low"), r.get("h"), r.get("h_high"), r.get("k"), r.get("reason"), err, time.time(),
                             r.get("purpose")))
                con.commit()
                progress["done"] += 1
        con.close()
        return {"service": name, "model": svc["model"], "estimated": progress["done"] - progress["errors"],
                "errors": progress["errors"], "last_error": progress["last_error"]}
    finally:
        progress["running"] = False
        _lock.release()
