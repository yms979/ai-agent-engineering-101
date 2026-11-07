"""Hourly status push for the user (Round 3): class rank, how it moved, and when we could pass each player above.

    python summary.py            (watch_round.sh runs it every 15 min; it sends at most once per EVERY seconds)
    python summary.py --force    send now
    python summary.py --dry      print the message, send nothing, keep the state as it was

The goal is first place on student_total (the class ranking: shop + buyer scored among students only), so
this ranks on student_total, not on the open board's total. Each run keeps a snapshot of every student's
total in .market/summary_state.json (history, 48 h). The passing time for a player above us is
gap / (our gain per hour - their gain per hour), both measured over the last WINDOW (12 h) of snapshots;
when the gap is not closing it says so instead of giving a time.
"""
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / ".market" / "summary_state.json"
NAME = "yms979"
EVERY = 3600 - 120            # cron runs every 15 min; a little slack so it lands on the hour's run
WINDOW = 13 * 3600            # rates are measured over the last ~12 h of snapshots (6 h was too noisy; +1 h slack)
MIN_SPAN = 3600               # and need at least 1 h of them
KEEP = 48 * 3600
ROUND_END = datetime(2026, 11, 17, 14, 59, tzinfo=timezone.utc)
KST = timezone(timedelta(hours=9))


def kst(t):
    return datetime.fromtimestamp(t, KST).strftime("%m/%d %H:%M")


def rate(hist, name, now):
    """Gain per hour of name's student_total over the last WINDOW of snapshots, or None if too little data."""
    pts = [(h["t"], h["totals"][name]) for h in hist if name in h["totals"] and now - h["t"] <= WINDOW]
    if len(pts) < 2 or pts[-1][0] - pts[0][0] < MIN_SPAN:
        return None
    return (pts[-1][1] - pts[0][1]) / ((pts[-1][0] - pts[0][0]) / 3600)


def eta(gap, ours, theirs, now):
    if ours is None or theirs is None:
        return "속도 측정 중"
    close = ours - theirs
    if close <= 0:
        return f"현재 속도로는 못 따라잡음 (격차 {'벌어지는' if close < 0 else '그대로인'} 중)"
    t = now + gap / close * 3600
    if t > ROUND_END.timestamp():
        return "라운드 안에는 어려움 (현재 속도 기준)"
    return f"약 {gap / close:.0f}시간 뒤, {kst(t)} KST"


def main():
    st = json.loads(STATE.read_text()) if STATE.is_file() else {}
    now = time.time()
    if "--force" not in sys.argv and "--dry" not in sys.argv and now - st.get("t", 0) < EVERY:
        return
    with urllib.request.urlopen("https://market.ourolabs.ai/api/board?round=3", timeout=30) as r:
        b = json.load(r)
    cls = sorted((p for p in b["players"] if p.get("student")), key=lambda p: -(p.get("student_total") or 0))
    totals = {p["player"]: p["student_total"] for p in cls}
    rank = next(i + 1 for i, p in enumerate(cls) if p["player"] == NAME)
    me = cls[rank - 1]

    hist = [h for h in st.get("history", []) if now - h["t"] <= KEEP] + [{"t": now, "totals": totals}]
    ours = rate(hist, NAME, now)

    old_rank = st.get("class_rank")
    move = ("" if old_rank is None else f" ▲{old_rank - rank}" if rank < old_rank else
            f" ▼{rank - old_rank}" if rank > old_rank else " =")
    since = f" (지난 알림 {kst(st['t'])} 이후 {me['student_total'] - st['class_total']:+.3f})" if "class_total" in st else ""
    lines = [f"반 순위 {rank}위/{len(cls)}{move}, 합계 {me['student_total']:.3f}{since}",
             f"  상점 {me['student_shop']:.3f} + 구매자 {me['student_buyer']:.3f}"
             + (f", 최근 시간당 {ours:+.4f}" if ours is not None else "")]

    lines.append("위 순위와 따라잡는 시점:")
    for i, p in enumerate(cls[:rank - 1]):
        gap = p["student_total"] - me["student_total"]
        theirs = rate(hist, p["player"], now)
        sp = f", 시간당 {theirs:+.4f}" if theirs is not None else ""
        lines.append(f"  {i + 1}위 {p['player']} {p['student_total']:.3f} (차이 {gap:.3f}{sp}) → {eta(gap, ours, theirs, now)}")
    if rank < len(cls):
        nxt = cls[rank]
        lines.append(f"아래: {rank + 1}위 {nxt['player']} {nxt['student_total']:.3f} (우리보다 {me['student_total'] - nxt['student_total']:.3f} 아래)")

    prev = {n: i + 1 for i, n in enumerate(st.get("order", []))}
    moved = [f"{p['player']} {prev[p['player']]}→{i + 1}" for i, p in enumerate(cls[:max(rank + 1, 6)])
             if p["player"] in prev and prev[p["player"]] != i + 1]
    if moved:
        lines.append("순위 변동: " + ", ".join(moved))

    u = next((x for x in b["buyers"] if x["name"] == NAME), None)
    if u:
        lines.append(f"구매자 거래 {u['deals']}, 글 {u.get('posts') or 0} | 라운드 종료까지 {(ROUND_END.timestamp() - now) / 3600:.0f}시간")
    msg = "\n".join(lines)

    if "--dry" in sys.argv:
        print(msg)
        return
    sent = subprocess.run([sys.executable, str(HERE / "notify.py"), f"[yms979] 반 {rank}위{move}", msg],
                          capture_output=True, text=True).stdout.strip()
    STATE.write_text(json.dumps({"t": now, "class_rank": rank, "class_total": me["student_total"],
                                 "order": [p["player"] for p in cls], "history": hist}))
    print(msg + f"\n(push: {sent or 'none'})")


if __name__ == "__main__":
    main()
