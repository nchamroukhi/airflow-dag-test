import json
import os
import argparse
import logging
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

# ---------------- Logging Setup ---------------- #
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------- Config ---------------- #
TOKEN = os.environ.get("BROWSERLESS_TOKEN")
OXY_USER = os.getenv("OXYLAB_ISP_USERNAME")
OXY_PASS = os.getenv("OXYLAB_ISP_PASSWORD")
OXY_HOST = "isp.oxylabs.io:8007"

config = {
    "topic_container_selector": "div.o-container section",
    "product_urls_xpath": "a.c-card--link",
}

# ---------------- Extract Related Products ---------------- #
def extract_related_products(topic_url, topic_name, soup_topic):
    try:
        products_elements = soup_topic.select(config["product_urls_xpath"])
        all_topics = []

        for product_element in products_elements:
            product_url = product_element.get("href")
            if not product_url:
                continue

            product_name = product_element.find("span", class_="c-product-card__heading")
            if not product_name:
                product_name = product_element.find("h2", class_="c-type-display-large")
            if not product_name:
                logger.warning("⚠️ Skipped product (no name found).")
                continue

            product_name = product_name.text.strip()
            absolute_url = urljoin(topic_url, product_url)

            topic = {
                "name": product_name,
                "sub_topics": [],
                "breadcrumbs": (
                    ["products", topic_name, product_name]
                    if topic_name else ["products", product_name]
                ),
                "url": absolute_url,
            }
            logger.info(f"🛒 Found product: {product_name} ({absolute_url})")
            all_topics.append(topic)

        return all_topics

    except Exception as e:
        logger.error(f"❌ Error extracting related products: {e}")
        return []

# ---------------- Get Topic Structure ---------------- #
def get_topic_structure(url):
    """
    Scrapes the 'Products' mega-menu from a given URL to build a hierarchical topic structure.
    """
    topic_structure = []
    topics_html = ""

    with sync_playwright() as p:
        browser = None
        try:
            logger.info("🚀 Launching remote Chrome browser...")
            browser = p.chromium.connect(
                f"wss://production-sfo.browserless.io/chrome/playwright?token={TOKEN}"
            )
            device = p.devices["Desktop Chrome"]
            context = browser.new_context(
                **device,
                proxy={
                    "server": f"http://{OXY_HOST}",
                    "username": OXY_USER,
                    "password": OXY_PASS,
                }
            )
            page = context.new_page()

            logger.info(f"🌐 Navigating to {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)

            logger.info("⏳ Waiting for content to load...")
            page.wait_for_timeout(3000)
            page.wait_for_selector(config["topic_container_selector"], state="visible")

            topics_html = page.content()
            logger.info("✅ Page content successfully retrieved.")

        except Exception as e:
            logger.error(f"❌ Error loading page: {e}")
        finally:
            if browser:
                browser.close()
                logger.info("🛑 Browser session closed.")

    if not topics_html:
        logger.warning("⚠️ No HTML content retrieved, returning empty structure.")
        return topic_structure

    try:
        soup = BeautifulSoup(topics_html, "lxml")
        main_topics = soup.select(config["topic_container_selector"])

        for topic in main_topics:
            topic_elem = topic.find("h2")
            topic_name = topic_elem.text.strip() if topic_elem else ""

            main_topic = {
                "name": topic_name if topic_name else "top products",
                "sub_topics": [],
                "breadcrumbs": ["products", topic_name] if topic_name else ["products"],
                "url": url,
            }

            logger.info(f"📂 Processing main topic: {topic_name or 'Unnamed'}")

            nested_topics = extract_related_products(url, topic_name, topic)
            if nested_topics:
                main_topic["sub_topics"].extend(nested_topics)

            topic_structure.append(main_topic)

        logger.info("🎉 Successfully parsed navigation structure.")

    except Exception as e:
        logger.error(f"❌ Error parsing topics: {e}")

    return topic_structure

# ---------------- Main ---------------- #
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="📁 Output file for the topic structure JSON.")
    args = parser.parse_args()

    output_dir = os.path.dirname(args.out)
    os.makedirs(output_dir, exist_ok=True)

    try:
        structure = get_topic_structure("https://www.raspberrypi.com/products/")
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(structure, f, indent=4, ensure_ascii=False)
        logger.info(f"💾 Topic structure saved to {args.out}")
    except Exception as e:
        logger.error(f"❌ Failed to save output JSON: {e}")