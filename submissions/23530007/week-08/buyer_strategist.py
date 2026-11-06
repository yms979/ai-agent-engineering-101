"""Buyer strategist: the buyer's own analyst, beside strategist.py (the shop's) (user, 11-06).

    python buyer_strategist.py [--force]      (watch_round.sh runs it every 15 min; it works once an hour)

Since Round 3 patch 3.4 the buyer scores 0.5 saved rate (the mean share of list price kept per deal; a deal that
served nothing counts -1) + 0.3 forum + 0.1 deals + 0.1 posts. The shop's strategist does not see any of that,
so the buyer gets its own loop, the same shape:
  memory    buyer_memory.json (every visit: shop, SKU, category, asks, outcome, price) read in windows
  learn     its 6 h predictions are scored from buyer_memory; per-kind step limits, reverts, playbook rollback and
            a critic call after misses, all through strategist_memory.Memory (its own file)
  evidence  the buyer's board components with the ladder above it, our saved rate per deal now vs the running
            mean, per category and per shop: visits, deals, conversion, mean saved, first ask / list
  schedule  every 30 min in the even 15-minute slots (:00, :30); the shop strategist takes the odd ones, and each
            reads the other's read and notes from the shared SQLite exchange (strategist_memory.post_board)
  goal      first place among buyers on buyer ALL, as fast as possible (user, 11-06)
  decide    MODEL (opus) moves whitelisted knobs: value_share (cap, share of list), quick_accept, food_extra_hours,
            and per category "cap"; each with a checkable prediction
  act       mode as the shop strategist's (params.json strategist.mode); "apply" writes params_auto_buyer.json,
            which policy.py lays over the buyer's parameters (market_buyer reads the category cap)
Log: logs/buyer_strategist.log.
"""
import argparse
import json
import time
from pathlib import Path

import market_probe as probe
import strategist as shop
from harness import last_json_object
from strategist_memory import Memory, my_turn, post_board, read_board, read_digest

HERE = Path(__file__).resolve().parent
AUTO = HERE / "params_auto_buyer.json"
STATE = HERE / "buyer_strategist_memory.json"
LOGF = HERE / "logs" / "buyer_strategist.log"
BMEM = HERE / "buyer_memory.json"
CATALOG = HERE / ".market" / "catalog.json"
EVERY = 3600 - 120
MAX_MOVES = 8
GLOBAL = {                                    # role, bounds, step
    "value_share": ((0.65, 0.85), 0.03),      # the most the buyer pays, as a share of list price
    "quick_accept": ((0.55, 0.75), 0.03),     # an ask at or under this share of list is taken at once
    "food_extra_hours": ((1, 12), 2),         # food must last the order's hours + this (a spoiled deal counts -1)
    "skip_below": ((0.15, 0.33), 0.02),       # skip an order when the best shop's expected saved per deal is under this
    "conv_weight": ((0.0, 0.3), 0.05),        # shop pick: expected saved + this x our close rate there
    "rival_penalty": ((0.0, 0.1), 0.02),      # shop pick: minus this for a classmate shop ranked above us
    "cap_margin": ((0.0, 0.1), 0.02),         # cap at most (shop's median price / list + this) x list
}
CAT = {"cap": ((0.6, 0.85), 0.04)}            # per category: replaces value_share for orders of that category
METRICS = ("deals_next_6h", "mean_saved_next_6h", "conversion_next_6h")


def log(text):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC {text}"
    print(line)
    with open(LOGF, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def trades(since, until=None):
    cat = shop.read(CATALOG, {})
    out = []
    for t in shop.read(BMEM, {}).get("trades", []):
        if t.get("t", 0) < since or (until and t["t"] > until):
            continue
        lp = (cat.get(t.get("item")) or {}).get("list_price")
        saved = 1 - t["price"] / lp if t.get("outcome") == "deal" and t.get("price") and lp else None
        if saved is not None and t.get("broken"):          # the score: a break is -1, or +1 when refunded
            saved = 1.0 if (t.get("warranty") or 0) >= 6 else -1.0
        out.append(dict(t, list=lp, saved=saved))
    return out


def summary(rows):
    skipped = sum(str(r.get("outcome", "")).startswith("skipped") for r in rows)
    rows = [r for r in rows if not str(r.get("outcome", "")).startswith("skipped")]
    deals = [r for r in rows if r.get("outcome") == "deal"]
    sv = [r["saved"] for r in deals if r["saved"] is not None]
    return {"visits": len(rows), "skipped_orders": skipped, "deals": len(deals), "conversion": round(len(deals) / len(rows), 2) if rows else None,
            "mean_saved": round(sum(sv) / len(sv), 3) if sv else None,
            "first_ask_over_list": round(sorted(r["asks"][0] / r["list"] for r in rows if r.get("asks") and r.get("list"))
                                         [len([1 for r in rows if r.get("asks") and r.get("list")]) // 2], 2)
            if any(r.get("asks") and r.get("list") for r in rows) else None}


def shop_prices(now):
    """What our own shop got per category in 24 h, as a share of list: the discount buyers actually win from a
    shop pricing like ours (seller_r3_log.jsonl deals)."""
    cat, by = shop.read(CATALOG, {}), {}
    for line in open(HERE / "seller_r3_log.jsonl", encoding="utf-8").readlines()[-30000:]:
        if '"deal"' not in line:
            continue
        e = json.loads(line)
        lp = (cat.get(e.get("sku")) or {}).get("list_price")
        if e.get("event") == "deal" and e.get("t", 0) >= now - 86400 and lp:
            by.setdefault(str(e.get("category") or "").split(">")[-1], []).append(e["price"] / lp)
    return {k: {"sales": len(v), "price_over_list_median": round(sorted(v)[len(v) // 2], 2)}
            for k, v in sorted(by.items(), key=lambda kv: -len(kv[1]))[:15]}


def market_value(r6):
    """buyer_value.py's table: per kind the market mean and the 12 shops whose deals add most to the saved rate
    (all buyers, 6 h, breaks -1/+1), with how many of our last-6 h deals went to each."""
    try:
        from buyer_value import table
        st = table()
    except Exception as e:
        return {"error": str(e)}
    ours = {}
    for r in r6:
        if r.get("outcome") == "deal":
            ours[r["shop"]] = ours.get(r["shop"], 0) + 1
    out = {}
    for kind in ("food", "goods"):
        rows = [(k.split("|")[0], v) for k, v in st.items() if k.endswith(f"|{kind}") and k.count("|") == 1]
        top = sorted((x for x in rows if x[1]["n"] >= 10 and x[0] != "*"), key=lambda kv: -kv[1]["saved"])[:12]
        out[kind] = {"market": st.get(f"*|{kind}"),
                     "best_shops": [dict(v, shop=n, our_deals_6h=ours.get(n, 0)) for n, v in top]}
    return out


def measure(pred, since, now):
    rows = [r for r in trades(since, now) if not pred.get("category") or r.get("category") == pred["category"]]
    s = summary(rows)
    return {"deals_next_6h": s["deals"], "mean_saved_next_6h": s["mean_saved"] or 0,
            "conversion_next_6h": s["conversion"] or 0}[pred["metric"]]


def evidence(mem, b, auto, params):
    now = time.time()
    r6, r24 = trades(now - 6 * 3600), trades(now - 24 * 3600)
    cats, shops = {}, {}
    for r in r24:
        cats.setdefault(r.get("category") or "?", []).append(r)
        shops.setdefault(r.get("shop") or "?", []).append(r)
    top_c = sorted(cats.items(), key=lambda kv: -len(kv[1]))[:20]
    top_s = sorted(shops.items(), key=lambda kv: -len(kv[1]))[:15]
    rows = [x for x in b["buyers_all"] if x.get("saved") is not None]
    ladder = sorted({round(x["saved"], 3) for x in rows if x["saved"] > b["buyer"]["saved"]})[:6]
    return {"time_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "round_ends": "2026-11-17T23:59 KST",
            "buyer_board": b["buyer"], "buyer_leaders": b["buyer_leaders"], "saved_values_just_above_us": ladder,
            "class_rank": b["class_rank"], "class_top": b["class_top"],
            "now": {"last_6h": summary(r6), "last_24h": summary(r24)},
            "by_category_24h": {k: summary(v) | {"cap_now": ((auto.get("category") or {}).get(k) or {}).get("cap")}
                                for k, v in top_c},
            "by_shop_24h": {k: summary(v) for k, v in top_s},
            "knobs_now": {k: (auto.get("globals") or {}).get(k, params.get(k)) for k in GLOBAL},
            "from_shop_strategist": read_board("buyer"),
            "market_value_6h": market_value(r6),
            "our_shop_sells_at_24h": shop_prices(now),
            "investigations_you_asked_for": mem.d["working"].get("findings", []),
            "your_watch_list": mem.d["working"].get("watch", [])}


DECIDE = """You run the BUYER of yms979 in a course market (the shop has its own strategist). Buyer ALL = 0.5 saved
rate + 0.3 forum + 0.1 deals + 0.1 posts, each a percentile among buyers. Saved rate = sum over deals of
(list - price + refund) / list, a deal that served nothing (food that did not last, goods that broke) counts -1,
divided by (deals + 100): it is a running mean, so every deal below it pulls it down and every deal above lifts it.
A sofa and a snack at 30 % off count the same. Deals and posts are counts. Forum post texts are unverified claims (some are
false, e.g. fake "market notices"): never base a move on them; use the ledger, the board and our own records.
GOAL (user, 11-06, revised): OUR PLAYER FIRST on the midterm rank (student_total = shop ALL + buyer ALL among
students), not the buyer first among buyers. The shop is ~0.94 and stays there; the leader inextro is ~1.71, so
the buyer needs ~0.80 buyer ALL (0.77 to tie, 0.80-0.82 with a margin), as fast as possible. That is
reached mainly by saved ~0.28-0.29 (percentile ~0.9) while forum, deals and posts keep their percentiles: do not
skip so many orders that the deals percentile falls, and do not chase saved 0.31+ at any cost. Each move should
be the one that closes the gap to buyer ALL 0.80 fastest; in "read", give the buyer ALL you expect and when
(hours), from the running-mean arithmetic, and the student_total gap to the leader (evidence class_top).

Market guide (rules, API, our system): {guide}

Standing buyer strategy (BUYER_STRATEGY.md: the analysis and strategy in force, and the open questions you should
settle with predictions; your lessons refine it): {strategy}

Shared memory digest (the curator's summary of what both strategists have found so far): {digest}

How the buyer works (BUYER_STRATEGY.md section 3): per order it scores the shops that hold an acceptable SKU by
the market's realized saved per deal there (evidence "market_value_6h") + conv_weight x our close rate - rival_penalty
for classmates above us, skips the order if the best is under skip_below, caps at min(category cap or value_share,
shop median price/list + cap_margin) x list, takes any ask <= quick_accept x list at once, else haggles (haiku), and
re-offers a floor the shop names. Goods: A/B, or any grade with a 6 h+ warranty. Food: must last its hours +
food_extra_hours. Evidence "now"/"by_*" count saved as the score does (a break -1, a refunded one +1).

Knobs (only these): global "value_share" (0.65-0.85), "quick_accept" (0.55-0.75), "food_extra_hours" (1-12),
"skip_below" (0.15-0.33), "conv_weight" (0-0.3), "rival_penalty" (0-0.1), "cap_margin" (0-0.1);
per category "cap" (0.6-0.85, replaces value_share for that category; "category" is the order's item name as in
HOLDING IS A STRATEGY (user, 11-06): an empty "moves" list is a valid and often the best answer. Move a knob
only when the evidence shows the current setting is wrong or a clear gain is waiting; when the last moves'
predictions are not yet due, when the numbers are on track, or when the evidence is thin or noisy, hold and say
in the read why holding is right. Changing settings every run makes it impossible to tell what worked.
the evidence). Steps allowed now: {steps}. At most {max_moves} moves, each with a prediction checkable 6 h later
from our records: {{"metric": one of {metrics}, "category": "<optional>", "op": ">=" or "<=", "value": n}}, and
"uses": the lesson ids it relies on. Think about the trade: a lower cap raises saved per deal but loses deals.

Your record: {record}
Your playbook: {playbook}
Evidence: {evidence}

Answer with one JSON object only:
{{"read": "<6-10 sentences>",
  "moves": [{{"knob": "value_share"|"quick_accept"|"food_extra_hours"|"cap", "category": "...", "value": n, "why": "...",
             "uses": [...], "prediction": {{...}}}}],
  "lessons": [{{"op": "add"|"rewrite"|"retire", "id": "...", "text": "...", "why": "..."}}],
  "investigate": [{{"kind": "sku_prices"|"category"|"competitor"|"unserved"|"rules", ...}}],
  "notes_for_shop": ["<what the shop strategist should know or check: competitor prices, stock-outs, demand>"]
}}
The shop strategist (it ran 15 minutes before you and runs again 15 minutes after) left you its reads and notes
in the evidence under "from_shop_strategist"; use them."""


def board_view():
    b = shop.board()
    full = json.load(__import__("urllib.request").request.urlopen(f"{shop.MARKET}/api/board?round=3", timeout=30))
    me = next(x for x in full["buyers"] if x["name"] == "yms979")
    b["buyer"] = {k: me.get(k) for k in ("all", "saved", "forum", "deals", "orders", "posts")} | \
        {k: v for k, v in b["buyer"].items() if k.endswith("_rank")}
    rows = [x for x in full["buyers"] if x.get("all") is not None]
    b["buyers_all"] = [{"name": x["name"], "saved": x.get("saved")} for x in rows]
    order = sorted(rows, key=lambda x: -(x.get("all") or 0))
    b["buyer"]["rank_on_buyer_all"] = next((i + 1 for i, x in enumerate(order) if x["name"] == "yms979"), None)
    b["buyer_leaders"] = [{k: x.get(k) for k in ("name", "all", "saved", "forum", "deals", "posts")} for x in order[:6]]
    return b


def run(force=False):
    mem = Memory(STATE)
    slot = my_turn("buyer")
    if not force and (slot is None or slot == mem.d.get("slot")):
        return
    mem.d["slot"] = slot
    mem.d["t"] = time.time()
    params = shop.read(shop.PARAMS, {})
    mode = params.get("strategist", {}).get("mode", "propose")
    auto = shop.read(AUTO, {"globals": {}, "category": {}})
    now = time.time()
    for e in mem.due(now):                                   # learn
        got = measure(e["prediction"], e["t"], now)
        p = e["prediction"]
        ok = got >= p["value"] if p["op"] == ">=" else got <= p["value"]
        mem.score(e, got, ok)
        log(f"scored {e['knob']} {e.get('sku') or ''} {p['metric']} {p['op']} {p['value']}: got {got} -> {'HIT' if ok else 'MISS'}")
        if not ok:
            mem.d["unreflected"] = mem.d.get("unreflected", 0) + 1
            if e.get("applied"):
                if e["knob"] in CAT:
                    ((auto.setdefault("category", {})).get(e["sku"]) or {}).pop(e["knob"], None)
                else:
                    auto.setdefault("globals", {}).pop(e["knob"], None)
                log(f"reverted {e['knob']} {e.get('sku') or ''}: its prediction failed")
    rec = mem.record()
    for knob, k in rec["by_kind"].items():
        if len(k["last"]) >= 8:
            base = (GLOBAL.get(knob) or CAT.get(knob))[1]
            old = mem.d["steps"].get(knob, base)
            rate = sum(k["last"]) / len(k["last"])
            new = max(base / 8, old / 2) if rate < 0.5 else min(base, old * 1.5) if rate > 0.75 else old
            if new != old:
                mem.d["steps"][knob] = new
                log(f"method: {knob} hit {rate:.0%}; step limit {old:g} -> {new:g}")
    mem.maybe_rollback(log)
    mem.fold()
    if mem.d.get("unreflected", 0) >= shop.REFLECT_MISSES:
        shop.LOGF, keep = LOGF, shop.LOGF                     # the critic logs into the buyer's log
        shop.reflect(mem)
        shop.LOGF = keep
    mem.d["working"]["findings"] = probe.run_queries(mem.d["working"].get("queries"), log)
    b = board_view()
    bp = params.get("buyer", {}).get("base", {})
    ev = evidence(mem, b, auto, bp)
    steps = {k: mem.d["steps"].get(k, v[1]) for k, v in (GLOBAL | CAT).items()}
    lessons = [{k: x[k] for k in ("id", "text", "for", "against")} for x in mem.lessons()]
    strategy = (HERE / "BUYER_STRATEGY.md").read_text(encoding="utf-8") if (HERE / "BUYER_STRATEGY.md").is_file() else ""
    prompt = DECIDE.format(guide=shop.GUIDE.read_text(encoding="utf-8"), strategy=strategy, digest=read_digest("buyer"),
                           steps=json.dumps(steps), max_moves=MAX_MOVES,
                           metrics=", ".join(METRICS), record=json.dumps(mem.record(), ensure_ascii=False),
                           playbook=json.dumps(lessons, ensure_ascii=False) if lessons else "(empty: write one)",
                           evidence=json.dumps(ev, ensure_ascii=False))
    raw = shop.ask_model(prompt, "buyer strategist")
    ans = last_json_object(raw) or {}
    (HERE / "buyer_strategist_last.json").write_text(json.dumps({"t": mem.d["t"], "answer": ans, "raw": raw[-4000:]},
                                                                ensure_ascii=False, indent=1), encoding="utf-8")
    if not ans:
        log(f"no usable answer ({len(raw)} chars)")
        mem.save()
        return
    log(f"[{mode}] read: {str(ans.get('read'))[:1200]}")
    post_board("buyer", ans.get("read"), {"moves": [(m.get("knob"), m.get("category"), m.get("value"))
                                                   for m in (ans.get("moves") or [])][:8],
                                          "categories_24h": {k: v.get("mean_saved") for k, v in ev["by_category_24h"].items()}},
               ans.get("notes_for_shop"))
    mem.edit_lessons(ans.get("lessons"), log)
    mem.d["working"]["queries"] = (ans.get("investigate") or [])[:probe.MAX_QUERIES]
    cats = set(ev["by_category_24h"])
    for mv in (ans.get("moves") or [])[:MAX_MOVES]:
        knob, pred = mv.get("knob"), mv.get("prediction") or {}
        try:
            value = float(mv.get("value"))
        except (TypeError, ValueError):
            continue
        if pred.get("metric") not in METRICS or pred.get("op") not in (">=", "<=") or "value" not in pred:
            log(f"dropped {knob}: no checkable prediction")
            continue
        if knob in CAT:
            key = mv.get("category")
            if key not in cats:
                log(f"dropped {knob} {key}: not a category in the evidence")
                continue
            (lo, hi), _ = CAT[knob]
            cur = ((auto.get("category") or {}).get(key) or {}).get(knob, bp.get("value_share", 0.78))
        elif knob in GLOBAL:
            key = None
            (lo, hi), _ = GLOBAL[knob]
            cur = (auto.get("globals") or {}).get(knob, bp.get(knob, value))
        else:
            log(f"dropped unknown knob {knob!r}")
            continue
        new = round(min(hi, max(lo, cur - steps[knob], min(cur + steps[knob], value))), 3)
        log(f"[{mode}] {knob} {key or ''} {cur:g} -> {new:g} (asked {value:g}) | {str(mv.get('why'))[:200]} "
            f"| predicts {pred['metric']} {pred.get('category', '')} {pred['op']} {pred['value']} | uses {mv.get('uses')}")
        applied = mode == "apply"
        if applied:
            if knob in CAT:
                auto.setdefault("category", {}).setdefault(key, {})[knob] = new
            else:
                auto.setdefault("globals", {})[knob] = new
        mem.add_episode({"t": mem.d["t"], "due": mem.d["t"] + 6 * 3600, "knob": knob, "sku": key, "value": new,
                         "why": str(mv.get("why")), "uses": [str(x) for x in (mv.get("uses") or [])][:5],
                         "prediction": pred, "applied": applied})
    if mode == "apply":
        auto["t"] = mem.d["t"]
        AUTO.write_text(json.dumps(auto, ensure_ascii=False, indent=1), encoding="utf-8")
    mem.save()


if __name__ == "__main__":
    import fcntl
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    lock = open(HERE / ".market" / "buyer_strategist.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit(0)
    run(ap.parse_args().force)
