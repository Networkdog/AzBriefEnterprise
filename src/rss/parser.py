"""Azure Update RSS Feed Parser."""

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup
from structlog import get_logger

logger = get_logger()

AZURE_UPDATE_RSS_URL = "https://www.microsoft.com/releasecommunications/api/v2/azure/rss"
AZURE_UPDATE_API_BASE = "https://www.microsoft.com/releasecommunications/api/v2/azure"

# Query parameters that are pure tracking noise — stripped from surfaced URLs so
# reference docs read as clean, professional links rather than telemetry blobs.
_TRACKING_PARAMS = frozenset(
    {
        "ocid",
        "ef_id",
        "msclkid",
        "wt.mc_id",
        "cid",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        # SafeLinks bookkeeping params (present if a wrapper is only partially decoded)
        "data",
        "sdata",
        "reserved",
    }
)


def clean_url(url: str) -> str:
    """Normalize a URL for professional display in reports.

    Two transformations, applied conservatively so functional links never break:

    1. **Unwrap Microsoft SafeLinks** — ``*.safelinks.protection.outlook.com/?url=<encoded>``
       redirect wrappers are decoded to their underlying target (recursively).
    2. **Strip tracking query params** (utm_*, ocid, msclkid, SafeLinks bookkeeping)
       while preserving functional ones such as ``?view=``, ``?tabs=``, and fragments.

    Args:
        url: The raw URL (possibly a SafeLinks wrapper or tracking-laden link).

    Returns:
        A cleaned URL. On any parsing error the original string is returned unchanged.
    """
    if not url or not isinstance(url, str):
        return url
    url = url.strip()
    try:
        parts = urlsplit(url)
        # 1. Unwrap SafeLinks wrappers to the real destination.
        if "safelinks.protection.outlook.com" in parts.netloc.lower():
            target = parse_qs(parts.query).get("url", [None])[0]
            if target:
                return clean_url(unquote(target))  # recurse — target may itself be tracked
        # 2. Drop tracking params, keep functional query components in order.
        if parts.query:
            kept = [
                (k, v)
                for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if k.lower() not in _TRACKING_PARAMS
            ]
            parts = parts._replace(query=urlencode(kept))
        return urlunsplit(parts)
    except Exception:
        return url


# Local archive of the full Azure Update history, produced by
# ``scripts/crawl_azure_updates.py``. The live RSS feed only returns a rolling
# window of the most recent ~200 items, so historical months age out of it.
# Merging this archive lets date-range queries reach back beyond that window.
HISTORY_ARCHIVE_PATH = Path(__file__).resolve().parents[2] / "data" / "azure_updates_history.jsonl"


@dataclass
class AzureUpdate:
    """Represents an Azure Update item from RSS feed."""

    id: str
    title: str
    description: str
    link: str
    published_date: Optional[datetime]
    categories: list[str]
    azure_services: list[str]
    update_type: Optional[str]
    status: Optional[str]
    learn_more_links: list[dict] = None  # [{"text": "...", "url": "..."}]
    detail_description: Optional[str] = None  # Full description from API

    def __post_init__(self):
        if self.learn_more_links is None:
            self.learn_more_links = []

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.detail_description or self.description,
            "link": self.link,
            "published_date": self.published_date.isoformat() if self.published_date else None,
            "categories": self.categories,
            "azure_services": self.azure_services,
            "update_type": self.update_type,
            "status": self.status,
            "learn_more_links": self.learn_more_links,
        }


class AzureUpdateParser:
    """Parser for Azure Update RSS feed."""

    def __init__(self, rss_url: str = AZURE_UPDATE_RSS_URL):
        """Initialize parser with RSS URL."""
        self.rss_url = rss_url

    async def fetch_feed(self) -> str:
        """Fetch RSS feed content."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(self.rss_url)
            response.raise_for_status()
            return response.text

    async def fetch_update_detail(self, update: AzureUpdate) -> None:
        """Fetch full update detail from API and enrich with Learn More links.

        Updates the AzureUpdate object in-place with:
        - learn_more_links: List of {text, url} dicts
        - detail_description: Full plain-text description from API

        Args:
            update: AzureUpdate to enrich
        """
        # Extract numeric ID from update.id (e.g., "543279" from guid/link)
        update_id = re.search(r"(\d+)", update.id)
        if not update_id:
            return

        api_url = f"{AZURE_UPDATE_API_BASE}/{update_id.group(1)}"
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(api_url)
                if response.status_code != 200:
                    logger.debug(
                        "update_detail_api_non_200",
                        update_id=update.id,
                        status=response.status_code,
                    )
                    return

                data = response.json()
                desc_html = data.get("description", "")
                if not desc_html:
                    return

                soup = BeautifulSoup(desc_html, "html.parser")

                # Extract all links from the full description
                links = []
                for a in soup.find_all("a", href=True):
                    href = clean_url(a.get("href", "").strip())
                    text = a.get_text(strip=True)
                    if href and text:
                        links.append({"text": text, "url": href})

                update.learn_more_links = links
                update.detail_description = soup.get_text(separator=" ", strip=True)

                logger.info(
                    "update_detail_fetched",
                    update_id=update.id,
                    links_count=len(links),
                )
        except Exception as e:
            logger.debug(
                "update_detail_fetch_error",
                update_id=update.id,
                error=str(e),
            )

    def parse_feed(self, feed_content: str) -> list[AzureUpdate]:
        """Parse RSS feed content into AzureUpdate objects."""
        updates = []

        try:
            root = ET.fromstring(feed_content)
        except ET.ParseError as e:
            logger.error("Failed to parse RSS XML", error=str(e))
            return updates

        seen_ids: set[str] = set()
        for item in root.findall(".//item"):
            try:
                title = self._get_xml_text(item, "title")
                link = self._get_xml_text(item, "link")
                guid = self._get_xml_text(item, "guid")
                update_id = guid or link

                # ID 기반 중복 제거
                if update_id in seen_ids:
                    continue
                seen_ids.add(update_id)

                raw_description = self._get_xml_text(item, "description")
                pub_date_raw = self._get_xml_text(item, "pubDate")
                categories = [
                    (category.text or "").strip()
                    for category in item.findall("category")
                    if (category.text or "").strip()
                ]

                update = AzureUpdate(
                    id=update_id,
                    title=title,
                    description=self._clean_html(raw_description),
                    link=link,
                    published_date=self._parse_published_date(pub_date_raw),
                    categories=categories,
                    azure_services=self._extract_azure_services(title, categories),
                    update_type=self._extract_update_type(categories, title),
                    status=self._extract_status(categories, title),
                )
                updates.append(update)
            except Exception as e:
                logger.warning("Failed to parse RSS entry", error=str(e))

        return updates

    def _get_xml_text(self, parent: ET.Element, tag: str) -> str:
        """Get normalized text from XML child tag."""
        element = parent.find(tag)
        if element is None or element.text is None:
            return ""
        return element.text.strip()

    def _parse_published_date(self, value: str) -> Optional[datetime]:
        """Parse RSS pubDate to timezone-aware UTC datetime."""
        if not value:
            return None

        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None

    def _clean_html(self, html_content: str) -> str:
        """Remove HTML tags and clean up text."""
        if not html_content:
            return ""
        soup = BeautifulSoup(html_content, "html.parser")
        text = soup.get_text(separator=" ", strip=True)
        # Normalize whitespace
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _extract_azure_services(self, title: str, categories: list[str]) -> list[str]:
        """Extract Azure service names from title and categories.

        Filters out generic category labels (e.g., 'Launched', 'Compute')
        that are not actual Azure service names.
        """
        # Non-service category labels to filter out
        non_service_labels = {
            # Status / lifecycle labels
            "launched",
            "in preview",
            "in development",
            "retired",
            "retiring",
            "generally available",
            "public preview",
            "private preview",
            # Generic Azure portal categories
            "compute",
            "containers",
            "internet of things",
            "networking",
            "storage",
            "databases",
            "analytics",
            "ai + machine learning",
            "developer tools",
            "management",
            "security",
            "identity",
            "integration",
            "media",
            "migration",
            "mixed reality",
            "mobile",
            "web",
            "hybrid",
            "devops",
            "blockchain",
            "iot",
            "monitor",
            "management and governance",
            "general",
            # Meta labels that are not service names
            "features",
            "regions & compliance",
            "compliance",
            "pricing & offerings",
            "sdk and tools",
            "services",
            "preview",
            "ga",
            "updates",
            "microsoft ignite",
            "microsoft build",
        }

        # Common Azure service patterns
        service_patterns = [
            r"App\s+Service",
            r"Virtual\s+Machines?",
            r"Storage\s+Accounts?",
            r"Blob\s+Storage",
            r"Container\s+Apps?",
            r"Container\s+Instances?",
            r"Container\s+Registry",
            r"Azure\s+Kubernetes\s+Service",
            r"Kubernetes\s+Service",
            r"AKS",
            r"Azure\s+SQL",
            r"Cosmos\s+DB",
            r"Azure\s+Functions?",
            r"Logic\s+Apps?",
            r"Event\s+Grid",
            r"Event\s+Hubs?",
            r"Service\s+Bus",
            r"API\s+Management",
            r"Application\s+Gateway",
            r"Load\s+Balancer",
            r"Front\s+Door",
            r"Azure\s+CDN",
            r"Key\s+Vault",
            r"Azure\s+AD|Entra\s+ID",
            r"Azure\s+Monitor",
            r"Log\s+Analytics",
            r"Application\s+Insights",
            r"Azure\s+Sentinel|Microsoft\s+Sentinel",
            r"Azure\s+Defender|Microsoft\s+Defender",
            r"Azure\s+Firewall",
            r"Azure\s+Bastion",
            r"Azure\s+VPN",
            r"ExpressRoute",
            r"Virtual\s+Network|VNet",
            r"Azure\s+Policy",
            r"Azure\s+Blueprints?",
            r"Resource\s+Graph",
            r"Azure\s+Automation",
            r"Azure\s+DevOps",
            r"Azure\s+Repos",
            r"Azure\s+Pipelines",
            r"Azure\s+OpenAI",
            r"Cognitive\s+Services",
            r"Azure\s+AI",
            r"Machine\s+Learning",
            r"Azure\s+ML",
            r"Databricks",
            r"Data\s+Factory",
            r"Synapse",
            r"Power\s+BI",
            r"Azure\s+Purview|Microsoft\s+Purview",
            r"Static\s+Web\s+Apps?",
            r"Azure\s+Communication\s+Services?",
            r"Azure\s+Spring\s+Apps?",
            r"Azure\s+Cache\s+for\s+Redis",
            r"Azure\s+Database\s+for\s+(?:PostgreSQL|MySQL|MariaDB)",
            r"Azure\s+Container\s+Storage",
            r"Azure\s+Managed\s+Lustre",
            r"Azure\s+NetApp\s+Files",
        ]

        services = set()

        # First, check categories that are actual service names (not generic labels)
        for cat in categories:
            cat_lower = cat.strip().lower()
            if cat_lower not in non_service_labels and len(cat) > 2:
                # Check if category looks like a service name (contains proper nouns)
                if re.match(r"^[A-Z]", cat.strip()) or "azure" in cat_lower:
                    services.add(cat.strip())

        # Then, extract from title using specific patterns
        for pattern in service_patterns:
            matches = re.findall(pattern, title, re.IGNORECASE)
            services.update(matches)

        # Filter out any remaining non-service labels
        filtered = {
            s
            for s in services
            if s.strip().lower() not in non_service_labels and len(s.strip()) > 2
        }

        # Deduplicate: if a service's full name contains another service as abbreviation
        # e.g. "Azure Kubernetes Service (AKS)" contains "AKS", keep only the full name
        result = list(filtered) if filtered else list(services)
        if len(result) > 1:
            deduped = []
            for s in sorted(result, key=len, reverse=True):  # longest first
                # Check if this service is already represented by a longer name
                is_substring = False
                for existing in deduped:
                    if s.lower() in existing.lower():
                        is_substring = True
                        break
                if not is_substring:
                    deduped.append(s)
            result = deduped

        return result

    _TITLE_TYPE_MAP: list[tuple[str, str]] = [
        ("[launched] generally available", "General Availability"),
        ("[in preview] public preview", "Public Preview"),
        ("[in preview] private preview", "Private Preview"),
        ("[retired]", "Retirement"),
        ("[launched]", "General Availability"),
        ("[in preview]", "Public Preview"),
        ("[in development]", "In Development"),
    ]

    _TITLE_STATUS_MAP: list[tuple[str, str]] = [
        ("[launched]", "Now Available"),
        ("[in preview]", "Preview"),
        ("[in development]", "Coming Soon"),
        ("[retired]", "Retiring"),
    ]

    def _extract_update_type(self, categories: list[str], title: str = "") -> Optional[str]:
        """Extract update type from title prefix first, then categories.

        Title prefixes like '[Launched] Generally Available:' are more reliable
        than generic category labels ('Features').
        """
        # Priority 1: title prefix (most reliable)
        title_lower = title.lower()
        for prefix, utype in self._TITLE_TYPE_MAP:
            if title_lower.startswith(prefix):
                return utype

        # Priority 2: categories
        update_types = [
            "General Availability",
            "Public Preview",
            "Private Preview",
            "Retirement",
            "Breaking Change",
            "Security Update",
            "Feature",
            "Regions",
            "Pricing",
            "SDK",
            "CLI",
            "API",
        ]

        for category in categories:
            for update_type in update_types:
                if update_type.lower() in category.lower():
                    return update_type

        return None

    def _extract_status(self, categories: list[str], title: str = "") -> Optional[str]:
        """Extract status from title prefix first, then categories."""
        # Priority 1: title prefix
        title_lower = title.lower()
        for prefix, st in self._TITLE_STATUS_MAP:
            if title_lower.startswith(prefix):
                return st

        # Priority 2: categories
        status_keywords = ["Now Available", "Coming Soon", "Retiring", "Deprecated", "Preview"]

        for category in categories:
            for status in status_keywords:
                if status.lower() in category.lower():
                    return status

        return None

    async def get_updates(self) -> list[AzureUpdate]:
        """Fetch and parse Azure updates."""
        logger.info("Fetching Azure Update RSS feed", url=self.rss_url)
        feed_content = await self.fetch_feed()
        updates = self.parse_feed(feed_content)
        logger.info("Parsed Azure updates", count=len(updates))
        return updates

    async def get_updates_by_date_range(
        self,
        start_date: datetime,
        end_date: Optional[datetime] = None,
        *,
        include_history: bool = True,
        history_path: Optional[Path] = None,
    ) -> list[AzureUpdate]:
        """Fetch updates and filter by date range.

        The live RSS feed only exposes a rolling window of the most recent
        ~200 items, so months that have aged out of that window return nothing
        when queried directly. To cover historical ranges, this method merges
        the live feed with the locally crawled history archive
        (``data/azure_updates_history.jsonl``), de-duplicated by canonical id.

        Args:
            start_date: Start date (inclusive). Only date part is used.
            end_date: End date (inclusive). If None, includes all updates from start_date onward.
            include_history: When True (default), merge the local history archive
                so historical months beyond the RSS window are covered.
            history_path: Override path to the history archive JSONL. Defaults to
                ``data/azure_updates_history.jsonl`` at the project root.

        Returns:
            List of AzureUpdate objects within the date range, sorted by date descending.
        """
        updates = await self.get_updates()
        live_count = len(updates)

        # Merge the local history archive to cover months that have aged out of
        # the live RSS window. De-duplicate by canonical id so overlapping
        # (recent) updates are not counted twice.
        history_added = 0
        if include_history:
            history_updates = self.load_history_updates(history_path)
            if history_updates:
                seen = {self._canonical_id(u.id) for u in updates}
                for hu in history_updates:
                    key = self._canonical_id(hu.id)
                    if key and key not in seen:
                        seen.add(key)
                        updates.append(hu)
                        history_added += 1

        # Normalize to date-only comparison (timezone-aware UTC)
        start = start_date.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
            tzinfo=timezone.utc,
        )
        end = (
            end_date.replace(
                hour=23,
                minute=59,
                second=59,
                microsecond=999999,
                tzinfo=timezone.utc,
            )
            if end_date
            else None
        )

        filtered = []
        for update in updates:
            if not update.published_date:
                continue
            if update.published_date < start:
                continue
            if end and update.published_date > end:
                continue
            filtered.append(update)

        # Sort by published_date descending (newest first)
        filtered.sort(key=lambda u: u.published_date, reverse=True)

        logger.info(
            "Filtered updates by date range",
            start=start.isoformat(),
            end=end.isoformat() if end else "now",
            live=live_count,
            history_added=history_added,
            total=len(updates),
            filtered=len(filtered),
        )
        return filtered

    def load_history_updates(self, history_path: Optional[Path] = None) -> list[AzureUpdate]:
        """Load AzureUpdate objects from the local history archive JSONL.

        The archive is produced by ``scripts/crawl_azure_updates.py`` and holds
        the full Azure Update history (thousands of records), unlike the live
        RSS feed which only exposes a rolling window of recent items.

        Args:
            history_path: Path to the JSONL archive. Defaults to
                ``data/azure_updates_history.jsonl`` at the project root.

        Returns:
            List of AzureUpdate objects (empty if the archive is missing).
        """
        path = Path(history_path) if history_path else HISTORY_ARCHIVE_PATH
        if not path.exists():
            logger.info("History archive not found", path=str(path))
            return []

        updates: list[AzureUpdate] = []
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    update = self._history_record_to_update(record)
                    if update:
                        updates.append(update)
        except OSError as e:
            logger.warning("Failed to read history archive", path=str(path), error=str(e))
            return []

        logger.info("Loaded history archive", path=str(path), count=len(updates))
        return updates

    _STATUS_TYPE_MAP: dict[str, str] = {
        "launched": "General Availability",
        "in preview": "Public Preview",
        "in development": "In Development",
        "retired": "Retirement",
    }

    def _history_record_to_update(self, record: dict) -> Optional[AzureUpdate]:
        """Convert a crawled history record into an AzureUpdate.

        Args:
            record: One JSON object from the history archive JSONL.

        Returns:
            An AzureUpdate, or None if the record has no usable id.
        """
        update_id = str(record.get("id") or "").strip()
        if not update_id:
            return None

        title = record.get("title", "") or ""
        categories = list(record.get("productCategories") or []) + list(record.get("tags") or [])
        products = [p for p in (record.get("products") or []) if p]
        status = record.get("status") or None

        update_type = self._extract_update_type(categories, title)
        if not update_type and status:
            update_type = self._STATUS_TYPE_MAP.get(status.strip().lower())

        return AzureUpdate(
            id=update_id,
            title=title,
            description=record.get("description", "") or "",
            link=self._build_update_link(update_id),
            published_date=self._parse_iso_date(record.get("created", "")),
            categories=categories,
            azure_services=products or self._extract_azure_services(title, categories),
            update_type=update_type,
            status=self._extract_status(categories, title) or status,
        )

    def _build_update_link(self, update_id: str) -> str:
        """Build the canonical Azure Updates URL for an update id."""
        uid = str(update_id).strip()
        if uid.isdigit():
            return f"https://azure.microsoft.com/en-us/updates?id={uid}"
        return f"https://azure.microsoft.com/en-us/updates/{uid}/"

    def _canonical_id(self, id_or_link: str) -> str:
        """Normalize an id or URL to a canonical id for de-duplication.

        Live RSS guids and history ids may express the same update as a bare
        numeric id, a slug, or a full URL. Extracting the numeric/slug id makes
        the two sources comparable.
        """
        if not id_or_link:
            return ""
        extracted = self._extract_update_id_from_url(id_or_link)
        if extracted:
            return extracted.strip().lower()
        return id_or_link.strip().lower()

    def _parse_iso_date(self, value: str) -> Optional[datetime]:
        """Parse an ISO 8601 timestamp (as used by the history API) to UTC.

        Handles a trailing ``Z`` and 7-digit fractional seconds, neither of
        which ``datetime.fromisoformat`` accepts on Python 3.10.
        """
        if not value:
            return None

        text = value.strip().replace("Z", "+00:00")
        # Trim fractional seconds to at most 6 digits (microsecond precision).
        text = re.sub(r"(\.\d{6})\d+", r"\1", text)
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            # Retry without any fractional-second component.
            try:
                parsed = datetime.fromisoformat(re.sub(r"\.\d+", "", text))
            except ValueError:
                return None

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    async def get_update_by_url(self, url: str) -> Optional[AzureUpdate]:
        """Fetch all updates and find the one matching the URL."""
        updates = await self.get_updates()
        for update in updates:
            if update.link == url or update.id == url:
                return update
        return None

    def _extract_update_id_from_url(self, url: str) -> Optional[str]:
        """Extract update ID from Azure Updates URL."""
        # Pattern: https://azure.microsoft.com/en-us/updates?id=555870
        match = re.search(r"[?&]id=(\d+)", url)
        if match:
            return match.group(1)

        # Pattern: https://azure.microsoft.com/en-us/updates/update-name/
        match = re.search(r"/updates/([^/?]+)/?", url)
        if match:
            return match.group(1)

        return None

    async def fetch_update_by_id(self, update_id: str) -> Optional[AzureUpdate]:
        """Fetch update details using the individual RSS API endpoint."""
        rss_url = f"https://www.microsoft.com/releasecommunications/api/v2/azure/rss/{update_id}"
        logger.info("Fetching individual update RSS", url=rss_url, update_id=update_id)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(rss_url)
                response.raise_for_status()

                updates = self.parse_feed(response.text)
                if updates:
                    logger.info(
                        "Successfully fetched update from individual RSS", update_id=update_id
                    )
                    return updates[0]

                logger.warning("No updates found in individual RSS", update_id=update_id)
                return None
        except Exception as e:
            logger.warning(
                "Failed to fetch individual update RSS", update_id=update_id, error=str(e)
            )
            return None

    async def fetch_update_details(self, url: str) -> dict:
        """Fetch detailed content from the update URL."""
        # First, try to extract update ID and use individual RSS API
        update_id = self._extract_update_id_from_url(url)
        if update_id:
            update = await self.fetch_update_by_id(update_id)
            if update:
                return {
                    "url": url,
                    "content": update.description,
                    "title": update.title,
                    "update": update,  # Include full update object
                }

        # Fallback: try to fetch from HTML (may not work for SPA pages)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # Try to extract main content
            content = ""
            main_content = (
                soup.find("main") or soup.find("article") or soup.find("div", class_="content")
            )
            if main_content:
                content = main_content.get_text(separator="\n", strip=True)

            return {
                "url": url,
                "content": content[:5000],  # Limit content length
                "title": soup.title.string if soup.title else "",
            }


# Legacy alias, still part of the package's public surface.
RSSParser = AzureUpdateParser
