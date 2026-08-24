"""Microsoft Learn documentation search service."""

from typing import Any, Optional
from urllib.parse import urlencode, urlparse

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


def _is_allowed_url(url: str) -> bool:
    """Validate URL against allowed domains whitelist (SSRF protection).

    Args:
        url: URL to validate

    Returns:
        True if the URL's domain is in the allowed list
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = (parsed.hostname or "").lower()
        return hostname in ALLOWED_FETCH_DOMAINS
    except Exception:
        return False


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
        clean_query = " ".join(clean_query.split())[:100]  # Limit query length

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

    async def fetch_page_content(
        self,
        url: str,
        max_chars: int = 3000,
    ) -> Optional[dict[str, Any]]:
        """Fetch and extract main content from a Microsoft Learn page.

        Args:
            url: Full URL of the Learn page
            max_chars: Maximum characters of content to return

        Returns:
            Dict with title, url, content (plain text), and sections,
            or None if fetch failed or URL not allowed
        """
        # SSRF protection: validate URL against allowed domains
        if not _is_allowed_url(url):
            logger.warning(
                "learn_page_fetch_blocked",
                url=url,
                reason="URL domain not in allowed list",
            )
            return None

        from bs4 import BeautifulSoup

        client = await self._get_client()
        try:
            import time as _time

            _t0 = _time.time()
            logger.info("learn_page_fetch_start", url=url)

            response = await client.get(url, follow_redirects=True)
            _elapsed = _time.time() - _t0

            if response.status_code != 200:
                logger.warning(
                    "learn_page_fetch_non_200",
                    url=url,
                    status=response.status_code,
                    elapsed_s=round(_elapsed, 2),
                )
                return None

            soup = BeautifulSoup(response.text, "html.parser")

            # Extract page title
            title_el = soup.find("h1")
            title = title_el.get_text(strip=True) if title_el else ""

            # Find main content area (Learn pages use <main> or article)
            main = (
                soup.find("main") or soup.find("article") or soup.find("div", {"id": "main-column"})
            )
            if not main:
                main = soup.body or soup

            # Remove nav, header, footer, aside, script, style, and UI elements
            for tag in main.find_all(
                ["nav", "header", "footer", "aside", "script", "style", "button", "form", "svg"]
            ):
                tag.decompose()

            # Remove Learn page UI noise (share buttons, feedback, etc.)
            for tag in main.find_all(
                "div",
                class_=lambda c: c
                and any(
                    x in str(c)
                    for x in [
                        "share",
                        "feedback",
                        "action-bar",
                        "metadata",
                        "alert",
                        "consent",
                        "cookie",
                        "sign-in",
                    ]
                ),
            ):
                tag.decompose()

            # Extract section headings for structure
            sections = []
            for h in main.find_all(["h2", "h3"]):
                text = h.get_text(strip=True)
                # Skip UI headings
                if text.lower() not in {
                    "feedback",
                    "additional resources",
                    "additional links",
                    "next steps",
                }:
                    sections.append(text)

            # Get plain text content
            content = main.get_text(separator="\n", strip=True)

            # Clean up excessive whitespace and UI boilerplate
            import re

            content = re.sub(r"\n{3,}", "\n\n", content)
            content = re.sub(r" {2,}", " ", content)
            # Remove common Learn page UI text patterns
            noise_patterns = [
                r"Read in English\n?",
                r"^Edit\n",
                r"Share via\n?",
                r"^Facebook\n?",
                r"^x\.com\n?",
                r"^LinkedIn\n?",
                r"^Email\n",
                r"(?:Note\n?)?Access to this page requires authorization\.[^\n]*\n?",
                r"You can try\n?signing in\n?or\n?changing directories\s*\.\n?",
                r"signing in\n?or\n?changing directories\s*\.\n?",
                r"changing directories\s*\.\n?",
                r"Summarize this article for me\s*\n?",
                r"Was this page helpful\?\n?YesNo\s*\n?",
            ]
            for pattern in noise_patterns:
                content = re.sub(pattern, "", content, flags=re.MULTILINE)
            content = content.strip()

            # Truncate to max_chars
            if len(content) > max_chars:
                content = content[:max_chars] + "\n... (truncated)"

            logger.info(
                "learn_page_fetch_ok",
                url=url,
                title=title,
                content_chars=len(content),
                sections=len(sections),
                elapsed_s=round(_elapsed, 2),
            )

            return {
                "title": title,
                "url": url,
                "content": content,
                "sections": sections[:15],
            }

        except Exception as e:
            logger.warning(
                "learn_page_fetch_error",
                url=url,
                error=str(e),
            )
            return None

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
            List of page content dicts (title, url, content, sections)
        """
        import asyncio as _asyncio

        # Filter to learn.microsoft.com and aka.ms links only
        fetchable = [
            link
            for link in links
            if any(
                domain in link.get("url", "")
                for domain in ["learn.microsoft.com", "aka.ms", "go.microsoft.com"]
            )
        ][:max_links]

        if not fetchable:
            return []

        tasks = [
            self.fetch_page_content(link["url"], max_chars=max_chars_per_page) for link in fetchable
        ]

        results = await _asyncio.gather(*tasks, return_exceptions=True)

        contents = []
        for result in results:
            if isinstance(result, dict) and result is not None:
                contents.append(result)

        return contents
