"""Round 3 restock hook: what to buy for the shop, every 60 s.

    python restock.py --state .market          (agent.json "restock"; prints {"buy": [{"sku", "qty"}, ...]})

The market says neither what sells nor who wants what, so this learns it from two places:
  - the public ledger: every settled deal names the SKU sold (GET /api/ledger?after=N, read incrementally);
    counted per category with a half-life of HALF_LIFE hours, so the target follows what sells now
  - the shop's own log (seller_r3_log.jsonl): its deals, and buyers it had to turn away (no stock)

Per category the target stock is 1 unit (be in every order's shop list), plus up to EXTRA more by the
category's share of recent market sales and our own sales. A category gets the SKU that sold most in it,
or, with no sales seen yet, the SKU nearest the category's median price (orders accept 0.5-1.5x a reference
price, so the median fits most). Limits, so stock never eats the shop:
  - cash kept back: KEEP_CASH; stock at cost at most MAX_STOCK (unsold stock is a loss, goods pay 1 %/day)
  - goods listed above MAX_GOODS_PRICE are skipped (one unit ties up too much cash)
  - food that keeps under 72 h: at most 1 unit at a time (it is thrown away at cost when it expires)
  - at most 3 units per line, 10 lines per run (the kit's limit); urgent lines first: sold-out categories
    with sales, then categories we do not cover yet
Below the bankruptcy line with (almost) nothing left to sell it files ("bankrupt": true).
State (ledger position, decayed counts) in restock_state.json; every run logs one line to stderr.
"""
import argparse
import json
import math
import os
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKET = "https://market.ourolabs.ai"
STATE = Path(os.environ.get("RESTOCK_STATE", HERE / "restock_state.json"))
SALES_LOG = HERE / "seller_r3_log.jsonl"
HALF_LIFE = 6.0              # hours
KEEP_CASH = 1_500_000
MAX_STOCK = 3_000_000
MAX_GOODS_PRICE = 300_000
EXTRA = 4
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
    if inv.get("round", 0) < 3 or inv.get("balance") is None:
        print(json.dumps({"buy": []}))
        return
    catalog = read_json(md / "catalog.json", {})
    demand = read_json(md / "demand.json", [])
    st = read_json(STATE, {"seq": None, "t": time.time(), "cat": {}, "sku": {}, "own_cat": {}, "log_pos": 0})
    now = time.time()
    hours = (now - st["t"]) / 3600
    st["cat"], st["sku"], st["own_cat"] = decay(st["cat"], hours), decay(st["sku"], hours), decay(st["own_cat"], hours)
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
    for sku, v in catalog.items():
        if v["kind"] == "goods" and v["list_price"] > MAX_GOODS_PRICE:
            continue
        if v["kind"] == "food" and (v.get("shelf_hours") or 0) < 24:
            continue
        by_cat[v["category"]].append(v)

    top_cat = max(st["cat"].values(), default=0) or 1
    top_own = max(st["own_cat"].values(), default=0) or 1
    wants = []                                              # (priority, category, sku, qty)
    for d in demand:
        cat = d["category"]
        options = by_cat.get(cat)
        if not options:
            continue
        heat = st["cat"].get(cat, 0) / top_cat + st["own_cat"].get(cat, 0) / top_own
        target = 1 + min(EXTRA, math.floor(EXTRA * heat / 2 + 0.5))
        missing = target - have.get(cat, 0)
        if missing <= 0:
            continue
        seen = [v for v in options if st["sku"].get(v["sku"], 0) > 0 or misses.get(v["sku"])]
        if seen:
            v = max(seen, key=lambda v: st["sku"].get(v["sku"], 0) + misses.get(v["sku"], 0))
        else:
            med = d.get("median_price") or 0
            v = min(options, key=lambda v: (abs(v["list_price"] - med), -(v.get("shelf_hours") or 0)))
        qty = min(3, missing)
        if v["kind"] == "food" and (v.get("shelf_hours") or 0) < 72:
            qty = max(0, min(qty, 1 - have_sku.get(v["sku"], 0)))
        if qty <= 0:
            continue
        priority = (0 if have.get(cat, 0) == 0 and heat > 0 else 1 if have.get(cat, 0) == 0 else 2, -heat)
        wants.append((priority, cat, v, qty))

    wants.sort(key=lambda w: w[0])
    buy, spend = [], 0
    for _, cat, v, qty in wants:
        if len(buy) >= 10:
            break
        cost = v["list_price"] * COST_GUESS * qty
        if cash - spend - cost < KEEP_CASH or stock_cost + spend + cost > MAX_STOCK:
            continue
        buy.append({"sku": v["sku"], "qty": qty})
        spend += cost

    STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    print(f"[restock] cash {cash}, stock {len(units)} units at cost {stock_cost}, categories {len(have)}; "
          f"market sales seen {sum(st['cat'].values()):.1f}; buying {len(buy)} lines (~{spend:.0f})", file=sys.stderr)
    print(json.dumps({"buy": buy}))


if __name__ == "__main__":
    main()
