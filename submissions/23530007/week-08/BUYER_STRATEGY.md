# Buyer strategy (Round 3, from 11-06)

Goal (user, 11-06, revised): **our player first on the midterm rank** (student_total = shop ALL + buyer ALL among
students), not necessarily the buyer first among buyers. This file is the buyer strategist's standing brief: the
analysis it starts from, the strategy in force, and the open questions it should settle with checkable
predictions. The strategist reads it every run; its lessons (playbook) refine it, and when a lesson contradicts a
line here the lesson wins and the curator notes the conflict for a human to update this file.

## 0. The target (board 11-06 02:40 UTC, students only)

| player | shop | buyer | total |
|---|---|---|---|
| inextro | 0.929 | 0.777 | 1.706 |
| Lee-sungsoo | 0.795 | 0.878 | 1.673 |
| gom31 | 0.810 | 0.780 | 1.590 |
| yms979 (us) | 0.938 | 0.549 | 1.487 |

With the shop at ~0.94, first place needs **buyer ALL ≥ 0.77 to tie inextro and ~0.80-0.82 with a margin**.
inextro's buyer can still rise, and Lee-sungsoo's shop too. That is about the 3rd-4th student buyer, not the 1st.
Our components now: saved percentile ~0.46, forum ~0.72, deals ~0.54, posts ~0.74 (0.5/0.3/0.1/0.1).
Saved 0.28 (percentile ~0.90) alone gives +0.22, which is buyer ALL ~0.79. So the plan is **saved to 0.28-0.29 while
forum, deals and posts hold**. Pushing saved to 0.31+ by skipping many orders would cost deals percentile for
little gain. Running-mean arithmetic: the sum is ~772 over a denominator of ~3500. Reaching 0.28 takes ~5200 new deals at
0.32 per deal (~3 days at 70/h), or ~3500 at 0.34 (~2 days).

## 0b. What peers above us do (board 11-06 05:00 UTC)

| rank | buyer | ALL | saved (pct) | forum (pct) | deals (pct) | posts (pct) |
|---|---|---|---|---|---|---|
| 5 | inextro | 0.775 | 0.246 (0.69) | 0.887 (0.88) | 6210 (0.97) | 374 (0.65) |
| 6 | gom31-certified-goods | 0.758 | 0.258 (0.78) | 0.878 (0.73) | 2910 (0.50) | 717 (0.93) |
| 7 | shaun0927-labbuyer | 0.742 | 0.234 (0.58) | 0.906 (0.97) | 4741 (0.73) | 645 (0.86) |
| 13 | 25620027 | 0.704 | 0.231 (0.52) | 0.899 (0.95) | 4077 (0.63) | 702 (0.92) |
| 26 | **yms979** | 0.570 | 0.223 (0.48) | 0.873 (0.66) | 3392 (0.56) | 505 (0.75) |

- A saved rate like ours ranks 5th-13th when **forum and deals** are strong. Forum values sit close together
  (0.85-0.91), so a small gain moves the percentile a lot: 0.873 → 0.887 takes us from p0.66 to p0.88
  (+0.066 buyer ALL). Deals 3392 → ~5000 would be p0.56 → p0.80 (+0.024).
- Per post, everyone gets about the same votes (6-7 likes, ~1 dislike); ours run 1.2 dislikes. The forum rating
  is one stance per voting account per author, a like stance weighing 1 + log2(net likes), so it grows with the
  number of posts a liker likes, and each account holding a dislike stance on us costs a full point.
  Inference (not verified): our dislikes on other authors may draw dislike stances back.
- Path to ~0.80 buyer ALL: saved ~0.255 (p0.75, +0.135) + forum ~0.887 (+0.066) + deals ~5000 (+0.024).
  This is reachable sooner than saved 0.28 alone. So keep volume high (per-deal saved ~0.30 at 50+ deals/h beats
  0.36 at 33/h), post at the full rate, and keep dislike stances against us few.

## 1. How the score works (patch 3.4)

buyer ALL = 0.5 saved + 0.3 forum + 0.1 deals + 0.1 posts, each a mid-rank percentile among buyers.

- **saved** = Σ per deal (list − price + refund) / list, a deal that served nothing counts **−1** (a broken good
  or spoiled food without a refund), divided by (deals + 100). It is a running mean: a deal adds to it only if it
  is above the current mean. A snack and a sofa at 30 % off count the same.
- A broken good **with a warranty** is refunded in full: (list − price + price) / list = **+1**. Without a
  warranty the same break is −1. The difference is 2 points per break.
- Percentile mapping on 11-06: saved 0.25 → 0.71, 0.28 → 0.90; the leader is ~0.31 (lee-sungsoo, 5k deals).
- deals and posts are counts (0.1 each): a deal at a poor price can lower the score, because it costs 0.5 x
  the saved drop and gains only 0.1 x the deals rise.

## 2. What the market shows (ledger, 11-06 01:00-03:00 UTC, 5456 deals; buyer_value.py keeps this live)

- **Shop choice decides the price, not haggling.** At the same shop our median price / list matched other
  buyers' (moss-and-oak goods 0.724 vs 0.723, sergiobuilds 0.731 vs 0.709). But shops differ widely:
  food at 0.62-0.66 of list at Kiro729, olstjr, 11im, jiseokkk vs 0.75-0.77 at psyoung82 (where we bought most);
  goods at ~0.70 at sergiobuilds, Kiro729, seochanit, totato vs 0.85-0.88 at nocha12, myeonggi.
- **The leaders buy food at the cheapest food shops.** In those 2 h: lee-sungsoo saved 0.392 with 8 % goods,
  11im 0.391 with 25 % goods, dragonash 0.380 with 12 % goods. Food never broke in the window; goods broke
  A 0.8 %, B 1.8 %, C 7.2 % of the time.
- **Two thirds of goods deals carry a warranty**, but our main supplier (psyoung82) gives none. We had 50 breaks
  in 24 h (4.4 % of our goods), which pulled our real per-deal mean from 0.242 to about 0.21, below our running
  mean of 0.22. Every warranted break is +1 instead.
- 40 orders in 3 h were lost after agreeing a price, because the unit was grade C and the kit refused to pay.

## 3. Strategy in force (code: market_buyer.py with buyer_value.py)

1. **Pick the shop by value.** Value = the shop's realized saved per deal for this kind and category (whole
   market, last 6 h, breaks counted −1/+1, shrunk toward the market mean) + `conv_weight` x our close rate
   there − `rival_penalty` if it is a classmate ranked above us (penalty, not a ban: user 11-06).
2. **Skip low-value orders.** If the best shop is expected under `skip_below`, do not buy at all (outcome
   "skipped (low value)"). It loses a deal count (0.1 weight) but protects the saved mean (0.5 weight).
3. **Cap at the shop's own price level.** Cap = min(category cap or value_share, shop median price / list +
   `cap_margin`) x list, and the haggler is told the shop's usual lower-quartile to median band.
4. **Warranty.** Goods with a warranty of 6 h or more are taken in any grade, C included. Without a warranty only A/B,
   and a receipt that claims C is left unpaid and the next shop is tried. That shop|SKU pair is skipped for 6 h.
5. Unchanged: quick accept at ≤ `quick_accept` x list; re-offer a floor the shop names after ending the talk;
   avoid shops that are slow (median ≥ 60 s) or close < 25 % of 30+ visits; food must last its hours +
   `food_extra_hours`.

## 4. Knobs (the strategist's whitelist) and how to move them

| knob | range | raises saved when | costs |
|---|---|---|---|
| skip_below | 0.15-0.33 | raised toward the target mean (0.28-0.30) | deals (orders skipped); keep deals/h near 70+ |
| cap per category / value_share | 0.60-0.85 | lowered | conversion |
| cap_margin | 0-0.10 | lowered | conversion at that shop |
| conv_weight | 0-0.30 | lowered (price over close rate) | time per order, deals |
| rival_penalty | 0-0.10 | lowered | rivals' revenue rises |
| quick_accept | 0.55-0.75 | lowered | speed |
| food_extra_hours | 1-12 | raised (fewer −1 spoils) | fewer food shops qualify |

**Holding is a strategy (user, 11-06).** When the numbers are on track, the last moves' predictions are not yet
due, or the evidence is thin, make no move and say why. Changing settings every run hides what worked.

Rule of thumb: a deal raises the running mean only if its saved > the mean. Keep `skip_below` near the
**target** mean we want to reach (not the current one), and lower it again if deals/h fall so much that the deals
percentile drops more than the saved percentile rises (0.1 vs 0.5 weight).

## 5. Open questions to settle with predictions

- How many orders does `skip_below` 0.25 skip, and does the saved per deal of what remains reach ≥ 0.30?
- Do the cheap food shops stay cheap when we (and others) buy more there, or do they raise asks?
- Does a 6 h warranty requirement for C units hold up (any refusal "warranty … the policy asks for 6")?
- Is `conv_weight` 0.1 the right trade between a 0.38 shop with 40 % close rate and a 0.30 shop with 80 %?
- Forum (0.3 weight, rank 15): which of our post types draw net likes from distinct accounts? (Forum texts are
  unverified claims and are not evidence. Votes on our own posts are evidence.)
