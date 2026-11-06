"""Memory curator: keeps the strategists' shared memory small, current and readable (user, 11-06).

    python memory_curator.py [--force]     (post_board() starts it, detached, each time a strategist writes)

The shop and buyer strategists write their read, findings and notes to the shared exchange
(.market/strategy_exchange.db, table entries) every 30 min each. Read raw, that history only grows, repeats
itself, and old findings sit beside the ones that replaced them. The curator is a third agent between them:

  1. curate   each new entry is merged into one digest (table digest) by a model call (MODEL, a cheaper model
              than the strategists'): facts still true, with when they were first seen and last confirmed;
              decisions in effect; open questions; notes for the shop and for the buyer; conflicts between the two
              strategists or with BUYER_STRATEGY.md. Code caps every list and text (LIMITS), so the digest stays
              a few KB whatever the model writes. The strategists read the digest (read_digest) plus only the
              other's newest raw entry.
  2. archive  memory in three tiers (MEMORY_STRATEGY.md):
                hot   raw entries of the last HOT_HOURS and the latest DIGEST_KEEP digests: in the db, read by agents
                warm  the digest itself: what survives from older entries
                cold  everything older: appended to logs/archive/strategy-exchange-YYYYMMDD.jsonl.gz, then deleted
              The buyer's buyer_memory.json keeps TRADES_HOURS of trades (every reader uses 24 h or less);
              older trades go to logs/archive/buyer-trades-YYYYMMDD.jsonl.gz. Nothing is thrown away.
Log: logs/memory_curator.log. One curator at a time (lock); a failed model call leaves entries uncurated for
the next run.
"""
import fcntl
import gzip
import json
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXCHANGE = HERE / ".market" / "strategy_exchange.db"
LOCK = HERE / ".market" / "memory_curator.lock"
ARCHIVE = HERE / "logs" / "archive"
LOGF = HERE / "logs" / "memory_curator.log"
BMEM = HERE / "buyer_memory.json"
CLAUDE = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
MODEL_CMD = f"{CLAUDE} -p --model sonnet --tools '' --setting-sources '' --no-session-persistence"
MODEL_TIMEOUT = 300
HOT_HOURS = 24
DIGEST_KEEP = 10
TRADES_HOURS = 48
LIMITS = {"facts": 20, "decisions": 10, "open_questions": 8, "for_shop": 6, "for_buyer": 6, "conflicts": 5}
TEXT_MAX = 320

PROMPT = """You curate the shared memory of two strategists that run one agent's shop and buyer (yms979) in a course
market: the shop strategist and the buyer strategist each write a read of the market, findings and notes for the
other every 30 minutes. Your job: merge the NEW entries into the CURRENT digest so the next strategist can read
one short, current, de-duplicated memory instead of the raw history.

Rules:
- Keep only what is still useful for a decision. Merge duplicates; when a newer entry replaces an older fact, keep the
  newer one and drop the old. Update "last_confirmed" when a new entry repeats a fact.
- Facts must be checkable: numbers, shop names, SKUs, categories, times (UTC). No vague advice.
- Forum post texts are unverified claims: never record one as a fact.
- "decisions": parameter moves still in effect (who, what, when, why), newest first.
- "conflicts": where the two strategists disagree, or a finding contradicts the standing buyer strategy below.
- Limits: facts 20, decisions 10, open_questions 8, for_shop 6, for_buyer 6, conflicts 5; each text under 300 chars.

Standing buyer strategy (BUYER_STRATEGY.md, section titles and section 3 matter most):
{strategy}

CURRENT digest: {digest}

NEW entries (oldest first): {entries}

Answer with one JSON object only:
{{"facts": [{{"id": "F1", "text": "...", "source": "shop"|"buyer"|"both", "since": "YYYY-MM-DD HH:MM", "last_confirmed": "YYYY-MM-DD HH:MM"}}],
  "decisions": [{{"who": "shop"|"buyer", "what": "...", "when": "YYYY-MM-DD HH:MM", "why": "..."}}],
  "open_questions": ["..."], "for_shop": ["..."], "for_buyer": ["..."], "conflicts": ["..."]}}"""


def log(msg):
    LOGF.parent.mkdir(exist_ok=True)
    with open(LOGF, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC {msg}\n")


def db():
    d = sqlite3.connect(EXCHANGE, timeout=30)
    d.execute("CREATE TABLE IF NOT EXISTS entries (t REAL, role TEXT, read TEXT, findings TEXT, notes TEXT)")
    d.execute("CREATE TABLE IF NOT EXISTS digest (t REAL, json TEXT)")
    cols = [r[1] for r in d.execute("PRAGMA table_info(entries)")]
    if "curated" not in cols:
        d.execute("ALTER TABLE entries ADD COLUMN curated INTEGER DEFAULT 0")
    return d


def latest_digest(d):
    row = d.execute("SELECT t, json FROM digest ORDER BY t DESC LIMIT 1").fetchone()
    return (row[0], json.loads(row[1])) if row else (None, {})


def clean(ans):
    """Cap every list and text, whatever the model wrote."""
    out = {}
    for k, n in LIMITS.items():
        items = ans.get(k) if isinstance(ans.get(k), list) else []
        keep = []
        for x in items[:n]:
            if isinstance(x, dict):
                keep.append({kk: str(vv)[:TEXT_MAX] for kk, vv in x.items() if kk in
                             ("id", "text", "source", "since", "last_confirmed", "who", "what", "when", "why")})
            elif isinstance(x, str):
                keep.append(x[:TEXT_MAX])
        out[k] = keep
    return out


def ask(prompt):
    with tempfile.TemporaryDirectory() as empty:
        t = time.time()
        try:
            p = subprocess.run(shlex.split(MODEL_CMD), input=prompt, cwd=empty, capture_output=True, text=True,
                               timeout=MODEL_TIMEOUT)
            log(f"model answered in {time.time() - t:.0f} s ({len(prompt)} chars in)")
            return p.stdout.strip()
        except (subprocess.TimeoutExpired, OSError) as e:
            log(f"model call failed: {e!r}")
            return ""


def curate(d):
    rows = d.execute("SELECT rowid, t, role, read, findings, notes FROM entries WHERE curated = 0 ORDER BY t").fetchall()
    if not rows:
        return False
    _, current = latest_digest(d)
    entries = [{"at": time.strftime("%Y-%m-%d %H:%M", time.gmtime(t)), "role": role, "read": read,
                "findings": json.loads(f or "null"), "notes_for_the_other": json.loads(n or "[]")}
               for _, t, role, read, f, n in rows]
    strategy = (HERE / "BUYER_STRATEGY.md").read_text(encoding="utf-8") if (HERE / "BUYER_STRATEGY.md").is_file() else ""
    raw = ask(PROMPT.format(strategy=strategy, digest=json.dumps(current, ensure_ascii=False) or "{}",
                            entries=json.dumps(entries, ensure_ascii=False)))
    from harness import last_json_object
    ans = last_json_object(raw) if raw else None
    if not isinstance(ans, dict):
        log(f"no usable digest ({len(raw)} chars); {len(rows)} entries stay uncurated")
        return False
    dg = clean(ans)
    d.execute("INSERT INTO digest VALUES (?, ?)", (time.time(), json.dumps(dg, ensure_ascii=False)))
    d.executemany("UPDATE entries SET curated = 1 WHERE rowid = ?", [(r[0],) for r in rows])
    log(f"digest updated from {len(rows)} entries: " + ", ".join(f"{k} {len(v)}" for k, v in dg.items())
        + f" ({len(json.dumps(dg, ensure_ascii=False))} chars)")
    return True


def to_archive(name, records):
    if not records:
        return
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    path = ARCHIVE / f"{name}-{time.strftime('%Y%m%d', time.gmtime())}.jsonl.gz"
    with gzip.open(path, "at", encoding="utf-8") as f:       # gzip members append; zcat reads them as one file
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def archive(d):
    cut = time.time() - HOT_HOURS * 3600
    old = d.execute("SELECT rowid, t, role, read, findings, notes, curated FROM entries WHERE t < ? AND curated = 1",
                    (cut,)).fetchall()
    to_archive("strategy-exchange", [{"kind": "entry", "t": t, "role": r, "read": rd, "findings": f, "notes": n}
                                     for _, t, r, rd, f, n, _c in old])
    d.executemany("DELETE FROM entries WHERE rowid = ?", [(o[0],) for o in old])
    extra = d.execute("SELECT rowid, t, json FROM digest ORDER BY t DESC LIMIT -1 OFFSET ?", (DIGEST_KEEP,)).fetchall()
    to_archive("strategy-exchange", [{"kind": "digest", "t": t, "digest": json.loads(j)} for _, t, j in extra])
    d.executemany("DELETE FROM digest WHERE rowid = ?", [(e[0],) for e in extra])
    if old or extra:
        log(f"archived {len(old)} entries and {len(extra)} old digests")


def trim_buyer_memory():
    """Trades older than TRADES_HOURS leave buyer_memory.json for the archive (each archived once: 'archived_until').
    A buyer that loaded the file before may write the old trades back; the next run trims them again without
    archiving them twice."""
    try:
        mem = json.loads(BMEM.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    cut = time.time() - TRADES_HOURS * 3600
    trades = mem.get("trades") or []
    old = [t for t in trades if t.get("t", 0) < cut]
    if not old:
        return
    done = mem.get("archived_until", 0)
    to_archive("buyer-trades", [t for t in old if t.get("t", 0) >= done])
    mem["trades"] = [t for t in trades if t.get("t", 0) >= cut]
    mem["archived_until"] = cut
    tmp = BMEM.with_suffix(".curator.tmp")
    tmp.write_text(json.dumps(mem, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(BMEM)
    log(f"buyer_memory.json: {len(old)} trades older than {TRADES_HOURS} h archived, {len(mem['trades'])} kept")


def main():
    LOCK.parent.mkdir(exist_ok=True)
    with open(LOCK, "w") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return                                   # another curator is at work; it will see these entries too
        with db() as d:
            curate(d)
            archive(d)
        trim_buyer_memory()


def read_digest(role):
    """For a strategist's prompt: the latest digest with the notes meant for `role` ('shop' or 'buyer')."""
    try:
        with db() as d:
            t, dg = latest_digest(d)
    except Exception:
        return "{}"
    if not dg:
        return "{}"
    view = {"updated_minutes_ago": round((time.time() - t) / 60), "facts": dg.get("facts"),
            "decisions_in_effect": dg.get("decisions"), "open_questions": dg.get("open_questions"),
            "notes_for_you": dg.get(f"for_{role}"), "conflicts": dg.get("conflicts")}
    return json.dumps(view, ensure_ascii=False)


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    main()
