import os
import json
import requests
import logging
import mimetypes
import argparse
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright
from datetime import datetime
from markdownify import markdownify as md
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
    "documentation_xpaths": ["a[href$='.pdf']"],
    "product_datasheet_selector": "a[href$='.pdf']",
    "image_xpaths": ["picture img"],
    "overview_text_xpath": [
        "div.rp-space-y-5",
        "div.c-product-hero__description",
        "p.font-normal.leading-normal",
        "div.sl-pi400-container"
    ],
    "block_diagram_xpath": ["div.slick-list a[aria-label*='diagram'] img"],
    "specifications_xpath": [
        "div.SpecsPanel-module--rich-text--febdb",
        "div.c-wysiwyg.c-product-slice__content"
    ],
}

# ---------------- File Downloader ---------------- #
def download_file(url, folder, file_type, use_this_name=None, file_date=None, version=None, description=None):
    if not url.startswith("http"):
        return None
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/116.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://files.latticesemi.com/",
    }
    try:
        response = requests.get(url, headers=headers, timeout=120, stream=True)
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "").lower()
        allowed_types = (
            content_type.startswith("application/pdf")
            or content_type.startswith("image/")
            or content_type.startswith("text/csv")
            or content_type.startswith("application/csv")
            or content_type.startswith("text/html")
            or content_type.startswith("video/")
        )
        if not allowed_types:
            logger.warning(f"⚠️ Skipped {file_type} (invalid type: {content_type}) → {url}")
            return None

        parsed_url = urlparse(url)
        filename = use_this_name or os.path.basename(parsed_url.path) or f"file_{int(datetime.now().timestamp())}"
        folder = os.path.normpath(folder)

        ext = os.path.splitext(filename)[1].lower()
        if ext == ".ashx":
            if content_type.startswith("image/"):
                ext = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".jpg"
            else:
                ext = ".jpg"
            filename = os.path.splitext(filename)[0] + ext
        if not ext:
            ext = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ""
            filename = filename + ext

        os.makedirs(folder, exist_ok=True)
        file_path = os.path.join(folder, filename)

        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

        file_path_json = file_path.replace(os.sep, "/")
        logger.info(f"📥 Downloaded {file_type} → {file_path_json}")

        return {
            "name": filename,
            "file_path": file_path_json,
            "version": version or None,
            "date": file_date or datetime.now().strftime("%Y-%m-%d"),
            "url": url,
            "language": "english",
            "description": description or None,
        }

    except requests.exceptions.Timeout:
        logger.error(f"⏱️ Timeout while downloading {file_type} → {url}")
    except requests.exceptions.HTTPError as e:
        logger.error(f"❌ HTTP {e.response.status_code} while downloading {file_type} → {url}")
    except Exception as e:
        logger.error(f"💥 Unexpected error while downloading {file_type} → {url}: {e}")
    return None

# ---------------- Save Metadata ---------------- #
def save_metadata(folder, metadata_list, filename="metadata.json"):
    try:
        metadata_path = os.path.join(folder, filename)
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata_list, f, indent=4)
        logger.info(f"🗂️ Saved metadata → {metadata_path}")
    except Exception as e:
        logger.error(f"💥 Failed to save metadata {filename}: {e}")

# ---------------- Detect URL Level ---------------- #
def detect_url_level(url: str) -> str:
    if url == "https://www.raspberrypi.com/products/":
        return "category"
    return "product"

# ---------------- Crawl Product Page ---------------- #
def crawl_product_page(url, output_folder, products_data_dict, level):
    if not url:
        return
    browser = None
    try:
        logger.info(f"🌐 Crawling {level} page → {url}")
        with sync_playwright() as p:
            try:
                logger.info("🚀 Launching remote Firefox browser...")
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
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(10000)
                html_content = page.content()
            finally:
                if browser:
                    browser.close()

        soup = BeautifulSoup(html_content, "lxml")

        folder_structure = {
            "documentations": [],
            "images": [],
            "block_diagrams": [],
            "design_resources": [],
            "software_tools": [],
            "markdowns": [],
            "trainings": [],
            "other": [],
        }
        if level == "product":
            for folder in folder_structure.keys():
                os.makedirs(os.path.join(output_folder, folder), exist_ok=True)

        overview_text = ""
        for selector_ in config["overview_text_xpath"]:
            for p in soup.select(selector_):
                overview_text += md(str(p)) + "\n\n"
        if level == "category":
            p_items = soup.select("section h2")
            if p_items:
                overview_text += md("## main category :") + "\n\n"
            for p in p_items:
                overview_text += md(str(p)) + "\n\n"

        if overview_text:
            markdown_path = (
                os.path.join(output_folder, "markdowns", "overview.md")
                if level == "product"
                else os.path.join(output_folder, "overview.md")
            )
            with open(markdown_path, "w", encoding="utf-8") as f:
                f.write(overview_text)
            logger.info(f"📝 Saved overview text → {markdown_path}")

        if level != "product":
            return

        # product_details = {}
        specifications = ""
        for _selector_ in config["specifications_xpath"]:
            specs_items = soup.select_one(_selector_)
            if specs_items:
                specifications += specs_items.get_text(strip=True)
        # product_details["specifications"] = specifications

        # if product_details:
        #     details_path = os.path.join(output_folder, "product_details.json")
        #     with open(details_path, "w", encoding="utf-8") as f:
        #         json.dump(product_details, f, indent=4)
        #     logger.info(f"📑 Saved product details → {details_path}")

        doc_metadata = []
        datasheet_link = None
        product_summary_link = soup.select_one(config["product_datasheet_selector"])
        if product_summary_link:
            file_url = urljoin(url, product_summary_link.get("href"))
            if file_url:
                datasheet_link = file_url
                metadata = download_file(file_url, os.path.join(output_folder, "documentations"), "documentation")
                if metadata:
                    doc_metadata.append(metadata)

        for selector in config["documentation_xpaths"]:
            for link in soup.select(selector):
                file_url = urljoin(url, link.get("href"))
                if file_url and file_url != datasheet_link:
                    metadata = download_file(file_url, os.path.join(output_folder, "documentations"), "documentation")
                    if metadata:
                        doc_metadata.append(metadata)
        save_metadata(os.path.join(output_folder, "documentations"), doc_metadata)

        image_metadata = []
        product_images = soup.select(config["image_xpaths"][0])
        for img in product_images:
            img_url = urljoin(url, img.get("src"))
            if img_url:
                metadata = download_file(img_url, os.path.join(output_folder, "images"), "product image")
                if metadata:
                    image_metadata.append(metadata)
        save_metadata(os.path.join(output_folder, "images"), image_metadata)

        block_metadata = []
        for selector in config["block_diagram_xpath"]:
            for img in soup.select(selector):
                img_url = urljoin(url, img.get("src"))
                if img_url:
                    metadata = download_file(img_url, os.path.join(output_folder, "block_diagrams"), "block diagram")
                    if metadata:
                        block_metadata.append(metadata)
        save_metadata(
            os.path.join(output_folder, "block_diagrams"),
            block_metadata,
            filename="bloack_diagram_mappings.json"
        )

        product_name = os.path.basename(urlparse(url).path)
        products_data_dict[product_name] = {
            "product_page_link": url,
            "specifications": specifications,
            "summary": overview_text,
        }

    except Exception as e:
        logger.error(f"💥 Error crawling product page {url}: {e}")

# ---------------- Main ---------------- #
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="🔗 URL of the topic/product page.")
    parser.add_argument("--out", required=True, help="📁 Output directory for the crawled data.")
    args = parser.parse_args()

    output_dir_base = args.out
    product_url = args.url
    level = detect_url_level(product_url)
    os.makedirs(output_dir_base, exist_ok=True)

    products_data = {}
    crawl_product_page(product_url, output_dir_base, products_data, level)

    if level == "product":
        tables_folder = os.path.join(output_dir_base, "tables")
        os.makedirs(tables_folder, exist_ok=True)
        save_metadata(tables_folder, [], filename="metadata.json")
        try:
            with open(os.path.join(tables_folder, "products.json"), "w", encoding="utf-8") as f:
                json.dump(products_data, f, indent=4)
            logger.info(f"📦 Saved products data → {tables_folder}/products.json")
        except Exception as e:
            logger.error(f"💥 Failed to save products.json: {e}")

    logger.info("🎉 Crawl finished successfully!")

if __name__ == "__main__":
    main()