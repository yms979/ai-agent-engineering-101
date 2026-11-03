"""Hourly status push for the user (Round 3): rank, scores, shop and buyer numbers, change since the last one.

    python summary.py          (watch_round.sh runs it every 15 min; it sends at most once per EVERY seconds)

Reads the public board (/api/board?round=3) and our .market/inventory.json, sends one push through notify.py,
and keeps the last numbers in .market/summary_state.json to show what changed. Prints the message.
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / ".market" / "summary_state.json"
NAME = "yms979"
EVERY = 3600 - 120            # cron runs every 15 min; a little slack so it lands on the hour's run


def main():
    st = json.loads(STATE.read_text()) if STATE.is_file() else {}
    now = time.time()
    if "--force" not in sys.argv and now - st.get("t", 0) < EVERY:
        return
    with urllib.request.urlopen("https://market.ourolabs.ai/api/board?round=3", timeout=30) as r:
        b = json.load(r)
    players = sorted(b["players"], key=lambda x: -(x.get("total") or 0))
    rank = next(i + 1 for i, x in enumerate(players) if x["player"] == NAME)
    me = players[rank - 1]
    shops = [s for s in b["shops"] if s.get("deals")]
    buyers = [x for x in b["buyers"] if x.get("deals")]
    s = next(x for x in shops if x["name"] == NAME)
    u = next(x for x in buyers if x["name"] == NAME)
    rk = lambda L, k, n: [i + 1 for i, x in enumerate(sorted(L, key=lambda x: -(x.get(k) or 0))) if x["name"] == n][0]
    inv = json.loads((HERE / ".market" / "inventory.json").read_text())
    stock = sum(x.get("cost") or 0 for x in inv.get("units", []))
    econ = (inv.get("balance") or 0) + stock - 5_000_000
    cur = {"t": now, "rank": rank, "total": me["total"], "revenue": s["revenue"] or 0, "deals": s["deals"]}

    def delta(k, fmt="{:+,}"):
        return f" ({fmt.format(cur[k] - st[k])})" if k in st else ""
    hours = (now - st["t"]) / 3600 if st.get("t") else None
    per_h = f", 시간당 매출 {(cur['revenue'] - st['revenue']) / hours:,.0f}" if hours else ""
    move = "" if "rank" not in st else (" ▲" + str(st["rank"] - rank) if rank < st["rank"] else
                                        " ▼" + str(rank - st["rank"]) if rank > st["rank"] else " =")
    top = " / ".join(f"{i + 1}.{x['player']} {x['total']:.2f}" for i, x in enumerate(players[:3]))
    msg = (f"전체 {rank}위/{len(players)}{move}, 합계 {me['total']:.3f} (상점 {me['shop']['all']:.2f}, 구매자 {me['buyer']['all']:.2f})\n"
           f"상점: 매출 {cur['revenue']:,}{delta('revenue')} {rk(shops, 'revenue', NAME)}위{per_h} | "
           f"순이익 {s['profit'] or 0:,} {rk(shops, 'profit', NAME)}위 | 별점 {s['stars']} {rk(shops, 'stars', NAME)}위 | "
           f"거래 {s['deals']}{delta('deals')}\n"
           f"실질 이익(현금+재고원가-500만) {econ:,} | 재고 {len(inv.get('units', []))}개, 원가 {stock:,}\n"
           f"구매자: {u['deals']}/{u['orders']} 성사 ({u['deals'] / max(u['orders'], 1):.0%}), 잉여 {u['surplus'] or 0:,} "
           f"{rk(buyers, 'surplus', NAME)}위, 글 {u.get('posts') or 0}\n"
           f"선두: {top}")
    sent = subprocess.run([sys.executable, str(HERE / "notify.py"), f"[yms979] {rank}위{move} 요약", msg],
                          capture_output=True, text=True).stdout.strip()
    STATE.write_text(json.dumps(cur))
    print(msg + f"\n(push: {sent or 'none'})")


if __name__ == "__main__":
    main()
