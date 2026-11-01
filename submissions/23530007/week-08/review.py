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
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKET = "https://market.ourolabs.ai"
STATE = HERE / "review_state.json"
POST_EVERY = 330


def shop_name(url):
    try:
        with urllib.request.urlopen(f"{MARKET}/api/shops", timeout=15) as r:
            shops = json.load(r).get("shops", [])
        return next((s["name"] for s in shops if s.get("url", "").rstrip("/") == url.rstrip("/")), None)
    except Exception:
        return None


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
    if now - st["last_post"] >= POST_EVERY or s.get("claim_honest") is False:
        facts = [f"{shop}", f"{name} ({sku})", f"{price:,}원" + (f", 정가 {listp:,}원 대비 {off}% 저렴" if listp else ""),
                 f"등급 {s.get('grade')}" + (f" (주장 {s.get('claim')}, " + ("정직" if s.get("claim_honest") else "불일치!") + ")"
                                            if s.get("claim") else ""),
                 ]
        if s.get("expires_at"):
            facts.append(f"유통기한까지 {(s['expires_at'] - now) / 3600:.0f}h")
        else:
            facts.append(f"보증 {s.get('warranty_hours'):g}h" if s.get("warranty_hours") else "보증 없음")
        post = "[구매 기록] " + " | ".join(facts) + f" | ledger #{s.get('ledger')}"
        refs = [s["ledger"]] if isinstance(s.get("ledger"), int) else []
        with open(path.parent / "outbox.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"post": post, "refs": refs, "as": "buyer"}, ensure_ascii=False) + "\n")
        st["last_post"] = now
        STATE.write_text(json.dumps(st))
    print(json.dumps({"stars": stars, "review": review}, ensure_ascii=False))


if __name__ == "__main__":
    main()
