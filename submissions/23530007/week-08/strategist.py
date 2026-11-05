"""Strategist: watches the market and our own records, and tunes the shop and the buyer (user's design, 11-05).

    python strategist.py [--force]        (watch_round.sh runs it every 15 min; it works once per EVERY seconds)

Loop, once an hour:
  1. memory      product_memory.update(): per-SKU purchases, sales, margins, holding time, walk-aways, sold-out
                 asks, stars and breaks (hourly buckets, 48 h)
  2. evidence    a data pack: score components and ranks on the board, our stock by SKU with its age, each
                 SKU's own and market sales rates, market stock and shops holding it, cost surge, plus the
                 strategist's own record (its past predictions, which came true, its playbook)
  3. analysis    a strong model (MODEL, opus) reads the pack and answers in JSON: a market read, per-SKU moves on
                 a fixed set of knobs, a measurable prediction for every move, and its revised playbook
  4. checks      code keeps only whitelisted knobs, clamps each to its bounds and to the step allowed for its
                 kind (smaller after wrong calls), and drops moves without a prediction
  5. act         mode "propose" (params.json strategist.mode) only logs; "apply" writes params_auto.json, which
                 policy.py lays over the shop's and buyer's parameters (seller ask per SKU, restock cover per SKU)
  6. learn       due predictions are scored from product memory. Per kind of move the hit rate sets how far the
                 next moves may go (below 50 % over 8+: steps halve; above 75 %: they grow back), applied moves
                 whose prediction failed are reverted, and the misses go back to the model, which must revise
                 its playbook (how it reads the market) in its next answer.
Everything goes to logs/strategist.log; state (predictions, scorecard, playbook, step limits) to
strategist_memory.json.
"""
import argparse
import json
import shlex
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import product_memory as pm
from harness import last_json_object

HERE = Path(__file__).resolve().parent
STATE = HERE / "strategist_memory.json"
AUTO = HERE / "params_auto.json"
LOGF = HERE / "logs" / "strategist.log"
PARAMS = HERE / "params.json"
MARKET = "https://market.ourolabs.ai"
EVERY = 3600 - 120
MODEL_CMD = "claude -p --model opus --tools '' --setting-sources '' --no-session-persistence"
MODEL_TIMEOUT = 600
TOP_SKUS = 45

# knob: (bounds, default step) ; per-SKU factors multiply what the seller asks / how much stock restock holds
KNOBS = {
    "ask": ((0.85, 1.05), 0.05),      # seller's ask for this SKU x factor (floor at cost stays)
    "cover": ((0.0, 2.0), 0.5),       # restock's target stock for this SKU x factor (0 = stop buying it)
}
GLOBAL = {                            # whitelisted shared parameters: (role, key, bounds, step)
    "sku_cover_hours": ("shop", (2, 8), 1),
    "market_share": ("shop", (0.05, 0.3), 0.05),
    "quick_accept": ("buyer", (0.7, 0.85), 0.03),
}
METRICS = ("sold_next_6h", "no_stock_next_6h", "cancels_next_6h", "margin_next_6h")


def read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def log(text):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC {text}"
    print(line)
    LOGF.parent.mkdir(exist_ok=True)
    with open(LOGF, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def board():
    with urllib.request.urlopen(f"{MARKET}/api/board?round=3", timeout=30) as r:
        b = json.load(r)
    shops = [s for s in b["shops"] if s.get("deals")]
    buyers = [x for x in b["buyers"] if x.get("all") is not None]

    def rank(rows, key, name="yms979"):
        order = sorted(rows, key=lambda x: -(x.get(key) or 0))
        return next((i + 1 for i, x in enumerate(order) if x["name"] == name), None), len(rows)
    s = next(x for x in shops if x["name"] == "yms979")
    u = next(x for x in buyers if x["name"] == "yms979")
    players = sorted((x for x in b["players"] if x.get("student")), key=lambda x: -(x.get("student_total") or 0))
    return {
        "class_rank": next((i + 1 for i, x in enumerate(players) if x["player"] == "yms979"), None),
        "class_top": [(x["player"], round(x["student_total"], 3)) for x in players[:6]],
        "shop": {k: s.get(k) for k in ("all", "book", "revenue", "stars", "deals", "profit", "stock_cost", "refunds")}
        | {f"{k}_rank": rank(shops, k) for k in ("book", "revenue", "stars")},
        "buyer": {k: u.get(k) for k in ("all", "surplus", "forum", "deals", "orders", "posts")}
        | {f"{k}_rank": rank(buyers, k) for k in ("surplus", "forum", "deals", "posts")},
        "top_shops": [{k: x.get(k) for k in ("name", "all", "book", "revenue", "stars", "deals", "stock_cost")}
                      for x in sorted(shops, key=lambda x: -(x.get("all") or 0))[:6]],
    }


def evidence(m, st):
    now = time.time()
    inv = read(HERE / ".market" / "inventory.json", {})
    rs = read(HERE / "restock_state.json", {})
    auto = read(AUTO, {})
    stock, oldest = {}, {}
    for u in inv.get("units", []):
        stock[u["sku"]] = stock.get(u["sku"], 0) + 1
        a = (now - (u.get("supplied_at") or now)) / 3600
        oldest[u["sku"]] = max(oldest.get(u["sku"], 0), round(a, 1))
    surge = {x["sku"]: x.get("factor") for x in inv.get("surge") or [] if x.get("sku")}
    mkt = rs.get("sku", {})
    rows = []
    for sku, s in m["skus"].items():
        w6, w24 = pm.window(m, sku, 6), pm.window(m, sku, 24)
        weight = stock.get(sku, 0) * (s.get("list_price") or 0) + w24["revenue"]
        if not weight and sku not in mkt:
            continue
        rows.append((weight, {
            "sku": sku, "name": str(s.get("name"))[:40], "kind": s.get("kind"), "list": s.get("list_price"),
            "category": str(s.get("category") or "").split(">")[-1],
            "stock": stock.get(sku, 0), "oldest_h": oldest.get(sku), "sold_6h": w6["sold"], "sold_24h": w24["sold"],
            "margin_24h": w24["margin"], "margin_per_unit": round(w24["margin"] / w24["sold"]) if w24["sold"] else None,
            "avg_hold_h": round(w24["hold_hours"] / w24["sold"], 1) if w24["sold"] else None,
            "walkaways_6h": w6["cancels"] + w6["lost"], "sold_out_asks_6h": w6["no_stock"],
            "stars": round(w24["stars_sum"] / w24["stars_n"], 2) if w24["stars_n"] else None,
            "broken_24h": w24["broken"],
            "market_sales_h": round(mkt.get(sku, 0) * 0.1155, 1),          # decayed count x ln2 / 6 h
            "market_units": (rs.get("mkt_units") or {}).get(sku, 0), "market_shops": (rs.get("mkt_shops") or {}).get(sku, 0),
            "cost_surge": surge.get(sku), "knobs_now": (auto.get("sku") or {}).get(sku),
        }))
    rows = [r for _, r in sorted(rows, key=lambda x: -x[0])[:TOP_SKUS]]
    return {"time_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "round_ends": "2026-11-17T23:59 KST",
            "cash": inv.get("balance"), "stock_cost": sum(u.get("cost") or 0 for u in inv.get("units", [])),
            "board": board(), "skus": rows,
            "globals_now": {k: (auto.get("globals") or {}).get(k) for k in GLOBAL},
            "my_record": {"scorecard": st["score"], "step_limits": st["steps"],
                          "recent_misses": st["misses"][-10:], "playbook": st["playbook"]}}


PROMPT = """You are the strategist of an agent shop and buyer (yms979) in a course market. Score: shop ALL =
0.5 book (cash + stock at cost) + 0.3 revenue + 0.2 stars; buyer ALL = 0.5 surplus + 0.3 forum + 0.1 deals +
0.1 posts; each a percentile among players. The midterm ranks students by shop ALL + buyer ALL at the end.
Rules that matter: supply windows every 20 min (18 lines, 20 units a line); a SKU's cost surges 0.2 % a unit past
50 units/h bought market-wide; goods pay 1 %/day storage; food past its date is lost; no warranty now.

Read the evidence widely and in depth: which products carry our revenue and margin, where stock sits idle or runs
out, where buyers walk away (price) or ask for sold-out items (stock), how competitors stock and price, how our
ranks move. Then choose moves on these knobs only:
  per SKU  "ask"   x the seller's ask for that SKU, bounds 0.85-1.05 (lower: buyers walk away; higher: it sells out)
           "cover" x the stock restock holds of that SKU, bounds 0-2 (0 stops buying it)
  global   "sku_cover_hours" (2-8), "market_share" (0.05-0.3), "quick_accept" (buyer, 0.7-0.85)
Each move must carry a prediction the code can check from our records 6 h later:
  {{"metric": one of {metrics}, "sku": "...", "op": ">=" or "<=", "value": number}}
Steps you may take now (bigger moves are clipped): {steps}.

Your record so far (predictions scored): {record}
Your playbook (how you read this market; revise it, especially where your misses show it was wrong):
{playbook}

Evidence:
{evidence}

Answer with one JSON object only:
{{"market_read": "<5-10 sentences>",
  "moves": [{{"knob": "ask"|"cover", "sku": "...", "value": <factor>, "why": "...", "prediction": {{...}}}},
            {{"knob": "sku_cover_hours"|"market_share"|"quick_accept", "value": <number>, "why": "...", "prediction": {{...}}}}],
  "playbook": ["<lesson>", ...]   (at most 15 short lessons; drop or rewrite the ones your misses contradict)
}}"""


def ask_model(prompt):
    with tempfile.TemporaryDirectory() as empty:
        try:
            p = subprocess.run(shlex.split(MODEL_CMD), input=prompt, cwd=empty, capture_output=True, text=True,
                               timeout=MODEL_TIMEOUT)
            return p.stdout.strip()
        except (subprocess.TimeoutExpired, OSError) as e:
            log(f"model call failed: {e!r}")
            return ""


def measure(m, pred, since, now):
    w = pm.window(m, pred["sku"], max(1, round((now - since) / 3600)), now)
    return {"sold_next_6h": w["sold"], "no_stock_next_6h": w["no_stock"],
            "cancels_next_6h": w["cancels"] + w["lost"], "margin_next_6h": w["margin"]}[pred["metric"]]


def learn(m, st, auto):
    """Score due predictions; adjust step limits per kind; revert applied moves whose prediction failed."""
    now, keep = time.time(), []
    for p in st["open"]:
        if now < p["due"]:
            keep.append(p)
            continue
        got = measure(m, p["prediction"], p["t"], now)
        ok = got >= p["prediction"]["value"] if p["prediction"]["op"] == ">=" else got <= p["prediction"]["value"]
        sc = st["score"].setdefault(p["knob"], {"n": 0, "hit": 0, "last": []})
        sc["n"] += 1
        sc["hit"] += ok
        sc["last"] = (sc["last"] + [int(ok)])[-12:]
        log(f"scored {p['knob']} {p.get('sku', '')} {p['prediction']['metric']} {p['prediction']['op']} "
            f"{p['prediction']['value']}: got {got} -> {'HIT' if ok else 'MISS'}")
        if not ok:
            st["misses"].append({"knob": p["knob"], "sku": p.get("sku"), "value": p["value"], "why": p["why"][:160],
                                 "predicted": p["prediction"], "got": got})
            if p.get("applied") and p["knob"] in KNOBS and p.get("sku"):
                (auto.setdefault("sku", {}).setdefault(p["sku"], {})).pop(p["knob"], None)
                log(f"reverted {p['knob']} on {p['sku']}: its prediction failed")
            elif p.get("applied") and p["knob"] in GLOBAL:
                (auto.setdefault("globals", {})).pop(p["knob"], None)
                log(f"reverted global {p['knob']}: its prediction failed")
    st["open"] = keep
    st["misses"] = st["misses"][-40:]
    for knob, sc in st["score"].items():                 # the analysis method's own correction: how far it may go
        if len(sc["last"]) >= 8:
            rate = sum(sc["last"]) / len(sc["last"])
            base = KNOBS[knob][1] if knob in KNOBS else GLOBAL[knob][2]
            old = st["steps"].get(knob, base)
            new = max(base / 8, old / 2) if rate < 0.5 else min(base, old * 1.5) if rate > 0.75 else old
            if new != old:
                st["steps"][knob] = new
                log(f"method: {knob} hit {rate:.0%} of its last {len(sc['last'])} calls; step limit {old:g} -> {new:g}")


def run(force=False):
    st = read(STATE, {})
    for k, v in (("t", 0), ("open", []), ("score", {}), ("misses", []), ("playbook", []), ("steps", {})):
        st.setdefault(k, v)
    if not force and time.time() - st["t"] < EVERY:
        return
    st["t"] = time.time()
    mode = read(PARAMS, {}).get("strategist", {}).get("mode", "propose")
    m = pm.update()
    auto = read(AUTO, {"sku": {}, "globals": {}})
    learn(m, st, auto)
    ev = evidence(m, st)
    steps = {k: st["steps"].get(k, KNOBS[k][1] if k in KNOBS else GLOBAL[k][2]) for k in list(KNOBS) + list(GLOBAL)}
    prompt = PROMPT.format(metrics=", ".join(METRICS), steps=json.dumps(steps), record=json.dumps(st["score"]),
                           playbook="\n".join(f"- {x}" for x in st["playbook"]) or "(empty: write one)",
                           evidence=json.dumps(ev, ensure_ascii=False))
    raw = ask_model(prompt)
    ans = last_json_object(raw) or {}
    (HERE / "strategist_last.json").write_text(json.dumps({"t": st["t"], "answer": ans, "raw": raw[-4000:]},
                                                          ensure_ascii=False, indent=1), encoding="utf-8")
    if not ans:
        log(f"no usable answer from the model ({len(raw)} chars); nothing changed")
        STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        return
    log(f"[{mode}] market read: {str(ans.get('market_read'))[:900]}")
    if isinstance(ans.get("playbook"), list) and ans["playbook"]:
        st["playbook"] = [str(x)[:200] for x in ans["playbook"][:15]]
    skus = {r["sku"] for r in ev["skus"]}
    for mv in ans.get("moves") or []:
        knob, pred = mv.get("knob"), mv.get("prediction") or {}
        try:
            value = float(mv.get("value"))
        except (TypeError, ValueError):
            continue
        if pred.get("metric") not in METRICS or pred.get("op") not in (">=", "<=") or "value" not in pred:
            log(f"dropped {knob} {mv.get('sku')}: no checkable prediction")
            continue
        if knob in KNOBS:
            sku = mv.get("sku")
            if sku not in skus:
                log(f"dropped {knob} {sku}: not in the evidence")
                continue
            lo, hi = KNOBS[knob][0]
            cur = ((auto.get("sku") or {}).get(sku) or {}).get(knob, 1.0)
        elif knob in GLOBAL:
            sku = pred.get("sku")
            lo, hi = GLOBAL[knob][1]
            base = read(PARAMS, {}).get(GLOBAL[knob][0], {}).get("base", {}).get(knob, value)
            cur = (auto.get("globals") or {}).get(knob, base)
        else:
            log(f"dropped unknown knob {knob!r}")
            continue
        step = steps[knob]
        new = round(min(hi, max(lo, cur - step, min(cur + step, value))), 3)
        pred["sku"] = pred.get("sku") or sku
        log(f"[{mode}] {knob} {sku or ''} {cur:g} -> {new:g} (asked {value:g}) | {str(mv.get('why'))[:200]} "
            f"| predicts {pred['metric']} {pred['op']} {pred['value']}")
        applied = mode == "apply"
        if applied:
            if knob in KNOBS:
                auto.setdefault("sku", {}).setdefault(sku, {})[knob] = new
            else:
                auto.setdefault("globals", {})[knob] = new
        st["open"].append({"t": st["t"], "due": st["t"] + 6 * 3600, "knob": knob, "sku": sku, "value": new,
                           "why": str(mv.get("why")), "prediction": pred, "applied": applied})
    if mode == "apply":
        auto["t"] = st["t"]
        AUTO.write_text(json.dumps(auto, ensure_ascii=False, indent=1), encoding="utf-8")
    STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    import fcntl
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    lock = open(HERE / ".market" / "strategist.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)     # one run at a time (a model call can take minutes)
    except OSError:
        raise SystemExit(0)
    run(ap.parse_args().force)
