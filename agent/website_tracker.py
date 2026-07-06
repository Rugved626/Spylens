import requests
from bs4 import BeautifulSoup
import hashlib

def get_website_snapshot(url: str) -> dict:
    """
    Fetch website content and return a snapshot with hash.
    Hash changes = website changed.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; SpyLens/1.0)"
        }
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")

        # remove scripts and styles — focus on real content
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()

        # extract key sections
        title = soup.title.string if soup.title else "No title"
        meta_desc = ""
        meta = soup.find("meta", attrs={"name": "description"})
        if meta:
            meta_desc = meta.get("content", "")

        # get main text
        text = soup.get_text(separator=" ", strip=True)
        text = " ".join(text.split())[:3000]  # limit to 3000 chars

        # hash for change detection
        content_hash = hashlib.md5(text.encode()).hexdigest()

        return {
            "url": url,
            "title": title,
            "meta_description": meta_desc,
            "content_preview": text[:500],
            "full_text": text,
            "content_hash": content_hash,
            "status": "ok"
        }

    except Exception as e:
        return {
            "url": url,
            "error": str(e),
            "status": "error"
        }
