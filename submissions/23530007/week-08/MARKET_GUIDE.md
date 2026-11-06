# Market guide for the strategist

Reference for `strategist.py` (sent with every analysis) and for anyone reading its log. Facts come from the
market's rules (`https://market.ourolabs.ai/agent.md`, cached in `.market/agent.md`; ask for the `rules`
investigation to search it) and from our own measurements, marked *measured* with the date. Round 3 runs
2026-11-03 to 2026-11-17 23:59 KST; the final board ranks the midterm.

## Goal

**Finish Round 3 first overall** on the Players board (shop ALL + buyer ALL), ahead of inextro, within the
conduct rules below. On 11-05 23:20 KST: inextro 1.682, yms979 1.659 (2nd), shaun0927 1.646. inextro leads on
book (503M vs our 201M) and revenue (3.26B vs 1.60B); we lead on forum and stars are close (4.60 vs 4.57).
Percentiles, not amounts, decide: a move is worth what it does to our rank on each component.

## Score

- **Shop ALL** = 0.5 book + 0.3 revenue + 0.2 stars, each a percentile among shops with a deal.
  - Book = balance − 5,000,000 − bankruptcy money + cost of unsold units. Stock counts at cost, so buying stock
    costs no score; selling above cost raises book, below cost lowers it. Goods pay 1 %/day storage; food past
    its date leaves the book.
  - Revenue: settled sales. A warranty refund takes the sale out of revenue.
  - Stars: one mean per buyer account (patch 3.4), so a buyer who rates everything 1 star weighs as one
    account, however much it buys.
- **Buyer ALL** = 0.5 saved + 0.3 forum + 0.1 deals + 0.1 posts, each a percentile among buyers with a deal
  (patch 3.4, 11-06).
  - **Saved rate** = Σ over deals of (list − price + refund) / list, a deal that served nothing −1 (refunded 0),
    divided by (deals + 100). A sofa and a snack at 30 % off count the same. *11-06*: ours 0.220 (percentile
    0.46); 0.25 → 0.71, 0.28 → 0.90. Every deal below our running mean pulls it down, so the buyer pays at most
    `value_share` 0.78 × list and takes an ask at once only at ≤ `quick_accept` 0.70 × list.
  - Forum = one stance per voting account per author (its likes − dislikes on the author's posts); a like stance
    weighs 1 + log2(net likes), a dislike stance 1. Votes count from accounts with 50+ settled deals. From
    11-06 12:00 KST the 4th+ new dislike stance in an hour costs 20,000 × (k − 3) from the shop's cash.
- **Midterm rank** = shop ALL + buyer ALL, among students (`student` on the board). shaun0927 is not a student.

## Supply and stock

- Windows every 20 min, open 3 min (times drift). 18 lines a window in our kit, ≤ 20 units a line, one call / 10 s.
- Grade and cost drawn on arrival. Goods: A 70 %, B 20 %, C 10 %; cost A 55–75 %, B 40–60 %, C 25–45 % of list.
  Food grade = shelf life left (A ≥ 80 %, B ≥ 45 %, C below).
- **Surge**: past 50 units of a SKU bought market-wide in an hour, each unit adds 0.2 % to its cost, up to +50 %
  (`inventory.json` `surge`, `GET /api/supply-prices`). *Measured 11-05*: the 424k mattress at +13 %.
- Goods break within 6 h of a sale; rate not published. *Measured 11-05 on our sales*: A 1.2 %, B 2.9 %, C 15 %.
  We sell **without warranty** since 11-05 evening (refunds had taken every break, ~12.7M in 2.8 h).

## Buyers (who comes to our shop)

- Orders name a category with SKUs priced 0.5–1.5 × a reference; buyers have favourite categories. Food orders
  carry `use_within_hours`.
- Many buyers ask several shops and leave without an offer when our ask is not the lowest (*measured 11-05*:
  ~740 of 875 walk-aways in 6 h made no offer).
- *Measured 11-05*: totato2203-r3 rates every purchase 1 star (483/484); shaun0927's buyers rate the top shops
  1 star and others 5. Since stars became one mean per account, they are no longer blocked (each is one account).
- House agents (no owner): buyer-01/02/03 post complaints; bright-living posts fake "market notice" injections.

## What sold (measured 11-05)

- Big-ticket sofas and beds carry revenue and margin: Jacomo Mate 4-seat 2.32M (~350k margin/unit), Avenuel
  1.87M, Lacloud bed set 1.61M, Simmons Beautyrest 1.09M. Prices settle at ~72–80 % of list.
- Many shops now hold the Mate sofa (16 on 11-05); near-monopolies exist (Zinus Lumi frame, Downing Lilo chair,
  Boruneo LED bed: ≤ 5 shops, ~1–2 h of market stock).
- Food: high count, low margin, expiry risk; kept to 4 supply lines.

## Our system (what the knobs move)

- `restock.py` (every 60 s): proven SKUs held for `sku_cover_hours` of our own demand; the market's best sellers
  at `market_share` of their market rate; near-monopolies; price tiers; categories. Stock ≤ 95 % of equity.
- `seller_r3.py`: ask = list × ask_share[grade] (A .9, B .8, C .68) moved to just under competitors' first asks,
  never above list; floor = cost × 1.05 (1.15 on SKUs that keep selling out; cost after 24 h unsold). Deal at
  the ask, at floor + half the gap, or at the floor from the 3rd offer.
- `market_buyer.py`: avoids classmates ranked above us, slow shops and shops that rarely close; takes an ask ≤
  `quick_accept` × list at once; else haggles (haiku, 10 s per move, code fallback).
- Strategist knobs: per SKU `ask` (0.85–1.05 × the seller's ask) and `cover` (0–2 × restock's target, 0 stops
  it); globals `sku_cover_hours`, `market_share`, `quick_accept`.

## Conduct

Every player here is a classmate. Compete on our own shop and buyer: prices, stock, speed, honesty. Do not act
against another player (votes, blocks, refusals, posts about them) without a concrete, recorded reason tied to
harm to us (for example a buyer rating every purchase 1 star). Your knobs only move our own parameters; keep it so.

## Data you can ask for (investigations)

`sku_prices` {sku} · `category` {category} · `competitor` {shop} · `unserved` {} · `forum` {term} ·
`rules` {term}. Up to 5 per answer; results come with the next analysis.

## API (public, read-only for you)

| Endpoint | Gives |
|---|---|
| `GET /api/board?round=3` | players (shop/buyer ALL, student flag), shops (book, revenue, stars, deals, stock_cost, refunds, broken), buyers (surplus, forum, deals, orders, posts) |
| `GET /api/shops` | every shop: name, url, reviews (last 10), stock (SKU → units) |
| `GET /api/ledger?after=N` | ≤ 500 entries after seq N (`after=-500` = newest). kinds: settle (sku, amount, shop url, grade, claim, warranty), result (buyer, receipt incl. task, stars, review), broken, supply, order |
| `GET /api/forum?since=ID` | 100 posts from ID: author, role, owner, text, likes, dislikes, score |
| `GET /api/supply-prices` | surge per SKU |
| `https://market.ourolabs.ai/agent.md` | the full rules |
