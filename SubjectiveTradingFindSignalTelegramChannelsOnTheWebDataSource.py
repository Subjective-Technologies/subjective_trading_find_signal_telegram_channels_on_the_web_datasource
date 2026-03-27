import os
import re
import json
import time
import random
import sqlite3
from typing import Any
from urllib.parse import unquote, urlparse

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
    r'https?://(?:t(?:elegram)?\.(?:me|org|dog))/(?:joinchat/[a-zA-Z0-9_\-]{5,}|\+[a-zA-Z0-9_\-]{5,}|[a-zA-Z][a-zA-Z0-9_]{3,})',
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

SCRAPE_BLOCKLIST_DOMAINS = {
    "zhihu.com",
}

PAGE_CACHE_FILENAME = "telegram_signal_page_cache.sqlite3"
FAILED_PAGE_RETRY_SECONDS = 24 * 60 * 60


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
        self._page_cache_conn: sqlite3.Connection | None = None
        self._page_scrape_memory: dict[str, tuple[set[str], str]] = {}

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
        url = unquote((url or "").strip()).split("#", 1)[0].split("?", 1)[0].rstrip("/")
        m = re.search(
            r'https?://(?:t(?:elegram)?\.(?:me|org|dog))/((?:joinchat/[a-zA-Z0-9_\-]+)|(?:\+[a-zA-Z0-9_\-]+)|(?:[a-zA-Z][a-zA-Z0-9_]+))',
            url, re.IGNORECASE,
        )
        if not m:
            return None

        path = m.group(1).strip("/")
        lower_path = path.lower()
        if lower_path.startswith("joinchat/"):
            invite_code = path.split("/", 1)[1].strip()
            return f"https://t.me/joinchat/{invite_code}" if invite_code else None

        if path.startswith("+"):
            invite_code = path[1:].strip()
            return f"https://t.me/+{invite_code}" if invite_code else None

        username = path.lower()
        if username in EXCLUDED_USERNAMES:
            return None
        return f"https://t.me/{path}"

    def _search_duckduckgo(self, query: str) -> list[dict]:
        """Search DuckDuckGo HTML directly and return result dicts with 'href' and 'body'."""
        import requests
        from bs4 import BeautifulSoup

        try:
            session = requests.Session()
            session.headers.update(
                {
                    "User-Agent": random.choice(USER_AGENTS),
                    "Referer": "https://html.duckduckgo.com/",
                    "Sec-Fetch-User": "?1",
                }
            )

            payload = {
                "q": query,
                "b": "",
                "kl": "wt-wt",
            }
            results: list[dict] = []
            seen_urls: set[str] = set()

            for _ in range(5):
                resp = session.post("https://html.duckduckgo.com/html/", data=payload, timeout=20)
                resp.raise_for_status()

                soup = BeautifulSoup(resp.text, "html.parser")
                page_results = 0

                for result_block in soup.select("div.result"):
                    link = result_block.select_one("a.result__a")
                    if not link:
                        continue

                    href = (link.get("href") or "").strip()
                    if not href or href in seen_urls:
                        continue
                    if href.startswith(("http://www.google.com/search?q=", "https://duckduckgo.com/y.js?ad_domain")):
                        continue

                    seen_urls.add(href)
                    title = link.get_text(" ", strip=True)
                    snippet_node = result_block.select_one(".result__snippet")
                    body = snippet_node.get_text(" ", strip=True) if snippet_node else ""

                    results.append({"href": href, "title": title, "body": body})
                    page_results += 1

                    if len(results) >= self.max_results_per_query:
                        return results

                if page_results == 0:
                    break

                next_form = soup.select_one("div.nav-link form")
                if not next_form:
                    break

                hidden_inputs = next_form.select("input[type='hidden']")
                payload = {
                    (input_tag.get("name") or "").strip(): (input_tag.get("value") or "").strip()
                    for input_tag in hidden_inputs
                    if (input_tag.get("name") or "").strip()
                }
                if not payload:
                    break

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

    def _scrape_page_for_channels(self, url: str) -> tuple[set[str], str]:
        """Visit a webpage, or reuse cached results, and extract Telegram links."""
        import requests
        from bs4 import BeautifulSoup

        cached_memory = self._page_scrape_memory.get(url)
        if cached_memory is not None:
            cached_channels, cached_source = cached_memory
            if cached_source in {"live", "cache", "memory"}:
                return set(cached_channels), "memory"
            return set(cached_channels), cached_source

        channels: set[str] = set()
        try:
            if self._should_skip_scrape(url):
                self._page_scrape_memory[url] = (set(), "skip")
                return set(), "skip"

            cached_entry = self._get_cached_page_entry(url)
            if cached_entry is not None:
                status = str(cached_entry["status"] or "").strip().lower()
                if status == "success":
                    try:
                        cached_channels = set(json.loads(cached_entry["channels_json"] or "[]"))
                    except Exception:
                        cached_channels = set()
                    self._page_scrape_memory[url] = (cached_channels, "cache")
                    return set(cached_channels), "cache"

                last_attempt_ts = float(cached_entry["last_attempt_ts"] or 0)
                if status == "failure" and (time.time() - last_attempt_ts) < FAILED_PAGE_RETRY_SECONDS:
                    self._page_scrape_memory[url] = (set(), "failure_cache")
                    return set(), "failure_cache"

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
            self._store_page_cache_success(url, channels)
            self._page_scrape_memory[url] = (set(channels), "live")
            return channels, "live"
        except requests.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else None
            self._store_page_cache_failure(url, str(e), http_status=status_code)
            BBLogger.log(f"Failed to scrape {url}: {e}")
        except Exception as e:
            self._store_page_cache_failure(url, str(e))
            BBLogger.log(f"Failed to scrape {url}: {e}")

        self._page_scrape_memory[url] = (set(), "live_failure")
        return set(), "live_failure"

    @staticmethod
    def _should_skip_scrape(url: str) -> bool:
        host = urlparse(url or "").netloc.lower()
        if not host:
            return True

        for blocked_domain in SCRAPE_BLOCKLIST_DOMAINS:
            if host == blocked_domain or host.endswith(f".{blocked_domain}"):
                return True
        return False

    def _resolve_state_dir(self) -> str:
        params = getattr(self, "params", {}) or {}
        config = getattr(self, "_config", {}) or {}

        candidates = [
            getattr(self, "output_dir", ""),
            getattr(self, "scratch_dir", ""),
            config.get("output_dir", ""),
            params.get("TARGET_DIRECTORY", ""),
            params.get("target_directory", ""),
            params.get("context_dir", ""),
            params.get("CONTEXT_DIR", ""),
        ]

        for candidate in candidates:
            path = str(candidate or "").strip()
            if path:
                return path
        return ""

    def _ensure_page_cache(self) -> sqlite3.Connection | None:
        if self._page_cache_conn is not None:
            return self._page_cache_conn

        state_dir = self._resolve_state_dir()
        if not state_dir:
            return None

        os.makedirs(state_dir, exist_ok=True)
        db_path = os.path.join(state_dir, PAGE_CACHE_FILENAME)

        try:
            conn = sqlite3.connect(db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS processed_pages (
                    page_url TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'success',
                    channels_json TEXT NOT NULL DEFAULT '[]',
                    first_seen_ts REAL NOT NULL DEFAULT 0,
                    last_attempt_ts REAL NOT NULL DEFAULT 0,
                    last_success_ts REAL NOT NULL DEFAULT 0,
                    fail_count INTEGER NOT NULL DEFAULT 0,
                    http_status INTEGER,
                    last_error TEXT NOT NULL DEFAULT ''
                );
                """
            )
            conn.commit()
            self._page_cache_conn = conn
        except sqlite3.DatabaseError as exc:
            BBLogger.log(f"Page cache disabled due to SQLite error: {exc}")
            self._page_cache_conn = None

        return self._page_cache_conn

    def _close_page_cache(self) -> None:
        if self._page_cache_conn is None:
            return

        try:
            self._page_cache_conn.close()
        finally:
            self._page_cache_conn = None

    def _get_cached_page_entry(self, url: str) -> sqlite3.Row | None:
        conn = self._ensure_page_cache()
        if conn is None:
            return None

        cursor = conn.execute(
            """
            SELECT page_url, status, channels_json, first_seen_ts, last_attempt_ts,
                   last_success_ts, fail_count, http_status, last_error
            FROM processed_pages
            WHERE page_url = ?
            """,
            (url,),
        )
        return cursor.fetchone()

    def _store_page_cache_success(self, url: str, channels: set[str]) -> None:
        conn = self._ensure_page_cache()
        if conn is None:
            return

        now = time.time()
        payload = json.dumps(sorted(channels), ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO processed_pages (
                page_url, status, channels_json, first_seen_ts, last_attempt_ts,
                last_success_ts, fail_count, http_status, last_error
            )
            VALUES (?, 'success', ?, ?, ?, ?, 0, NULL, '')
            ON CONFLICT(page_url) DO UPDATE SET
                status = 'success',
                channels_json = excluded.channels_json,
                last_attempt_ts = excluded.last_attempt_ts,
                last_success_ts = excluded.last_success_ts,
                fail_count = 0,
                http_status = NULL,
                last_error = ''
            """,
            (url, payload, now, now, now),
        )
        conn.commit()

    def _store_page_cache_failure(self, url: str, error_message: str, http_status: int | None = None) -> None:
        conn = self._ensure_page_cache()
        if conn is None:
            return

        now = time.time()
        conn.execute(
            """
            INSERT INTO processed_pages (
                page_url, status, channels_json, first_seen_ts, last_attempt_ts,
                last_success_ts, fail_count, http_status, last_error
            )
            VALUES (?, 'failure', '[]', ?, ?, 0, 1, ?, ?)
            ON CONFLICT(page_url) DO UPDATE SET
                status = 'failure',
                last_attempt_ts = excluded.last_attempt_ts,
                fail_count = processed_pages.fail_count + 1,
                http_status = excluded.http_status,
                last_error = excluded.last_error
            """,
            (url, now, now, http_status, error_message[:1000]),
        )
        conn.commit()

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
        self._page_scrape_memory = {}
        self._ensure_page_cache()

        for i, query in enumerate(queries):
            BBLogger.log(f"[{i+1}/{len(queries)}] Searching: {query}")
            before_query_total = len(all_channels)
            results = self._search_duckduckgo(query)
            queries_executed += 1

            # Extract channels from search result metadata
            found = self._extract_channels_from_search_results(results)
            all_channels.update(found)
            scraped_new = 0
            live_page_fetches = 0
            cached_page_hits = 0
            skipped_page_hits = 0

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
                    page_channels, page_source = self._scrape_page_for_channels(page_url)
                    if page_source == "live":
                        live_page_fetches += 1
                    elif page_source in {"cache", "memory"}:
                        cached_page_hits += 1
                    elif page_source in {"failure_cache", "skip", "live_failure"}:
                        skipped_page_hits += 1
                    before_page_total = len(all_channels)
                    all_channels.update(page_channels)
                    scraped_new += len(all_channels) - before_page_total

            added_this_query = len(all_channels) - before_query_total
            BBLogger.log(
                f"  Found {len(found)} in search results, {scraped_new} new from page scraping. "
                f"Fetched {live_page_fetches} live pages, reused {cached_page_hits} cached pages, "
                f"skipped {skipped_page_hits} blocked/known pages. "
                f"Added {added_this_query} total. Total unique so far: {len(all_channels)}"
            )

            # Rate-limit delay between queries
            if i < len(queries) - 1:
                time.sleep(self.delay_between_queries + random.uniform(0, 1.0))

        # Save to context file
        channel_list = sorted(all_channels)
        context_file = self._save_context_file(channel_list)
        self._close_page_cache()

        BBLogger.log(f"Crawl complete. {len(channel_list)} unique channels found across {queries_executed} queries.")

        return {
            "channels": json.dumps(channel_list),
            "total_found": len(channel_list),
            "queries_executed": queries_executed,
            "context_file": context_file,
        }
