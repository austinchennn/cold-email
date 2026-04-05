"""
Web Search & Page-Scraping Skill
=================================
Priority:
  1. Tavily API  (if TAVILY_API_KEY is set)  — highest quality
  2. DuckDuckGo  (free, no key needed)       — good fallback
  3. Direct URL fetch + BeautifulSoup        — for known URLs

Public interface
----------------
  searcher = WebSearchSkill()
  results  = searcher.search("query", num_results=5)
  text     = searcher.fetch_page("https://...")
"""

import logging
import re
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from config.settings import TAVILY_API_KEY, REQUEST_TIMEOUT, MAX_WEBPAGE_CHARS

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}


class WebSearchSkill:
    """Unified web search + page scraping interface."""

    # ── Public API ─────────────────────────────────────────────────────────────

    def search(self, query: str, num_results: int = 5) -> List[Dict]:
        """
        Return a list of search result dicts:
          [{title: str, url: str, snippet: str}, ...]
        """
        if TAVILY_API_KEY:
            return self._tavily_search(query, num_results)
        return self._ddg_search(query, num_results)

    def fetch_page(self, url: str, max_chars: int = MAX_WEBPAGE_CHARS) -> str:
        """
        Fetch a URL and return cleaned plain text (capped at max_chars).
        Returns empty string on failure rather than raising.
        """
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return self._clean_html(resp.text)[:max_chars]
        except Exception as exc:
            logger.warning(f"fetch_page failed for {url}: {exc}")
            return ""

    def search_professors(
        self,
        domain: str,
        university: Optional[str] = None,
        num_results: int = 10,
    ) -> List[Dict]:
        """Specialised helper: search for faculty in a research domain."""
        if university:
            query = f'site:{university}.edu professor "{domain}" research lab'
        else:
            query = f'professor "{domain}" research faculty lab publications university'
        return self.search(query, num_results)

    # ── Search Backends ────────────────────────────────────────────────────────

    def _tavily_search(self, query: str, num_results: int) -> List[Dict]:
        try:
            resp = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "max_results": num_results,
                },
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            return [
                {
                    "title":   r.get("title", ""),
                    "url":     r.get("url", ""),
                    "snippet": r.get("content", ""),
                }
                for r in resp.json().get("results", [])
            ]
        except Exception as exc:
            logger.warning(f"Tavily search failed, falling back to DuckDuckGo: {exc}")
            return self._ddg_search(query, num_results)

    def _ddg_search(self, query: str, num_results: int) -> List[Dict]:
        try:
            from duckduckgo_search import DDGS  # type: ignore

            results: List[Dict] = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=num_results):
                    results.append(
                        {
                            "title":   r.get("title", ""),
                            "url":     r.get("href", ""),
                            "snippet": r.get("body", ""),
                        }
                    )
            return results
        except Exception as exc:
            logger.warning(f"DuckDuckGo search failed: {exc}")
            return []

    # ── HTML Cleaning ──────────────────────────────────────────────────────────

    @staticmethod
    def _clean_html(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r"\s{2,}", " ", text)
        return text.strip()
