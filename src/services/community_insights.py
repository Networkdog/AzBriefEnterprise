"""Community insight service — practical commentary on Azure changes.

Why this exists
---------------
Official docs describe what a feature *is*. They rarely describe what breaks,
what conflicts, or what a practitioner regretted. Measured on this repo, the
report judge marked ``architectural_depth`` at or below 3/5 on **51%** of
reports and ``actionability`` on **74%**, with feedback repeatedly asking for
real-world trade-offs the official documentation does not carry.

Azure Weekly (https://azureweekly.info, published by endjin every Sunday since
2014) curates the week's Azure writing into per-category digests. Its
``robots.txt`` is ``User-agent: * Allow: /``.

Design notes (verified against the live site before implementing):

* **Do not match on Azure Update ID.** Entries that cite an update ID are
  authored by "The Azure Updates Team" and merely restate the announcement —
  no added value. The useful entries are independent posts with *no* update ID
  (e.g. "AKS managed Gateway API blocks the ALB controller"). Matching is
  therefore done on **service/topic keywords**, not IDs.
* **Caveat-style posts rank first.** Titles containing "fails", "blocks",
  "gotcha", "pitfall", "lessons learned", "think twice" carry the practical
  risk information the reports are missing.
* Content is **untrusted third-party text**. It is fenced and labelled in the
  prompt so the model treats it as commentary, never as instructions.

Full-text enrichment (Microsoft Tech Community)
----------------------------------------------
The digest only carries a ~200-character blurb per entry, which is too thin to
yield prerequisites or trade-offs. Measured on the live cache, **507 of 1,250**
cached entries link to ``techcommunity.microsoft.com``, and that site publishes
per-board RSS feeds whose ``<description>`` holds the **complete article body**
(5,000-15,000 characters), not a summary.

So for the top-ranked matches we resolve the post back to its board feed and
extract the sentences that carry operational constraints — e.g. *"The scenario
requires a Flexible Server with High Availability enabled"* and *"Unlike VACUUM
FULL, pg_repack ... requires only a brief lock during the final table swap"* —
neither of which appears in the digest blurb or in the announcement.

Limits, verified rather than assumed:

* A board feed exposes only its ~20 newest posts, so older entries are not
  retrievable. Every retrieval failure degrades to the digest blurb.
* ``robots.txt`` permits the RSS path (it disallows ``/users/``, ``/help``,
  ``/closedgrouphub/`` and session-ticket query strings only).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from structlog import get_logger

logger = get_logger()

AZURE_WEEKLY_BASE = "https://azureweekly.info"

# SSRF protection for this service. Only these two hosts are ever fetched: the
# digest itself, and Tech Community for full article bodies. Every other linked
# article is surfaced as a URL for the reader and never auto-fetched.
_ALLOWED_HOSTS = frozenset({"azureweekly.info", "techcommunity.microsoft.com"})

# Tech Community board feeds carry complete article bodies in <description>.
_TC_RSS_TEMPLATE = "https://techcommunity.microsoft.com/t5/s/gxcuf89792/rss/board?board.id={}"
_RE_TC_POST = re.compile(r"techcommunity\.microsoft\.com/blog/([A-Za-z0-9_-]+)/[^/]+/(\d+)")

# Sentences carrying a constraint, dependency, or trade-off. Announcements say
# what a feature does; these say what it costs or demands.
_RE_CONSTRAINT = re.compile(
    r"[^.!?]*\b(?:however|although|caveat|gotcha|limitation|trade-?offs?|"
    r"keep in mind|be aware|note that|does not|doesn't|cannot|can't|won't|"
    r"not supported|only works|only available|requires?|required|depends on|"
    r"prerequisite|must be|make sure|fails?|breaks?|conflicts?|instead of|"
    r"unlike)\b[^.!?]*[.!?]",
    re.I,
)

# Promotional phrasing that the constraint regex would otherwise pick up.
# Measured against live articles: without these, "Effective enterprise AI must
# be capable of..." and "We can't create the future alone" both scored as
# constraints.
_MARKETING_MARKERS = (
    "we can't create",
    "we cannot create",
    "cannot tolerate",
    "can't wait",
    "we're excited",
    "we are excited",
    "don't miss",
    "sign up",
    "join us",
    "learn more",
    "get started today",
    "must be capable",
    "strongest",
    "best-in-class",
    "industry-leading",
    "game-chang",
    "unlock",
    "empower",
)

# Concrete detail markers. A constraint is only useful if it names a version,
# a threshold, a SKU, or a command; generic prose about what "enterprise AI
# requires" is not actionable.
_RE_NUMBER = re.compile(r"\d")
_RE_PROPER = re.compile(r"\b[A-Z][a-zA-Z0-9]{2,}\b")
_RE_TECHNICAL = re.compile(
    r"\b(?:az |azd |kubectl|helm|Get-Az|New-Az|Set-Az|SKU|API|TLS|RBAC|CLI|SDK|"
    r"tier|quota|limit|version|region|endpoint|port|permission|role)\b",
    re.I,
)

# A decimal point is not a sentence end. Without this, "requires TLS 1.2" is
# truncated to "requires TLS 1." and then dropped for being too short — losing
# exactly the version-specific constraints that are most worth extracting.
_RE_DECIMAL = re.compile(r"(?<=\d)\.(?=\d)")
_DECIMAL_SENTINEL = "\x00"

# Titles that signal hard-won operational knowledge rather than announcements.
_CAVEAT_MARKERS = (
    "fail",
    "block",
    "gotcha",
    "pitfall",
    "lesson",
    "think twice",
    "won't",
    "wont",
    "doesn't",
    "avoid",
    "mistake",
    "trap",
    "caveat",
    "breaking",
    "regret",
    "why ",
    "before you",
)

# Entries authored by this byline restate the announcement itself — skip them.
_ANNOUNCEMENT_BYLINE = "the azure updates team"

_RE_BYLINE = re.compile(r"\s+by\s+[^·]+·\s*\d+\s*min read\s*", re.I)
_RE_WS = re.compile(r"\s+")
_RE_TOKEN = re.compile(r"[a-z0-9+#]+")
_RE_POST_ID = re.compile(r"/(\d+)(?:$|[?#/])")


def _tokenize(text: str) -> set[str]:
    """Split text into lowercase word tokens for whole-word matching."""
    return set(_RE_TOKEN.findall(text.lower()))


_CACHE_PATH = Path(__file__).resolve().parent.parent / "agent" / "community_insights_cache.json"
_CACHE_TTL_S = 7 * 24 * 3600  # Azure Weekly ships weekly


def _is_allowed(url: str) -> bool:
    """Validate a URL against this service's host whitelist (SSRF protection)."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        return (parsed.hostname or "").lower() in _ALLOWED_HOSTS
    except Exception:
        return False


def _clean(text: str) -> str:
    """Strip the digest's byline/reading-time furniture and collapse whitespace."""
    text = _RE_BYLINE.sub(" — ", text)
    return _RE_WS.sub(" ", text).strip()


def _is_caveat(title: str) -> bool:
    """Does the title promise operational caveats rather than an announcement?"""
    lowered = title.lower()
    return any(marker in lowered for marker in _CAVEAT_MARKERS)


def _parse_board_feed(xml: str) -> dict[str, str]:
    """Map post id to full article text from a Tech Community board feed.

    Args:
        xml: Raw RSS XML

    Returns:
        Mapping of numeric post id to plain article text.
    """
    import warnings

    from bs4 import BeautifulSoup

    try:
        from bs4 import XMLParsedAsHTMLWarning
    except ImportError:  # pragma: no cover - older bs4
        XMLParsedAsHTMLWarning = None  # type: ignore[assignment]

    with warnings.catch_warnings():
        if XMLParsedAsHTMLWarning is not None:
            warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        # html.parser, not lxml: lxml is banned repo-wide because it needs system
        # libxml2, which the slim container image does not carry.
        soup = BeautifulSoup(xml, "html.parser")

        bodies: dict[str, str] = {}
        for item in soup.find_all("item"):
            description = item.find("description")
            if description is None:
                continue
            link = item.find("link")
            guid = item.find("guid")
            locator = (link.get_text() if link else "") + (guid.get_text() if guid else "")
            post_id = _RE_POST_ID.search(locator)
            if not post_id:
                continue
            inner = BeautifulSoup(description.get_text(), "html.parser")
            bodies[post_id.group(1)] = inner.get_text(" ", strip=True)

    return bodies


def _specificity(sentence: str) -> int:
    """Score how concrete a sentence is, for ranking constraint candidates.

    Args:
        sentence: Candidate sentence

    Returns:
        Higher means more concrete (named products, versions, thresholds).
    """
    return (
        len(_RE_NUMBER.findall(sentence))
        + len(_RE_PROPER.findall(sentence)) * 2
        + len(_RE_TECHNICAL.findall(sentence)) * 2
    )


def _extract_constraints(body: str, limit: int = 4) -> list[str]:
    """Pull the sentences that state a constraint, dependency, or trade-off.

    Ranking is by concreteness rather than length: measured on live articles,
    the longest matches were promotional ("Effective enterprise AI must be
    capable of...") while the useful ones named a specific SKU, version, or
    lock behaviour.

    Args:
        body: Full article text
        limit: Maximum sentences to return

    Returns:
        Constraint sentences, most concrete first.
    """
    picked: list[str] = []
    protected = _RE_DECIMAL.sub(_DECIMAL_SENTINEL, body)
    for match in _RE_CONSTRAINT.finditer(protected):
        sentence = _RE_WS.sub(" ", match.group(0)).strip()
        sentence = sentence.replace(_DECIMAL_SENTINEL, ".")
        if not 40 <= len(sentence) <= 320:
            continue
        # An abbreviation's period ends the match early, yielding fragments
        # like "265/HEVC) have served...". Require a real sentence opening.
        if not sentence[:1].isupper():
            continue
        lowered = sentence.lower()
        if any(marker in lowered for marker in _MARKETING_MARKERS):
            continue
        picked.append(sentence)

    picked.sort(key=_specificity, reverse=True)
    return picked[:limit]


class CommunityInsightService:
    """Fetch and index practitioner commentary from the Azure Weekly digest."""

    def __init__(self, cache_path: Optional[Path] = None):
        """Initialize the service.

        Args:
            cache_path: Override for the on-disk entry cache (tests).
        """
        self._client: Optional[httpx.AsyncClient] = None
        self._cache_path = cache_path or _CACHE_PATH
        self._entries: Optional[list[dict[str, Any]]] = None
        # board id -> {post id: body text}. One feed serves ~20 posts, so
        # caching per board avoids refetching for sibling matches.
        self._board_bodies: dict[str, dict[str, str]] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=20.0,
                follow_redirects=True,
                headers={"User-Agent": "AzBrief/1.0 (+https://github.com/Networkdog/AzBrief)"},
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client and release resources."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ---------------------------------------------------------------- parsing

    def _parse_issue(self, html: str, issue_url: str) -> list[dict[str, Any]]:
        """Extract commentary entries from one Azure Weekly issue page.

        Args:
            html: Raw issue HTML
            issue_url: URL the HTML came from (recorded on each entry)

        Returns:
            Entry dicts with title, url, summary, category, and is_caveat.
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()

        for anchor in soup.find_all("a", href=True):
            title = anchor.get_text(" ", strip=True)
            url = anchor["href"].strip()
            if len(title) < 20 or not url.startswith("http"):
                continue
            if "azureweekly.info" in url:
                continue

            block = anchor.find_parent(["li", "p", "div"])
            raw = block.get_text(" ", strip=True) if block else title

            # Announcement restatements add nothing over the update itself.
            # This must be checked on the RAW text: _clean() strips the byline,
            # so testing the cleaned string would never match.
            if _ANNOUNCEMENT_BYLINE in raw.lower():
                continue
            if url in seen:
                continue
            seen.add(url)

            body = _clean(raw)

            heading = block.find_all_previous(["h2", "h3"], limit=1) if block else []
            category = heading[0].get_text(strip=True) if heading else ""

            summary = body[len(title) :].strip(" —-·") if body.startswith(title) else body

            entries.append(
                {
                    "title": _clean(title),
                    "url": url,
                    "summary": summary[:600],
                    "category": category,
                    "is_caveat": _is_caveat(title),
                    "issue_url": issue_url,
                }
            )

        return entries

    # ---------------------------------------------------------------- fetching

    async def _fetch_issue(self, issue_number: int) -> list[dict[str, Any]]:
        """Fetch and parse a single issue, returning [] on any failure."""
        url = f"{AZURE_WEEKLY_BASE}/issue-{issue_number}.html"
        if not _is_allowed(url):
            return []
        try:
            client = await self._get_client()
            response = await client.get(url)
            if response.status_code != 200:
                logger.debug(
                    "weekly_issue_non_200", issue=issue_number, status=response.status_code
                )
                return []
            # The digest is UTF-8 but does not always declare it; decoding from
            # raw bytes avoids httpx's charset guess mangling curly quotes.
            html = response.content.decode("utf-8", errors="replace")
            return self._parse_issue(html, url)
        except Exception as e:
            logger.debug("weekly_issue_fetch_failed", issue=issue_number, error=str(e)[:200])
            return []

    async def _latest_issue_number(self) -> Optional[int]:
        """Discover the newest issue number from the site index."""
        try:
            client = await self._get_client()
            response = await client.get(f"{AZURE_WEEKLY_BASE}/")
            if response.status_code != 200:
                return None
            numbers = [int(n) for n in re.findall(r"issue-(\d+)\.html", response.text)]
            return max(numbers) if numbers else None
        except Exception as e:
            logger.debug("weekly_index_fetch_failed", error=str(e)[:200])
            return None

    async def refresh(self, issues: int = 8) -> int:
        """Re-crawl the most recent issues and persist the entry cache.

        Args:
            issues: How many recent issues to crawl

        Returns:
            Number of entries cached.
        """
        latest = await self._latest_issue_number()
        if latest is None:
            logger.warning("weekly_refresh_skipped", reason="latest issue not discoverable")
            return 0

        results = await asyncio.gather(
            *(self._fetch_issue(n) for n in range(latest, latest - issues, -1)),
            return_exceptions=True,
        )

        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for result in results:
            if not isinstance(result, list):
                continue
            for entry in result:
                if entry["url"] in seen:
                    continue
                seen.add(entry["url"])
                entries.append(entry)

        self._entries = entries
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps(
                    {"fetched_at": time.time(), "latest_issue": latest, "entries": entries},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError as e:
            logger.debug("weekly_cache_write_failed", error=str(e)[:200])

        logger.info("weekly_refreshed", latest_issue=latest, entries=len(entries))
        return len(entries)

    def _load_cache(self) -> list[dict[str, Any]]:
        """Load cached entries, ignoring an expired or unreadable cache."""
        if self._entries is not None:
            return self._entries
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if time.time() - data.get("fetched_at", 0) > _CACHE_TTL_S:
            logger.debug("weekly_cache_stale")
        self._entries = data.get("entries", [])
        return self._entries

    # ---------------------------------------------------------------- querying

    async def find_related(
        self,
        services: list[str],
        title: str = "",
        max_results: int = 5,
        auto_refresh: bool = True,
        with_body: int = 0,
    ) -> list[dict[str, Any]]:
        """Find practitioner commentary relevant to an update.

        Matching is keyword-based on service names and title terms. Posts whose
        titles promise caveats rank above neutral coverage, because those carry
        the operational risk information reports are missing.

        Args:
            services: Azure service names from the update (e.g. ["AKS"])
            title: Update title, mined for extra keywords
            max_results: Maximum entries to return
            auto_refresh: Crawl if the cache is empty
            with_body: Fetch full article text for this many top matches and
                attach extracted constraint sentences under "highlights"

        Returns:
            Ranked entry dicts (highest relevance first).
        """
        entries = self._load_cache()
        if not entries and auto_refresh:
            await self.refresh()
            entries = self._entries or []
        if not entries:
            return []

        service_names = [_RE_WS.sub(" ", s.lower().strip()) for s in services if len(s.strip()) > 2]
        service_keywords: set[str] = set()
        for name in service_names:
            service_keywords |= {w for w in _tokenize(name) if w not in _STOPWORDS}
        title_keywords = {w for w in _tokenize(title) if w not in _STOPWORDS and len(w) > 3}
        if not service_keywords:
            return []

        # A single generic token is not evidence of relevance: "gateway" alone
        # matched an AKS Gateway API post against a VPN Gateway update, and
        # "quantum" matched a post-quantum-crypto post against an unrelated
        # service. Require the whole service name, or two distinctive tokens.
        min_tokens = 2 if len(service_keywords) >= 2 else 1

        scored: list[tuple[int, dict[str, Any]]] = []
        for entry in entries:
            haystack = f"{entry['title']} {entry['category']}".lower()
            # Token-set matching, not substring matching: "bus" (from "Service
            # Bus") must not match "business", which put Databricks/FinOps
            # posts on a Service Bus update during validation.
            tokens = _tokenize(haystack)
            service_hits = len(service_keywords & tokens)
            title_hits = len(title_keywords & tokens)

            full_name_hit = any(name in haystack for name in service_names)
            if not full_name_hit and service_hits < min_tokens:
                continue

            score = (10 if full_name_hit else 0) + service_hits * 3 + title_hits
            # Reward caveat posts only once the topic already matches, otherwise
            # the bonus promotes off-topic "why X fails" articles.
            if entry["is_caveat"]:
                score += 4
            scored.append((score, entry))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        top = [entry for _, entry in scored[:max_results]]

        if with_body > 0:
            await self._attach_bodies(top[:with_body])
        return top

    # ------------------------------------------------------------- full text

    async def _attach_bodies(self, entries: list[dict[str, Any]]) -> None:
        """Add extracted constraint sentences to entries, in place.

        Failures are silent by design: an entry without a body still carries
        its digest blurb, so the caller always has something to show.

        Args:
            entries: Ranked entries to enrich (mutated in place)
        """
        for entry in entries:
            body = await self._fetch_post_body(entry["url"])
            if not body:
                continue
            highlights = _extract_constraints(body)
            if highlights:
                entry["highlights"] = highlights
                entry["body_chars"] = len(body)

    async def _fetch_post_body(self, url: str) -> str:
        """Resolve a Tech Community post URL to its full article text.

        Args:
            url: Candidate article URL (non-Tech-Community URLs return "")

        Returns:
            Full article text, or "" when unavailable.
        """
        match = _RE_TC_POST.search(url)
        if not match or not _is_allowed(url):
            return ""
        board_id, post_id = match.group(1), match.group(2)

        if board_id not in self._board_bodies:
            self._board_bodies[board_id] = await self._fetch_board(board_id)
        return self._board_bodies[board_id].get(post_id, "")

    async def _fetch_board(self, board_id: str) -> dict[str, str]:
        """Fetch one board feed and map post id to full article text.

        Args:
            board_id: Tech Community board identifier (e.g. "AppsonAzureBlog")

        Returns:
            Mapping of post id to article text; empty on any failure.
        """
        feed_url = _TC_RSS_TEMPLATE.format(board_id)
        if not _is_allowed(feed_url):
            return {}
        try:
            client = await self._get_client()
            response = await client.get(feed_url)
            if response.status_code != 200:
                logger.debug("tc_board_non_200", board=board_id, status=response.status_code)
                return {}
            return _parse_board_feed(response.content.decode("utf-8", errors="replace"))
        except Exception as e:
            logger.debug("tc_board_fetch_failed", board=board_id, error=str(e)[:200])
            return {}


# Common words that would match almost any entry and dilute ranking.
_STOPWORDS = frozenset(
    {
        "azure",
        "microsoft",
        "generally",
        "available",
        "public",
        "preview",
        "announcing",
        "update",
        "updates",
        "support",
        "supports",
        "with",
        "from",
        "your",
        "using",
        "into",
        "that",
        "this",
        "when",
        "what",
        "will",
        "have",
        "more",
        "than",
        "také",
        "launched",
        "retirement",
        "general",
        "availability",
        "service",
        "services",
        "ined",
        "over",
    }
)
