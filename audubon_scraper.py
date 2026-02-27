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
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote_plus

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

# Titles containing these (case-insensitive) are skipped
TITLE_EXCLUDE = [
    "edward lear",
    "lear's",
    "john gould",
    "quadruped",
]

def is_excluded(title, body=""):
    combined = (title + " " + body).lower()
    for term in TITLE_EXCLUDE:
        if term in combined:
            return True
    return False

def make_id(source, url):
    return hashlib.md5(f"{source}:{url}".encode()).hexdigest()[:12]

def safe_price(text):
    if not text:
        return None
    cleaned = str(text).replace(",", "").replace(" ", "")
    match = re.search(r'(\d+(?:\.\d{2})?)', cleaned)
    if match:
        try:
            val = float(match.group(1))
            if val > 0:
                return val
        except:
            pass
    return None

def fetch_page(url, timeout=15, headers=None):
    try:
        resp = requests.get(url, headers=headers or HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp
    except Exception as e:
        print(f"  [!] Error fetching {url}: {e}")
        return None

def detect_edition(text):
    text_lower = text.lower()
    if any(x in text_lower for x in ["havell", "double elephant", "elephant folio"]):
        return "Havell"
    if any(x in text_lower for x in ["bien", "chromolithograph"]):
        return "Bien"
    if any(x in text_lower for x in ["1st ed", "first ed", "1st royal", "first royal",
                                      "1840", "1841", "1842", "1843", "1844",
                                      "1839-1844", "1840-1844"]):
        return "Octavo 1st Ed"
    if any(x in text_lower for x in ["later ed", "2nd ed", "second ed", "3rd ed",
                                      "1856", "1859", "1860", "1861", "1865", "1871"]):
        return "Octavo Later Ed"
    if "octavo" in text_lower:
        return "Octavo"
    return "Unknown"

def extract_plate_number(text):
    patterns = [
        r'[Pp]l(?:ate)?\.?\s*#?\s*(\d+)',
        r'[Pp]late\s+(\d+)',
        r'[Nn]o\.?\s*(\d+)',
        r'#\s*(\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            num = int(match.group(1))
            if 1 <= num <= 500:
                return num
    return None

def make_listing(source, source_key, title, price, url, image_url=None, edition=None,
                 plate_number=None, description="", available=True):
    return {
        "id": make_id(source_key, url),
        "source": source,
        "source_key": source_key,
        "title": title,
        "price": price,
        "currency": "USD",
        "url": url,
        "image_url": image_url,
        "available": available,
        "edition": edition or detect_edition(title + " " + description),
        "plate_number": plate_number or extract_plate_number(title),
        "description": description[:300] if description else "",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# IMAGE HELPERS
# ============================================================

def _get_detail_image(product_url, selectors):
    """Fetch a product detail page and extract the highest-res image."""
    resp = fetch_page(product_url, timeout=10)
    if not resp:
        return None
    soup = BeautifulSoup(resp.text, "lxml")
    for sel in selectors:
        imgs = soup.select(sel)
        for img in imgs:
            src = (img.get("data-zoom") or img.get("data-large") or
                   img.get("data-src") or img.get("data-original") or "")
            if not src:
                srcset = img.get("srcset", "")
                if srcset:
                    parts = [s.strip().split() for s in srcset.split(",") if s.strip()]
                    best = None
                    best_w = 0
                    for part in parts:
                        if len(part) >= 2 and part[1].endswith("w"):
                            try:
                                w = int(part[1].replace("w", ""))
                                if w > best_w:
                                    best_w = w
                                    best = part[0]
                            except ValueError:
                                pass
                    if best:
                        src = best
            if not src:
                src = img.get("src", "")
            if src and not src.endswith((".svg", ".gif")) and "logo" not in src.lower() and "icon" not in src.lower():
                if not src.startswith("http"):
                    src = urljoin(product_url, src)
                # Remove size constraints from common CDN URL patterns
                src = re.sub(r'_\d+x\d+', '', src)
                src = re.sub(r'\?.*$', '', src)
                return src
    return None


# ============================================================
# SCRAPER MODULES
# ============================================================

def scrape_princeton_audubon():
    """Princeton Audubon Prints - Shopify store with JSON API."""
    print("[*] Scraping Princeton Audubon Prints...")
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
            title = p.get("title", "")
            body = p.get("body_html", "")
            if is_excluded(title, body):
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
            product_url = f"https://princetonaudubonprints.com/products/{handle}"
            desc_text = BeautifulSoup(body, "html.parser").get_text(strip=True) if body else ""
            listings.append(make_listing(
                "Princeton Audubon", "princeton", title, price, product_url,
                image_url=image_url, description=desc_text, available=available
            ))
        page += 1
        if page > 10:
            break
    print(f"  [OK] Found {len(listings)} listings")
    return listings


def scrape_panteek():
    """Panteek - Shopify store. Filters out Edward Lear."""
    print("[*] Scraping Panteek...")
    listings = []
    page = 1
    while True:
        url = f"https://www.panteek.com/collections/all/products.json?page={page}&limit=250"
        resp = fetch_page(url)
        if not resp:
            break
        data = resp.json()
        products = data.get("products", [])
        if not products:
            break
        for p in products:
            title = p.get("title", "")
            body = p.get("body_html", "")
            combined = (title + " " + body).lower()
            if "audubon" not in combined:
                continue
            if is_excluded(title, body):
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
            desc_text = BeautifulSoup(body, "html.parser").get_text(strip=True) if body else ""
            listings.append(make_listing(
                "Panteek", "panteek", title, price, product_url,
                image_url=image_url, description=desc_text, available=available
            ))
        page += 1
        if page > 20:
            break
    print(f"  [OK] Found {len(listings)} listings")
    return listings


def scrape_old_print_shop():
    """The Old Print Shop - fetches detail pages for high-res images."""
    print("[*] Scraping The Old Print Shop...")
    listings = []
    base_url = "https://oldprintshop.com/shop"

    for page_num in range(1, 6):
        params = f"?subjectdetail=1544&sort-price=high-to-low&page={page_num}"
        resp = fetch_page(f"{base_url}{params}")
        if not resp:
            break
        soup = BeautifulSoup(resp.text, "lxml")

        links = soup.find_all("a", href=re.compile(r'/product/\d+'))
        if not links:
            break

        seen_urls = set()
        for link in links:
            href = link.get("href", "")
            if not href or href in seen_urls:
                continue
            seen_urls.add(href)
            product_url = urljoin("https://oldprintshop.com", href)

            container = link
            for _ in range(5):
                if container.parent:
                    container = container.parent
                    text = container.get_text()
                    if '$' in text and len(text) > 20:
                        break

            text_content = container.get_text(separator="\n", strip=True)

            title_el = container.find("h2") or container.find("h3")
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                for line in text_content.split("\n"):
                    line = line.strip()
                    if len(line) > 10 and '$' not in line and 'Artist' not in line:
                        title = line
                        break

            if not title or is_excluded(title):
                continue

            price = None
            price_match = re.search(r'\$[\d,]+(?:\.\d{2})?', text_content)
            if price_match:
                price = safe_price(price_match.group())

            img = container.find("img")
            thumb_url = img.get("src", "") if img else None
            if thumb_url and not thumb_url.startswith("http"):
                thumb_url = urljoin("https://oldprintshop.com", thumb_url)

            listings.append(make_listing(
                "The Old Print Shop", "oldprintshop", title, price, product_url,
                image_url=thumb_url
            ))

        time.sleep(0.5)

    # Dedupe
    seen = set()
    deduped = []
    for l in listings:
        if l["url"] not in seen:
            seen.add(l["url"])
            deduped.append(l)
    listings = deduped

    # Fetch detail pages for high-res images
    print(f"  [..] Fetching {len(listings)} detail pages for high-res images...")
    for i, l in enumerate(listings):
        hi_res = _get_detail_image(l["url"], [
            "img.product-image", "img.main-image", ".product-detail img",
            ".product-image-container img", "#product-image img",
            "img[data-zoom]", ".gallery img", "figure img", ".product img"
        ])
        if hi_res:
            l["image_url"] = hi_res
        if (i + 1) % 10 == 0:
            print(f"    {i + 1}/{len(listings)} detail pages fetched")
        time.sleep(0.3)

    print(f"  [OK] Found {len(listings)} listings")
    return listings


def scrape_antique_audubon():
    """AntiqueAudubon.com - Weebly site. Fetches detail pages for high-res images."""
    print("[*] Scraping Antique Audubon...")
    listings = []

    urls = [
        ("https://www.antiqueaudubon.com/store/c32/Octavo-First-Edition-Birds", "Octavo 1st Ed"),
        ("https://www.antiqueaudubon.com/store/c33/Octavo-Later-Edition-Birds", "Octavo Later Ed"),
    ]

    for page_url, edition_hint in urls:
        resp = fetch_page(page_url)
        if not resp:
            continue
        soup = BeautifulSoup(resp.text, "lxml")

        products = soup.select(".wsite-com-product-wrap, .wsite-com-category-product")
        if not products:
            products = soup.find_all("div", class_=re.compile(r'product'))

        for prod in products:
            link = prod.find("a", href=True)
            if not link:
                continue
            product_url = urljoin(page_url, link["href"])

            title_el = (prod.find(class_=re.compile(r'product-title|product-name'))
                        or prod.find("h2") or prod.find("h3"))
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)

            if not title or is_excluded(title):
                continue

            price_el = prod.find(class_=re.compile(r'product-price|price'))
            price_text = price_el.get_text(strip=True) if price_el else ""
            price = safe_price(price_text)

            sale_el = prod.find(class_=re.compile(r'sale'))
            if sale_el:
                sale_price = safe_price(sale_el.get_text(strip=True))
                if sale_price:
                    price = sale_price

            img = prod.find("img")
            thumb_url = img.get("src", "") if img else None
            if thumb_url and not thumb_url.startswith("http"):
                thumb_url = urljoin(page_url, thumb_url)

            listings.append(make_listing(
                "Antique Audubon", "antiqueaudubon", title, price, product_url,
                image_url=thumb_url, edition=edition_hint
            ))

        time.sleep(0.5)

    # Fetch detail pages for high-res images
    print(f"  [..] Fetching {len(listings)} detail pages for high-res images...")
    for i, l in enumerate(listings):
        hi_res = _get_detail_image(l["url"], [
            ".wsite-com-product-images img", ".wsite-image img",
            "img.wsite-com-product-image", ".product-large img",
            "img[data-image-id]", ".wsite-image-border-none img",
            "figure img"
        ])
        if hi_res:
            l["image_url"] = hi_res
        if (i + 1) % 10 == 0:
            print(f"    {i + 1}/{len(listings)} detail pages fetched")
        time.sleep(0.3)

    print(f"  [OK] Found {len(listings)} listings")
    return listings


def scrape_audubon_art():
    """AudubonArt.com - WooCommerce site."""
    print("[*] Scraping Audubon Art...")
    listings = []

    category_urls = [
        "https://www.audubonart.com/product-category/john-james-audubon/birds-of-america/1st-edition-octavos-antique-originals/",
        "https://www.audubonart.com/product-category/audubon-1st-ed-octavo/",
    ]

    woo_headers = {
        **HEADERS,
        "Referer": "https://www.audubonart.com/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Ch-Ua": '"Chromium";v="131", "Google Chrome";v="131"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Cache-Control": "max-age=0",
    }

    for base_url in category_urls:
        for page_num in range(1, 6):
            url = base_url if page_num == 1 else f"{base_url}page/{page_num}/"
            resp = fetch_page(url, headers=woo_headers)
            if not resp:
                if page_num == 1:
                    continue
                break

            soup = BeautifulSoup(resp.text, "lxml")
            products = soup.select("li.product, .product, .wc-block-grid__product")

            if not products:
                break

            for prod in products:
                link = prod.find("a", href=re.compile(r'/product/'))
                if not link:
                    continue
                product_url = link.get("href", "")

                title_el = prod.find("h2") or prod.find(class_=re.compile(r'product.*title|title'))
                title = title_el.get_text(strip=True) if title_el else ""

                if not title or is_excluded(title):
                    continue

                price_el = prod.find(class_=re.compile(r'price'))
                price = safe_price(price_el.get_text(strip=True)) if price_el else None

                img = prod.find("img")
                image_url = img.get("src", "") if img else None

                if title and product_url:
                    listings.append(make_listing(
                        "Audubon Art", "audubonart", title, price, product_url,
                        image_url=image_url
                    ))

            time.sleep(0.5)

        if listings:
            break

    print(f"  [OK] Found {len(listings)} listings")
    return listings


def scrape_invaluable():
    """Invaluable.com - auction aggregator. Search for 'audubon octavo'."""
    print("[*] Scraping Invaluable...")
    listings = []

    search_headers = {
        **HEADERS,
        "Referer": "https://www.invaluable.com/",
    }

    search_urls = [
        "https://www.invaluable.com/auction-lot/search?keyword=audubon+octavo&upcoming=true",
        "https://www.invaluable.com/auction-lot/search?keyword=audubon+octavo&sortBy=itemStartDateDesc",
    ]

    for url in search_urls:
        resp = fetch_page(url, headers=search_headers, timeout=20)
        if not resp:
            continue

        soup = BeautifulSoup(resp.text, "lxml")

        # Look for JSON data in script tags
        for script in soup.find_all("script"):
            text = script.string or ""
            if "audubon" in text.lower() and ("lot" in text.lower() or "price" in text.lower()):
                for pattern in [r'__NEXT_DATA__\s*=\s*({.*?})\s*;',
                                r'window\.__data\s*=\s*({.*?})\s*;',
                                r'"lots"\s*:\s*(\[.*?\])',
                                r'"results"\s*:\s*(\[.*?\])']:
                    match = re.search(pattern, text, re.DOTALL)
                    if match:
                        try:
                            data = json.loads(match.group(1))
                            lots = _extract_invaluable_lots(data)
                            listings.extend(lots)
                        except json.JSONDecodeError:
                            pass

        # HTML fallback
        lot_cards = soup.select("[class*='lot-card'], [class*='LotCard'], .search-result-item, [data-lot-id]")
        for card in lot_cards:
            link = card.find("a", href=True)
            if not link:
                continue
            lot_url = urljoin("https://www.invaluable.com", link.get("href", ""))
            title_el = card.find("h3") or card.find("h2") or card.find(class_=re.compile(r'title|name'))
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)

            if not title or is_excluded(title):
                continue
            if "audubon" not in title.lower():
                continue

            price_el = card.find(class_=re.compile(r'price|estimate'))
            price = safe_price(price_el.get_text(strip=True)) if price_el else None

            img = card.find("img")
            image_url = img.get("src", "") or img.get("data-src", "") if img else None

            listings.append(make_listing(
                "Invaluable", "invaluable", title, price, lot_url,
                image_url=image_url
            ))

        if listings:
            break
        time.sleep(1)

    # Dedupe
    seen = set()
    deduped = []
    for l in listings:
        if l["url"] not in seen:
            seen.add(l["url"])
            deduped.append(l)

    print(f"  [OK] Found {len(deduped)} listings")
    return deduped


def _extract_invaluable_lots(data, depth=0):
    if depth > 6:
        return []
    lots = []
    if isinstance(data, dict):
        if "lotTitle" in data or ("title" in data and "saleTitle" in data):
            title = data.get("lotTitle", data.get("title", ""))
            if "audubon" in title.lower() and not is_excluded(title):
                url = data.get("url", data.get("lotUrl", ""))
                if url and not url.startswith("http"):
                    url = f"https://www.invaluable.com{url}"
                lots.append(make_listing(
                    "Invaluable", "invaluable", title,
                    safe_price(str(data.get("estimateLow", data.get("price", "")))),
                    url,
                    image_url=data.get("photoUrl", data.get("image", "")),
                ))
        for v in data.values():
            lots.extend(_extract_invaluable_lots(v, depth + 1))
    elif isinstance(data, list):
        for item in data:
            lots.extend(_extract_invaluable_lots(item, depth + 1))
    return lots


def scrape_liveauctioneers():
    """LiveAuctioneers.com - auction aggregator. Search for 'audubon octavo'."""
    print("[*] Scraping LiveAuctioneers...")
    listings = []

    search_headers = {
        **HEADERS,
        "Referer": "https://www.liveauctioneers.com/",
    }

    url = "https://www.liveauctioneers.com/search/?keyword=audubon+octavo"
    resp = fetch_page(url, headers=search_headers, timeout=20)
    if not resp:
        print("  [!] LiveAuctioneers may require browser-based access")
        return listings

    soup = BeautifulSoup(resp.text, "lxml")

    # Check for JSON data in script tags
    for script in soup.find_all("script"):
        text = script.string or ""
        if "audubon" in text.lower() and ("item" in text.lower() or "lot" in text.lower()):
            for pattern in [r'__NEXT_DATA__\s*=\s*({.*?})\s*</script',
                            r'window\.__PRELOADED_STATE__\s*=\s*({.*?})\s*;',
                            r'"items"\s*:\s*(\[.*?\])',
                            r'"lots"\s*:\s*(\[.*?\])']:
                match = re.search(pattern, text, re.DOTALL)
                if match:
                    try:
                        data = json.loads(match.group(1))
                        lots = _extract_la_lots(data)
                        listings.extend(lots)
                    except json.JSONDecodeError:
                        pass

    # HTML fallback
    if not listings:
        items = soup.select("[class*='item-card'], [class*='ItemCard'], .search-item, [data-item-id]")
        for item in items:
            link = item.find("a", href=True)
            if not link:
                continue
            lot_url = urljoin("https://www.liveauctioneers.com", link.get("href", ""))
            title_el = item.find("h3") or item.find("h2") or item.find(class_=re.compile(r'title'))
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)

            if not title or is_excluded(title):
                continue
            if "audubon" not in title.lower():
                continue

            price_el = item.find(class_=re.compile(r'price|estimate'))
            price = safe_price(price_el.get_text(strip=True)) if price_el else None

            img = item.find("img")
            image_url = img.get("src", "") or img.get("data-src", "") if img else None

            listings.append(make_listing(
                "LiveAuctioneers", "liveauctioneers", title, price, lot_url,
                image_url=image_url
            ))

    # Dedupe
    seen = set()
    deduped = []
    for l in listings:
        if l["url"] not in seen:
            seen.add(l["url"])
            deduped.append(l)

    print(f"  [OK] Found {len(deduped)} listings")
    return deduped


def _extract_la_lots(data, depth=0):
    if depth > 6:
        return []
    lots = []
    if isinstance(data, dict):
        if "title" in data and ("itemId" in data or "lotNumber" in data or "currentBid" in data):
            title = data.get("title", "")
            if "audubon" in title.lower() and not is_excluded(title):
                item_id = data.get("itemId", data.get("id", ""))
                url = data.get("url", f"https://www.liveauctioneers.com/item/{item_id}")
                if url and not url.startswith("http"):
                    url = f"https://www.liveauctioneers.com{url}"
                lots.append(make_listing(
                    "LiveAuctioneers", "liveauctioneers", title,
                    safe_price(str(data.get("currentBid", data.get("estimate", data.get("price", ""))))),
                    url,
                    image_url=data.get("imageUrl", data.get("image", data.get("photo", ""))),
                ))
        for v in data.values():
            lots.extend(_extract_la_lots(v, depth + 1))
    elif isinstance(data, list):
        for item in data:
            lots.extend(_extract_la_lots(item, depth + 1))
    return lots


# ============================================================
# CROSS-SOURCE DEDUPLICATION
# ============================================================

def _normalize_title(title):
    t = title.lower().strip()
    for prefix in ["audubon", "j.j. audubon", "john james audubon", "jj audubon"]:
        t = t.replace(prefix, "")
    for ed in ["1st ed", "first ed", "2nd ed", "octavo", "royal octavo",
               "hand colored", "hand-colored", "lithograph", "pl.", "plate",
               "birds of america", "bowen"]:
        t = t.replace(ed, "")
    t = re.sub(r'[^\w\s]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def deduplicate_cross_source(listings):
    """Remove duplicate listings ONLY between Invaluable and LiveAuctioneers,
    which often list the same auction lots. All dealer listings are kept as-is."""
    auction_sources = {"invaluable", "liveauctioneers"}
    
    # Separate auction listings from dealer listings
    dealer_listings = [l for l in listings if l.get("source_key") not in auction_sources]
    auction_listings = [l for l in listings if l.get("source_key") in auction_sources]
    
    if len(auction_listings) <= 1:
        return listings  # Nothing to dedup
    
    # Dedup auction listings by normalized title
    seen = {}
    deduped_auctions = []
    dup_count = 0
    for l in auction_listings:
        norm = _normalize_title(l["title"])
        key = norm[:50] if len(norm) > 5 else l["id"]
        if key not in seen:
            seen[key] = l
            deduped_auctions.append(l)
        else:
            dup_count += 1
    
    if dup_count > 0:
        print(f"  [Dedup] Removed {dup_count} auction duplicates (Invaluable/LiveAuctioneers)")
    
    return dealer_listings + deduped_auctions


# ============================================================
# MAIN
# ============================================================

def load_previous_listings():
    path = DATA_DIR / "listings.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"listings": [], "last_run": None, "history": []}


def save_listings(data):
    path = DATA_DIR / "listings.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def run_scraper():
    print("=" * 60)
    print(f"[Audubon] Audubon Print Monitor - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
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
        ("Invaluable", scrape_invaluable),
        ("LiveAuctioneers", scrape_liveauctioneers),
    ]

    for name, scraper_fn in scrapers:
        try:
            results = scraper_fn()
            all_listings.extend(results)
        except Exception as e:
            print(f"  [X] {name} failed: {e}")
            errors.append({"source": name, "error": str(e)})
        time.sleep(1)

    # Cross-source deduplication
    all_listings = deduplicate_cross_source(all_listings)

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

    for l in all_listings:
        src = l["source"]
        if src not in output["sources"]:
            output["sources"][src] = {"count": 0, "new": 0}
        output["sources"][src]["count"] += 1
        if l.get("is_new"):
            output["sources"][src]["new"] += 1

    output["history"].append({
        "date": now,
        "total": len(all_listings),
        "new": new_count,
        "by_source": {k: v["count"] for k, v in output["sources"].items()}
    })
    output["history"] = output["history"][-90:]

    save_listings(output)

    print()
    print("=" * 60)
    print(f"[Stats] Results: {len(all_listings)} total listings, {new_count} new")
    for src, stats in output["sources"].items():
        new_badge = f" ({stats['new']} new)" if stats["new"] else ""
        print(f"   {src}: {stats['count']}{new_badge}")
    if errors:
        print(f"[!]  {len(errors)} source(s) had errors")
    print(f"[Saved] Saved to {DATA_DIR / 'listings.json'}")
    print("=" * 60)

    return output


if __name__ == "__main__":
    run_scraper()
