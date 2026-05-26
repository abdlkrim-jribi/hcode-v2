"""Web tools: web_fetch, web_search, web_scrape."""

from __future__ import annotations

import os
from typing import Optional

from langchain_core.tools import tool


@tool
def web_fetch(url: str, raw: bool = False) -> str:
    """Fetch a URL and return its content as plain text.

    Args:
        url: URL to fetch.
        raw: If True, return raw HTML instead of converting to text.
    """
    try:
        import httpx
    except ImportError:
        return "Error: httpx not installed. Run: pip install httpx"

    try:
        with httpx.Client(follow_redirects=True, timeout=30) as client:
            resp = client.get(url, headers={"User-Agent": "HCode/2.0"})
            resp.raise_for_status()
            content = resp.text
    except httpx.HTTPStatusError as exc:
        return f"HTTP {exc.response.status_code}: {url}"
    except Exception as exc:
        return f"Error fetching {url}: {exc}"

    if raw:
        return content

    try:
        import html2text
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.body_width = 0
        return h.handle(content)
    except ImportError:
        # strip tags manually
        import re
        text = re.sub(r"<[^>]+>", "", content)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


@tool
def web_search(query: str, count: int = 5) -> str:
    """Search the web using the Brave Search API.

    Requires BRAVE_API_KEY environment variable.

    Args:
        query: Search query string.
        count: Number of results to return (default 5).
    """
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        return "Error: BRAVE_API_KEY environment variable not set."

    try:
        import httpx
    except ImportError:
        return "Error: httpx not installed. Run: pip install httpx"

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": count},
                headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return f"Search error: {exc}"

    results = data.get("web", {}).get("results", [])
    if not results:
        return f"No results for: {query}"

    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        url = r.get("url", "")
        desc = r.get("description", "")
        lines.append(f"{i}. {title}\n   {url}\n   {desc}\n")
    return "\n".join(lines)


@tool
def web_scrape(url: str, selector: Optional[str] = None) -> str:
    """Scrape a URL and return structured text, optionally filtered by CSS selector.

    Args:
        url: URL to scrape.
        selector: CSS selector to extract a specific element (e.g. 'main', 'article').
    """
    try:
        import httpx
    except ImportError:
        return "Error: httpx not installed. Run: pip install httpx"

    try:
        with httpx.Client(follow_redirects=True, timeout=30) as client:
            resp = client.get(url, headers={"User-Agent": "HCode/2.0"})
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        return f"Error fetching {url}: {exc}"

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return "Error: beautifulsoup4 not installed. Run: pip install beautifulsoup4"

    soup = BeautifulSoup(html, "html.parser")

    if selector:
        el = soup.select_one(selector)
        if el is None:
            return f"Selector '{selector}' not found on page."
        text = el.get_text(separator="\n", strip=True)
    else:
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)

    import re
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
