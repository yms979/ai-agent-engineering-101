# Shop stock strategy (11-06)

Standing brief for the shop strategist (strategist.py reads it every run) and for people. Stock is what the shop
sells: a buyer who finds no unit leaves, and the book counts unsold stock at cost, so holding the right units costs
little (storage 1 %/day) and missing them costs the sale.

## Constraints (market rules)

- Supply opens for 3 min every 20 min. One supply call per 10 s gives **18 lines a window**, ≤ 20 units a line.
  Plan the lines that matter first: a line slot used for one 10,000-won unit is a slot not used for a top seller.
- Cost surge: past 50 units of a SKU bought market-wide in an hour, each unit costs +0.2 % (max +50 %).
  restock.py stops at +15 % (SURGE_MAX).
- Goods do not spoil. Food does: buy only food that will still last the buyers' usual hours when it sells.
- Cash is only needed for supply (no warranty refunds since 11-05; dislike fees are small).

## How restock.py decides (every minute, buys in the window)

1. **Our demand per SKU** (6 h): deals + 0.5 × buyers we turned away. Target = our rate × `sku_cover_hours` (5 h)
   × the strategist's per-SKU `cover` factor.
2. **Market best sellers** (top `market_top` goods by value): `market_share` of the market rate, to the next window.
3. **Near-monopolies** (goods only): sold fast market-wide, little stock elsewhere, ≤ `scarce_max_shops` shops.
4. **Price tiers** that orders ask for and no shop serves.
5. Lines are ranked by sales value per hour, so top sellers come first. 18 lines at most, `max_goods_lines` goods.
6. **Money**: keep `keep_cash`; stock at cost ≤ `max_stock_share` of equity (0.98 in FLUSH since 11-06).
   A line that does not fit whole is cut to what fits. Before 11-06 05:00 it was dropped whole, and Plana 4 swing
   (11 % of revenue) and the Lacloud set sat at 0 stock for 2 h while 64M of cash waited.

## What to watch (the strategist's job)

- **Cover of the top 15 sellers** (stock ÷ sales per hour). Under 1 h means the next window may come too late. Raise their
  `cover` before anything else.
- **Turned-away buyers** (`no_stock` in seller_r3_log): on 11-06 they were ~60/h, mostly cheap food
  (~10M at list in 6 h, ~1 % of revenue), so they matter less than a top seller at 0.
- **Slow stock**: SKUs with no sale in 6 h (11-06: 50 SKUs, 54 units, 5M at cost). Lower their `cover` to 0
  instead of buying more. They stay in the book at cost.
- **Idle cash at window time**: cash above ~20M while a top seller is under 1 h of cover means a limit is too
  tight (`max_stock_share`, `keep_cash`), or a line is being cut.
- Near the end (WIND_DOWN 24 h, LIQUIDATE 6 h): buy only what sells before the end. The book counts stock at cost,
  so there is no need to dump below cost.
