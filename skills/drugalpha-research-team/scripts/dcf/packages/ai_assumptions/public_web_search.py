"""Small read-only public search helper for auditable DCF assumption research."""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=100)
def search_public_web(query: str, limit: int = 4) -> tuple[dict[str, str], ...]:
    clean_query = re.sub(r"\s+", " ", str(query or "")).strip()
    if not clean_query:
        return ()
    brave_url = "https://search.brave.com/search?q=" + urllib.parse.quote(clean_query)
    try:
        results = _parse_brave_results(clean_query, _fetch_html(brave_url), limit)
        if results:
            return tuple(results)
    except Exception:
        pass
    duck_url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(clean_query)
    try:
        results = _parse_duckduckgo_results(clean_query, _fetch_html(duck_url), limit)
        if results:
            return tuple(results)
    except Exception:
        pass
    return tuple(_search_bing_rss(clean_query, limit))


def dedupe_search_results(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        url = str(item.get("url") or "").strip()
        key = url or f"{item.get('title')}|{item.get('snippet')}"
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(
            {
                "query": str(item.get("query") or ""),
                "title": str(item.get("title") or ""),
                "url": url,
                "snippet": str(item.get("snippet") or "")[:500],
            }
        )
    return output


def _fetch_html(url: str) -> str:
    if sys.platform.startswith("win"):
        completed = subprocess.run(
            ["curl.exe", "-L", "--fail", "--silent", "--max-time", "8", "-A", "Mozilla/5.0", url],
            capture_output=True,
            check=True,
            timeout=11,
        )
        return completed.stdout.decode("utf-8", errors="replace")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 DrugAlpha-DCF/1.0"})
    with urllib.request.urlopen(request, timeout=8) as response:
        return response.read().decode("utf-8", errors="replace")


def _parse_brave_results(query: str, body: str, limit: int) -> list[dict[str, str]]:
    js_string = r'((?:\\.|[^"\\])*)'
    pattern = re.compile(
        r'\{title:"' + js_string + r'",url:"' + js_string + r'",full_title:.*?,description:"' + js_string + r'"',
        flags=re.DOTALL,
    )
    results: list[dict[str, str]] = []
    for match in pattern.finditer(body):
        try:
            title = html.unescape(json.loads(f'"{match.group(1)}"'))
            url = json.loads(f'"{match.group(2)}"')
            snippet = _strip_html(json.loads(f'"{match.group(3)}"'))[:500]
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if url.startswith(("http://", "https://")):
            results.append({"query": query, "title": title, "url": url, "snippet": snippet})
        if len(results) >= limit:
            break
    return results


def _parse_duckduckgo_results(query: str, body: str, limit: int) -> list[dict[str, str]]:
    pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
        r'.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        flags=re.DOTALL | re.IGNORECASE,
    )
    results: list[dict[str, str]] = []
    for raw_url, raw_title, raw_snippet in pattern.findall(body)[:limit]:
        url = html.unescape(raw_url)
        if url.startswith("//"):
            url = "https:" + url
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc.endswith("duckduckgo.com"):
            url = urllib.parse.parse_qs(parsed.query).get("uddg", [url])[0]
        results.append({"query": query, "title": _strip_html(raw_title), "url": url, "snippet": _strip_html(raw_snippet)[:500]})
    return results


def _search_bing_rss(query: str, limit: int) -> list[dict[str, str]]:
    url = "https://www.bing.com/search?format=rss&q=" + urllib.parse.quote(query)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 DrugAlpha-DCF/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            root = ET.fromstring(response.read())
    except Exception:
        return []
    return [
        {
            "query": query,
            "title": _clean_text(item.findtext("title")),
            "url": _clean_text(item.findtext("link")),
            "snippet": _clean_text(item.findtext("description"))[:500],
        }
        for item in root.findall("./channel/item")[:limit]
    ]


def _strip_html(value: Any) -> str:
    return _clean_text(html.unescape(re.sub(r"<[^>]+>", " ", str(value or ""))))


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
