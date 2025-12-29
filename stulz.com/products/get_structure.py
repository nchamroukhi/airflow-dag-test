import argparse
import requests
from lxml import html
import os
import json
import logging
from requests.exceptions import ChunkedEncodingError

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )
    return logging.getLogger(__name__)

logger = setup_logging()
BASE_URL = "https://www.stulz.com/"
_session = None

def get_session():
    global _session
    if _session is None:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        _session = requests.Session()
        _session.headers.update(headers)
    return _session

def safe_get(url, retries=3):
    session = get_session()
    for i in range(retries):
        try:
            response = session.get(url, timeout=20, allow_redirects=True)
            return response
        except ChunkedEncodingError:
            if i < retries - 1:
                continue
            raise
        except Exception as e:
            if i < retries - 1:
                continue
            raise

def make_absolute(url):
    if not url:
        return "#"
    if url.startswith("http"):
        return url
    if url.startswith("#"):
        return BASE_URL.rstrip("/") + url
    if not url.startswith("/"):
        url = "/" + url
    return BASE_URL.rstrip("/") + url

def get_categories():
    logger.info(f"Fetching categories from {BASE_URL}")
    res = safe_get(BASE_URL)
    tree = html.fromstring(res.text)
    xpath = "(//ul[@class='header-subnav__list'])[1]//a"
    links = tree.xpath(xpath)
    logger.info(f"Found {len(links)} category links")
    categories = []
    for link in links:
        text = link.text_content().strip()
        href = link.get('href', '').strip()
        if not text or not href:
            continue
        url = make_absolute(href)
        categories.append({
            "name": text,
            "url": url,
            "sub_topics": [],
            "breadcrumbs": ["Products", text]
        })
    logger.info(f"Extracted {len(categories)} categories")
    return categories

def crawl_category(category):
    logger.info(f"Crawling category: {category['name']} (URL: {category['url']})")
    if not category['url'] or category['url'] == "#" or not category['url'].startswith("http"):
        return
    try:
        res = safe_get(category['url'])
        tree = html.fromstring(res.text)
        prds_container = tree.xpath("//prds-container[@api-url]")
        if not prds_container:
            logger.warning(f"  No prds-container found, trying API directly")
            api_url = f"{category['url']}?tx_products_products%5BproductCategoryId%5D=&tx_products_products%5BproductCountryId%5D=35&tx_products_products%5BproductDetailPid%5D=811&type=1200"
        else:
            api_url_attr = prds_container[0].get('api-url', '')
            if api_url_attr.startswith('/'):
                api_url = BASE_URL.rstrip('/') + api_url_attr
            elif api_url_attr.startswith('http'):
                api_url = api_url_attr
            else:
                api_url = make_absolute(api_url_attr)
        logger.info(f"  Fetching API: {api_url}")
        session = get_session()
        api_headers = {'Accept': 'application/json'}
        api_res = session.get(api_url, headers=api_headers, timeout=20)
        if api_res.status_code != 200:
            logger.warning(f"  API returned status {api_res.status_code}")
            return
        data = api_res.json()
        products = data.get('products', [])
        logger.info(f"  Found {len(products)} products from API")
        seen = set()
        for product in products:
            name = product.get('name', '').strip()
            url = product.get('url', '').strip()
            if not name or not url:
                continue
            if (name, url) in seen:
                continue
            seen.add((name, url))
            category['sub_topics'].append({
                "name": name,
                "url": url,
                "sub_topics": [],
                "breadcrumbs": category['breadcrumbs'] + [name]
            })
        logger.info(f"  Extracted {len(category['sub_topics'])} items")
    except Exception as e:
        logger.error(f"  Error crawling category {category['url']}: {e}")

def main(out_path):
    logger.info("Starting stulz.com structure crawl")
    categories = get_categories()
    for category in categories:
        crawl_category(category)
    total_subcategories = sum(len(cat["sub_topics"]) for cat in categories)
    logger.info(f"Found {len(categories)} categories and {total_subcategories} subcategories/products")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(categories, f, indent=4, ensure_ascii=False)
    logger.info(f"Structure saved to {out_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="stulz.com topic scraper")
    parser.add_argument("--out", required=True, help="Output JSON file path")
    args = parser.parse_args()
    main(args.out)
