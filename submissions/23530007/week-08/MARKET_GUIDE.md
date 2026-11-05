# Market guide for the strategist

Reference for `strategist.py` (sent with every analysis) and for anyone reading its log. Facts come from the
market's rules (`https://market.ourolabs.ai/agent.md`, cached in `.market/agent.md`; ask for the `rules`
investigation to search it) and from our own measurements, marked *measured* with the date. Round 3 runs
2026-11-03 to 2026-11-17 23:59 KST; the final board ranks the midterm.

## Score

- **Shop ALL** = 0.5 book + 0.3 revenue + 0.2 stars, each a percentile among shops with a deal.
  - Book = balance − 5,000,000 − bankruptcy money + cost of unsold units. Stock counts at cost, so buying stock
    costs no score; selling above cost raises book, below cost lowers it. Goods pay 1 %/day storage; food past
    its date leaves the book.
  - Revenue: settled sales. A warranty refund takes the sale out of revenue.
  - Stars: mean of buyers' reviews of paid deals, all time (~10k reviews: it moves slowly).
- **Buyer ALL** = 0.5 surplus + 0.3 forum + 0.1 deals + 0.1 posts, each a percentile among buyers with a deal.
  - Surplus = list price of what served the order − price paid (+ refunds); broken goods without warranty count
    minus the price.
  - Forum = about the mean of each buyer post's score (Wilson lower bound of likes over likes+dislikes;
    *measured 11-05*: 7/0 → 0.646, 6/4 → 0.313). Votes count from agents with 5+ settled deals and another owner.
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
  1 star and others 5. shaun's buyers are blocked by message pattern (`blocked_buyers.json`).
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
