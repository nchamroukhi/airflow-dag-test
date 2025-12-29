#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import argparse
import logging
import shutil
import requests
from lxml import html
from markdownify import markdownify
from urllib.parse import urljoin, urlparse

# =========================
# Logging
# =========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# =========================
# HTTP session
# =========================
BASE_URL = "https://www.stulz.com"
_session = None

def get_session(for_html=False):
    global _session
    if _session is None:
        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8' if for_html else '*/*',
            'accept-language': 'en-GB,en-US;q=0.9,en;q=0.8',
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36'
        }
        if not for_html:
            headers.update({
                'priority': 'u=1, i',
                'sec-ch-ua': '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"macOS"',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-origin'
            })
        _session = requests.Session()
        _session.headers.update(headers)
    return _session

def safe_get(url, retries=3, timeout=20):
    session = get_session(for_html=True)
    for i in range(retries):
        try:
            response = session.get(url, timeout=timeout, allow_redirects=True)
            return response
        except Exception as e:
            if i == retries - 1:
                raise
    return None

def make_absolute(url):
    if not url:
        return "#"
    if url.startswith("http"):
        return url
    if not url.startswith("/"):
        url = "/" + url
    return BASE_URL.rstrip("/") + url

def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()

def strip_html(html_text):
    """Strip HTML tags from text"""
    if not html_text:
        return ""
    # Simple HTML tag removal
    text = re.sub(r'<[^>]+>', '', html_text)
    # Decode HTML entities
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'")
    # Clean up whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def ensure(p):
    os.makedirs(p, exist_ok=True)

def fullpath(p):
    return p.replace("\\", "/")

def fname(u, default="file.bin"):
    n = os.path.basename(urlparse(u).path) or default
    return re.sub(r'[^a-zA-Z0-9._-]+', '_', n)

def download(url, path, chunk=262144):
    res = safe_get(url)
    if res and res.status_code == 200:
        with open(path, "wb") as f:
            for chunk_data in res.iter_content(chunk_size=chunk):
                f.write(chunk_data)
        return True
    return False

def empty_to_none(s):
    return None if (s == "" or s is None) else s

def slug_from_name(name):
    if not name:
        return "item"
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", name.strip())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug.lower() or "item"

# =========================
# API call and extraction
# =========================

# Category name to ID mapping
CATEGORY_IDS = {
    'room-cooling': 3,
    'high-density-and-rack-based-cooling': 4,
    'liquid-cooling': 90,
    'micro-data-center': 7,
    'shelter-cooling': 6,
    'humidifiers': 10,
    'air-handling-units': 5,
    'chiller-units': 9,
    'monitoring': 11
}

def get_category_id_from_url(category_url):
    """Extract category ID from URL"""
    # Extract category name from URL
    # e.g., https://www.stulz.com/products/room-cooling -> room-cooling
    parts = category_url.rstrip('/').split('/')
    category_name = parts[-1] if parts else ''
    
    # Return mapped ID or None if not found
    return CATEGORY_IDS.get(category_name)

def crawl_category(category_url, outdir):
    """Main function to crawl a category - uses only API"""
    logger.info(f"[CATEGORY] {category_url}")
    
    # Remove trailing slash from category_url
    category_url = category_url.rstrip('/')
    
    # Get category ID from URL
    category_id = get_category_id_from_url(category_url)
    if not category_id:
        logger.warning(f"Category ID not found for URL: {category_url}, using empty value")
        category_id = ""
    
    # Build API URL
    api_url = f"{category_url}?tx_products_products%5BproductCategoryId%5D={category_id}&tx_products_products%5BproductCountryId%5D=35&tx_products_products%5BproductDetailPid%5D=811&type=1200"
    
    logger.info(f"Fetching API: {api_url}")
    session = get_session()
    
    try:
        api_res = session.get(api_url, timeout=20)
        
        if api_res.status_code != 200:
            logger.error(f"API returned status {api_res.status_code}")
            return
        
        data = api_res.json()
        logger.info(f"API response received")
        
        # Extract products from API
        products = data.get('products', [])
        logger.info(f"Found {len(products)} products from API")
        
        # Extract markdown content from API response
        # Description from filter.categories[0].desc
        category_desc = ""
        category_name = ""
        if data.get('filter') and data['filter'].get('categories'):
            first_category = data['filter']['categories'][0]
            category_name = first_category.get('name', '')
            category_desc = first_category.get('desc', '')
        
        # Build breadcrumb: Products -> Category Name
        breadcrumb = f"Products → {category_name}" if category_name else "Products"
        
        combined_content = {
            "breadcrumb": breadcrumb,
            "title": category_name,
            "description": category_desc
        }
        
        # Build products map - parametric table fields
        products_map = {}
        seen = set()
        
        for product in products:
            name = product.get('name', '').strip()
            url = product.get('url', '').strip()
            if not name or not url:
                continue
            
            if (name, url) in seen:
                continue
            seen.add((name, url))
            
            # Make image URL absolute
            img = product.get('img', '')
            if img and not img.startswith('http'):
                img = make_absolute(img)
            
            # Strip HTML from description
            desc = strip_html(product.get('desc', ''))
            
            product_key = slug_from_name(name)
            if product_key in products_map:
                product_key = f"{product_key}_{len(products_map)}"
            
            # Parametric table fields only
            product_entry = {
                "Product": empty_to_none(name),
                "name": empty_to_none(name),
                "product_page_link": empty_to_none(url),
                "image_url": empty_to_none(img),
                "description": empty_to_none(desc),
                "pdf_link": None,
                "pdf_filename": None
            }
            
            products_map[product_key] = product_entry
        
        # Write to files
        write_category_flat(outdir, category_name or "Category", category_url, products_map, combined_content)
        logger.info(f"Successfully processed category: {category_name or category_url}")
        
    except Exception as e:
        logger.error(f"Error crawling category: {e}")

# =========================
# Writing functions
# =========================

def write_category_flat(outdir, title, url, products_map, combined_content=None):
    """Write category data to markdowns/ and tables/ folders"""
    ensure(outdir)
    
    # Create markdowns directory
    markdowns_dir = os.path.join(outdir, "markdowns")
    ensure(markdowns_dir)
    
    with open(os.path.join(markdowns_dir, "overview.md"), "w", encoding="utf-8") as f:
        # Write breadcrumb if available
        if combined_content and combined_content.get("breadcrumb"):
            f.write(f"{combined_content['breadcrumb']}\n\n")
        
        # Write title
        if combined_content and combined_content.get("title"):
            f.write(f"# {combined_content['title']}\n\n")
        elif title:
            f.write(f"# {title}\n\n")
        
        # Write description
        if combined_content and combined_content.get("description"):
            f.write(f"{combined_content['description']}\n\n")
        
        # Write products list from products.json
        if products_map:
            f.write("## Products\n\n")
            for product_key, product in products_map.items():
                product_name = product.get("Product") or product.get("name", "")
                product_url = product.get("product_page_link", "")
                product_desc = product.get("description", "")
                
                if product_name:
                    # Write product as markdown link
                    if product_url:
                        f.write(f"- [{product_name}]({product_url})")
                    else:
                        f.write(f"- {product_name}")
                    
                    # Add description if available
                    if product_desc:
                        f.write(f"\n  - {product_desc}")
                    
                    f.write("\n\n")
    
    # Create tables directory
    tables_dir = os.path.join(outdir, "tables")
    ensure(tables_dir)
    
    # Write products.json
    with open(os.path.join(tables_dir, "products.json"), "w", encoding="utf-8") as f:
        json.dump(products_map, f, indent=2, ensure_ascii=False)

# =========================
# Product parsing
# =========================

def parse_product(product_url):
    """Parse product page"""
    res = safe_get(product_url)
    if not res or res.status_code != 200:
        raise Exception(f"Failed to fetch product page: {res.status_code if res else 'No response'}")
    tree = html.fromstring(res.text)
    # Title
    title = ""
    h1_elem = tree.xpath("//stulz-stage//h1//text()[normalize-space()]")
    if h1_elem:
        title = clean(" ".join(h1_elem))
    if not title:
        title_elem = tree.xpath("//title/text()")
        if title_elem:
            title = clean(title_elem[0])
    
    # Breadcrumb
    breadcrumb_parts = []
    breadcrumb_items = tree.xpath("//ul[@class='breadcrumb']//li")
    for item in breadcrumb_items:
        link_text = item.xpath(".//a/text()[normalize-space()] | text()[normalize-space()]")
        if link_text:
            text = clean(link_text[0])
            if text:
                breadcrumb_parts.append(text)
    breadcrumb = " → ".join(breadcrumb_parts) if breadcrumb_parts else None
    
    # Subheader from stulz-stage
    subheader = ""
    subheader_attr = tree.xpath("//stulz-stage/@subheader")
    if subheader_attr:
        subheader = clean(subheader_attr[0])
    # Intro text header
    intro_header = ""
    intro_header_elem = tree.xpath("//div[@class='intro-text']//h2[@class='h1 intro-text__header']//text()[normalize-space()]")
    if intro_header_elem:
        intro_header = clean(" ".join(intro_header_elem))
    # Description
    description = ""
    desc_elem = tree.xpath("//div[@class='intro-text__content']//p")
    if desc_elem:
        desc_html = html.tostring(desc_elem[0], encoding='unicode')
        description = markdownify(desc_html).strip()
    
    # Extract Product Overview tabs
    tabs_data = {}
    product_detail_tabs = tree.xpath("//div[@class='product-detail-tabs']")
    if product_detail_tabs:
        tabs_html = html.tostring(product_detail_tabs[0], encoding='unicode')
        tabs_md = markdownify(tabs_html).strip()
        if tabs_md:
            tabs_data["Product Overview"] = tabs_md
    # Features from tabs
    features = []
    tab_contents = product_detail_tabs[0].xpath(".//div[@slot='tab-content']") if product_detail_tabs else []
    for tab_content in tab_contents:
        rte_elem = tab_content.xpath(".//div[@class='rte-text']")
        if rte_elem:
            feat_items = rte_elem[0].xpath(".//li//text()[normalize-space()]")
            for feat in feat_items:
                feat_text = clean(feat)
                if feat_text and len(feat_text) > 5 and feat_text not in features:
                    features.append(feat_text)
    # Technical specs table
    specs_table = {}
    for tab_content in tab_contents:
        spec_table = tab_content.xpath(".//table//tr")
        for tr in spec_table:
            cells = tr.xpath(".//td")
            if len(cells) >= 2:
                key = clean(" ".join(cells[0].xpath(".//text()[normalize-space()]")))
                value = clean(" ".join(cells[1].xpath(".//text()[normalize-space()]")))
                if key and value:
                    specs_table[key] = value
    
    # Extract accordion content (All details section)
    accordion_content = ""
    accordion_items = tree.xpath("//wyn-accordion-item")
    for item in accordion_items:
        header = item.xpath(".//h3[@slot='header']//text()[normalize-space()]")
        content = item.xpath(".//div[@slot='content']")
        if header and content:
            header_text = clean(" ".join(header))
            content_html = html.tostring(content[0], encoding='unicode')
            content_md = markdownify(content_html).strip()
            accordion_content += f"### {header_text}\n\n{content_md}\n\n"
    
    # Images - only main product image
    images = []
    stage_img = tree.xpath("//stulz-stage//picture//source[@srcset] | //stulz-stage//picture//img[@src]")
    if stage_img:
        img_url = stage_img[0].get('srcset') or stage_img[0].get('src', '')
        if img_url:
            img_url = img_url.split()[0] if ' ' in img_url else img_url
            if not img_url.startswith('http'):
                img_url = make_absolute(img_url)
            images.append(img_url)
    # Catch phrase images
    catch_phrases = []
    catch_phrase_items = tree.xpath("//stulz-catch-phrase-item")
    for item in catch_phrase_items:
        headline = item.get('headline', '')
        subline = item.get('subline', '')
        img_elem = item.xpath(".//img[@slot='icon']/@src")
        if img_elem:
            img_url = img_elem[0]
            if not img_url.startswith('http'):
                img_url = make_absolute(img_url)
            catch_phrases.append({"headline": headline, "subline": subline, "image_url": img_url})
    
    # Documents (PDFs)
    docs = []
    pdf_urls = tree.xpath("//wyn-option[contains(@value, '.pdf')]/@value")
    pdf_options = tree.xpath("//wyn-option[contains(@value, '.pdf')]")
    for i, opt in enumerate(pdf_options):
        pdf_url = pdf_urls[i] if i < len(pdf_urls) else opt.get('value', '').strip()
        if not pdf_url:
            continue
        if not pdf_url.startswith('http'):
            pdf_url = make_absolute(pdf_url)
        link_text = clean(" ".join(opt.xpath(".//text()[normalize-space()]")))
        filename = os.path.basename(urlparse(pdf_url).path)
        if not filename or filename == '.pdf':
            filename = fname(pdf_url, "document.pdf")
        docs.append({"name": filename, "url": pdf_url, "link_text": link_text, "is_pdf": True})
    
    # Extract YouTube videos from iframes
    videos = []
    youtube_iframes = tree.xpath("//iframe[@class='video__player video__player--youtube']")
    for iframe in youtube_iframes:
        embed_src = iframe.get('src', '').strip()
        if not embed_src:
            continue
        if not embed_src.startswith('http'):
            embed_src = make_absolute(embed_src)
        
        # Fetch the embed page to get the video URL
        try:
            headers = {
                'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'accept-language': 'en-GB,en-US;q=0.9,en;q=0.8',
                'priority': 'u=0, i',
                'referer': 'https://www.stulz.com/',
                'sec-ch-ua': '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"macOS"',
                'sec-fetch-dest': 'iframe',
                'sec-fetch-mode': 'navigate',
                'sec-fetch-site': 'cross-site',
                'sec-fetch-storage-access': 'active',
                'upgrade-insecure-requests': '1',
                'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36'
            }
            session = requests.Session()
            session.headers.update(headers)
            embed_res = session.get(embed_src, timeout=20, allow_redirects=True)
            if embed_res and embed_res.status_code == 200:
                # Extract video URL from the response JSON
                response_text = embed_res.text
                video_url = None
                match = re.search(r'\\"copyTextEndpoint\\":\s*\{\s*\\"text\\":\s*\\"([^"]+)\\"', response_text)
                if match:
                    video_url = match.group(1)
                    # Normalize YouTube URL to canonical format
                    if "youtu.be/" in video_url:
                        video_id = video_url.split("youtu.be/")[1].split("?")[0].split("&")[0]
                        video_url = f"https://www.youtube.com/watch?v={video_id}"
                    elif "youtube.com/watch?v=" not in video_url:
                        # Extract video ID if it's in a different format
                        video_id_match = re.search(r'(?:youtu\.be/|youtube\.com/watch\?v=)([\w-]+)', video_url)
                        if video_id_match:
                            video_url = f"https://www.youtube.com/watch?v={video_id_match.group(1)}"
                
                # Extract link text from embedPreview.thumbnailPreviewRenderer.title.runs[0].text
                link_text = None
                title_match = re.search(r'\\"embedPreview\\":\s*\{\s*\\"thumbnailPreviewRenderer\\":\s*\{\s*\\"title\\":\s*\{\s*\\"runs\\":\s*\[\s*\{\s*\\"text\\":\s*\\"([^"]+)\\"', response_text)
                if title_match:
                    link_text = title_match.group(1)
                
                if video_url:
                    videos.append({"url": video_url, "link_text": link_text})
        except Exception as e:
            logger.warning(f"Failed to extract YouTube video from {embed_src}: {e}")
            continue
    
    return {
        "title": title,
        "subheader": subheader,
        "intro_header": intro_header,
        "description": description,
        "features": features,
        "images": images,
        "documents": docs,
        "videos": videos,
        "breadcrumb": breadcrumb,
        "product_specs_table": specs_table,
        "accordion_content": accordion_content,
        "tabs_data": tabs_data,
        "catch_phrases": catch_phrases
    }

def write_docs(base_outdir, docs):
    documentation = []
    seen = set()
    for d in docs:
        u = (d.get("url") or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        outdir = os.path.join(base_outdir, "documentation")
        ensure(outdir)
        n = d.get("name") or fname(u, "document.pdf")
        p = os.path.join(outdir, n)
        entry = {"name": n, "file_path": None, "url": u, "version": None, "date": None, "language": d.get("link_text"), "description": None}
        try:
            download(u, p)
            entry["file_path"] = fullpath(p)
        except Exception as e:
            entry["description"] = f"Download failed: {str(e)}"
        documentation.append(entry)
    outdir = os.path.join(base_outdir, "documentation")
    ensure(outdir)
    with open(os.path.join(outdir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(documentation, f, indent=2, ensure_ascii=False)

def write_trainings(base_outdir, videos):
    trainings = []
    seen = set()
    for v in videos:
        u = (v.get("url") or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        link_text = v.get("link_text") or ""
        # Normalize YouTube URL to canonical format
        if "youtu.be/" in u:
            video_id = u.split("youtu.be/")[1].split("?")[0].split("&")[0]
            u = f"https://www.youtube.com/watch?v={video_id}"
        # Extract video ID from YouTube URL for naming
        video_id = None
        if "youtube.com/watch?v=" in u:
            video_id = u.split("v=")[1].split("&")[0]
        
        name = video_id if video_id else fname(u, "video")
        entry = {"name": name, "file_path": None, "url": u, "version": None, "date": None, "language": None, "description": link_text if link_text else None}
        trainings.append(entry)
    outdir = os.path.join(base_outdir, "trainings")
    ensure(outdir)
    with open(os.path.join(outdir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(trainings, f, indent=2, ensure_ascii=False)

def write_images(img_urls, outdir, alt_text="product image", only_first=False):
    ensure(outdir)
    meta = []
    saved = []
    seen = set()
    images_to_process = img_urls[:1] if only_first else img_urls
    for u in images_to_process:
        u = (u or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        if u.endswith('.html') or not any(ext in u.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
            continue
        n = fname(u, "image.jpg")
        p = os.path.join(outdir, n)
        if not os.path.exists(p):
            try:
                download(u, p)
            except Exception as e:
                logger.warning(f"Skip image {u}: {e}")
                continue
        saved.append(n)
    if saved:
        alias = os.path.join(outdir, "product.jpeg")
        first_file_path = os.path.join(outdir, saved[0])
        if not os.path.exists(alias):
            try:
                shutil.copyfile(first_file_path, alias)
            except Exception as e:
                logger.warning(f"Failed to copy image to product.jpeg: {e}")
        if os.path.exists(first_file_path) and first_file_path != alias:
            try:
                os.remove(first_file_path)
            except Exception as e:
                logger.warning(f"Failed to remove duplicate image {first_file_path}: {e}")
    if saved:
        first_url = img_urls[0] if img_urls else None
        meta.append({"name": "product.jpeg", "file_path": fullpath(os.path.join(outdir, "product.jpeg")), "url": first_url, "alt": alt_text, "version": None, "date": None, "language": None, "description": "Main product image"})
        seen_file_paths = {fullpath(os.path.join(outdir, "product.jpeg"))}
        if not only_first:
            for i, n in enumerate(saved[1:], 1):
                file_path = os.path.join(outdir, n)
                full_file_path = fullpath(file_path)
                if full_file_path not in seen_file_paths and os.path.exists(file_path):
                    seen_file_paths.add(full_file_path)
                    img_url = img_urls[i] if i < len(img_urls) else None
                    meta.append({"name": n, "file_path": full_file_path, "url": img_url, "alt": alt_text, "version": None, "date": None, "language": None, "description": None})
    with open(os.path.join(outdir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

def write_overview_md(md_dir, title, summary, features, breadcrumb=None, product_specs_table=None, accordion_content=None, tabs_data=None, catch_phrases=None, subheader=None, intro_header=None):
    ensure(md_dir)
    with open(os.path.join(md_dir, "overview.md"), "w", encoding="utf-8") as f:
        if breadcrumb:
            f.write(f"{breadcrumb}\n\n")
        f.write(f"# {title or 'Product'}\n\n")
        if subheader:
            f.write(f"{subheader}\n\n")
        if intro_header:
            f.write(f"## {intro_header}\n\n")
        if summary:
            f.write(f"{summary}\n\n")
        if catch_phrases:
            f.write("## Key Features\n\n")
            for cp in catch_phrases:
                f.write(f"### {cp.get('headline', '')}\n\n")
                if cp.get('subline'):
                    f.write(f"{cp.get('subline')}\n\n")
        if tabs_data:
            f.write("## Product Overview\n\n")
            for tab_label, tab_content in tabs_data.items():
                f.write(f"### {tab_label}\n\n")
                f.write(f"{tab_content}\n\n")
        if product_specs_table and not tabs_data:
            f.write("## Product Specifications\n\n")
            f.write("| Specification | Value |\n")
            f.write("|---------------|-------|\n")
            for key, value in product_specs_table.items():
                f.write(f"| {key} | {value} |\n")
            f.write("\n")
        if features and not tabs_data:
            f.write("## Features\n\n")
            for feat in features:
                f.write(f"- {feat}\n")
            f.write("\n")
        if accordion_content:
            f.write("## All Details\n\n")
            f.write(f"{accordion_content}\n")

def write_product(outdir, data, page_url):
    """Write product data"""
    ensure(outdir)
    dirs = ["documentation", "images", "block_diagrams", "design_resources", "software_tools", "tables", "markdowns", "trainings", "other"]
    for d in dirs:
        ensure(os.path.join(outdir, d))
    if data.get("images"):
        write_images(data["images"], os.path.join(outdir, "images"), alt_text=data.get("title") or "product image", only_first=True)
    else:
        with open(os.path.join(outdir, "images", "metadata.json"), "w", encoding="utf-8") as f:
            json.dump([], f, indent=2, ensure_ascii=False)
    catch_phrases = data.get("catch_phrases", [])
    if catch_phrases:
        img_dir = os.path.join(outdir, "images")
        ensure(img_dir)
        with open(os.path.join(img_dir, "metadata.json"), "r", encoding="utf-8") as f:
            meta = json.load(f)
        for cp in catch_phrases:
            img_url = cp.get("image_url", "")
            if img_url:
                n = fname(img_url, "catchphrase.jpg")
                p = os.path.join(img_dir, n)
                try:
                    download(img_url, p)
                    meta.append({"name": n, "file_path": fullpath(p), "url": img_url, "alt": cp.get("headline", ""), "version": None, "date": None, "language": None, "description": cp.get("subline")})
                except Exception as e:
                    logger.warning(f"Skip catch phrase image {img_url}: {e}")
        with open(os.path.join(img_dir, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
    write_docs(outdir, data.get("documents") or [])
    write_trainings(outdir, data.get("videos") or [])
    for d in ["design_resources", "software_tools", "other"]:
        with open(os.path.join(outdir, d, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump([], f, indent=2, ensure_ascii=False)
    with open(os.path.join(outdir, "block_diagrams", "block_diagram_mappings.json"), "w", encoding="utf-8") as f:
        json.dump([], f, indent=2, ensure_ascii=False)
    write_overview_md(os.path.join(outdir, "markdowns"), data.get("title", ""), data.get("description", ""), data.get("features", []), data.get("breadcrumb"), data.get("product_specs_table"), data.get("accordion_content"), data.get("tabs_data"), data.get("catch_phrases"), data.get("subheader"), data.get("intro_header"))
    tables_dir = os.path.join(outdir, "tables")
    ensure(tables_dir)
    with open(os.path.join(tables_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump([], f, indent=2, ensure_ascii=False)
    pdf_link = None
    pdf_filename = None
    documents = data.get("documents", [])
    for doc in documents:
        doc_url = doc.get("url", "")
        if doc_url:
            if doc.get("is_pdf") or doc_url.lower().endswith(".pdf"):
                pdf_link = doc_url
                pdf_filename = doc.get("name") or doc.get("link_text") or fname(doc_url, "document.pdf")
                break
    product_title = data.get("title", "")
    product_key = slug_from_name(product_title) if product_title else slug_from_name(page_url)
    products_map = {
        product_key: {
            "Product": empty_to_none(product_title),
            "name": empty_to_none(product_title),
            "product_page_link": empty_to_none(page_url),
            "image_url": empty_to_none(data.get("images", [""])[0] if data.get("images") else ""),
            "description": empty_to_none(data.get("description", "")),
            "pdf_link": empty_to_none(pdf_link),
            "pdf_filename": empty_to_none(pdf_filename)
        }
    }
    with open(os.path.join(tables_dir, "products.json"), "w", encoding="utf-8") as f:
        json.dump(products_map, f, indent=2, ensure_ascii=False)

def crawl_product(product_url, outdir):
    """Crawl product page"""
    logger.info(f"[PRODUCT] {product_url}")
    try:
        data = parse_product(product_url)
        write_product(outdir, data, product_url)
        logger.info(f"Successfully processed product: {data.get('title', product_url)}")
    except Exception as e:
        logger.error(f"Error crawling product: {e}")

# =========================
# CLI
# =========================

def is_product_url(url):
    """Check if URL is a product (contains 'detail')"""
    return '/detail/' in url

def main():
    ap = argparse.ArgumentParser(description="STULZ crawler")
    ap.add_argument("--url", required=True, help="Category or Product URL")
    ap.add_argument("--out", required=True, help="Output directory")
    args = ap.parse_args()

    url = args.url.strip()
    logger.info(f"Starting STULZ crawler for URL: {url}")

    if is_product_url(url):
        crawl_product(url, args.out)
    else:
        crawl_category(url, args.out)
    logger.info(f"Successfully processed: {url}")

if __name__ == "__main__":
    main()
