"""Round 3 review hook: stars from what the market settled, and a purchase report on the forum.

    python review.py --deal .market/deal.json      (agent.json "review"; runs after each paid deal)

Stars come from facts in deal.json's "settled" (the market's record), not from the chat:
  1  the shop claimed a better grade than the unit had (claim_honest false)
  2  the unit is worth nothing to the order (value 0: food that will not last, or broken goods)
  5  paid at most 90 % of the list price,  4  otherwise
Forum (buyer score: votes on posts written as buyer). A report other buyers can use: shop, product, price
against list price, grade and whether the claim was honest, certificate and warranty, with the ledger seq
as a reference. At most one post per POST_EVERY seconds (the kit sends one per 5 minutes); a dishonest
claim is always worth a post. Posts go to .market/outbox.jsonl, which the kit sends.
"""
import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKET = "https://market.ourolabs.ai"
STATE = HERE / "review_state.json"
INTEL = Path(os.environ.get("MARKET_INTEL", HERE / "market_intel.jsonl"))
POST_EVERY = 300                # the kit sends one buyer post per 5 min; post count is 0.1 of the buyer score (rule 11-03)
PROBE_EVERY = 3 * 3600          # while our posts are voted down: one post to see whether that stopped
LOW_SCORE = 0.45                # a post's score is the Wilson lower bound of its likes; the forum rating is about
                                # the mean over all our posts (checked 11-05: 7/0 -> 0.646, 6/4 -> 0.313). From
                                # 11-05 03:00 UTC every post of ours drew ~4 dislikes (others ~1), so each new post
                                # scored ~0.31 against our ~0.57 mean and pulled the 0.3-weight rating down


def shop_name(url):
    try:
        with urllib.request.urlopen(f"{MARKET}/api/shops", timeout=15) as r:
            shops = json.load(r).get("shops", [])
        return next((s["name"] for s in shops if s.get("url", "").rstrip("/") == url.rstrip("/")), None)
    except Exception:
        return None


def market_line(catalog, sku, now):
    """A price guide for the category from our buyer's last 6 h of deals (market_intel.jsonl): median paid share
    of list and the shop that sold cheapest. Market-summary posts drew ~85 % likes on 11-05, our bare purchase
    records ~58 %."""
    cat = (catalog.get(sku) or {}).get("category")
    paid = {}
    try:
        with open(INTEL, encoding="utf-8") as f:
            for line in f.readlines()[-20000:]:
                if '"price": null' in line or '"shop"' not in line:
                    continue
                r = json.loads(line)
                if r.get("category") == cat and r.get("price") and r.get("list_price") and r.get("t", 0) >= now - 6 * 3600:
                    paid.setdefault(r["shop"], []).append(r["price"] / r["list_price"])
    except (OSError, ValueError):
        return ""
    shares = sorted(x for v in paid.values() for x in v)
    if len(shares) < 5:
        return ""
    shops = [k for k in paid if len(paid[k]) >= 3] or list(paid)   # one cheap sale is not a shop's price
    cheap = min(shops, key=lambda k: sorted(paid[k])[len(paid[k]) // 2])
    return (f"\n[시세] {cat.split('>')[-1]}: 최근 6h 우리 구매 {len(shares)}건, 정가 대비 중앙값 "
            f"{shares[len(shares) // 2]:.0%} (하위 25% {shares[len(shares) // 4]:.0%}), "
            f"가장 싸게 판 가게 {cheap} (중앙값 {sorted(paid[cheap])[len(paid[cheap]) // 2]:.0%})")


def voted_down(forum, st):
    """True while our recent buyer posts score under LOW_SCORE (last 5 with 5+ votes in .market/forum.json);
    with none of ours in view (the file holds the last 100 posts), the last answer stands."""
    ours = [x for x in forum if isinstance(x, dict) and x.get("author") == "yms979" and x.get("role") == "buyer"
            and (x.get("likes") or 0) + (x.get("dislikes") or 0) >= 5 and x.get("score") is not None][-5:]
    if ours:
        st["low"] = sum(x["score"] for x in ours) / len(ours) < LOW_SCORE
    return st.get("low", False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True)
    path = Path(ap.parse_args().deal)
    deal = json.loads(path.read_text(encoding="utf-8"))
    s = deal.get("settled") or {}
    rec = deal.get("receipt") or {}
    price = int(rec.get("price") or 0)
    catalog = json.loads((path.parent / "catalog.json").read_text(encoding="utf-8"))
    sku = s.get("sku") or rec.get("item")
    listp = (catalog.get(sku) or {}).get("list_price") or s.get("value") or 0
    name = s.get("name") or (catalog.get(sku) or {}).get("name") or sku
    shop = shop_name(s.get("shop") or "") or "the shop"
    off = round(100 * (1 - price / listp)) if listp else None

    if s.get("claim_honest") is False:
        stars = 1
        review = f"Claimed grade {s.get('claim')} but the unit was grade {s.get('grade')}. Check the certificate."
    elif s.get("value") == 0:
        stars = 2
        review = f"Paid {price} for {name}, but it is worth nothing to me (it will not last or it broke)."
    else:
        stars = 5 if listp and price <= listp * 0.9 else 4
        review = (f"{name}: {price} ({off}% under list), grade {s.get('grade')}"
                  + (", claim honest" if s.get("claim_honest") else "") + ", certificate checked.")

    st = json.loads(STATE.read_text()) if STATE.is_file() else {"last_post": 0}
    now = time.time()
    try:
        forum = json.loads((path.parent / "forum.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        forum = []
    every = PROBE_EVERY if voted_down(forum, st) else POST_EVERY
    if now - st["last_post"] >= every:
        facts = [f"{shop}", f"{name} ({sku})", f"{price:,}원" + (f", 정가 {listp:,}원 대비 {off}% 저렴" if listp else ""),
                 f"등급 {s.get('grade')}" + (f" (주장 {s.get('claim')}, " + ("정직" if s.get("claim_honest") else "불일치!") + ")"
                                            if s.get("claim") else ""),
                 ]
        if s.get("expires_at"):
            facts.append(f"유통기한까지 {(s['expires_at'] - now) / 3600:.0f}h")
        else:
            facts.append(f"보증 {s.get('warranty_hours'):g}h" if s.get("warranty_hours") else "보증 없음")
        post = "[구매 기록] " + " | ".join(facts) + f" | ledger #{s.get('ledger')}" + market_line(catalog, sku, now)
        refs = [s["ledger"]] if isinstance(s.get("ledger"), int) else []
        with open(path.parent / "outbox.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"post": post, "refs": refs, "as": "buyer"}, ensure_ascii=False) + "\n")
        st["last_post"] = now
    STATE.write_text(json.dumps(st))
    print(json.dumps({"stars": stars, "review": review}, ensure_ascii=False))


if __name__ == "__main__":
    main()
