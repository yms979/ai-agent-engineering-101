"""Round 3 restock hook: what to buy for the shop, every 60 s.

    python restock.py --state .market          (agent.json "restock"; prints {"buy": [{"sku", "qty"}, ...]})

The market says neither what sells nor who wants what, so this learns it from two places:
  - the public ledger: every settled deal names the SKU sold (GET /api/ledger?after=N, read incrementally);
    counted per category with a half-life of HALF_LIFE hours, so the target follows what sells now
  - the shop's own log (seller_r3_log.jsonl): its deals, and buyers it had to turn away (no stock)

The limits come from policy.py (params.json) by the shop's situation: launch, cash tight (no expansion),
flush (deeper stock, dearer goods), wind-down (one unit per category, small stock), liquidation (buy nothing).

Per category the target stock is 1 unit (be in every order's shop list), plus up to `extra` more by the
category's share of recent market sales and our own sales. A category gets the SKU that sold most in it,
or, with no sales seen yet, the SKU nearest the category's median price (orders accept 0.5-1.5x a reference
price, so the median fits most). Limits, so stock never eats the shop:
  - cash kept back: keep_cash; stock at cost at most max_stock_share of equity (balance + stock at cost),
    so stock grows with the shop (unsold stock is a loss, goods pay 1 %/day)
  - goods listed above max_goods_price are skipped (one unit ties up too much cash), except up to
    max_goods_price_hot in a category with at least 30 % of the top category's market sales (Round 3 opened with
    beds/mattresses at 58 sales in 15 min, mean 209k, up to 392k)
  - food that keeps under 72 h: at most 1 unit at a time (it is thrown away at cost when it expires)
  - at most 3 units per line, 10 lines per run (the kit's limit); urgent lines first: sold-out categories
    with sales, then categories we do not cover yet
Supply windows (rule added when Round 3 opened, 11-03): supply opens once every 20 minutes for 3 minutes
(inventory.json "restock": open, opens_at, closes_at). Outside a window, more than LEAD seconds before it opens,
this buys nothing: the kit would spend up to 90 s per line waiting. In a window each category's target also
covers the next gap: our sales rate there x GAP_HOURS x 1.5, up to MAX_QTY units per line.
Within clear_window_hours of the end a category is only refilled up to what our sales rate in it can sell
before the end (policy.clearance's rule, seen from the buying side).
Below the bankruptcy line with (almost) nothing left to sell it files ("bankrupt": true).
Forum demand: a post that asks for something ("구합니다", "찾습니다", "wanted", "looking for", ...) and names a
category from demand.json (its item, e.g. 김치) counts as half a sale's worth of heat for that category, decaying the
same way. Matching is plain text in code: forum posts never reach the model, so nothing written there can
instruct our agents.
It also keeps the shop's reputation for policy.py: the average stars of our newest Round 3 reviews on
/api/shops (the market shows the last 10), in restock_state.json "rep".
State (ledger position, decayed counts) in restock_state.json; every run logs one line to stderr.
"""
import argparse
import json
import math
import os
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

from policy import shop_params

HERE = Path(__file__).resolve().parent
MARKET = "https://market.ourolabs.ai"
STATE = Path(os.environ.get("RESTOCK_STATE", HERE / "restock_state.json"))
SHOP_NAME = os.environ.get("SHOP_NAME", "yms979")
WANTED = re.compile(r"구합니다|구해요|구함|찾습니다|찾아요|삽니다|사고 ?싶|필요합니다|wanted|\bwtb\b|looking for|need(?:s|ed)? (?:a|some)", re.I)
SALES_LOG = Path(os.environ.get("SELLER_LOG", HERE / "seller_r3_log.jsonl"))
HALF_LIFE = 6.0
LEAD = 60                    # seconds before a window opens that a run may already send its lines
GAP_HOURS = 0.4              # until the window after this one: 20 min apart, 3 min open, slots move a little
MAX_QTY = 20                 # the market's limit per supply line
MAX_LINES = 18               # one supply call per 10 s fills a 3-minute window (our kit copy takes 18 per run)
RECENT = 3600                # seconds of our own sales that set the current rate per category              # hours
COST_GUESS = 0.6             # cost as a share of list price before a unit arrives (A 55-75 %, B 40-60 %, C 25-45 %)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def ledger_after(seq):
    """Settled deals after seq: [(seq, ts, sku)]. Reads at most 5 pages of 500 per run."""
    out, last = [], seq
    for _ in range(5):
        url = f"{MARKET}/api/ledger?after={last if last is not None else -500}"
        with urllib.request.urlopen(url, timeout=20) as r:
            page = json.load(r)
        if not page:
            break
        for e in page:
            last = e["seq"]
            d = e.get("data") or {}
            sku = d.get("item") or d.get("sku")
            if e.get("kind") == "settle" and isinstance(sku, str):
                out.append((e["seq"], e.get("ts") or time.time(), sku))
        if len(page) < 500:
            break
    return out, last


def decay(counts, hours):
    k = 0.5 ** (hours / HALF_LIFE)
    return {c: v * k for c, v in counts.items() if v * k > 0.01}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=".market")
    md = Path(ap.parse_args().state)
    inv = read_json(md / "inventory.json", {})
    regime, p, sig = shop_params()
    win = inv.get("restock") or {}
    shut = (not win.get("open") and win.get("opens_at") and win["opens_at"] - time.time() > LEAD)
    if inv.get("round", 0) < 3 or inv.get("balance") is None or not p["buy"] or shut:
        if shut:                                       # the ledger is read again on the next run in a window
            print(f"[restock] supply closed, opens in {win['opens_at'] - time.time():.0f} s; nothing to buy now",
                  file=sys.stderr)
        if inv.get("round", 0) >= 3:
            print(f"[restock] {regime}: not buying", file=sys.stderr)
        print(json.dumps({"buy": []}))
        return
    catalog = read_json(md / "catalog.json", {})
    demand = read_json(md / "demand.json", [])
    st = read_json(STATE, {"seq": None, "t": time.time(), "cat": {}, "sku": {}, "own_cat": {}, "log_pos": 0})
    now = time.time()
    hours = (now - st["t"]) / 3600
    st["cat"], st["sku"], st["own_cat"] = decay(st["cat"], hours), decay(st["sku"], hours), decay(st["own_cat"], hours)
    st["forum_cat"] = decay(st.get("forum_cat", {}), hours)
    seen = set(st.get("forum_seen", []))                    # forum demand: "wanted" posts naming a category
    for post in read_json(md / "forum.json", []):
        if not isinstance(post, dict) or post.get("id") in seen or post.get("author") == SHOP_NAME:
            continue
        seen.add(post.get("id"))
        text = str(post.get("text") or "")
        if not WANTED.search(text):
            continue
        for d in demand:
            names = [d.get("item") or ""] + (d.get("item") or "").split("/")
            if any(len(n) >= 2 and n in text for n in names):
                st["forum_cat"][d["category"]] = st["forum_cat"].get(d["category"], 0) + 1
    st["forum_seen"] = list(seen)[-1000:]
    st["t"] = now

    try:                                                    # market-wide sales
        sold, st["seq"] = ledger_after(st["seq"])
        for _, _, sku in sold:
            cat = (catalog.get(sku) or {}).get("category")
            if cat:
                st["cat"][cat] = st["cat"].get(cat, 0) + 1
                st["sku"][sku] = st["sku"].get(sku, 0) + 1
    except Exception as e:
        print(f"[restock] ledger: {type(e).__name__}: {e}", file=sys.stderr)

    try:                                                    # our reputation, for the seller's prices
        with urllib.request.urlopen(f"{MARKET}/api/shops", timeout=15) as r:
            me = next((x for x in json.load(r).get("shops", []) if x.get("name") == SHOP_NAME), {})
        stars = [v["stars"] for v in me.get("reviews") or []
                 if (v.get("round") or 0) >= 3 and isinstance(v.get("stars"), (int, float))]
        st["rep"] = {"avg": sum(stars) / len(stars) if stars else None, "n": len(stars), "t": now}
    except Exception as e:
        print(f"[restock] reviews: {type(e).__name__}: {e}", file=sys.stderr)

    recent = Counter()                                      # our deals and turned-away buyers, last RECENT s
    if SALES_LOG.is_file():
        with open(SALES_LOG, encoding="utf-8") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("t", 0) < now - RECENT:
                    continue
                if ev.get("event") == "deal" and ev.get("category"):
                    recent[ev["category"]] += 1
                elif ev.get("event") == "no_stock":
                    for sku in ev.get("skus") or []:
                        if (catalog.get(sku) or {}).get("category"):
                            recent[catalog[sku]["category"]] += 1
    hours_seen = min(1.0, max(0.25, (now - (read_json(HERE / "policy_shop.json", {}).get("open_ts") or now)) / 3600))

    misses = Counter()                                      # our own sales and turned-away buyers
    if SALES_LOG.is_file():
        with open(SALES_LOG, encoding="utf-8") as f:
            f.seek(st.get("log_pos", 0))
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("event") == "deal" and ev.get("category"):
                    st["own_cat"][ev["category"]] = st["own_cat"].get(ev["category"], 0) + 1
                elif ev.get("event") == "no_stock":
                    for sku in ev.get("skus") or []:
                        misses[sku] += 1
            st["log_pos"] = f.tell()

    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    have = Counter(u.get("category") for u in units)
    have_sku = Counter(u["sku"] for u in units)
    stock_cost = sum(u.get("cost") or 0 for u in units)
    cash = inv["balance"]

    if (cash < (inv.get("bankrupt_line") or 500_000) and stock_cost < 100_000
            and (inv.get("bankruptcies_left") or 0) > 0):
        print(f"[restock] balance {cash}, nothing left to sell: filing", file=sys.stderr)
        print(json.dumps({"buy": [], "bankrupt": True}))
        STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        return

    by_cat = defaultdict(list)
    top_market = max(st["cat"].values(), default=0) or 1
    for sku, v in catalog.items():
        hot = st["cat"].get(v["category"], 0) / top_market >= 0.3     # a category the market is buying now
        cap = max(p["max_goods_price"], p.get("max_goods_price_hot", 0)) if hot else p["max_goods_price"]
        if v["kind"] == "goods" and v["list_price"] > cap:
            continue
        if v["kind"] == "food" and (v.get("shelf_hours") or 0) < 24:
            continue
        by_cat[v["category"]].append(v)

    top_cat = max(st["cat"].values(), default=0) or 1
    top_own = max(st["own_cat"].values(), default=0) or 1
    top_forum = max(st["forum_cat"].values(), default=0) or 1
    wants = []                                              # (priority, category, sku, qty)
    for d in demand:
        cat = d["category"]
        options = by_cat.get(cat)
        if not options:
            continue
        heat = (st["cat"].get(cat, 0) / top_cat + st["own_cat"].get(cat, 0) / top_own
                + 0.5 * st["forum_cat"].get(cat, 0) / top_forum)
        target = 1 + min(p["extra"], math.floor(p["extra"] * heat / 2 + 0.5))
        rate = max(st["own_cat"].get(cat, 0) * math.log(2) / HALF_LIFE,  # our sales per hour here: the decayed
                   recent.get(cat, 0) / hours_seen)                   # count, or the last hour if higher
        target = max(target, math.ceil(rate * GAP_HOURS * 1.5))       # last until the next window
        missing = target - have.get(cat, 0)
        if missing <= 0:
            continue
        if sig["hours_left"] <= p["clear_window_hours"]:    # near the end: only what will sell before it
            rate = st["own_cat"].get(cat, 0) * math.log(2) / HALF_LIFE
            missing = min(missing, math.floor(rate * sig["hours_left"]) - have.get(cat, 0))
            if missing <= 0:
                continue
        seen = [v for v in options if st["sku"].get(v["sku"], 0) > 0 or misses.get(v["sku"])]
        if seen:
            v = max(seen, key=lambda v: st["sku"].get(v["sku"], 0) + misses.get(v["sku"], 0))
        else:
            med = d.get("median_price") or 0
            v = min(options, key=lambda v: (abs(v["list_price"] - med), -(v.get("shelf_hours") or 0)))
        qty = min(MAX_QTY, missing)
        if v["kind"] == "food" and (v.get("shelf_hours") or 0) < 72:
            qty = max(0, min(qty, 1 - have_sku.get(v["sku"], 0)))
        if qty <= 0:
            continue
        priority = (0 if have.get(cat, 0) == 0 and heat > 0 else 1 if have.get(cat, 0) == 0 else 2, -heat)
        wants.append((priority, cat, v, qty))

    wants.sort(key=lambda w: w[0])
    max_stock = p["max_stock_share"] * (cash + stock_cost)
    buy, spend = [], 0
    for _, cat, v, qty in wants:
        if len(buy) >= MAX_LINES:
            break
        cost = v["list_price"] * COST_GUESS * qty
        if cash - spend - cost < p["keep_cash"] or stock_cost + spend + cost > max_stock:
            continue
        buy.append({"sku": v["sku"], "qty": qty})
        spend += cost

    STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    print(f"[restock] {regime}: cash {cash}, stock {len(units)} units at cost {stock_cost}, categories {len(have)}; "
          f"market sales seen {sum(st['cat'].values()):.1f}; buying {len(buy)} lines (~{spend:.0f})", file=sys.stderr)
    print(json.dumps({"buy": buy}))


if __name__ == "__main__":
    main()
