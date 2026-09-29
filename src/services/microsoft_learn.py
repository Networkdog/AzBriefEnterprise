"""Microsoft Learn documentation search and bounded article extraction."""

import asyncio
import ipaddress
import re
import time
from typing import Any, Optional, TypedDict
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlsplit, urlunsplit

import httpx
from structlog import get_logger

logger = get_logger()

# SSRF protection: only fetch content from these trusted domains
ALLOWED_FETCH_DOMAINS = frozenset(
    {
        "learn.microsoft.com",
        "azure.microsoft.com",
        "www.microsoft.com",
        "techcommunity.microsoft.com",
        "devblogs.microsoft.com",
        "github.com",
    }
)

DOCUMENTATION_REDIRECT_DOMAINS = frozenset({"aka.ms", "go.microsoft.com"})
MAX_DOCUMENTATION_REDIRECTS = 5
MAX_DOCUMENTATION_BYTES = 2 * 1024 * 1024
MAX_DOCUMENTATION_LINKS = 200

_DOCUMENTATION_TRACKING_PARAMETERS = frozenset({"ocid", "ef_id", "msclkid", "wt.mc_id"})
_DOCUMENTATION_UI_TOKENS = frozenset(
    {
        "toc",
        "table-of-contents",
        "in-this-article",
        "breadcrumb",
        "breadcrumbs",
        "share",
        "sharing",
        "social-share",
        "feedback",
        "article-feedback",
        "action-bar",
        "page-actions",
        "metadata",
        "article-metadata",
        "consent",
        "cookie",
        "cookie-banner",
        "sign-in",
        "language-selector",
        "language-switcher",
        "language-toggle",
        "translation",
        "translation-selector",
    }
)
_DOCUMENTATION_UI_LINK_TEXT = frozenset(
    {
        "read in english",
        "edit",
        "share",
        "share via",
        "sign in",
        "change language",
        "select language",
        "translate",
        "print",
        "download pdf",
        "table of contents",
        "in this article",
    }
)


class DocumentationLink(TypedDict):
    """A canonical article link and its nearest preceding section heading."""

    text: str
    url: str
    section: str


class DocumentationPage(TypedDict):
    """Complete extracted article data within the declared download limit."""

    title: str
    url: str
    requested_url: str
    content: str
    sections: list[str]
    links: list[DocumentationLink]
    code_blocks: list[str]
    visuals: list[dict[str, str]]
    links_truncated: bool


class DocumentationFetchResult(TypedDict):
    """Explicit documentation success or failure without partial article data."""

    success: bool
    data: DocumentationPage | None
    error: str


def normalize_documentation_url(url: str, base_url: str = "") -> str:
    """Canonicalize a public HTTP(S) link without authorizing a network request.

    Args:
        url: Absolute URL or a relative article link.
        base_url: Final article URL used to resolve relative links.

    Returns:
        URL without fragments or known tracking parameters, retaining functional queries.

    Raises:
        ValueError: The URL is malformed, credentialed, or uses an unsupported scheme or port.
    """
    return _normalize_documentation_url(url, base_url)


def _normalize_documentation_url(
    url: str, base_url: str = "", *, require_https: bool = False
) -> str:
    """Normalize links while validating every locally unwrapped SafeLinks target."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Documentation URL must be a non-empty string")
    if any(ord(char) < 32 or ord(char) == 127 for char in url) or "\\" in url:
        raise ValueError("Documentation URL contains forbidden characters")
    url = url.strip()
    if any(char.isspace() for char in url) or re.search(r"%(?![0-9a-fA-F]{2})", url):
        raise ValueError("Documentation URL contains invalid whitespace or percent encoding")
    initial = urlsplit(url)
    if initial.scheme and (initial.scheme not in {"http", "https"} or not initial.netloc):
        raise ValueError("Documentation URL must be an absolute HTTP(S) URL")
    if base_url:
        base_url = _normalize_documentation_url(base_url, require_https=require_https)
        url = urljoin(base_url, url)

    for _ in range(MAX_DOCUMENTATION_REDIRECTS + 1):
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("Documentation URL must be an absolute HTTP(S) URL")
        if require_https and parts.scheme != "https":
            raise ValueError("Documentation fetch requires HTTPS")
        if parts.username is not None or parts.password is not None:
            raise ValueError("Documentation URL credentials are not allowed")
        hostname = parts.hostname or ""
        if "%" in hostname or not hostname:
            raise ValueError("Documentation URL has an invalid hostname")
        hostname = hostname.encode("idna").decode("ascii").lower()
        if ":" in hostname:
            ipaddress.IPv6Address(hostname)
            authority = f"[{hostname}]"
        else:
            if len(hostname) > 253 or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in hostname.split(".")
            ):
                raise ValueError("Documentation URL has an invalid hostname")
            authority = hostname
        if parts.netloc.endswith(":") or parts.port not in (
            None,
            443 if parts.scheme == "https" else 80,
        ):
            raise ValueError("Documentation URL uses a nonstandard port")

        # 기존 clean_url의 부분 호스트 매칭·이중 디코딩은 문서 접근 검증에 사용하지 않는다.
        if hostname == "safelinks.protection.outlook.com" or hostname.endswith(
            ".safelinks.protection.outlook.com"
        ):
            targets = [value for key, value in parse_qsl(parts.query) if key.lower() == "url"]
            if len(targets) != 1:
                raise ValueError("SafeLinks URL must contain exactly one destination")
            url = targets[0]
            if (
                not url
                or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url)
                or "\\" in url
                or re.search(r"%(?![0-9a-fA-F]{2})", url)
            ):
                raise ValueError("SafeLinks destination contains invalid characters")
            continue

        query = urlencode(
            [
                (key, value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if not key.lower().startswith("utm_")
                and key.lower() not in _DOCUMENTATION_TRACKING_PARAMETERS
            ]
        )
        path = quote(parts.path or "/", safe="/:@!$&'()*+,;=-._~%")
        return urlunsplit((parts.scheme, authority, path, query, ""))
    raise ValueError("SafeLinks nesting exceeds the documentation redirect limit")


def _validated_documentation_fetch_url(url: str, base_url: str = "") -> str:
    """Authorize a canonical HTTPS URL before sending any request."""
    normalized = _normalize_documentation_url(url, base_url, require_https=True)
    hostname = urlsplit(normalized).hostname
    if hostname not in ALLOWED_FETCH_DOMAINS | DOCUMENTATION_REDIRECT_DOMAINS:
        raise ValueError("Documentation URL host is not in the allowed list")
    return normalized


_EMAIL_VISUAL_EXTENSIONS = frozenset({".gif", ".jpeg", ".jpg", ".png"})
_DECORATIVE_IMAGE_LABELS = frozenset(
    {
        "icon",
        "logo",
        "microsoft logo",
        "note",
        "tip",
        "warning",
    }
)


def _is_allowed_url(url: str) -> bool:
    """Validate URL against allowed domains whitelist (SSRF protection).

    Args:
        url: URL to validate

    Returns:
        True if the URL's domain is in the allowed list
    """
    try:
        if (urlparse(url).hostname or "").lower() not in ALLOWED_FETCH_DOMAINS:
            return False
        parsed = urlparse(normalize_documentation_url(url))
        hostname = (parsed.hostname or "").lower()
        return hostname in ALLOWED_FETCH_DOMAINS
    except ValueError:
        return False


def _extract_email_visuals(main: Any, page_url: str, page_title: str) -> list[dict[str, str]]:
    """Extract bounded, descriptive image candidates from trusted documentation."""
    visuals: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for image in main.find_all("img"):
        raw_url = str(image.get("src") or image.get("data-src") or "").strip()
        image_url = urljoin(page_url, raw_url)
        parsed = urlparse(image_url)
        extension = parsed.path.lower().rsplit(".", 1)
        suffix = f".{extension[-1]}" if len(extension) == 2 else ""
        if (
            not raw_url
            or parsed.scheme != "https"
            or not _is_allowed_url(image_url)
            or suffix not in _EMAIL_VISUAL_EXTENSIONS
            or image_url in seen_urls
        ):
            continue

        alt = " ".join(str(image.get("alt") or "").split())
        figure = image.find_parent("figure")
        caption_element = figure.find("figcaption") if figure else None
        caption = (
            " ".join(caption_element.get_text(" ", strip=True).split()) if caption_element else ""
        )
        label = (caption or alt).strip()
        if not label or label.casefold() in _DECORATIVE_IMAGE_LABELS:
            continue

        visuals.append(
            {
                "url": image_url,
                "alt": alt or label,
                "caption": caption,
                "source_url": page_url,
                "source_title": page_title,
            }
        )
        seen_urls.add(image_url)
        if len(visuals) == 3:
            break

    return visuals


def _remove_documentation_noise(main: Any) -> None:
    """Remove page chrome, but preserve article admonitions and technical warnings."""
    for tag in main.find_all(True):
        if tag.decomposed or tag.attrs is None:
            continue
        tokens = {
            str(value).casefold()
            for value in [
                *(tag.get("class") or []),
                tag.get("id", ""),
                tag.get("data-bi-name", ""),
            ]
        }
        technical_aside = tag.name == "aside" and bool(
            tokens & {"alert", "admonition", "note", "warning", "important", "caution", "tip"}
        )
        if (
            tag.name
            in {
                "nav",
                "header",
                "footer",
                "script",
                "style",
                "button",
                "form",
                "svg",
                "template",
                "noscript",
                "iframe",
                "input",
                "select",
                "textarea",
            }
            or (tag.name == "aside" and not technical_aside)
            or tag.get("role") in {"navigation", "contentinfo"}
            or (tag.get("role") == "complementary" and not technical_aside)
            or tokens & _DOCUMENTATION_UI_TOKENS
        ):
            tag.decompose()
        elif tag.name == "a":
            text = " ".join(tag.get_text(" ", strip=True).split()).casefold()
            if (
                tag.has_attr("hreflang")
                or text in _DOCUMENTATION_UI_LINK_TEXT
                or text.startswith(("share on ", "share via "))
            ):
                tag.decompose()


def _documentation_code_block(tag: Any) -> str:
    """Keep code whitespace and choose a fence that cannot collide with its contents."""
    code = tag.get_text().strip("\n")
    if not code.strip():
        return ""
    code_tag = tag.find("code") if tag.name == "pre" else tag
    language = ""
    classes = [*((code_tag or tag).get("class") or []), *(tag.get("class") or [])]
    for token in classes:
        match = re.fullmatch(r"(?:language|lang)-([A-Za-z0-9_+.-]+)", str(token))
        if match:
            language = match.group(1)
            break
    fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", code)), default=0))
    return f"{fence}{language}\n{code}\n{fence}"


def _documentation_content(main: Any) -> tuple[str, list[str]]:
    """Render article structure without shortening text, tables, or executable examples."""
    from bs4 import Comment, NavigableString

    code_blocks: list[str] = []

    def render(node: Any) -> str:
        if isinstance(node, Comment):
            return ""
        if isinstance(node, NavigableString):
            return re.sub(r"\s+", " ", str(node))
        if node.name == "pre" or (node.name == "code" and "\n" in node.get_text()):
            block = _documentation_code_block(node)
            if block:
                code_blocks.append(block)
                return f"\n\n{block}\n\n"
            return ""
        if node.name == "br":
            return "\n"
        if node.name == "img":
            alt = " ".join(str(node.get("alt") or "").split())
            return f" [{alt}] " if alt else ""
        if node.name == "table":
            rows = []
            caption = node.find("caption")
            if caption:
                rows.append(caption.get_text(" ", strip=True))
            for row in node.find_all("tr"):
                cells = row.find_all(["th", "td"], recursive=False)
                if not cells:
                    continue
                values = [
                    "".join(render(child) for child in cell.children)
                    .strip()
                    .replace("|", "\\|")
                    .replace("\n", "<br>")
                    for cell in cells
                ]
                rows.append("| " + " | ".join(values) + " |")
                if any(cell.name == "th" for cell in cells) and len(rows) == (2 if caption else 1):
                    rows.append("| " + " | ".join("---" for _ in cells) + " |")
            return "\n\n" + "\n".join(rows) + "\n\n"

        text = "".join(render(child) for child in node.children)
        if node.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            return f"\n\n{'#' * int(node.name[1])} {text.strip()}\n\n"
        if node.name == "code":
            fence = "`" * max(1, 1 + max((len(run) for run in re.findall(r"`+", text)), default=0))
            return f"{fence}{text}{fence}"
        if node.name == "li":
            marker = "-"
            if node.parent.name == "ol":
                marker = f"{len(node.find_previous_siblings('li')) + 1}."
            return f"\n{marker} {text.strip()}\n"
        if node.name == "blockquote":
            return "\n\n" + "\n".join(f"> {line}" for line in text.strip().splitlines()) + "\n\n"
        if node.name in {
            "p",
            "div",
            "section",
            "article",
            "main",
            "figure",
            "figcaption",
            "dl",
            "dt",
            "dd",
            "ul",
            "ol",
        }:
            return f"\n\n{text.strip()}\n\n"
        return text

    return render(main).strip(), code_blocks


def _extract_documentation_page(html: str, page_url: str, requested_url: str) -> DocumentationPage:
    """Extract one article; linked pages remain untrusted, unfetched references."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    if soup.find() is None:
        raise ValueError("Documentation response contains no HTML article markup")
    container = (
        soup.find("main")
        or soup.find("article")
        or soup.find("div", id="main-column")
        or soup.body
        or soup
    )
    title_element = container.find("h1") or soup.find("title")
    fallback_title = title_element.get_text(" ", strip=True) if title_element else ""
    _remove_documentation_noise(container)
    article = container if container.name == "article" else container.find("article")
    main = article or container
    content_regions = (
        container.find_all("div", class_="content")
        if urlsplit(page_url).hostname == "learn.microsoft.com"
        else []
    )
    if content_regions:
        region_ids = {id(region) for region in content_regions}
        outer_regions = [
            region
            for region in content_regions
            if not any(id(parent) in region_ids for parent in region.parents)
        ]
        if article is None or not all(
            any(parent is article for parent in region.parents) for region in outer_regions
        ):
            main = soup.new_tag("article")
            for region in outer_regions:
                main.append(region.extract())
    title_element = main.find("h1")
    title = title_element.get_text(" ", strip=True) if title_element else fallback_title
    sections: list[str] = []
    links: list[DocumentationLink] = []
    seen_urls: set[str] = set()
    links_truncated = False
    section = ""
    page_parts = urlsplit(page_url)
    locale_prefix = re.compile(r"^/[a-z]{2}-[a-z]{2}(?=/)", re.IGNORECASE)

    for tag in main.find_all(["h2", "h3", "a"]):
        text = " ".join(tag.get_text(" ", strip=True).split())
        if tag.name in {"h2", "h3"}:
            if text:
                sections.append(text)
                section = text
            continue
        raw_url = tag.get("href", "")
        if not text or not isinstance(raw_url, str) or raw_url.lstrip().startswith("#"):
            continue
        try:
            link_url = normalize_documentation_url(raw_url, page_url)
        except ValueError:
            continue
        if link_url == page_url or link_url in seen_urls:
            continue
        link_parts = urlsplit(link_url)
        if (
            link_parts.hostname == page_parts.hostname
            and link_parts.path != page_parts.path
            and link_parts.query == page_parts.query
            and locale_prefix.sub("", link_parts.path) == locale_prefix.sub("", page_parts.path)
        ):
            continue
        seen_urls.add(link_url)
        if len(links) < MAX_DOCUMENTATION_LINKS:
            links.append({"text": text, "url": link_url, "section": section})
        else:
            links_truncated = True

    content, code_blocks = _documentation_content(main)
    if not content:
        raise ValueError("Documentation page contains no article content")
    if content.strip() == f"# {title}":
        raise ValueError("Documentation page contains a title but no article body")
    if title and not title_element:
        content = f"# {title}\n\n{content}"
    return {
        "title": title,
        "url": page_url,
        "requested_url": requested_url,
        "content": content,
        "sections": sections,
        "links": links,
        "code_blocks": code_blocks,
        "visuals": _extract_email_visuals(main, page_url, title),
        "links_truncated": links_truncated,
    }


class MicrosoftLearnService:
    """Service for searching Microsoft Learn documentation."""

    # Use the newer search API endpoint
    BASE_URL = "https://learn.microsoft.com/api/search"

    def __init__(self, locale: str = "en-us"):
        """Initialize the service.

        Args:
            locale: Locale for search results (default: en-us for better results)
        """
        self.locale = locale
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client and release resources."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def search_docs(
        self,
        query: str,
        top: int = 5,
        filter_products: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Search Microsoft Learn documentation.

        Args:
            query: Search query
            top: Maximum number of results
            filter_products: Optional list of product filters (e.g., ["azure"])

        Returns:
            Search results dictionary
        """
        client = await self._get_client()

        # Clean up the query - remove special characters and limit length
        clean_query = query.replace("[", "").replace("]", "").replace(":", " ")
        clean_query = " ".join(clean_query.split())[:180]  # Keep feature name + region terms

        # Build search URL with proper encoding.
        # NOTE: The Learn search API's server-side OData filter
        # `$filter=products/any(p: p eq 'azure')` returns ZERO results (the current
        # response schema no longer exposes a `products` field), which silently
        # broke ALL documentation search. Product filtering is therefore applied
        # client-side on the result URL below; over-fetch here so the post-filter
        # set still yields roughly `top` results.
        params = {
            "search": clean_query,
            "locale": self.locale,
            "$top": str(top * 3 if filter_products else top),
        }

        try:
            import time as _time

            _t0 = _time.time()
            logger.info("learn_search_start", query=clean_query, top=top)

            # Build URL manually to avoid encoding issues
            url = f"{self.BASE_URL}?{urlencode(params)}"
            response = await client.get(url)
            _elapsed = _time.time() - _t0

            if response.status_code != 200:
                logger.warning(
                    "learn_search_non_200",
                    status_code=response.status_code,
                    elapsed_s=round(_elapsed, 2),
                    query=clean_query,
                )
                # Try alternative approach - direct Bing search with site filter
                return await self._fallback_search(clean_query, top)

            data = response.json()
            results = data.get("results", [])

            # Client-side product filter (the server-side $filter is broken — see
            # the note above). Soft filter: prefer results whose URL matches a
            # product keyword, but fall back to all results when none match so a
            # valid search never collapses to zero hits.
            if filter_products:
                lowered = [p.lower() for p in filter_products]
                matched = [
                    r for r in results if any(p in r.get("url", "").lower() for p in lowered)
                ]
                if matched:
                    results = matched

            # Format results
            formatted_results = []
            for result in results[:top]:
                formatted_results.append(
                    {
                        "title": result.get("title", ""),
                        "url": result.get("url", ""),
                        "description": result.get("description", ""),
                        "last_updated": result.get("lastUpdatedDate", ""),
                        "products": result.get("products", []),
                        "category": result.get("category", ""),
                    }
                )

            logger.info(
                "learn_search_ok",
                query=clean_query,
                count=len(formatted_results),
                elapsed_s=round(_elapsed, 2),
            )
            return {
                "query": query,
                "count": len(formatted_results),
                "results": formatted_results,
            }

        except httpx.HTTPStatusError as e:
            logger.error(
                "learn_search_http_error", status_code=e.response.status_code, query=clean_query
            )
            return await self._fallback_search(clean_query, top)
        except Exception as e:
            logger.error("learn_search_error", error=str(e), query=clean_query)
            return await self._fallback_search(clean_query, top)

    async def _fallback_search(self, query: str, top: int = 5) -> dict[str, Any]:
        """Fallback search using direct URL construction for known documentation patterns."""
        # Generate estimated relevant URLs based on Azure service names
        results = []

        # Extract key terms
        terms = query.lower().split()
        azure_terms = [
            t
            for t in terms
            if t
            in [
                "storage",
                "blob",
                "sftp",
                "vm",
                "virtual",
                "machine",
                "container",
                "function",
                "app",
                "service",
                "database",
                "sql",
                "cosmos",
                "network",
                "kubernetes",
                "aks",
                "monitor",
                "security",
                "identity",
            ]
        ]

        # Generate documentation URLs
        if "blob" in terms or "storage" in terms:
            results.append(
                {
                    "title": "Azure Blob Storage documentation",
                    "url": "https://learn.microsoft.com/azure/storage/blobs/",
                    "description": "Azure Blob Storage is Microsoft's object storage solution for the cloud.",
                }
            )
            if "sftp" in terms:
                results.append(
                    {
                        "title": "SSH File Transfer Protocol (SFTP) support for Azure Blob Storage",
                        "url": "https://learn.microsoft.com/azure/storage/blobs/secure-file-transfer-protocol-support",
                        "description": "Learn how to securely connect to Blob containers using SFTP in Azure Blob Storage.",
                    }
                )
                results.append(
                    {
                        "title": "Connect to Azure Blob Storage by using SFTP",
                        "url": "https://learn.microsoft.com/azure/storage/blobs/secure-file-transfer-protocol-support-connect",
                        "description": "Describes how to connect to Azure Blob Storage and transfer files using an SFTP client.",
                    }
                )

        if "container" in terms or "aks" in terms or "kubernetes" in terms:
            results.append(
                {
                    "title": "Azure Kubernetes Service (AKS) documentation",
                    "url": "https://learn.microsoft.com/azure/aks/",
                    "description": "Learn how to deploy and manage containerized applications in Azure Kubernetes Service.",
                }
            )

        if "function" in terms:
            results.append(
                {
                    "title": "Azure Functions documentation",
                    "url": "https://learn.microsoft.com/azure/azure-functions/",
                    "description": "Azure Functions is a serverless compute service that lets you run code without managing infrastructure.",
                }
            )

        if "vm" in terms or "virtual" in terms or "machine" in terms:
            results.append(
                {
                    "title": "Virtual Machines documentation",
                    "url": "https://learn.microsoft.com/azure/virtual-machines/",
                    "description": "Learn how to create and manage Windows and Linux virtual machines in Azure Virtual Machines.",
                }
            )

        # If no specific matches, add general Azure docs
        if not results:
            results.append(
                {
                    "title": "Azure documentation",
                    "url": "https://learn.microsoft.com/azure/",
                    "description": "Find comprehensive documentation for Azure cloud services.",
                }
            )

        return {
            "query": query,
            "count": len(results[:top]),
            "results": results[:top],
        }

    async def search_azure_docs(
        self,
        query: str,
        service_name: Optional[str] = None,
        top: int = 5,
    ) -> dict[str, Any]:
        """Search Azure-specific documentation.

        Args:
            query: Search query
            service_name: Optional Azure service name to filter
            top: Maximum number of results

        Returns:
            Search results dictionary
        """
        # Build Azure-focused query
        search_query = query
        if service_name:
            search_query = f"Azure {service_name} {query}"

        return await self.search_docs(
            query=search_query,
            top=top,
            filter_products=["azure"],
        )

    async def get_service_documentation(
        self,
        service_name: str,
        topics: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Get documentation for a specific Azure service.

        Args:
            service_name: Name of the Azure service
            topics: Optional list of topics to search for

        Returns:
            Documentation results
        """
        results = []

        # Base search for the service
        base_results = await self.search_azure_docs(
            query=f"{service_name} overview",
            service_name=service_name,
            top=3,
        )
        results.extend(base_results.get("results", []))

        # Search for specific topics
        if topics:
            for topic in topics[:3]:  # Limit topics
                topic_results = await self.search_azure_docs(
                    query=f"{service_name} {topic}",
                    service_name=service_name,
                    top=2,
                )
                results.extend(topic_results.get("results", []))

        # Deduplicate by URL
        seen_urls = set()
        unique_results = []
        for r in results:
            if r.get("url") not in seen_urls:
                seen_urls.add(r.get("url"))
                unique_results.append(r)

        return {
            "service": service_name,
            "topics": topics,
            "count": len(unique_results),
            "results": unique_results[:10],
        }

    async def fetch_documentation_page(self, url: str) -> DocumentationFetchResult:
        """Fetch a complete, size-bounded HTML article with validated manual redirects.

        Args:
            url: An HTTPS documentation URL or an approved redirect-only shortener.

        Returns:
            An explicit success/data/error envelope. Content is never silently truncated.
            Cancellation propagates to the caller.
        """
        started = time.monotonic()
        current_url = ""
        try:
            current_url = _validated_documentation_fetch_url(url)
            client = await self._get_client()
            visited: set[str] = set()
            for redirect_count in range(MAX_DOCUMENTATION_REDIRECTS + 1):
                if current_url in visited:
                    raise ValueError("Documentation redirect loop detected")
                visited.add(current_url)
                async with client.stream(
                    "GET",
                    current_url,
                    headers={"Accept": "text/html, application/xhtml+xml"},
                    follow_redirects=False,
                ) as response:
                    final_url = _validated_documentation_fetch_url(str(response.url))
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location", "")
                        if not location:
                            raise ValueError(
                                "Documentation redirect is missing its Location header"
                            )
                        target = _validated_documentation_fetch_url(location, final_url)
                        if target in visited:
                            raise ValueError("Documentation redirect loop detected")
                        if redirect_count == MAX_DOCUMENTATION_REDIRECTS:
                            raise ValueError(
                                f"Documentation redirect limit ({MAX_DOCUMENTATION_REDIRECTS}) exceeded"
                            )
                        current_url = target
                        continue
                    if response.status_code != 200:
                        raise ValueError(
                            f"Documentation request returned HTTP {response.status_code}, not a complete page"
                        )
                    if urlsplit(final_url).hostname in DOCUMENTATION_REDIRECT_DOMAINS:
                        raise ValueError(
                            "Redirect-only documentation host returned non-redirect content"
                        )
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
                    if content_type.lower() not in {"text/html", "application/xhtml+xml"}:
                        raise ValueError("Documentation response is not HTML")
                    declared_length = response.headers.get("content-length")
                    if declared_length is not None:
                        length = int(declared_length)
                        if length < 0:
                            raise ValueError("Documentation response has an invalid Content-Length")
                        if length > MAX_DOCUMENTATION_BYTES:
                            raise ValueError(
                                f"Documentation page exceeds {MAX_DOCUMENTATION_BYTES} bytes"
                            )
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                        if len(body) + len(chunk) > MAX_DOCUMENTATION_BYTES:
                            raise ValueError(
                                f"Documentation page exceeds {MAX_DOCUMENTATION_BYTES} bytes"
                            )
                        body.extend(chunk)
                    page = _extract_documentation_page(
                        body.decode(response.encoding or "utf-8", errors="replace"),
                        final_url,
                        url,
                    )
                    logger.info(
                        "learn_documentation_fetch_ok",
                        url=final_url,
                        content_chars=len(page["content"]),
                        links=len(page["links"]),
                        links_truncated=page["links_truncated"],
                        bytes_received=len(body),
                        elapsed_s=round(time.monotonic() - started, 3),
                    )
                    return {"success": True, "data": page, "error": ""}
            raise ValueError("Documentation redirect limit exceeded")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc) or 'Documentation fetch failed'}"
            logger.warning(
                "learn_documentation_fetch_failed",
                url=current_url,
                error=error,
                elapsed_s=round(time.monotonic() - started, 3),
            )
            return {"success": False, "data": None, "error": error}

    async def fetch_page_content(
        self,
        url: str,
        max_chars: int = 3000,
    ) -> Optional[dict[str, Any]]:
        """Return a bounded preview using the validated full-article fetch path.

        Args:
            url: Documentation URL.
            max_chars: Maximum content characters before the truncation marker.

        Returns:
            Legacy title/url/content/sections/visuals mapping, or None on failure.
        """
        result = await self.fetch_documentation_page(url)
        page = result["data"]
        if not result["success"] or page is None:
            return None
        content = page["content"]
        if len(content) > max_chars:
            content = content[:max_chars] + "\n... (truncated)"
        return {
            "title": page["title"],
            "url": page["url"],
            "content": content,
            "sections": page["sections"][:15],
            "visuals": page["visuals"],
        }

    async def fetch_learn_more_contents(
        self,
        links: list[dict],
        max_links: int = 3,
        max_chars_per_page: int = 3000,
    ) -> list[dict[str, Any]]:
        """Fetch content from multiple Learn More links in parallel.

        Args:
            links: List of {text, url} dicts from AzureUpdate.learn_more_links
            max_links: Maximum number of links to fetch (to limit API calls)
            max_chars_per_page: Maximum content chars per page

        Returns:
            List of page content dicts (title, url, content, sections, visuals)
        """
        if max_links <= 0:
            return []
        fetchable: list[str] = []
        seen_urls: set[str] = set()
        for link in links:
            try:
                candidate = _validated_documentation_fetch_url(link.get("url", ""))
            except ValueError as exc:
                logger.warning("learn_more_link_blocked", error=str(exc))
                continue
            if candidate not in seen_urls:
                seen_urls.add(candidate)
                fetchable.append(candidate)
            if len(fetchable) == max_links:
                break

        if not fetchable:
            return []

        tasks = [self.fetch_page_content(link, max_chars=max_chars_per_page) for link in fetchable]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        contents = []
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, dict):
                contents.append(result)
            elif isinstance(result, Exception):
                logger.warning("learn_more_link_failed", error=str(result))

        return contents
