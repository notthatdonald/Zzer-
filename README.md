# ZZER (只二) handbag tracker

Scrapes every handbag listed on [zzer.com](https://www.zzer.com) – the Chinese
pre-owned luxury consignment platform 只二 – and tracks price vs. original
retail, discount, and SKU counts per brand over time.

## How the site works

`zzer.com` on a phone browser loads the shop from `mix.goshare2.com`, which
pulls listings from `gwapi-v2.goshare2.com`. Requests are signed inside the
page, so the scraper opens the public Bags search page in headless Chromium
(iPhone profile) and calls the page's own HTTP client. Nothing needs a login.

The Bags category (`ppath=10:78;`) is crawled sub-category by sub-category
(Shoulder, Tote, Crossbody, Belt, Backpacks, Wallets, Clutches, Cardholders,
Luggage, …) 100 listings per request with a 0.6 s pause between requests.

## Fields collected

| From the listing feed (every bag) | From the product page (`details.js`) |
|---|---|
| id, SKU, brand, title (Chinese) | collection / model |
| sub-category (Shoulder, Tote …) | material, weight, L×H×D, strap length |
| condition grade (New / 9.8 / 9.5 / 9.0 / 8.5 …) | flaws found at inspection, maintenance done |
| size | included accessories (dust bag, box, strap …) |
| listed price, price after coupon, coupon | condition notes, inspection date |
| **original retail price** | bargain floor (lowest offer accepted), offers on/off |
| discount vs retail (calculated) | seller grade, seller's live listings |
| date first listed, days listed | brand id, image count |
| gender, colour, warehouse city | |
| image URL, product URL | |

Prices are in CNY. The site's own `discount` field is in Chinese 折 notation
(`22` = sells for 22 % of retail = 78 % off); the reports use `% off retail`.

## Running it

```bash
# 1. crawl all handbags (≈1–2 h incl. gender/colour/location labels)
node scraper/scrape.js                       # writes data/snapshots/<today>/
node scraper/scrape.js --enrich=none         # faster: skip the label passes (~30 min)

# 2. optional: product-page details (resumable, cached; ~1 s/bag first time,
#    later runs only fetch new listings)
node scraper/details.js

# 3. build reports + update price history
python3 analysis/analyze.py                  # writes reports/<date>/ and data/zzer.sqlite
```

Requires Node 18+ with `playwright` (Chromium) and Python 3 with `pandas` and `openpyxl`.

Run steps 1 and 3 daily or weekly to track discounts: each run adds a
snapshot to `price_history` in `data/zzer.sqlite`, and the report gains a
"Price changes" sheet (price drops/raises, new listings, sold/removed).

## Outputs (`reports/<date>/`)

- `zzer_handbags_<date>.xlsx` – brand summary, brand × condition / type /
  gender / colour / location, models, condition and type summaries,
  discount bands, deepest discounts, price changes, and every handbag.
- `brand_summary.csv` – SKUs, share, price and discount stats per brand.
- `handbags.csv` – one row per handbag with every field.
- `summary.json` – headline numbers used by the dashboard.

`data/zzer.sqlite` tables: `listings` (every bag ever seen, with first/last
seen dates), `price_history` (price per bag per snapshot), `facets`
(site-reported filter counts per snapshot).
