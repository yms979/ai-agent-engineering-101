"""Situation -> strategy. seller_r3.py, restock.py and market_buyer.py ask this module for their parameters.

    from policy import shop_params, buyer_params
    regime, p, sig = shop_params()      # p["ask_share"], p["floor_mult"], p["buy"], ...

params.json holds a base set per role and overrides per regime; tuning means editing that file, not code.
Signals, read fresh on each call (the seller caches them CACHE seconds):
  shop:  hours since Round 3 opened (first time inventory.json says round 3, kept in policy_shop.json),
         hours to the end (params.json "end"), balance, stock at cost, own Round 3 deals (seller_r3_log.jsonl)
  buyer: hours to the end, deal rate over the last low_fill_window Round 3 orders (buyer_memory.json)
Shop regimes, first match wins:
  LIQUIDATE_LAST (end within 2 h) > LIQUIDATE (6 h) > WIND_DOWN (24 h) > LAUNCH (first 12 h and under 100 deals)
  > CASH_TIGHT (balance under 1M) > FLUSH (balance 8M or more) > STEADY
  Unsold stock at the end is a loss of its whole cost, so the last hours sell at any price above zero.
Buyer regimes: END_SALE (end within 12 h: shops clear stock, pay less) > LOW_FILL (deal rate under 30 %) > STEADY.
A regime change is written to logs/policy.log with the signals that caused it.
POLICY_NOW (epoch seconds) overrides the clock and POLICY_DIR the state/log folder, for tests.
"""
import json
import os
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PARAMS = HERE / "params.json"
MARKET_DIR = Path(os.environ.get("MARKET_DIR", HERE / ".market"))
SALES_LOG = Path(os.environ.get("SELLER_LOG", HERE / "seller_r3_log.jsonl"))
MEMORY = Path(os.environ.get("BUYER_MEMORY", HERE / "buyer_memory.json"))
OUT = Path(os.environ.get("POLICY_DIR", HERE))          # tests point this elsewhere
LOG = OUT / "logs" / "policy.log"
STATE = {"shop": OUT / "policy_shop.json", "buyer": OUT / "policy_buyer.json"}


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def now():
    return float(os.environ.get("POLICY_NOW") or time.time())


def merged(cfg, role, regime):
    p = json.loads(json.dumps(cfg[role]["base"]))
    for k, v in cfg[role].get(regime, {}).items():
        p[k] = dict(p[k], **v) if isinstance(v, dict) and isinstance(p.get(k), dict) else v
    return p


def note(role, regime, sig):
    """Remember the regime; on a change, one line in logs/policy.log."""
    st = read_json(STATE[role], {})
    if st.get("regime") != regime:
        LOG.parent.mkdir(exist_ok=True)
        stamp = datetime.fromtimestamp(sig["now"]).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        shown = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in sig.items() if k != "now"}
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{stamp} [{role}] {st.get('regime', '-')} -> {regime} {json.dumps(shown, ensure_ascii=False)}\n")
    st["regime"] = regime
    tmp = STATE[role].with_suffix(".tmp")
    tmp.write_text(json.dumps(st), encoding="utf-8")
    tmp.replace(STATE[role])


def shop_signals(cfg):
    t = now()
    inv = read_json(MARKET_DIR / "inventory.json", {})
    st = read_json(STATE["shop"], {})
    if inv.get("round", 0) >= 3 and not st.get("open_ts"):
        st["open_ts"] = t
        STATE["shop"].write_text(json.dumps(st), encoding="utf-8")
    units = [u for u in inv.get("units") or [] if u.get("grade") != "expired"]
    deals = 0
    if SALES_LOG.is_file():
        with open(SALES_LOG, encoding="utf-8") as f:
            for line in f:
                if '"event": "deal"' in line and '"category": null' not in line:
                    deals += 1
    return {"now": t, "round": inv.get("round", 0),
            "hours_open": (t - st["open_ts"]) / 3600 if st.get("open_ts") else 0.0,
            "hours_left": (datetime.fromisoformat(cfg["end"]).timestamp() - t) / 3600,
            "balance": inv.get("balance") or 0, "stock_cost": sum(u.get("cost") or 0 for u in units),
            "units": len(units), "deals": deals}


def shop_params():
    cfg = read_json(PARAMS, {})
    r, sig = cfg["regimes"], shop_signals(cfg)
    if sig["round"] < 3:
        regime = "STEADY"
    elif sig["hours_left"] <= r["liquidate_last_hours"]:
        regime = "LIQUIDATE_LAST"
    elif sig["hours_left"] <= r["liquidate_hours"]:
        regime = "LIQUIDATE"
    elif sig["hours_left"] <= r["wind_down_hours"]:
        regime = "WIND_DOWN"
    elif sig["hours_open"] < r["launch_hours"] and sig["deals"] < r["launch_deals"]:
        regime = "LAUNCH"
    elif sig["balance"] < r["cash_tight"]:
        regime = "CASH_TIGHT"
    elif sig["balance"] >= r["flush"]:
        regime = "FLUSH"
    else:
        regime = "STEADY"
    if sig["round"] >= 3:
        note("shop", regime, sig)
    return regime, merged(cfg, "shop", regime), sig


def buyer_params():
    cfg = read_json(PARAMS, {})
    r, t = cfg["regimes"], now()
    trades = [x for x in read_json(MEMORY, {}).get("trades", []) if x.get("category")][-r["low_fill_window"]:]
    rate = sum(x.get("outcome") == "deal" for x in trades) / len(trades) if trades else None
    sig = {"now": t, "hours_left": (datetime.fromisoformat(cfg["end"]).timestamp() - t) / 3600,
           "orders": len(trades), "deal_rate": rate}
    if sig["hours_left"] <= r["end_sale_hours"]:
        regime = "END_SALE"
    elif rate is not None and len(trades) >= r["low_fill_window"] and rate < r["low_fill_rate"]:
        regime = "LOW_FILL"
    else:
        regime = "STEADY"
    if trades:
        note("buyer", regime, sig)
    return regime, merged(cfg, "buyer", regime), sig


if __name__ == "__main__":
    for name, fn in (("shop", shop_params), ("buyer", buyer_params)):
        regime, p, sig = fn()
        print(name, regime, json.dumps(p, ensure_ascii=False))
        print("   ", {k: (round(v, 1) if isinstance(v, float) else v) for k, v in sig.items()})
