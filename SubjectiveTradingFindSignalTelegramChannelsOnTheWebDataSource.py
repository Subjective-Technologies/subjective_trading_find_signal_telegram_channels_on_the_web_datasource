import os
import re
import json
import time
import random
from typing import Any
from urllib.parse import unquote

from subjective_abstract_data_source_package import SubjectiveDataSource
from brainboost_data_source_logger_package.BBLogger import BBLogger


# Default search queries covering crypto, forex, and general trading signal channels
DEFAULT_SEARCH_QUERIES = [
    "free crypto signals telegram channel",
    "best crypto trading signals telegram",
    "binance signals telegram group",
    "bitcoin trading signals telegram free",
    "ethereum signals telegram channel",
    "altcoin signals telegram free",
    "crypto pump signals telegram",
    "forex signals telegram free",
    "free forex signals telegram group",
    "gold trading signals telegram",
    "crypto futures signals telegram",
    "binance futures signals telegram free",
    "telegram crypto signal channel list",
    "best free telegram crypto signals 2025",
    "cryptocurrency signals telegram group free",
    "day trading signals telegram",
    "scalping signals telegram crypto",
    "telegram channels for crypto trading signals",
    "free vip crypto signals telegram",
    "spot trading signals telegram",
    "defi signals telegram channel",
    "nft signals telegram",
    "crypto whale signals telegram",
    "leverage trading signals telegram",
    "bybit signals telegram free",
    "okx signals telegram channel",
    "kucoin signals telegram free",
    "crypto technical analysis telegram",
    "free daily crypto signals telegram",
    "verified crypto signals telegram",
]


TELEGRAM_LINK_PATTERN = re.compile(
    r'https?://(?:t(?:elegram)?\.(?:me|org|dog))/(?:joinchat/[a-zA-Z0-9_\-]{5,}|[a-zA-Z][a-zA-Z0-9_]{3,})',
    re.IGNORECASE,
)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

# Telegram usernames to exclude (bots, generic, non-channel)
EXCLUDED_USERNAMES = {
    "joinchat", "share", "addstickers", "addtheme", "setlanguage",
    "socks", "proxy", "iv", "login", "confirmphone", "s",
}


class SubjectiveTradingFindSignalTelegramChannelsOnTheWebDataSource(SubjectiveDataSource):
    """Crawls the web via DuckDuckGo searching for Telegram channels
    that provide free crypto/forex trading signals. Collects unique
    channel links and saves them to a context file."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        conn = self._connection or {}
        self.max_results_per_query = int(conn.get("max_results_per_query", 15))
        self.delay_between_queries = float(conn.get("delay_between_queries", 2.0))
        self.custom_queries = conn.get("custom_queries", "")
        self.enable_page_scraping = conn.get("enable_page_scraping", True)

    @classmethod
    def connection_schema(cls) -> dict:
        return {
            "max_results_per_query": {
                "type": "int",
                "label": "Max Results Per Query",
                "description": "Maximum number of DuckDuckGo results to fetch per search query.",
                "default": 15,
                "min": 5,
                "max": 50,
            },
            "delay_between_queries": {
                "type": "number",
                "label": "Delay Between Queries (seconds)",
                "description": "Seconds to wait between search queries to avoid rate limiting.",
                "default": 2.0,
                "min": 0.5,
                "max": 30.0,
                "step": 0.5,
            },
            "custom_queries": {
                "type": "textarea",
                "label": "Custom Search Queries",
                "description": "Additional search queries, one per line. These are appended to the built-in queries.",
                "placeholder": "free crypto signals telegram\nbinance pump signals telegram group",
                "rows": 6,
                "required": False,
            },
            "enable_page_scraping": {
                "type": "checkbox",
                "label": "Scrape Result Pages for Telegram Links",
                "description": "When enabled, visits each search result page and extracts Telegram links from the HTML. Slower but finds more channels.",
                "default": True,
            },
        }

    @classmethod
    def request_schema(cls) -> dict:
        return {
            "queries_override": {
                "type": "textarea",
                "label": "Queries Override",
                "description": "If provided, use ONLY these queries (one per line) instead of the built-in + custom list.",
                "required": False,
            },
        }

    @classmethod
    def output_schema(cls) -> dict:
        return {
            "channels": {
                "type": "text",
                "label": "Telegram Channels (JSON list)",
                "description": "JSON array of unique Telegram channel URLs found.",
            },
            "total_found": {
                "type": "int",
                "label": "Total Channels Found",
            },
            "queries_executed": {
                "type": "int",
                "label": "Queries Executed",
            },
            "context_file": {
                "type": "text",
                "label": "Context File Path",
                "description": "Path to the saved context file with all channels.",
            },
        }

    @classmethod
    def icon(cls) -> str:
        icon_path = os.path.join(os.path.dirname(__file__), "icon.svg")
        try:
            with open(icon_path, "r", encoding="utf-8") as handle:
                return handle.read()
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _build_query_list(self, request: dict) -> list[str]:
        """Build the list of search queries to execute."""
        override = (request.get("queries_override") or "").strip()
        if override:
            return [q.strip() for q in override.splitlines() if q.strip()]

        queries = list(DEFAULT_SEARCH_QUERIES)
        if self.custom_queries:
            for q in self.custom_queries.splitlines():
                q = q.strip()
                if q and q not in queries:
                    queries.append(q)
        return queries

    @staticmethod
    def _normalize_channel(url: str) -> str | None:
        """Normalize a Telegram URL and filter out non-channel links."""
        url = url.rstrip("/").split("?")[0]
        # Extract the username/joinchat part
        m = re.search(
            r'https?://(?:t(?:elegram)?\.(?:me|org|dog))/(joinchat/[a-zA-Z0-9_\-]+|[a-zA-Z][a-zA-Z0-9_]+)',
            url, re.IGNORECASE,
        )
        if not m:
            return None
        path = m.group(1)
        # Filter excluded
        username = path.split("/")[-1].lower() if "/" not in path else path.split("/")[0].lower()
        if username in EXCLUDED_USERNAMES:
            return None
        return f"https://t.me/{path}"

    def _search_duckduckgo(self, query: str) -> list[dict]:
        """Search DuckDuckGo and return result dicts with 'href' and 'body'."""
        from duckduckgo_search import DDGS

        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=self.max_results_per_query))
            return results
        except Exception as e:
            BBLogger.log(f"DuckDuckGo search failed for '{query}': {e}")
            return []

    def _extract_channels_from_search_results(self, results: list[dict]) -> set[str]:
        """Extract Telegram links directly from search result URLs and snippets."""
        channels = set()
        for r in results:
            href = r.get("href", "")
            body = r.get("body", "")
            title = r.get("title", "")
            # Check if the result URL itself is a Telegram link
            norm = self._normalize_channel(href)
            if norm:
                channels.add(norm)
            # Also scan the snippet and title text for telegram links
            for text in [body, title, href]:
                for match in TELEGRAM_LINK_PATTERN.findall(text):
                    norm = self._normalize_channel(match)
                    if norm:
                        channels.add(norm)
        return channels

    def _scrape_page_for_channels(self, url: str) -> set[str]:
        """Visit a webpage and extract all Telegram links from its HTML."""
        import requests
        from bs4 import BeautifulSoup

        channels = set()
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": random.choice(USER_AGENTS)},
                timeout=15,
                allow_redirects=True,
            )
            resp.raise_for_status()
            html = resp.text
            # Regex extraction from raw HTML
            for match in TELEGRAM_LINK_PATTERN.findall(html):
                norm = self._normalize_channel(match)
                if norm:
                    channels.add(norm)
            # Also parse href attributes with BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                norm = self._normalize_channel(href)
                if norm:
                    channels.add(norm)
        except Exception as e:
            BBLogger.log(f"Failed to scrape {url}: {e}")
        return channels

    def _save_context_file(self, channels: list[str]) -> str:
        """Save the channel list to the output context directory."""
        output_dir = self.output_dir
        os.makedirs(output_dir, exist_ok=True)
        context_path = os.path.join(output_dir, "telegram_signal_channels.json")

        # If file already exists, merge with existing channels
        existing = set()
        if os.path.exists(context_path):
            try:
                with open(context_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        existing = set(data)
                    elif isinstance(data, dict) and "channels" in data:
                        existing = set(data["channels"])
            except Exception:
                pass

        all_channels = sorted(existing | set(channels))
        output_data = {
            "channels": all_channels,
            "total": len(all_channels),
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        with open(context_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        BBLogger.log(f"Saved {len(all_channels)} channels to {context_path}")
        return context_path

    def run(self, request: dict) -> dict:
        request = request or {}
        queries = self._build_query_list(request)
        all_channels: set[str] = set()
        queries_executed = 0

        BBLogger.log(f"Starting Telegram channel crawl with {len(queries)} queries")

        for i, query in enumerate(queries):
            BBLogger.log(f"[{i+1}/{len(queries)}] Searching: {query}")
            results = self._search_duckduckgo(query)
            queries_executed += 1

            # Extract channels from search result metadata
            found = self._extract_channels_from_search_results(results)
            all_channels.update(found)

            # Optionally scrape each result page for more links
            if self.enable_page_scraping:
                for r in results:
                    page_url = r.get("href", "")
                    if not page_url:
                        continue
                    # Skip scraping telegram.me itself — we already have the link
                    if re.match(r'https?://t(elegram)?\.me/', page_url):
                        norm = self._normalize_channel(page_url)
                        if norm:
                            all_channels.add(norm)
                        continue
                    page_channels = self._scrape_page_for_channels(page_url)
                    all_channels.update(page_channels)

            BBLogger.log(f"  Found {len(found)} from search + scraping. Total unique so far: {len(all_channels)}")

            # Rate-limit delay between queries
            if i < len(queries) - 1:
                time.sleep(self.delay_between_queries + random.uniform(0, 1.0))

        # Save to context file
        channel_list = sorted(all_channels)
        context_file = self._save_context_file(channel_list)

        BBLogger.log(f"Crawl complete. {len(channel_list)} unique channels found across {queries_executed} queries.")

        return {
            "channels": json.dumps(channel_list),
            "total_found": len(channel_list),
            "queries_executed": queries_executed,
            "context_file": context_file,
        }
