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
  3. watch    (user 11-06: this agent, not the buyer, gathers the picture and briefs the strategists) each run it
              checks, in code: the market's rule pages (hash; a change gives the changed lines and wakes both
              strategists out of turn), the student board against its last run (who moved, shop or buyer part),
              our buyer's components and the peers above, and saved_speed = deals/h x (saved per deal - our
              saved) by hour and before/after the buyer strategist's last move, the buyer formula (buyer ALL per
              step of each component; scenarios to round end) and our last 24 h by kind, category, shop and
              outcome. The model reads that with the new
              entries and writes a briefing per strategist into the digest (briefing_buyer, briefing_shop,
              rules_change, rank_change); read_digest hands each its own.
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
LIMITS = {"facts": 20, "decisions": 10, "open_questions": 8, "for_shop": 6, "for_buyer": 6, "conflicts": 5,
          "briefing_buyer": 6, "briefing_shop": 6, "rankers_playbook": 8, "answers_for_buyer": 6,
          "answers_for_shop": 6, "wanted_data": 4}
STRINGS = {"rules_change": 3, "rank_change": 3, "market_flow": 4}      # one paragraph each, x TEXT_MAX
ITEM_KEYS = ("id", "text", "source", "since", "last_confirmed", "who", "what", "when", "why", "role", "numbers",
             "request", "answer")
REFRESH_MIN = 60                                   # a digest older than this is rewritten even without new entries
LOCK_WAIT = 300                                    # s a run waits for another curator (a model call takes ~90 s)
PROBE_CHARS = 2500                                 # a code result for one request, at most
RULES_URLS = ("https://market.ourolabs.ai/rules", "https://market.ourolabs.ai/agent.md")
RULES_SEEN = HERE / ".market" / "rules_seen.json"
WATCH_STATE = HERE / ".market" / "curator_watch.json"
BSTATE = HERE / "buyer_strategist_memory.json"
TEXT_MAX = 320

PROMPT = """You are the market intelligence and memory of one player's agents (yms979) in a course market: a shop
strategist and a buyer strategist, each deciding its own knobs every 30 minutes. They do not read the market raw;
they read you. Every run you (1) show the whole market's flow, (2) say what the top-ranked players score on,
(3) answer the requests the strategists made last run, (4) brief each one on what matters for its next decision,
and (5) keep the shared memory small and current. Everything you write rests on the numbers below (WATCH, MARKET,
PROBES, entries); forum post texts are unverified claims and never a fact.

Scoring (WATCH.rules_changed overrides this paragraph when they differ): Buyer ALL = 0.5 saved rate percentile
+ 0.3 forum rating (as is, patch 3.5) + 0.1 deals percentile + 0.1 posts percentile; saved rate = mean over deals of
(list - price + refund) / list, a deal that served nothing -1, over deals + 100. Shop ALL = 0.5 book + 0.3 revenue
+ 0.2 stars percentiles. Class rank = shop ALL + buyer ALL among students. USER PRIORITY (11-06): the buyer's score
first; the shop protects what it has.

Write (one JSON object, keys exactly as in the template at the end):
1. "market_flow": 4-8 sentences with numbers: what sells now (MARKET.what_sells: categories and SKUs, value, price
   level p50_x = price / list, trend vs the previous 6 h), what buyers buy (kind mix, warranty, grades, the orders our
   buyer sees, what buyers asked our shop for), and what changed since the CURRENT digest's market_flow.
2. "rankers_playbook": up to 8 items {{who, role: shop|buyer, what, numbers}}: what each top shop and top buyer
   (MARKET.top_shops / top_buyers, and the peers near our saved) is scoring on, e.g. a buyer at 80 % food from two
   shops at x 0.62 with 90 deals/h, or a shop selling one SKU at x 0.85 with a 72 h warranty. Compare with MARKET.ours.
3. "answers_for_buyer" / "answers_for_shop": one item {{request, answer}} per request in the entries' "requests"
   (PROBES holds the code results, keyed by the role that asked; a "question" you answer from everything you have,
   or say what cannot be measured and put it in "wanted_data"). Keep the numbers; say when a result was measured.
4. "briefing_buyer" / "briefing_shop": up to 6 items each, what that strategist must weigh now, with numbers.
   Buyer: item 1 the best setting by the formula (WATCH.buyer_formula: buyer ALL per step of each component and the
   scenarios to 24 h / 72 h / round end; name the scenario, the expected buyer ALL change and the knobs that get
   there: value_share, category caps, skip_below_food / skip_below_goods, quick_accept, open_offset, max_shops,
   shop_bias / shop_cap); state the break-even saved per deal (user 11-06: a deal pays off when its saved is above
   the projected round-end saved minus the small deals bonus, ~0.235 at saved 0.228; it rises with our saved) and
   whether skip_below_goods / skip_below_food sit at it, and say to raise them when our saved has risen; item 2 where saved is lost (WATCH.where_saved_goes_24h: kind, category, shop, outcome;
   minus_one deals cost most; name the knob); then what saved_speed says about the last move (WATCH.saved_speed,
   higher is better: since_last_move vs before_6h), what the ranking buyers do that ours does not, and what the shop
   strategist found that concerns it. Shop: cover of its top sellers, its prices vs the market's p50_x in those
   categories, what buyers asked for and it lacked, what the ranking shops do, and anything that could hurt the
   buyer. Do not repeat what a strategist already said it acts on.
5. "rules_change": if WATCH.rules_changed has lines, what the patch changes in the score and what should change in
   each agent's behaviour, concretely; else "". "rank_change": if our class rank or the totals around us moved since
   the last run (WATCH.rank), why (ours or theirs, which part, which component); else "".
6. Memory: merge the NEW entries into the CURRENT digest: "facts" (checkable: numbers, names, SKUs, UTC times;
   merge duplicates, a newer fact replaces an older one, update last_confirmed), "decisions" (moves in effect,
   newest first), "open_questions", "for_shop" / "for_buyer" (notes from one strategist to the other that still
   stand), "conflicts" (the two disagree, or a finding contradicts the standing strategy).
7. "wanted_data": up to 4 measurements that would help and that no source below gives (for the operator).
Limits: facts 20, decisions 10, open_questions 8, for_shop 6, for_buyer 6, conflicts 5, rankers_playbook 8,
answers 6 each, briefings 6 each, wanted_data 4; texts under 300 chars (market_flow under 1200).

WATCH (rules, rank, our buyer's components and formula, saved_speed, our last 24 h): {watch}

MARKET (the whole market's last 6 h, measured in code): {market}

PROBES (code results for the strategists' requests, by the role that asked): {probes}

Standing buyer strategy (BUYER_STRATEGY.md): {strategy}

CURRENT digest: {digest}

NEW entries (oldest first; each may carry "requests"): {entries}

Answer with one JSON object only:
{{"market_flow": "...",
  "rankers_playbook": [{{"who": "...", "role": "shop"|"buyer", "what": "...", "numbers": "..."}}],
  "answers_for_buyer": [{{"request": "...", "answer": "..."}}], "answers_for_shop": [{{"request": "...", "answer": "..."}}],
  "briefing_buyer": ["..."], "briefing_shop": ["..."], "rules_change": "...", "rank_change": "...",
  "facts": [{{"id": "F1", "text": "...", "source": "shop"|"buyer"|"both", "since": "YYYY-MM-DD HH:MM", "last_confirmed": "YYYY-MM-DD HH:MM"}}],
  "decisions": [{{"who": "shop"|"buyer", "what": "...", "when": "YYYY-MM-DD HH:MM", "why": "..."}}],
  "open_questions": ["..."], "for_shop": ["..."], "for_buyer": ["..."], "conflicts": ["..."],
  "wanted_data": ["..."]}}"""


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
    if "requests" not in cols:
        d.execute("ALTER TABLE entries ADD COLUMN requests TEXT")      # 11-06: what a strategist asks the curator
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
                keep.append({kk: str(vv)[:TEXT_MAX] for kk, vv in x.items() if kk in ITEM_KEYS})
            elif isinstance(x, str):
                keep.append(x[:TEXT_MAX])
        out[k] = keep
    for k, n in STRINGS.items():
        out[k] = str(ans.get(k) or "")[:TEXT_MAX * n]
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


def rules_watch():
    """The market's rule pages, hashed; a change comes back as the changed lines (a patch)."""
    import difflib
    import hashlib
    import urllib.request
    seen = read_json(RULES_SEEN, {})
    out = {}
    for url in RULES_URLS:
        try:
            text = urllib.request.urlopen(url, timeout=20).read().decode("utf-8", "replace")
        except Exception:
            continue
        h = hashlib.sha256(text.encode()).hexdigest()
        old = seen.get(url) or {}
        if old.get("sha") and old["sha"] != h:
            diff = [x for x in difflib.unified_diff(old.get("text", "").splitlines(), text.splitlines(), lineterm="", n=0)
                    if x[:1] in "+-" and not x.startswith(("+++", "---"))]
            out[url] = "\n".join(diff)[:4000]
        seen[url] = {"sha": h, "text": text, "t": time.time()}
    RULES_SEEN.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")
    return out


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def watch(rules_changed):
    """What the curator measures in code each run, for its model call: rules, rank, buyer components, saved_speed."""
    import buyer_strategist as bs
    st = read_json(WATCH_STATE, {})
    b = bs.board_view()
    now = time.time()
    top = {x[0]: {"total": x[1], "shop": x[3], "buyer": x[5]} for x in b.get("class_top") or []}
    before = st.get("class_top") or {}
    moved = {}
    for name, v in top.items():
        o = before.get(name)
        if o:
            dd = {k: round((v[k] or 0) - (o[k] or 0), 3) for k in ("total", "shop", "buyer")}
            if any(abs(x) >= 0.003 for x in dd.values()):
                moved[name] = dd
    base = b["buyer"].get("saved") or 0
    hourly = []
    for h in range(6, 0, -1):
        rows = bs.trades(now - h * 3600, now - (h - 1) * 3600)
        sm = bs.summary(rows)
        hourly.append({"hours_ago": h, "deals": sm["deals"], "mean_saved": sm["mean_saved"],
                       "saved_speed": bs.saved_speed(rows, 1, base)})
    eps = [e for e in read_json(BSTATE, {}).get("episodes", []) if e.get("applied")]
    last = max((e["t"] for e in eps), default=None)
    out = {"time_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(now)),
           "rules_changed": rules_changed or {},
           "rank": {"class_rank_now": b.get("class_rank"), "class_rank_last_run": st.get("class_rank"),
                    "minutes_since_last_run": round((now - st["t"]) / 60) if st.get("t") else None,
                    "class_top": b.get("class_top"), "changes_since_last_run": moved},
           "buyer": b["buyer"], "peers_above": (b.get("peers_above") or [])[:10],
           "saved_speed": {"by_hour": hourly, "since_last_move": None if not last else {
               "hours": round((now - last) / 3600, 2),
               "speed": bs.saved_speed(bs.trades(last), (now - last) / 3600, base),
               "before_6h": bs.saved_speed(bs.trades(last - 6 * 3600, last), 6, base),
               "moves": [(e.get("knob"), e.get("sku"), e.get("value")) for e in eps if e["t"] == last]}}}
    out["buyer_formula"] = formula(b, hourly)
    out["where_saved_goes_24h"] = breakdown(bs, now)
    WATCH_STATE.write_text(json.dumps({"t": now, "class_top": top, "class_rank": b.get("class_rank")},
                                      ensure_ascii=False), encoding="utf-8")
    return out


def breakdown(bs, now):
    """Our buyer's last 24 h cut every way the score can be won or lost (user 11-06: finding e.g. that food ran
    0.279 per deal and goods 0.194 is the curator's job): by kind, category, shop and outcome, each with orders,
    deals, saved per deal and the deals that counted -1."""
    rows = bs.trades(now - 24 * 3600)

    def cut(key, top=None):
        g = {}
        for r in rows:
            g.setdefault(key(r) or "?", []).append(r)
        res = []
        for k, v in g.items():
            sm = bs.summary(v)
            dl = [x for x in v if x.get("outcome") == "deal" and x.get("saved") is not None]
            res.append({"key": k, "orders": len({x.get("order") for x in v}), "deals": sm["deals"],
                        "saved_per_deal": sm["mean_saved"], "minus_one": sum(1 for x in dl if x["saved"] < 0),
                        "saved_sum": round(sum(x["saved"] for x in dl), 2)})
        res.sort(key=lambda x: -x["deals"])
        return res[:top] if top else res
    outcomes = {}
    for r in rows:
        o = str(r.get("outcome") or "")[:24]
        outcomes[o] = outcomes.get(o, 0) + 1
    return {"by_kind": cut(lambda r: r.get("kind")), "by_category": cut(lambda r: r.get("category"), 15),
            "by_shop": cut(lambda r: r.get("shop"), 15),
            # 11-07 A/B (params buyer.base push_share/push_band): "control" takes an ask once it is under our
            # cap; the push arm counters once more when the ask is above the shop's p25 + band ("pushed" = it
            # did, "arm" = the ask was already low). Compare saved_per_deal AND deals per order of control vs
            # arm+pushed: pushing wins only if it raises saved without losing many deals
            # judged over ALL shops and kinds together (user 11-07: not tuned to one shop): the number that
            # decides is score_gain_per_order = deals/order x (saved per deal - our running saved ~0.245)
            "push_ab": [dict(x, deals_per_order=round(x["deals"] / max(x["orders"], 1), 3),
                             score_gain_per_order=round(x["deals"] / max(x["orders"], 1)
                                                        * ((x["saved_per_deal"] or 0) - 0.245), 4))
                        for x in cut(lambda r: ("control" if r["push"] == "control" else "push")
                                     if "push" in r else "before_ab")],
            "outcomes": dict(sorted(outcomes.items(), key=lambda kv: -kv[1])[:12]),
            "knobs_now": {k: v for k, v in (read_json(HERE / "params_auto_buyer.json", {}).get("globals") or {}).items()}}


def formula(b, hourly):
    """The buyer score's arithmetic, in code (user 11-06: weigh deals, posts, forum and saved by the formula):
    buyer ALL gained per step of each component at today's board, and where saved lands for a few
    (deals/h, saved per deal) settings, so the briefing compares them on buyer ALL itself."""
    import urllib.request
    full = json.load(urllib.request.urlopen("https://market.ourolabs.ai/api/board?round=3", timeout=30))
    rows = [x for x in full["buyers"] if x.get("all") is not None]
    me = next(x for x in rows if x["name"] == "yms979")

    def pct(k, v):
        vals = [x.get(k) or 0 for x in rows]
        return (sum(q < v for q in vals) + 0.5 * sum(q == v for q in vals)) / len(vals)

    sv, dl, po, fo = me.get("saved") or 0, me.get("deals") or 0, me.get("posts") or 0, me.get("forum") or 0
    gain = {"saved_plus_0.01": round(0.5 * (pct("saved", sv + 0.01) - pct("saved", sv)), 4),
            "saved_plus_0.03": round(0.5 * (pct("saved", sv + 0.03) - pct("saved", sv)), 4),
            "deals_plus_500": round(0.1 * (pct("deals", dl + 500) - pct("deals", dl)), 4),
            "deals_plus_2000": round(0.1 * (pct("deals", dl + 2000) - pct("deals", dl)), 4),
            "posts_plus_100": round(0.1 * (pct("posts", po + 100) - pct("posts", po)), 4),
            "forum_rating_plus_0.01": 0.003}
    total = sv * (dl + 100)
    scen = {}
    for rate, per in ((55, 0.32), (70, 0.30), (85, 0.28), (100, 0.26), (40, 0.35)):
        end = (1794927540 - time.time()) / 3600        # 2026-11-17 23:59 KST
        for hours in (24, 72, int(end)):
            n = rate * hours
            s2 = (total + n * per) / (dl + 100 + n)
            scen[f"{rate}/h at {per} for {hours}h"] = {
                "saved": round(s2, 4),
                "buyer_ALL_change": round(0.5 * (pct("saved", s2) - pct("saved", sv))
                                          + 0.1 * (pct("deals", dl + n) - pct("deals", dl)), 4)}
    last3 = [h for h in hourly[-3:] if h["deals"]]
    now_rate = sum(h["deals"] for h in last3) / max(len(last3), 1)
    now_per = (sum(h["deals"] * (h["mean_saved"] or 0) for h in last3) / max(sum(h["deals"] for h in last3), 1))
    return {"now": {"saved": sv, "deals": dl, "posts": po, "forum": fo,
                    "deals_per_h_3h": round(now_rate, 1), "saved_per_deal_3h": round(now_per, 3)},
            "buyer_ALL_gain_per_step": gain, "scenarios": scen,
            "note": "others move too; percentiles are against today's board. Posts are capped at 12/h by the market."}


def wake_strategists():
    """A patch landed: both strategists run now, out of turn, and read the curator's rules_change."""
    for script in ("buyer_strategist.py", "strategist.py"):
        subprocess.Popen([sys.executable, str(HERE / script), "--force"], cwd=HERE, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    log("rules changed: woke both strategists")


def probe_knobs(q):
    """What the knob store and the strategists' records say about one SKU or knob (wanted_data 08:22: "do knobs
    reset?"): the live params_auto entries, the episodes that set it (when, value, applied, outcome) and the last
    log lines naming it."""
    key = str(q.get("sku") or q.get("knob") or q.get("category") or "").strip()
    if not key:
        return {"error": "knobs: give sku, knob or category"}
    auto_s, auto_b = read_json(HERE / "params_auto.json", {}), read_json(HERE / "params_auto_buyer.json", {})
    out = {"key": key,
           "shop_params_auto": {"sku": (auto_s.get("sku") or {}).get(key), "globals": (auto_s.get("globals") or {}).get(key),
                                "written": time.strftime("%m-%d %H:%M", time.gmtime(auto_s.get("t") or 0))},
           "buyer_params_auto": {"globals": (auto_b.get("globals") or {}).get(key),
                                 "category": (auto_b.get("category") or {}).get(key),
                                 "shop": (auto_b.get("shop") or {}).get(key),
                                 "written": time.strftime("%m-%d %H:%M", time.gmtime(auto_b.get("t") or 0))},
           "entries_holding_this_knob": {
               "shop_sku": {k: v for k, v in (auto_s.get("sku") or {}).items() if isinstance(v, dict) and key in v},
               "buyer_shop": {k: v for k, v in (auto_b.get("shop") or {}).items() if isinstance(v, dict) and key in v},
               "buyer_category": {k: v for k, v in (auto_b.get("category") or {}).items() if isinstance(v, dict) and key in v}},
           "episodes": [], "log_lines": []}
    for role, path in (("shop", HERE / "strategist_memory.json"), ("buyer", BSTATE)):
        for e in read_json(path, {}).get("episodes", []):
            if key in (e.get("sku"), e.get("knob")):
                out["episodes"].append({"role": role, "at": time.strftime("%m-%d %H:%M", time.gmtime(e.get("t") or 0)),
                                        "knob": e.get("knob"), "sku": e.get("sku"), "value": e.get("value"),
                                        "applied": e.get("applied"), "outcome": e.get("outcome"),
                                        "due": time.strftime("%H:%M", time.gmtime(e.get("due") or 0))})
    out["episodes"] = out["episodes"][-12:]
    for path in (HERE / "logs" / "strategist.log", HERE / "logs" / "buyer_strategist.log"):
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 400_000))
                lines = f.read().decode("utf-8", "replace").splitlines()
        except OSError:
            continue
        out["log_lines"] += [x[:220] for x in lines if key in x][-6:]
    return out


def probe_trades(q):
    """Our buyer's own trade records that match (wanted_data 08:22: why 'unpaid' happens): outcome prefix, shop,
    category or kind; the last 20 with their fields, and the outcome counts among the matches in 24 h."""
    trades = read_json(BMEM, {}).get("trades") or []
    cut = time.time() - 24 * 3600
    want = {k: str(q[k]) for k in ("outcome", "shop", "category", "kind") if q.get(k)}
    rows = [t for t in trades if t.get("t", 0) >= cut
            and all(str(t.get(k) or "").startswith(v) if k == "outcome" else str(t.get(k) or "") == v
                    for k, v in want.items())]
    counts = {}
    for t in rows:
        o = str(t.get("outcome") or "")[:60]
        counts[o] = counts.get(o, 0) + 1
    keep = ("shop", "item", "category", "kind", "outcome", "price", "cap", "list", "warranty", "expected")
    bands = {}                                       # expected saved per deal (market_buyer records it) -> what happened
    for t in rows:
        x = t.get("expected")
        if isinstance(x, (int, float)):
            b = bands.setdefault(f"{int(x * 20) / 20:.2f}", {"n": 0, "deals": 0, "realized": []})
            b["n"] += 1
            if t.get("outcome") == "deal" and t.get("price") and t.get("list"):
                b["deals"] += 1
                b["realized"].append(1 - t["price"] / t["list"])
    return {"filter": want, "matches_24h": len(rows),
            "outcomes": dict(sorted(counts.items(), key=lambda kv: -kv[1])[:12]),
            "by_expected_band": {k: {"n": v["n"], "deals": v["deals"],
                                     "realized_saved": round(sum(v["realized"]) / len(v["realized"]), 3) if v["realized"] else None}
                                 for k, v in sorted(bands.items())} or "no 'expected' on these records yet (recorded since 11-06 08:40 UTC)",
            "last": [{"at": time.strftime("%H:%M", time.gmtime(t.get("t") or 0))}
                     | {k: (str(t[k])[:100] if k == "outcome" else t[k]) for k in keep if k in t} for t in rows[-8:]]}


def run_probes(entries):
    """Code answers for the strategists' requests (strategist_memory.REQUEST_KINDS): the market_probe
    investigations, a buyer's or a shop's profile from market_picture, "knobs" (one SKU's or knob's store entries,
    episodes and log lines), "trades" (our buyer's matching trade records), nothing for a "question" (the model
    answers it). Keyed by the role that asked; each result capped at PROBE_CHARS."""
    out = {}
    cat = read_json(HERE / ".market" / "catalog.json", {})
    for e in entries:
        for q in (e.get("requests") or [])[:5]:
            kind = q.get("kind")
            try:
                if kind in ("buyer", "shop"):
                    import market_picture as mp
                    res = (mp.profile_buyer if kind == "buyer" else mp.profile_shop)(str(q.get("name") or ""))
                elif kind == "question":
                    res = {"note": "no code probe: answered by the curator from WATCH, MARKET and the digest"}
                elif kind == "knobs":
                    res = probe_knobs(q)
                elif kind == "trades":
                    res = probe_trades(q)
                else:
                    import market_probe as probe
                    res = probe.QUERIES[kind](q, cat)
                txt = json.dumps(res, ensure_ascii=False)
                item = {"request": q, "measured_at": time.strftime("%H:%M UTC", time.gmtime()),
                        "result": res if len(txt) <= PROBE_CHARS else
                        {"cut_to_chars": PROBE_CHARS, "of": len(txt), "head": txt[:PROBE_CHARS]}}
            except Exception as ex:
                item = {"request": q, "error": f"{type(ex).__name__}: {ex}"}
            out.setdefault(e["role"], []).append(item)
            log(f"probe for {e['role']}: {json.dumps(q, ensure_ascii=False)[:120]} -> {'error' if 'error' in item else 'ok'}")
    return out


def curate(d, w=None, market=None, force=False):
    rows = d.execute("SELECT rowid, t, role, read, findings, notes, requests FROM entries WHERE curated = 0 ORDER BY t").fetchall()
    dt, current = latest_digest(d)
    w = w or {}
    rk = w.get("rank") or {}
    why = []                                         # the model is called only for one of these
    if rows:
        why.append(f"{len(rows)} new entries")
    if w.get("rules_changed"):
        why.append("rules changed")
    if rk.get("class_rank_last_run") is not None and rk.get("class_rank_now") != rk.get("class_rank_last_run"):
        why.append(f"class rank {rk['class_rank_last_run']} -> {rk['class_rank_now']}")
    if dt is None or time.time() - dt > REFRESH_MIN * 60:
        why.append(f"digest older than {REFRESH_MIN} min")
    if not current.get("briefing_buyer"):
        why.append("no briefing yet")
    if force:
        why.append("forced")
    if not why:
        return False                                 # nothing new to merge, answer or brief
    log("curating: " + ", ".join(why))
    entries = [{"at": time.strftime("%Y-%m-%d %H:%M", time.gmtime(t)), "role": role, "read": read,
                "findings": json.loads(f or "null"), "notes_for_the_other": json.loads(n or "[]"),
                "requests": json.loads(rq or "[]")}
               for _, t, role, read, f, n, rq in rows]
    probes = run_probes(entries)
    strategy = (HERE / "BUYER_STRATEGY.md").read_text(encoding="utf-8") if (HERE / "BUYER_STRATEGY.md").is_file() else ""
    raw = ask(PROMPT.format(strategy=strategy, digest=json.dumps(current, ensure_ascii=False) or "{}",
                            entries=json.dumps(entries, ensure_ascii=False),
                            watch=json.dumps(w or {}, ensure_ascii=False),
                            market=json.dumps(market or {"error": "no market picture"}, ensure_ascii=False),
                            probes=json.dumps(probes, ensure_ascii=False) if probes else "(no requests this run)"))
    from harness import last_json_object
    ans = last_json_object(raw) if raw else None
    if not isinstance(ans, dict):
        log(f"no usable digest ({len(raw)} chars); {len(rows)} entries stay uncurated")
        return False
    dg = clean(ans)
    d.execute("INSERT INTO digest VALUES (?, ?)", (time.time(), json.dumps(dg, ensure_ascii=False)))
    d.executemany("UPDATE entries SET curated = 1 WHERE rowid = ?", [(r[0],) for r in rows])
    log(f"digest updated from {len(rows)} entries ({sum(len(v) for v in probes.values())} probes): "
        + ", ".join(f"{k} {len(v)}" for k, v in dg.items())
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
    old = d.execute("SELECT rowid, t, role, read, findings, notes, requests FROM entries WHERE t < ? AND curated = 1",
                    (cut,)).fetchall()
    to_archive("strategy-exchange", [{"kind": "entry", "t": t, "role": r, "read": rd, "findings": f, "notes": n,
                                      "requests": rq} for _, t, r, rd, f, n, rq in old])
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


def _lock(lk, wait):
    """One curator at a time; a second one waits (a strategist's post that lands during the cron tick's run would
    otherwise sit unanswered until the next tick: the buyer's 08:30 requests did on 11-06)."""
    t0 = time.time()
    while True:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.time() - t0 > wait:
                return False
            time.sleep(5)


def main(force=False):
    LOCK.parent.mkdir(exist_ok=True)
    with open(LOCK, "w") as lk:
        if not _lock(lk, LOCK_WAIT):
            log(f"another curator held the lock for {LOCK_WAIT} s; giving up this run")
            return
        pending = read_json(WATCH_STATE, {}).get("pending_rules") or {}
        try:
            changed = pending | rules_watch()      # a change stays pending until a digest has taken it in
        except Exception as e:
            changed = pending
            log(f"rules watch failed: {e!r}")
        try:
            w = watch(changed)
        except Exception as e:
            w = {"rules_changed": changed, "error": repr(e)}
            log(f"watch failed: {e!r}")
        try:
            import market_picture as mp
            market = mp.picture()
        except Exception as e:
            market = {"error": repr(e)}
            log(f"market picture failed: {e!r}")
        with db() as d:
            done = curate(d, w, market, force)
            archive(d)
        st = read_json(WATCH_STATE, {})
        st["pending_rules"] = {} if done else changed
        WATCH_STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        if changed and done:
            wake_strategists()
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
            "notes_for_you": dg.get(f"for_{role}"), "conflicts": dg.get("conflicts"),
            "briefing_for_you": dg.get(f"briefing_{role}"), "rules_change": dg.get("rules_change"),
            "rank_change": dg.get("rank_change"), "market_flow": dg.get("market_flow"),
            "rankers_playbook": dg.get("rankers_playbook"),
            "answers_to_your_requests": dg.get(f"answers_for_{role}"), "wanted_data": dg.get("wanted_data")}
    return json.dumps(view, ensure_ascii=False)


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    main("--force" in sys.argv)
