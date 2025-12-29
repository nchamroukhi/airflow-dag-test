import requests, re, time
import json
import argparse
from pathlib import Path
import os
from parsel import Selector
from urllib.parse import unquote, urljoin
import logging
from markdownify import markdownify as md
from urllib.parse import quote

# ---------------- Logging Setup ----------------
class EmojiFormatter(logging.Formatter):
    LEVEL_EMOJIS = {
        logging.DEBUG: "🔍",
        logging.INFO: "ℹ️",
        logging.WARNING: "⚠️",
        logging.ERROR: "❌",
        logging.CRITICAL: "🚨",
    }

    def format(self, record):
        emoji = self.LEVEL_EMOJIS.get(record.levelno, "")
        record.msg = f"{emoji} {record.msg}"
        return super().format(record)

handler = logging.StreamHandler()
handler.setFormatter(EmojiFormatter("%(asctime)s - %(levelname)s - %(message)s"))
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(handler)

BASE_URL = "https://www.nuvoton.com"

HEADERS = {
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'en-US,en;q=0.9,es;q=0.8,ja;q=0.7',
    'priority': 'u=1, i',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
}

BASE_URL_TYPES = {"GRP": "group", "PDT": "product"}

BASE_OUTPUT_TBL = "tables"
BASE_OUTPUT_MD = "markdowns"
BASE_OUTPUT_IMG = "images"
BASE_OUTPUT_DOC = "documentation"
BASE_OUTPUT_ST = "software_tools"
BASE_OUTPUT_OTH = "other"

NEED_METADATA_FILE = ["markdowns", "tables", "documentation", "block_diagrams", "design_resources", "software_tools", "trainings", "other", "images"]

# ---------------- Crawl Running ----------------
def make_folder(output):
    for folder_str in NEED_METADATA_FILE:
        os.makedirs(f"{output}/{folder_str}", exist_ok=True)
        with open(f"{output}/{folder_str}/metadata.json", "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)

def save_file(url, file_name, dir_name, root_dir, session):
    try:
        if os.path.exists(f"{root_dir}/{dir_name}/{file_name}") and os.path.getsize(f"{root_dir}/{dir_name}/{file_name}") > 0:
            return True
        rest = session.get(url)
        if "text/html" in rest.headers.get("Content-Type", "") or "application/xml" in rest.headers.get("Content-Type", ""):
            logger.info(f"Skipping {url} (HTML page, not a file)")
            return False
        os.makedirs(f"{root_dir}/{dir_name}", exist_ok=True)
        with open(f"{root_dir}/{dir_name}/{file_name}", "wb") as f:
            f.write(rest.content)
        return True
    except Exception as e:
        logger.warning(f"[FAILED] {e}")
    return False

def download_images(selector, output, session):
    metadata_img = []
    items = selector.css("img")
    for item in items:
        image_url = item.css("::attr(src)").get()
        if image_url and not image_url.startswith("http"):
            image_url = urljoin(BASE_URL, image_url)
        if not image_url or 'icon_' in image_url or 'pic-' in image_url:
            continue
        name = unquote(image_url.rsplit('/', 1)[-1])
        desc = item.css("::attr(alt)").get(default='').strip()
        dir_name = f"{BASE_OUTPUT_IMG}"
        metadata = {
            "name": name,
            "url": image_url,
            "description": desc,
            "language": None,
            "version": None,
            "date": None
        }
        is_download = save_file(metadata['url'], metadata['name'], dir_name, output, session)
        if is_download:
            metadata['file_path'] = f"{output}/{dir_name}/{metadata['name']}"
        else:
            metadata['name'] = None
            metadata['file_path'] = None
        metadata_img.append(metadata)
    with open(f"{output}/{BASE_OUTPUT_IMG}/metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata_img, f, ensure_ascii=False, indent=2)

def download_files(output, current_folder, product_url):
    file_types = ["Document", "Software", "Hardware"]
    for file_type in file_types:
        if file_type == 'Document':
            dir_name = BASE_OUTPUT_DOC
        elif file_type == 'Software':
            dir_name = BASE_OUTPUT_ST
        else:
            dir_name = BASE_OUTPUT_OTH
        session = requests.Session()
        session.headers.update(HEADERS)
        
        # Construct the full URL with query parameters
        doc_url = f"{product_url.replace('#', '')}?group={file_type}&tab=2"
        
        res = session.get(doc_url)
        sel = Selector(text=res.text)
        
        # Extract CSRF token from JavaScript (X-CSRF-Token in Ext.Ajax.on)
        token = ""
        script_with_token = sel.xpath('//script[contains(text(), "X-CSRF-Token")]/text()').get()
        if script_with_token:
            token_match = re.search(r'"X-CSRF-Token":\s*"([^"]*)"', script_with_token)
            if token_match:
                token = token_match.group(1)
        
        if not token:
            # Try alternative pattern with single quotes
            all_scripts = sel.xpath('//script/text()').getall()
            for script in all_scripts:
                if 'X-CSRF-Token' in script or 'CSRF' in script:
                    # Try different patterns
                    patterns = [
                        r'"X-CSRF-Token":\s*"([^"]*)"',
                        r"'X-CSRF-Token':\s*'([^']*)'",
                        r'X-CSRF-Token["\']?\s*:\s*["\']([^"\']+)["\']',
                    ]
                    for pattern in patterns:
                        match = re.search(pattern, script)
                        if match:
                            token = match.group(1)
                            logger.info(f"CSRF token found with alternate pattern: {token[:20]}...")
                            break
                    if token:
                        break
        
        if not token:
            logger.warning("CSRF token not found, skipping document downloads")
            return
        
        # Extract nvdt parameter from JavaScript
        nvdt = ""
        nvdt_script = sel.xpath('//script[contains(text(), "nvdt=")]/text()').get()
        if nvdt_script:
            # Pattern matches: '?nvdt='+'20251216163037' or "?nvdt="+"20251216163037"
            nvdt_match = re.search(r"nvdt=['\"]?\+?['\"](\d+)['\"]", nvdt_script)
            if nvdt_match:
                nvdt = nvdt_match.group(1)
                logger.info(f"nvdt parameter extracted: {nvdt}")
        metadata_doc = []
        category = current_folder.lstrip('/')
        
        session.headers.update({
            'accept': '*/*',
            'accept-language': 'en-US,en;q=0.9,es;q=0.8,ja;q=0.7',
            'priority': 'u=1, i',
            'referer': 'https://www.nuvoton.com/',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
            'x-csrf-token': token,
            'x-requested-with': 'XMLHttpRequest',
        })
        
        # Generate dynamic timestamp
        timestamp = int(time.time() * 1000)
        
        # Build API URL with extracted nvdt or fallback to empty
        nvdt_param = f'nvdt={nvdt}&' if nvdt else ''
        res = session.get(
            f'{BASE_URL}/system/modules/com.thesys.project.nuvoton/pages/ajax/resource-list.json?{nvdt_param}currentFolder={current_folder}&_dc={timestamp}&rt=All&dateStart=&dateEnd=&keyword=&category={category}&group={file_type}&page=1&start=0&limit=100',
        )

        data = json.loads(res.text)
        items = data.get("itemList", [])
        
        for item in items:
            doc_url = item.get('link', '')
            if not doc_url:
                continue
            if not doc_url.startswith("http"):
                doc_url = urljoin(BASE_URL, doc_url)
            name = item.get('filename', '').strip()
            desc = item.get('typeTitle', '').strip()
            metadata = {
                "name": name,
                "url": doc_url,
                "description": desc,
                "language": None,
                "version": item.get('fileVersion', None),
                "date": item.get('date', None)
            }
            
            is_download = save_file(metadata['url'], metadata['name'], dir_name, output, session)
            if is_download:
                metadata['file_path'] = f"{output}/{dir_name}/{metadata['name']}"
            else:
                metadata['name'] = None
                metadata['file_path'] = None
            metadata_doc.append(metadata)
        
        logger.info(f"Downloaded {len(metadata_doc)} {file_type}s")
        with open(f"{output}/{dir_name}/metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata_doc, f, ensure_ascii=False, indent=2)

def get_product_page(product_url, output):
    session = requests.Session()
    session.headers.update(HEADERS)
    response = session.get(product_url)
    selector = Selector(text=response.text)
    el = selector.css("div.main")

    # Save overview.md
    inner_html = "".join(el.xpath("node()").getall())
    markdown = md(inner_html)
    os.makedirs(f"{output}/{BASE_OUTPUT_MD}", exist_ok=True)
    with open(f"{output}/{BASE_OUTPUT_MD}/overview.md", "w", encoding="utf-8") as f:
        f.write(markdown)
        
    # Extract parameters from JavaScript variables (PROD_PAGE_VARS or HEARDER_2)
    current_folder = ""
    family = ""
    series = ""
    part_no = ""
    
    # Try to find PROD_PAGE_VARS first
    prod_script = selector.xpath('//script[contains(text(), "PROD_PAGE_VARS")]/text()').get()
    if prod_script:
        cf_match = re.search(r"currentFolder :\s*'([^']*)'", prod_script)
        if cf_match:
            current_folder = cf_match.group(1)
        
        fam_match = re.search(r"family :\s*'([^']*)'", prod_script)
        if fam_match:
            family = fam_match.group(1)
        
        ser_match = re.search(r"series :\s*'([^']*)'", prod_script)
        if ser_match:
            series = ser_match.group(1)
            
        ptn_match = re.search(r"partNo :\s*'([^']*)'", prod_script)
        if ptn_match:
            part_no = ptn_match.group(1)
    
    # Fallback: Try HEARDER_2 for currentFolder if not found
    if not current_folder:
        header_script = selector.xpath('//script[contains(text(), "HEARDER_2")]/text()').get()
        if header_script:
            cf_match = re.search(r"currentFolder:\s*'([^']*)'", header_script)
            if cf_match:
                current_folder = cf_match.group(1)
    
    # Additional fallback: Extract from HTML comment
    if not current_folder:
        comment = selector.xpath('//comment()[contains(., "currentFolder:")]').get()
        if comment:
            cf_match = re.search(r"currentFolder:\s*([^\s,]+)", comment)
            if cf_match:
                current_folder = cf_match.group(1).rstrip(',')
    
    # Try to derive family and series from breadcrumbs if not found
    if not family or not series or not part_no:
        # Extract from breadcrumbs - use more specific selector to get only navigation links
        # Get the href attributes along with text to filter out buttons/form elements
        breadcrumb_items = selector.xpath('//div[@class="breadcrumbs"]//a[@href and not(contains(@href, "#"))]')
        breadcrumb_links = []
        
        for item in breadcrumb_items:
            text = item.xpath('string()').get('').strip()
            href = item.xpath('@href').get('')
            # Only include links that start with /products/ and have actual text
            if text and href.startswith('/products/') and text.lower() not in ['home', 'products']:
                breadcrumb_links.append(text)
        
        if len(breadcrumb_links) >= 3 and not family:
            family = breadcrumb_links[-3]
        if len(breadcrumb_links) >= 2 and not series:
            series = breadcrumb_links[-2]
        if len(breadcrumb_links) >= 1 and not part_no:
            part_no = breadcrumb_links[-1]

    logger.info(f"Extracted - currentFolder: {current_folder}, family: {family}, series: {series}, part_no: {part_no}")

    # Save products.json
    products = {}
    
    if current_folder and family and series and part_no:
        # URL encode the parameters
        family_encoded = quote(family)
        series_encoded = quote(series)
        part_no_encoded = quote(part_no)
        
        api_response = session.get(
            f'{BASE_URL}/system/modules/com.thesys.project.nuvoton/pages/selection-guide/ajax/selectionPage.json?currentFolder={current_folder}&family={family_encoded}&ProductSeries={series_encoded}&partNo={part_no_encoded}&page=1&start=0&limit=1000',
        )
        logger.info(f"API Response status: {api_response.status_code}")

        if api_response.status_code == 200:
            data = api_response.json()
            # Process the API data to build products dict
            items = data.get("itemList", [])
            for item in items:
                product_name = item.get("PartNo", "").strip()
                products[product_name] = {
                    "Product": product_name,
                    "product_page_link": urljoin(BASE_URL, item.get("partNoLink", "")) if item.get("partNoLink") else None,
                    "pdf_link": urljoin(BASE_URL, item.get("link", None)) if item.get("link") else None,
                    "pdf_filename": unquote(item.get("link", "").rsplit('/', 1)[-1]) if item.get("link") else None,
                    "image_url": None,
                    "Product Line": item.get("ProductLine", ""),
                    "Product Family": family,
                    "Product Series": series,
                    "Flash": item.get("Flash", ""),
                    "SRAM": item.get("SRAM", ""),
                    "Data Flash": item.get("DataFlash", ""),
                    "LDROM": item.get("LDROM", ""),
                    "I/O": item.get("IO", ""),
                    "Timers": item.get("Timers", ""),
                    "UART": item.get("UART", ""),
                    "SPI": item.get("SPI", ""),
                    "I2C": item.get("I2C", ""),
                    "PWM": item.get("PWM", ""),
                    "Comparator": item.get("Comparator", ""),
                    "ADC": item.get("ADC", ""),
                    "ISP/ICP/IAP": item.get("ISPICPIAP", ""),
                    "Package": item.get("Package", ""),
                    "Status": item.get("Status", ""),
                    "INT": item.get("INT_", ""),
                    "Special Function": item.get("SpecialFunction", ""),
                }

    os.makedirs(f"{output}/{BASE_OUTPUT_TBL}", exist_ok=True)
    with open(f'{output}/{BASE_OUTPUT_TBL}/products.json', "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
        
    # file downloads
    make_folder(output)
    download_images(el, output, session)
    download_files(output, current_folder, product_url)

def get_group_page(url, output):
    session = requests.Session()
    session.headers.update(HEADERS)
    response = session.get(url)
    selector = Selector(text=response.text)
    el = selector.css("div.main")

    # Save overview.md
    inner_html = "".join(el.xpath("node()").getall())
    markdown = md(inner_html)
    os.makedirs(f"{output}/{BASE_OUTPUT_MD}", exist_ok=True)
    with open(f"{output}/{BASE_OUTPUT_MD}/overview.md", "w", encoding="utf-8") as f:
        f.write(markdown)

    # Extract parameters from JavaScript variables (PROD_PAGE_VARS or HEARDER_2)
    current_folder = ""
    family = ""
    series = ""
    
    # Try to find PROD_PAGE_VARS first
    prod_script = selector.xpath('//script[contains(text(), "PROD_PAGE_VARS")]/text()').get()
    if prod_script:
        cf_match = re.search(r"currentFolder :\s*'([^']*)'", prod_script)
        if cf_match:
            current_folder = cf_match.group(1)
        
        fam_match = re.search(r"family :\s*'([^']*)'", prod_script)
        if fam_match:
            family = fam_match.group(1)
        
        ser_match = re.search(r"series :\s*'([^']*)'", prod_script)
        if ser_match:
            series = ser_match.group(1)
    
    # Fallback: Try HEARDER_2 for currentFolder if not found
    if not current_folder:
        header_script = selector.xpath('//script[contains(text(), "HEARDER_2")]/text()').get()
        if header_script:
            cf_match = re.search(r"currentFolder:\s*'([^']*)'", header_script)
            if cf_match:
                current_folder = cf_match.group(1)
    
    # Additional fallback: Extract from HTML comment
    if not current_folder:
        comment = selector.xpath('//comment()[contains(., "currentFolder:")]').get()
        if comment:
            cf_match = re.search(r"currentFolder:\s*([^\s,]+)", comment)
            if cf_match:
                current_folder = cf_match.group(1).rstrip(',')
    
    # Try to derive family and series from breadcrumbs if not found
    if not family or not series:
        # Extract from breadcrumbs - use more specific selector to get only navigation links
        # Get the href attributes along with text to filter out buttons/form elements
        breadcrumb_items = selector.xpath('//div[@class="breadcrumbs"]//a[@href and not(contains(@href, "#"))]')
        breadcrumb_links = []
        
        for item in breadcrumb_items:
            text = item.xpath('string()').get('').strip()
            href = item.xpath('@href').get('')
            # Only include links that start with /products/ and have actual text
            if text and href.startswith('/products/') and text.lower() not in ['home', 'products']:
                breadcrumb_links.append(text)
        
        if len(breadcrumb_links) >= 2 and not family:
            family = breadcrumb_links[-2]
        if len(breadcrumb_links) >= 1 and not series:
            series = breadcrumb_links[-1]
    
    logger.info(f"Extracted - currentFolder: {current_folder}, family: {family}, series: {series}")

    # Save products.json
    products = {}
    
    if current_folder and family and series:
        # URL encode the parameters
        from urllib.parse import quote
        family_encoded = quote(family)
        series_encoded = quote(series)
        
        api_response = session.get(
            f'{BASE_URL}/system/modules/com.thesys.project.nuvoton/pages/selection-guide/ajax/selectionPage.json?currentFolder={current_folder}&family={family_encoded}&ProductSeries={series_encoded}&page=1&start=0&limit=1000',
        )
        logger.info(f"API Response status: {api_response.status_code}")

        if api_response.status_code == 200:
            data = api_response.json()
            # Process the API data to build products dict
            items = data.get("itemList", [])
            for item in items:
                product_name = item.get("PartNo", "").strip()
                products[product_name] = {
                    "Product": product_name,
                    "product_page_link": urljoin(BASE_URL, item.get("partNoLink", "")) if item.get("partNoLink") else None,
                    "pdf_link": urljoin(BASE_URL, item.get("link", None)) if item.get("link") else None,
                    "pdf_filename": unquote(item.get("link", "").rsplit('/', 1)[-1]) if item.get("link") else None,
                    "image_url": None,
                    "Product Line": item.get("ProductLine", ""),
                    "Product Family": family,
                    "Product Series": series,
                    "Flash": item.get("Flash", ""),
                    "SRAM": item.get("SRAM", ""),
                    "Data Flash": item.get("DataFlash", ""),
                    "LDROM": item.get("LDROM", ""),
                    "I/O": item.get("IO", ""),
                    "Timers": item.get("Timers", ""),
                    "UART": item.get("UART", ""),
                    "SPI": item.get("SPI", ""),
                    "I2C": item.get("I2C", ""),
                    "PWM": item.get("PWM", ""),
                    "Comparator": item.get("Comparator", ""),
                    "ADC": item.get("ADC", ""),
                    "ISP/ICP/IAP": item.get("ISPICPIAP", ""),
                    "Package": item.get("Package", ""),
                    "Status": item.get("Status", ""),
                    "INT": item.get("INT_", ""),
                    "Special Function": item.get("SpecialFunction", ""),
                }
    if products:
        os.makedirs(f"{output}/{BASE_OUTPUT_TBL}", exist_ok=True)
        with open(f'{output}/{BASE_OUTPUT_TBL}/products.json', "w", encoding="utf-8") as f:
            json.dump(products, f, ensure_ascii=False, indent=2)


def get_url_type(url):
    if "#" in url:
        return BASE_URL_TYPES["PDT"]
    else:
        return BASE_URL_TYPES["GRP"]

def main():
    parser = argparse.ArgumentParser(
        description="Extract URLs from product category structure."
    )
    parser.add_argument("--url", required=True, help="Path to structure.json")
    parser.add_argument("--out", required=True, help="Path to output dir.")
    args = parser.parse_args()

    try:
        output_path = str(Path(args.out))
        url_type = get_url_type(args.url)
        if url_type == BASE_URL_TYPES["GRP"]:
            get_group_page(args.url, output_path)
        else:
            get_product_page(args.url, output_path)

        logger.info(f"Success to {output_path}")
    except Exception as e:
        logger.error(f"Failed to : {e}")


if __name__ == "__main__":
    main()
