import json
import requests
import argparse
from pathlib import Path
from parsel import Selector
from urllib.parse import urljoin
import logging
import re

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

BASE_URL = "https://www.nuvoton.com/"

def get_structure():
    results = []
    
    headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'en-US,en;q=0.9,es;q=0.8,ja;q=0.7',
        'priority': 'u=1, i',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
    }
    
    session = requests.Session()
    session.headers.update(headers)

    params = {
        'structure': 'productLeftMenu-structure_v20251216-1200.json',
        'language': 'productLeftMenu_job_v20251216-1200_en.json',
        'locale': 'en',
    }

    response = session.get(
        f'{BASE_URL}system/modules/com.thesys.project.nuvoton/elements/menuCache.jsp',
        params=params
    )
    logger.info(f"Response status: {response.status_code}")
    data = json.loads(response.text)
    
    # Build lookup maps
    structure_map = {item['id']: item for item in data.get('sCache', [])}
    label_map = {item['id']: item['title'] for item in data.get('lCache', [])}
    
    # Build hierarchy recursively
    def build_node(item_id, parent_breadcrumbs=None):
        if item_id not in structure_map:
            return None
        
        if parent_breadcrumbs is None:
            parent_breadcrumbs = ['Products']
        
        struct = structure_map[item_id]
        title = label_map.get(item_id, '')
        url = struct.get('url', '')
        
        # Make URL absolute if needed
        if url and not url.startswith('http'):
            url = urljoin(BASE_URL, url)
        
        # Build breadcrumbs for current node
        breadcrumbs = parent_breadcrumbs + [title]
        
        # Find children (items with this id as parentId)
        children = []
        for sid, sitem in structure_map.items():
            if sitem.get('parentId') == item_id:
                child_node = build_node(sid, breadcrumbs)
                if child_node:
                    children.append(child_node)
        if not children:
            url = url + '#'
        node = {
            'name': title,
            'url': url,
            'breadcrumbs': breadcrumbs,
            'sub_topics': children
        }
        
        return node
    
    # Find all root items (those with empty parentId)
    for item_id, item in structure_map.items():
        if not item.get('parentId'):
            root_node = build_node(item_id)
            if root_node:
                results.append(root_node)
    
    logger.info(f"Extracted {len(results)} root categories")

    return results

def main():
    parser = argparse.ArgumentParser(description="Export NI Products mega menu to JSON.")
    parser.add_argument("--out", required=True, help="Path to output JSON file.")
    args = parser.parse_args()

    data = get_structure()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
