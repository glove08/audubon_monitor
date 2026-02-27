# Audubon Print Monitor

A mobile-first PWA that aggregates **Birds of America** original print listings across 7+ dealer sources.

## Quick Deploy (5 minutes)

### 1. Create a GitHub repo

```bash
# In this folder:
git init
git add .
git commit -m "Initial commit"

# Create a repo on github.com called "audubon-monitor", then:
git remote add origin https://github.com/YOUR_USERNAME/audubon-monitor.git
git branch -M main
git push -u origin main
```

### 2. Enable GitHub Pages

1. Go to your repo → **Settings** → **Pages**
2. Under **Source**, select **Deploy from a branch**
3. Set branch to **main**, folder to **/ (root)**
4. Click **Save**
5. Wait ~60 seconds → your site is live at:
   `https://YOUR_USERNAME.github.io/audubon-monitor/`

### 3. Add to your iPhone Home Screen

1. Open the URL in Safari
2. Tap the **Share** button (box with arrow)
3. Tap **Add to Home Screen**
4. Name it "Audubon" → **Add**

It now launches as a standalone app — no browser chrome.

---

## Running the Scraper

```bash
# Install dependencies
pip install requests beautifulsoup4 lxml

# Run once
python3 audubon_scraper.py

# This outputs data/listings.json which the dashboard reads
```

### Automate daily scans

**macOS cron (run at 8am daily):**
```bash
crontab -e
# Add this line:
0 8 * * * cd /path/to/audubon-monitor && python3 audubon_scraper.py && git add data/listings.json && git commit -m "Daily scan $(date +\%F)" && git push
```

This runs the scraper, commits the updated data, and pushes to GitHub — your live site updates automatically.

---

## File Structure

```
audubon-monitor/
├── index.html          ← Dashboard (single-file React app)
├── manifest.json       ← PWA configuration
├── icon-192.png        ← App icon (192×192)
├── icon-512.png        ← App icon (512×512)
├── data/
│   └── listings.json   ← Scraper output (updated daily)
├── audubon_scraper.py  ← Python scraper (add from earlier)
└── README.md
```

## Features

- **Image gallery** — tap any listing thumbnail to open fullscreen
- **Pinch to zoom** — 1x–5x zoom on print images
- **Tap to zoom** — zooms to 2.8x centered on where you tapped
- **Pan when zoomed** — drag to explore the full image
- **Swipe navigation** — swipe left/right between images
- **Filter by source** — 7 dealers: OPS, Princeton, Antique Audubon, Audubon Art, Panteek, eBay, 1stDibs
- **Filter by edition** — Havell, Bien, Octavo 1st Ed, Octavo Later Ed
- **Search** — by species name or plate number
- **Sort** — price (high/low), new first, plate number
- **New listing flags** — red indicators for newly discovered prints
- **Trend charts** — inventory and price distribution over time

## Sources Monitored

| Source | Method |
|--------|--------|
| The Old Print Shop | HTML scraping |
| Princeton Audubon Prints | Shopify JSON API |
| Antique Audubon | Weebly HTML scraping |
| Audubon Art | WooCommerce scraping |
| Panteek | Shopify JSON API |
| eBay | Search results scraping |
| 1stDibs | HTML + embedded JSON |

## GitHub Pages Note

If deploying to a **project page** (not user page), the URL will be:
`https://username.github.io/audubon-monitor/`

The `manifest.json` uses `"start_url": "/"` — if your site is at a subpath, update this to `"/audubon-monitor/"` for the PWA to work correctly. Also update the fetch path in `index.html` if needed.
