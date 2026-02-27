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
import random
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote_plus

# Optional: cloudscraper for Cloudflare-protected sites (pip install cloudscraper)
try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False

# Optional: curl_cffi for TLS fingerprint impersonation (pip install curl_cffi)
try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# Rotate User-Agents to reduce fingerprinting
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
]

def _random_ua():
    return random.choice(_USER_AGENTS)

HEADERS = {
    "User-Agent": _USER_AGENTS[0],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Shared cloudscraper session (created lazily)
_cloudscraper_session = None

def get_cloudscraper():
    """Get or create a cloudscraper session for Cloudflare-protected sites."""
    global _cloudscraper_session
    if _cloudscraper_session is None and HAS_CLOUDSCRAPER:
        _cloudscraper_session = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True},
        )
    return _cloudscraper_session

# Titles containing these (case-insensitive) are skipped
TITLE_EXCLUDE = [
    "edward lear",
    "lear's",
    "john gould",
    "quadruped",
    "oppenheimer",
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
        try:
            data = resp.json()
        except Exception as e:
            print(f"  [!] JSON parse error on page {page}: {e}")
            break
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
        try:
            data = resp.json()
        except Exception as e:
            print(f"  [!] JSON parse error on page {page}: {e}")
            break
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

    # Use thumbnail images (skip detail page fetching for speed)
    print(f"  [OK] Found {len(listings)} listings")
    return listings


def scrape_antique_audubon():
    """AntiqueAudubon.com - Weebly site. Uses thumbnail images."""
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

    # Use thumbnail images (skip detail page fetching for speed)
    print(f"  [OK] Found {len(listings)} listings")
    return listings


def scrape_audubon_art():
    """AudubonArt.com - WooCommerce site behind Cloudflare.
    Strategy: cloudscraper > curl_cffi > session with cookies > plain requests.
    """
    print("[*] Scraping Audubon Art...")
    listings = []

    category_urls = [
        "https://www.audubonart.com/product-category/john-james-audubon/birds-of-america/1st-edition-octavos-antique-originals/",
        "https://www.audubonart.com/product-category/john-james-audubon/birds-of-america/",
        "https://www.audubonart.com/product-category/john-james-audubon/",
    ]

    # --- Strategy 1: cloudscraper (best for Cloudflare) ---
    def _try_cloudscraper(url):
        scraper = get_cloudscraper()
        if not scraper:
            return None
        try:
            resp = scraper.get(url, timeout=20)
            resp.raise_for_status()
            return resp
        except Exception as e:
            print(f"  [!] cloudscraper failed for {url}: {e}")
            return None

    # --- Strategy 2: curl_cffi (TLS fingerprint impersonation) ---
    def _try_curl_cffi(url):
        if not HAS_CURL_CFFI:
            return None
        try:
            resp = curl_requests.get(url, impersonate="chrome131", timeout=20)
            resp.raise_for_status()
            return resp
        except Exception as e:
            print(f"  [!] curl_cffi failed for {url}: {e}")
            return None

    # --- Strategy 3: requests.Session with homepage warm-up ---
    def _try_session(url):
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": _random_ua(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Sec-Ch-Ua": '"Chromium";v="131", "Google Chrome";v="131"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Cache-Control": "max-age=0",
            })
            # Visit homepage first to get cookies
            session.get("https://www.audubonart.com/", timeout=15)
            time.sleep(1)
            # Now fetch the category page with Referer set
            session.headers["Referer"] = "https://www.audubonart.com/"
            session.headers["Sec-Fetch-Site"] = "same-origin"
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
            return resp
        except Exception as e:
            print(f"  [!] Session approach failed for {url}: {e}")
            return None

    def _fetch_with_fallback(url):
        """Try each strategy in order until one works."""
        for strategy_name, strategy_fn in [
            ("cloudscraper", _try_cloudscraper),
            ("curl_cffi", _try_curl_cffi),
            ("session", _try_session),
        ]:
            resp = strategy_fn(url)
            if resp and resp.status_code == 200 and len(resp.text) > 1000:
                return resp
        print(f"  [!] All strategies failed for {url}")
        return None

    for base_url in category_urls:
        for page_num in range(1, 6):
            url = base_url if page_num == 1 else f"{base_url}page/{page_num}/"
            resp = _fetch_with_fallback(url)
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
                price = None
                if price_el:
                    # WooCommerce: sale price in <ins>, regular in <del> or <bdi>
                    sale_el = price_el.find("ins")
                    if sale_el:
                        price = safe_price(sale_el.get_text(strip=True))
                    if not price:
                        # Get the last price amount (usually the current/sale price)
                        amounts = price_el.find_all(class_=re.compile(r'amount'))
                        if amounts:
                            price = safe_price(amounts[-1].get_text(strip=True))
                    if not price:
                        price = safe_price(price_el.get_text(strip=True))

                img = prod.find("img")
                image_url = None
                if img:
                    # WooCommerce lazy-load: real URL in data-src or data-lazy-src
                    image_url = (img.get("data-src") or img.get("data-lazy-src")
                                 or img.get("data-original") or img.get("srcset", "").split(",")[0].split(" ")[0]
                                 or img.get("src", ""))
                    # Skip SVG placeholders
                    if image_url and image_url.startswith("data:"):
                        image_url = None

                if title and product_url:
                    listings.append(make_listing(
                        "Audubon Art", "audubonart", title, price, product_url,
                        image_url=image_url
                    ))

            time.sleep(1)

    # Dedupe across overlapping categories
    seen = set()
    deduped = []
    for l in listings:
        if l["url"] not in seen:
            seen.add(l["url"])
            deduped.append(l)
    listings = deduped

    print(f"  [OK] Found {len(listings)} listings")
    return listings


def scrape_invaluable():
    """Invaluable.com - auction aggregator with heavy bot protection.
    Strategy: Try internal API first, then cloudscraper/curl_cffi for HTML.
    """
    print("[*] Scraping Invaluable...")
    listings = []

    # --- Strategy 1: Internal search API (JSON) ---
    # Invaluable's frontend calls internal API endpoints for search results
    api_urls = [
        "https://www.invaluable.com/api/search?keyword=audubon+octavo&upcoming=true&limit=96",
        "https://www.invaluable.com/api/auction-lot/search?keyword=audubon+octavo&upcoming=true&limit=96",
    ]

    api_headers = {
        "User-Agent": _random_ua(),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.invaluable.com/auction-lot/search?keyword=audubon+octavo",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Connection": "keep-alive",
    }

    def _try_api():
        results = []
        # Try curl_cffi first (best TLS fingerprinting)
        for api_url in api_urls:
            if HAS_CURL_CFFI:
                try:
                    resp = curl_requests.get(
                        api_url, headers=api_headers,
                        impersonate="chrome131", timeout=20
                    )
                    if resp.status_code == 200:
                        try:
                            data = resp.json()
                            lots = _extract_invaluable_lots(data)
                            if lots:
                                print(f"  [OK] Invaluable API returned {len(lots)} lots via curl_cffi")
                                return lots
                        except (json.JSONDecodeError, ValueError):
                            pass
                except Exception as e:
                    print(f"  [!] curl_cffi API attempt failed: {e}")

            # Try cloudscraper
            scraper = get_cloudscraper()
            if scraper:
                try:
                    resp = scraper.get(api_url, headers=api_headers, timeout=20)
                    if resp.status_code == 200:
                        try:
                            data = resp.json()
                            lots = _extract_invaluable_lots(data)
                            if lots:
                                print(f"  [OK] Invaluable API returned {len(lots)} lots via cloudscraper")
                                return lots
                        except (json.JSONDecodeError, ValueError):
                            pass
                except Exception as e:
                    print(f"  [!] cloudscraper API attempt failed: {e}")
        return results

    # --- Strategy 2: Full page scrape with bot bypass ---
    def _try_page_scrape():
        results = []
        search_urls = [
            "https://www.invaluable.com/auction-lot/search?keyword=audubon+octavo&upcoming=true",
            "https://www.invaluable.com/auction-lot/search?keyword=audubon+octavo&sortBy=itemStartDateDesc",
        ]

        for url in search_urls:
            resp = None

            # Try curl_cffi
            if HAS_CURL_CFFI:
                try:
                    resp = curl_requests.get(url, impersonate="chrome131", timeout=20)
                    if resp.status_code != 200:
                        resp = None
                except Exception as e:
                    resp = None

            # Try cloudscraper
            if not resp:
                scraper = get_cloudscraper()
                if scraper:
                    try:
                        resp = scraper.get(url, timeout=20)
                        if resp.status_code != 200:
                            resp = None
                    except Exception as e:
                        resp = None

            # Try session with warm-up
            if not resp:
                try:
                    session = requests.Session()
                    session.headers.update({
                        "User-Agent": _random_ua(),
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Accept-Encoding": "gzip, deflate",
                        "Sec-Fetch-Dest": "document",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Site": "none",
                        "Sec-Fetch-User": "?1",
                    })
                    # Warm up with homepage
                    home_resp = session.get("https://www.invaluable.com/", timeout=15)
                    time.sleep(1.5)
                    session.headers["Referer"] = "https://www.invaluable.com/"
                    session.headers["Sec-Fetch-Site"] = "same-origin"
                    resp = session.get(url, timeout=20)
                    if resp.status_code != 200:
                        resp = None
                except Exception as e:
                    print(f"  [!] Session approach failed: {e}")
                    resp = None

            if not resp:
                print(f"  [!] All strategies failed for {url}")
                continue

            soup = BeautifulSoup(resp.text, "lxml")

            # Look for __NEXT_DATA__ (Next.js server-rendered data)
            next_data_script = soup.find("script", id="__NEXT_DATA__")
            if next_data_script and next_data_script.string:
                try:
                    data = json.loads(next_data_script.string)
                    lots = _extract_invaluable_lots(data)
                    if lots:
                        results.extend(lots)
                        break
                except json.JSONDecodeError:
                    pass

            # Fallback: search all script tags for JSON data
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
                                results.extend(lots)
                            except json.JSONDecodeError:
                                pass

            # HTML fallback
            if not results:
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

                    results.append(make_listing(
                        "Invaluable", "invaluable", title, price, lot_url,
                        image_url=image_url
                    ))

            if results:
                break
            time.sleep(1)

        return results

    # Execute strategies
    listings = _try_api()
    if not listings:
        listings = _try_page_scrape()

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
    """Extract lots from Invaluable API response.
    Primary structure: data.itemViewList[].itemView with nested lot details.
    """
    if depth > 6:
        return []
    lots = []

    if isinstance(data, dict):
        # Primary: itemViewList array (the actual API response format)
        if "itemViewList" in data and isinstance(data["itemViewList"], list):
            for item_wrapper in data["itemViewList"]:
                iv = item_wrapper.get("itemView", {}) if isinstance(item_wrapper, dict) else {}
                if not iv:
                    continue
                title = iv.get("title", "")
                if not title or "audubon" not in title.lower() or is_excluded(title):
                    continue

                # Build URL from ref (always use invaluable.com, not auctionzip)
                ref = iv.get("ref", "")
                slug = iv.get("slug", "")
                if slug:
                    url = f"https://www.invaluable.com/auction-lot/{slug}-{ref}"
                elif ref:
                    url = f"https://www.invaluable.com/auction-lot/{ref}"
                else:
                    url = iv.get("url", iv.get("lotUrl", ""))
                    if url and not url.startswith("http"):
                        url = f"https://www.invaluable.com{url}"

                # Price: priceResult (hammer) > estimateLow > price (starting)
                # Use or-chain so 0/0.0 (unsold) falls through
                price_val = iv.get("priceResult") or iv.get("estimateLow") or iv.get("price") or ""
                price = safe_price(str(price_val))

                # Image from photos array
                image_url = None
                photos = iv.get("photos", [])
                if photos and isinstance(photos, list) and isinstance(photos[0], dict):
                    p = photos[0]
                    fname = (p.get("mediumFileName") or p.get("thumbnailFileName")
                             or p.get("fileName") or p.get("largeFileName") or "")
                    if fname:
                        image_url = f"https://image.invaluable.com/housePhotos/{fname}"

                lots.append(make_listing(
                    "Invaluable", "invaluable", title, price, url,
                    image_url=image_url
                ))
            return lots

        # Fallback: older format with lotTitle
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

        # Recurse into nested dicts
        for v in data.values():
            lots.extend(_extract_invaluable_lots(v, depth + 1))

    elif isinstance(data, list):
        for item in data:
            lots.extend(_extract_invaluable_lots(item, depth + 1))
    return lots


def scrape_liveauctioneers():
    """LiveAuctioneers.com - React SPA with heavy bot protection.
    Strategy: Try internal search API first, then page scrape with bypass.
    """
    print("[*] Scraping LiveAuctioneers...")
    listings = []

    # --- Strategy 1: Internal search API ---
    # LiveAuctioneers uses internal API endpoints for search
    api_urls = [
        "https://www.liveauctioneers.com/api/v1/search?keyword=audubon+octavo&sort=-relevance&status=online&limit=96",
        "https://www.liveauctioneers.com/api/search?keyword=audubon+octavo&sort=-relevance&limit=96",
    ]

    api_headers = {
        "User-Agent": _random_ua(),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.liveauctioneers.com/search/?keyword=audubon+octavo",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }

    def _try_api():
        for api_url in api_urls:
            # Try curl_cffi
            if HAS_CURL_CFFI:
                try:
                    resp = curl_requests.get(
                        api_url, headers=api_headers,
                        impersonate="chrome131", timeout=20
                    )
                    if resp.status_code == 200:
                        try:
                            data = resp.json()
                            lots = _extract_la_lots(data)
                            if lots:
                                print(f"  [OK] LA API returned {len(lots)} lots via curl_cffi")
                                return lots
                        except (json.JSONDecodeError, ValueError):
                            pass
                except Exception as e:
                    print(f"  [!] curl_cffi LA API failed: {e}")

            # Try cloudscraper
            scraper = get_cloudscraper()
            if scraper:
                try:
                    resp = scraper.get(api_url, headers=api_headers, timeout=20)
                    if resp.status_code == 200:
                        try:
                            data = resp.json()
                            lots = _extract_la_lots(data)
                            if lots:
                                print(f"  [OK] LA API returned {len(lots)} lots via cloudscraper")
                                return lots
                        except (json.JSONDecodeError, ValueError):
                            pass
                except Exception:
                    pass
        return []

    # --- Strategy 2: Page scrape with bot bypass ---
    def _try_page_scrape():
        url = "https://www.liveauctioneers.com/search/?keyword=audubon+octavo"
        resp = None

        # Try curl_cffi
        if HAS_CURL_CFFI:
            try:
                resp = curl_requests.get(url, impersonate="chrome131", timeout=20)
                if resp.status_code != 200:
                    print(f"  [!] curl_cffi returned {resp.status_code}")
                    resp = None
            except Exception as e:
                print(f"  [!] curl_cffi page scrape failed: {e}")

        # Try cloudscraper
        if not resp:
            scraper = get_cloudscraper()
            if scraper:
                try:
                    resp = scraper.get(url, timeout=20)
                    if resp.status_code != 200:
                        resp = None
                except Exception as e:
                    print(f"  [!] cloudscraper page scrape failed: {e}")

        # Try session
        if not resp:
            try:
                session = requests.Session()
                session.headers.update({
                    "User-Agent": _random_ua(),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                })
                session.get("https://www.liveauctioneers.com/", timeout=15)
                time.sleep(1.5)
                session.headers["Referer"] = "https://www.liveauctioneers.com/"
                session.headers["Sec-Fetch-Site"] = "same-origin"
                resp = session.get(url, timeout=20)
                if resp.status_code != 200:
                    resp = None
            except Exception as e:
                print(f"  [!] Session approach failed: {e}")

        if not resp:
            print("  [!] All page scrape strategies failed for LiveAuctioneers")
            return []

        results = []
        soup = BeautifulSoup(resp.text, "lxml")

        # Strategy A: Parse window.__data (LiveAuctioneers' primary data store)
        for script in soup.find_all("script"):
            text = script.string or ""
            if text.startswith("window.__data="):
                try:
                    json_str = text[len("window.__data="):]
                    # Replace JS undefined/NaN with null for valid JSON
                    json_str = re.sub(r'\bundefined\b', 'null', json_str)
                    json_str = re.sub(r'\bNaN\b', 'null', json_str)
                    # Use raw_decode to stop at end of first JSON object
                    # (there may be trailing JS statements after the object)
                    decoder = json.JSONDecoder()
                    data, _ = decoder.raw_decode(json_str)
                    lots = _extract_la_lots(data)
                    if lots:
                        print(f"  [OK] Extracted {len(lots)} lots from window.__data")
                        results.extend(lots)
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"  [!] Failed to parse window.__data: {e}")
                break

        # Strategy B: Check for __NEXT_DATA__
        if not results:
            next_data = soup.find("script", id="__NEXT_DATA__")
            if next_data and next_data.string:
                try:
                    data = json.loads(next_data.string)
                    lots = _extract_la_lots(data)
                    results.extend(lots)
                except json.JSONDecodeError:
                    pass

        # Strategy C: Regex fallback for other JSON patterns
        if not results:
            for script in soup.find_all("script"):
                text = script.string or ""
                if "audubon" in text.lower() and ("item" in text.lower() or "lot" in text.lower()):
                    for pattern in [r'window\.__data\s*=\s*({.*})\s*;?\s*$',
                                    r'window\.__PRELOADED_STATE__\s*=\s*({.*?})\s*;',
                                    r'"items"\s*:\s*(\[.*?\])',
                                    r'"lots"\s*:\s*(\[.*?\])']:
                        match = re.search(pattern, text, re.DOTALL)
                        if match:
                            try:
                                raw = re.sub(r'\bundefined\b', 'null', match.group(1))
                                data = json.loads(raw)
                                lots = _extract_la_lots(data)
                                results.extend(lots)
                            except json.JSONDecodeError:
                                pass

        # Strategy D: HTML fallback (Tailwind class patterns)
        if not results:
            # Find links to /item/ pages
            item_links = soup.find_all("a", href=re.compile(r'/item/\d+'))
            seen_urls = set()
            for link in item_links:
                href = link.get("href", "")
                lot_url = urljoin("https://www.liveauctioneers.com", href)
                if lot_url in seen_urls:
                    continue
                seen_urls.add(lot_url)

                # Walk up to find container with title and price
                container = link
                for _ in range(5):
                    if container.parent:
                        container = container.parent

                title = ""
                # Look for title text in the link or nearby elements
                title_el = (link.find("h3") or link.find("h2") or
                           link.find(class_=re.compile(r'title')) or
                           container.find("h3") or container.find("h2"))
                if title_el:
                    title = title_el.get_text(strip=True)
                elif link.get_text(strip=True):
                    title = link.get_text(strip=True)

                if not title or is_excluded(title):
                    continue
                if "audubon" not in title.lower():
                    continue

                price = None
                price_el = container.find(string=re.compile(r'\$[\d,]+'))
                if price_el:
                    price = safe_price(price_el)

                img = container.find("img")
                image_url = img.get("src", "") or img.get("data-src", "") if img else None

                results.append(make_listing(
                    "LiveAuctioneers", "liveauctioneers", title, price, lot_url,
                    image_url=image_url
                ))

        return results

    # Execute strategies
    listings = _try_api()
    if not listings:
        listings = _try_page_scrape()

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

                # Price: salePrice (sold) > leadingBid (current) > startPrice > estimate
                price_val = (data.get("salePrice") or data.get("leadingBid")
                             or data.get("startPrice") or data.get("lowBidEstimate")
                             or data.get("currentBid") or "")
                price = safe_price(str(price_val)) if price_val else None

                # Construct image URL from itemId + catalogId
                image_url = None
                catalog_id = data.get("catalogId", data.get("saleId", ""))
                if item_id and catalog_id:
                    image_url = f"https://p1.liveauctioneers.com/{catalog_id}/{item_id}_1_lg.jpg"

                lots.append(make_listing(
                    "LiveAuctioneers", "liveauctioneers", title, price, url,
                    image_url=image_url,
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

    # Report available bypass libraries
    bypass_status = []
    if HAS_CLOUDSCRAPER:
        bypass_status.append("cloudscraper ✓")
    else:
        bypass_status.append("cloudscraper ✗ (pip install cloudscraper)")
    if HAS_CURL_CFFI:
        bypass_status.append("curl_cffi ✓")
    else:
        bypass_status.append("curl_cffi ✗ (pip install curl_cffi)")
    print(f"[Deps] {' | '.join(bypass_status)}")
    if not HAS_CLOUDSCRAPER and not HAS_CURL_CFFI:
        print("[!] No bypass libraries installed - protected sites will likely fail")
        print("    Install with: pip install cloudscraper curl_cffi")
    print()

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
