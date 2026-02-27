#!/usr/bin/env python3
"""
Audubon Print Monitor - Aggregates listings from multiple dealer sites.
Run periodically (e.g., daily via cron) to detect new listings.
"""

import requests
from bs4 import BeautifulSoup
import json
import re
import hashlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

def make_id(source, url):
    """Generate a stable ID for a listing."""
    return hashlib.md5(f"{source}:{url}".encode()).hexdigest()[:12]

def safe_price(text):
    """Extract numeric price from text."""
    if not text:
        return None
    match = re.search(r'[\$\u00a3\u20ac]?\s*([\d,]+(?:\.\d{2})?)', text.replace(',', ''))
    if match:
        try:
            return float(match.group(1).replace(',', ''))
        except:
            return None
    return None

def fetch_page(url, timeout=15):
    """Fetch a page with error handling."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp
    except Exception as e:
        print(f"  \u26a0 Error fetching {url}: {e}")
        return None


# \u2500\u2500\u2500 SCRAPER MODULES \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def scrape_princeton_audubon():
    """Princeton Audubon Prints - Shopify store with JSON API."""
    print("\ud83d\udd0d Scraping Princeton Audubon Prints...")
    listings = []
    page = 1
    while True:
        url = f"https://princetonaudubonprints.com/collections/octavo-bird-originals/products.json?page={page}&limit=250"
        resp = fetch_page(url)
        if not resp:
            break
        data = resp.json()
        products = data.get("products", [])
        if not products:
            break
        for p in products:
            if not p.get("variants"):
                continue
            variant = p["variants"][0]
            price = safe_price(variant.get("price"))
            available = variant.get("available", False)
            image_url = None
            if p.get("images"):
                image_url = p["images"][0].get("src", "")
            handle = p.get("handle", "")
            product_url = f"https://princetonaudubonprints.com/products/{handle}"
            listings.append({
                "id": make_id("princeton", product_url),
                "source": "Princeton Audubon",
                "source_key": "princeton",
                "title": p.get("title", ""),
                "price": price,
                "currency": "USD",
                "url": product_url,
                "image_url": image_url,
                "available": available,
                "edition": detect_edition(p.get("title", "") + " " + p.get("body_html", "")),
                "plate_number": extract_plate_number(p.get("title", "")),
                "description": BeautifulSoup(p.get("body_html", ""), "html.parser").get_text(strip=True)[:300],
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            })
        page += 1
        if page > 10:
            break
    print(f"  \u2713 Found {len(listings)} listings")
    return listings


def scrape_panteek():
    """Panteek - Shopify store with JSON API."""
    print("\ud83d\udd0d Scraping Panteek...")
    listings = []
    page = 1
    while True:
        url = f"https://www.panteek.com/collections/all/products.json?page={page}&limit=250"
        resp = fetch_page(url)
        if not resp:
            # Try search-based approach
            break
        data = resp.json()
        products = data.get("products", [])
        if not products:
            break
        for p in products:
            title = p.get("title", "")
            body = p.get("body_html", "")
            # Filter for Audubon
            combined = (title + " " + body).lower()
            if "audubon" not in combined:
                continue
            if not p.get("variants"):
                continue
            variant = p["variants"][0]
            price = safe_price(variant.get("price"))
            available = variant.get("available", False)
            image_url = None
            if p.get("images"):
                image_url = p["images"][0].get("src", "")
            handle = p.get("handle", "")
            product_url = f"https://www.panteek.com/products/{handle}"
            listings.append({
                "id": make_id("panteek", product_url),
                "source": "Panteek",
                "source_key": "panteek",
                "title": title,
                "price": price,
                "currency": "USD",
                "url": product_url,
                "image_url": image_url,
                "available": available,
                "edition": detect_edition(title + " " + body),
                "plate_number": extract_plate_number(title),
                "description": BeautifulSoup(body, "html.parser").get_text(strip=True)[:300] if body else "",
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            })
        page += 1
        if page > 20:
            break
    print(f"  \u2713 Found {len(listings)} listings")
    return listings


def scrape_old_print_shop():
    """The Old Print Shop - custom site, HTML scraping."""
    print("\ud83d\udd0d Scraping The Old Print Shop...")
    listings = []
    base_url = "https://oldprintshop.com/shop"
    
    for page_num in range(1, 6):  # Check first 5 pages
        params = f"?subjectdetail=1544&sort-price=high-to-low&page={page_num}"
        resp = fetch_page(f"{base_url}{params}")
        if not resp:
            break
        soup = BeautifulSoup(resp.text, "lxml")
        
        # Find product cards
        items = soup.select("li a[href*='/product/']")
        if not items:
            # Try broader selectors
            items = soup.find_all("a", href=re.compile(r'/product/\d+'))
        
        if not items:
            break
        
        seen_urls = set()
        for item in items:
            href = item.get("href", "")
            if not href or href in seen_urls:
                continue
            seen_urls.add(href)
            
            product_url = urljoin("https://oldprintshop.com", href)
            
            # Extract info from the card
            parent = item.parent if item.parent else item
            # Walk up a bit to find container
            container = item
            for _ in range(5):
                if container.parent:
                    container = container.parent
                    # Check if this container has both title and price
                    text = container.get_text()
                    if '$' in text and len(text) > 20:
                        break
            
            text_content = container.get_text(separator="\
", strip=True)
            
            # Extract title
            title_el = container.find("h2") or container.find("h3")
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                # Try to find text that looks like a title
                for line in text_content.split("\
"):
                    line = line.strip()
                    if len(line) > 10 and '$' not in line and 'Artist' not in line:
                        title = line
                        break
            
            # Extract price
            price = None
            price_match = re.search(r'\$[\d,]+(?:\.\d{2})?', text_content)
            if price_match:
                price = safe_price(price_match.group())
            
            # Extract image
            img = container.find("img")
            image_url = img.get("src", "") if img else None
            if image_url and not image_url.startswith("http"):
                image_url = urljoin("https://oldprintshop.com", image_url)
            
            if title:
                listings.append({
                    "id": make_id("oldprintshop", product_url),
                    "source": "The Old Print Shop",
                    "source_key": "oldprintshop",
                    "title": title,
                    "price": price,
                    "currency": "USD",
                    "url": product_url,
                    "image_url": image_url,
                    "available": True,
                    "edition": detect_edition(title),
                    "plate_number": extract_plate_number(title),
                    "description": "",
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                })
        
        time.sleep(0.5)
    
    # Dedupe
    seen = set()
    deduped = []
    for l in listings:
        if l["url"] not in seen:
            seen.add(l["url"])
            deduped.append(l)
    listings = deduped
    
    print(f"  \u2713 Found {len(listings)} listings")
    return listings


def scrape_antique_audubon():
    """AntiqueAudubon.com - Weebly site, HTML scraping."""
    print("\ud83d\udd0d Scraping Antique Audubon...")
    listings = []
    
    # Scrape both first edition and later edition pages
    urls = [
        ("https://www.antiqueaudubon.com/store/c32/Octavo-First-Edition-Birds", "1st Edition"),
        ("https://www.antiqueaudubon.com/store/c33/Octavo-Later-Edition-Birds", "Later Edition"),
    ]
    
    for page_url, edition_hint in urls:
        resp = fetch_page(page_url)
        if not resp:
            continue
        soup = BeautifulSoup(resp.text, "lxml")
        
        # Weebly store products
        products = soup.select(".wsite-com-product-wrap, .wsite-com-category-product")
        if not products:
            products = soup.find_all("div", class_=re.compile(r'product'))
        
        for prod in products:
            # Find link
            link = prod.find("a", href=True)
            if not link:
                continue
            product_url = urljoin(page_url, link["href"])
            
            # Title
            title_el = prod.find(class_=re.compile(r'product-title|product-name')) or prod.find("h2") or prod.find("h3")
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
            
            # Price
            price_el = prod.find(class_=re.compile(r'product-price|price'))
            price_text = price_el.get_text(strip=True) if price_el else ""
            price = safe_price(price_text)
            
            # Check for sale price
            sale_el = prod.find(class_=re.compile(r'sale'))
            if sale_el:
                sale_price = safe_price(sale_el.get_text(strip=True))
                if sale_price:
                    price = sale_price
            
            # Image
            img = prod.find("img")
            image_url = img.get("src", "") if img else None
            if image_url and not image_url.startswith("http"):
                image_url = urljoin(page_url, image_url)
            
            if title:
                listings.append({
                    "id": make_id("antiqueaudubon", product_url),
                    "source": "Antique Audubon",
                    "source_key": "antiqueaudubon",
                    "title": title,
                    "price": price,
                    "currency": "USD",
                    "url": product_url,
                    "image_url": image_url,
                    "available": True,
                    "edition": edition_hint if edition_hint else detect_edition(title),
                    "plate_number": extract_plate_number(title),
                    "description": "",
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                })
        
        time.sleep(0.5)
    
    print(f"  \u2713 Found {len(listings)} listings")
    return listings


def scrape_audubon_art():
    """AudubonArt.com - WooCommerce site."""
    print("\ud83d\udd0d Scraping Audubon Art...")
    listings = []
    
    # Try to get product listings
    for page_num in range(1, 4):
        url = f"https://www.audubonart.com/product-category/audubon-1st-ed-octavo/page/{page_num}/"
        resp = fetch_page(url)
        if not resp:
            # Try alternative URL structure
            if page_num == 1:
                url = "https://www.audubonart.com/product-category/audubon-1st-ed-octavo/"
                resp = fetch_page(url)
                if not resp:
                    break
            else:
                break
        
        soup = BeautifulSoup(resp.text, "lxml")
        products = soup.select(".product, .wc-block-grid__product, li.product")
        
        for prod in products:
            link = prod.find("a", href=re.compile(r'/product/'))
            if not link:
                continue
            product_url = link.get("href", "")
            
            title_el = prod.find("h2") or prod.find(class_=re.compile(r'product.*title|title'))
            title = title_el.get_text(strip=True) if title_el else ""
            
            price_el = prod.find(class_=re.compile(r'price'))
            price = safe_price(price_el.get_text(strip=True)) if price_el else None
            
            img = prod.find("img")
            image_url = img.get("src", "") if img else None
            
            if title and product_url:
                listings.append({
                    "id": make_id("audubonart", product_url),
                    "source": "Audubon Art",
                    "source_key": "audubonart",
                    "title": title,
                    "price": price,
                    "currency": "USD",
                    "url": product_url,
                    "image_url": image_url,
                    "available": True,
                    "edition": detect_edition(title),
                    "plate_number": extract_plate_number(title),
                    "description": "",
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                })
        
        time.sleep(0.5)
    
    print(f"  \u2713 Found {len(listings)} listings")
    return listings


def scrape_1stdibs():
    """1stDibs - search results page."""
    print("\ud83d\udd0d Scraping 1stDibs...")
    listings = []
    
    url = "https://www.1stdibs.com/search/?q=audubon+birds+of+america+print&sort=price-desc"
    resp = fetch_page(url)
    if not resp:
        print("  \u26a0 1stDibs may require browser-based scraping (anti-bot)")
        return listings
    
    soup = BeautifulSoup(resp.text, "lxml")
    
    # 1stDibs uses React, so we look for JSON data in script tags
    scripts = soup.find_all("script", type="application/json")
    for script in scripts:
        try:
            data = json.loads(script.string)
            # Try to find product data in the JSON
            if isinstance(data, dict):
                # Look recursively for product-like structures
                products = find_products_in_json(data)
                for p in products:
                    if p.get("title") and p.get("url"):
                        listings.append(p)
        except:
            continue
    
    # Also try HTML parsing
    items = soup.select("[data-tn='search-result-item'], .search-result-item, .listing-tile")
    for item in items:
        link = item.find("a", href=True)
        if not link:
            continue
        product_url = urljoin("https://www.1stdibs.com", link["href"])
        title = item.find("p") or item.find("h3") or item.find("span")
        title_text = title.get_text(strip=True) if title else ""
        
        price_el = item.find(class_=re.compile(r'price'))
        price = safe_price(price_el.get_text(strip=True)) if price_el else None
        
        img = item.find("img")
        image_url = img.get("src", "") if img else None
        
        if title_text:
            listings.append({
                "id": make_id("1stdibs", product_url),
                "source": "1stDibs",
                "source_key": "1stdibs",
                "title": title_text,
                "price": price,
                "currency": "USD",
                "url": product_url,
                "image_url": image_url,
                "available": True,
                "edition": detect_edition(title_text),
                "plate_number": extract_plate_number(title_text),
                "description": "",
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            })
    
    print(f"  \u2713 Found {len(listings)} listings")
    return listings


def scrape_ebay():
    """eBay - search results."""
    print("\ud83d\udd0d Scraping eBay...")
    listings = []
    
    queries = [
        "audubon birds america original print octavo",
        "audubon birds america havell print",
        "audubon birds america bien print",
    ]
    
    for query in queries:
        url = f"https://www.ebay.com/sch/i.html?_nkw={query.replace(' ', '+')}&_sacat=0&LH_BIN=1&_sop=16"
        resp = fetch_page(url)
        if not resp:
            continue
        
        soup = BeautifulSoup(resp.text, "lxml")
        items = soup.select(".s-item, .srp-results .s-item__wrapper")
        
        for item in items:
            link = item.find("a", class_=re.compile(r's-item__link'))
            if not link:
                link = item.find("a", href=re.compile(r'ebay\.com/itm/'))
            if not link:
                continue
            
            product_url = link.get("href", "").split("?")[0]
            if not product_url or "ebay.com/itm/" not in product_url:
                continue
            
            title_el = item.find(class_=re.compile(r's-item__title'))
            title = title_el.get_text(strip=True) if title_el else ""
            if title.lower().startswith("shop on ebay"):
                continue
            
            price_el = item.find(class_=re.compile(r's-item__price'))
            price = safe_price(price_el.get_text(strip=True)) if price_el else None
            
            img = item.find("img")
            image_url = img.get("src", "") if img else None
            
            if title and "audubon" in title.lower():
                listings.append({
                    "id": make_id("ebay", product_url),
                    "source": "eBay",
                    "source_key": "ebay",
                    "title": title,
                    "price": price,
                    "currency": "USD",
                    "url": product_url,
                    "image_url": image_url,
                    "available": True,
                    "edition": detect_edition(title),
                    "plate_number": extract_plate_number(title),
                    "description": "",
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                })
        
        time.sleep(1)
    
    # Dedupe by URL
    seen = set()
    deduped = []
    for l in listings:
        if l["url"] not in seen:
            seen.add(l["url"])
            deduped.append(l)
    listings = deduped
    
    print(f"  \u2713 Found {len(listings)} listings")
    return listings


# \u2500\u2500\u2500 HELPER FUNCTIONS \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def detect_edition(text):
    """Detect the edition type from listing text."""
    text_lower = text.lower()
    if any(x in text_lower for x in ["havell", "double elephant", "elephant folio"]):
        return "Havell"
    if any(x in text_lower for x in ["bien", "chromolithograph"]):
        return "Bien"
    if any(x in text_lower for x in ["1st ed", "first ed", "1840", "1841", "1842", "1843", "1844"]):
        return "Octavo 1st Ed"
    if any(x in text_lower for x in ["later ed", "2nd ed", "second ed", "1856", "1859", "1860", "1861", "1865", "1871"]):
        return "Octavo Later Ed"
    if "octavo" in text_lower:
        return "Octavo"
    return "Unknown"


def extract_plate_number(text):
    """Extract plate number from title."""
    patterns = [
        r'[Pp]l(?:ate)?\.?\s*#?\s*(\d+)',
        r'[Pp]late\s+(\d+)',
        r'#\s*(\d+)',
        r'\b(\d{1,3})\b(?=\s)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            num = int(match.group(1))
            if 1 <= num <= 500:
                return num
    return None


def find_products_in_json(data, depth=0):
    """Recursively find product-like structures in JSON."""
    if depth > 5:
        return []
    products = []
    if isinstance(data, dict):
        # Check if this looks like a product
        if "title" in data and ("price" in data or "amount" in data):
            url = data.get("url", data.get("href", data.get("link", "")))
            if url and not url.startswith("http"):
                url = f"https://www.1stdibs.com{url}"
            products.append({
                "id": make_id("1stdibs", url),
                "source": "1stDibs",
                "source_key": "1stdibs",
                "title": data.get("title", ""),
                "price": safe_price(str(data.get("price", data.get("amount", "")))),
                "currency": "USD",
                "url": url,
                "image_url": data.get("image", data.get("imageUrl", "")),
                "available": True,
                "edition": detect_edition(data.get("title", "")),
                "plate_number": extract_plate_number(data.get("title", "")),
                "description": data.get("description", "")[:300],
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            })
        for v in data.values():
            products.extend(find_products_in_json(v, depth + 1))
    elif isinstance(data, list):
        for item in data:
            products.extend(find_products_in_json(item, depth + 1))
    return products


# \u2500\u2500\u2500 MAIN \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def load_previous_listings():
    """Load previously scraped listings to detect new ones."""
    path = DATA_DIR / "listings.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"listings": [], "last_run": None, "history": []}


def save_listings(data):
    """Save listings to JSON."""
    path = DATA_DIR / "listings.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def run_scraper():
    print("=" * 60)
    print(f"\ud83e\udd85 Audubon Print Monitor - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    previous = load_previous_listings()
    previous_ids = {l["id"] for l in previous.get("listings", [])}
    
    all_listings = []
    errors = []
    
    scrapers = [
        ("Princeton Audubon", scrape_princeton_audubon),
        ("Panteek", scrape_panteek),
        ("Old Print Shop", scrape_old_print_shop),
        ("Antique Audubon", scrape_antique_audubon),
        ("Audubon Art", scrape_audubon_art),
        ("1stDibs", scrape_1stdibs),
        ("eBay", scrape_ebay),
    ]
    
    for name, scraper_fn in scrapers:
        try:
            results = scraper_fn()
            all_listings.extend(results)
        except Exception as e:
            print(f"  \u2718 {name} failed: {e}")
            errors.append({"source": name, "error": str(e)})
        time.sleep(1)
    
    # Mark new listings
    new_count = 0
    for listing in all_listings:
        if listing["id"] not in previous_ids:
            listing["is_new"] = True
            new_count += 1
        else:
            listing["is_new"] = False
    
    # Sort by price descending (None prices at end)
    all_listings.sort(key=lambda x: (x["price"] is None, -(x["price"] or 0)))
    
    # Build output
    now = datetime.now(timezone.utc).isoformat()
    output = {
        "listings": all_listings,
        "last_run": now,
        "total_count": len(all_listings),
        "new_count": new_count,
        "sources": {},
        "errors": errors,
        "history": previous.get("history", [])
    }
    
    # Source stats
    for l in all_listings:
        src = l["source"]
        if src not in output["sources"]:
            output["sources"][src] = {"count": 0, "new": 0}
        output["sources"][src]["count"] += 1
        if l.get("is_new"):
            output["sources"][src]["new"] += 1
    
    # Add to history
    output["history"].append({
        "date": now,
        "total": len(all_listings),
        "new": new_count,
        "by_source": {k: v["count"] for k, v in output["sources"].items()}
    })
    # Keep last 90 days of history
    output["history"] = output["history"][-90:]
    
    save_listings(output)
    
    print("\
" + "=" * 60)
    print(f"\ud83d\udcca Results: {len(all_listings)} total listings, {new_count} new")
    for src, stats in output["sources"].items():
        new_badge = f" ({stats['new']} new)" if stats["new"] else ""
        print(f"   {src}: {stats['count']}{new_badge}")
    if errors:
        print(f"\u26a0  {len(errors)} source(s) had errors")
    print(f"\ud83d\udcbe Saved to {DATA_DIR / 'listings.json'}")
    print("=" * 60)
    
    return output


if __name__ == "__main__":
    run_scraper()
