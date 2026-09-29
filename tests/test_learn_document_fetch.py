"""Offline contracts for bounded, redirect-safe documentation extraction."""

import asyncio
import gzip
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from html import escape
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import httpx
import pytest
from structlog.testing import capture_logs

from src.services.microsoft_learn import (
    ALLOWED_FETCH_DOMAINS,
    DOCUMENTATION_REDIRECT_DOMAINS,
    MAX_DOCUMENTATION_BYTES,
    MAX_DOCUMENTATION_LINKS,
    MAX_DOCUMENTATION_REDIRECTS,
    DocumentationFetchResult,
    DocumentationLink,
    DocumentationPage,
    MicrosoftLearnService,
    normalize_documentation_url,
)

PAGE_URL = "https://learn.microsoft.com/en-us/azure/storage/overview"
Handler = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


@asynccontextmanager
async def mock_service(handler: Handler) -> AsyncGenerator[MicrosoftLearnService, None]:
    """Ensure every fetch uses a local transport, including unexpected redirects."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        service = MicrosoftLearnService()
        service._client = client
        yield service


def html_response(
    html: str = "<main><h1>Article</h1><p>Complete evidence.</p></main>",
) -> httpx.Response:
    return httpx.Response(200, headers={"Content-Type": "text/html; charset=utf-8"}, text=html)


class CountingStream(httpx.AsyncByteStream):
    """Track whether bounded reads stop and close before consuming the entire response."""

    def __init__(self, chunks: list[bytes], failure: BaseException | None = None):
        self.chunks = chunks
        self.failure = failure
        self.reads = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.reads += 1
            yield chunk
        if self.failure is not None:
            raise self.failure

    async def aclose(self) -> None:
        self.closed = True


def test_documentation_contract_and_limits() -> None:
    assert DocumentationLink.__required_keys__ == {"text", "url", "section"}
    assert DocumentationPage.__required_keys__ == {
        "title",
        "url",
        "requested_url",
        "content",
        "sections",
        "links",
        "code_blocks",
        "visuals",
        "links_truncated",
    }
    assert DocumentationFetchResult.__required_keys__ == {"success", "data", "error"}
    assert MAX_DOCUMENTATION_REDIRECTS == 5
    assert MAX_DOCUMENTATION_BYTES == 2 * 1024 * 1024
    assert MAX_DOCUMENTATION_LINKS == 200
    assert DOCUMENTATION_REDIRECT_DOMAINS == {"aka.ms", "go.microsoft.com"}
    assert DOCUMENTATION_REDIRECT_DOMAINS.isdisjoint(ALLOWED_FETCH_DOMAINS)


@pytest.mark.asyncio
async def test_live_learn_split_title_and_body_regions_are_both_preserved() -> None:
    html = """
    <main>
      <div class="content"><h1>PostgreSQL troubleshooting guides</h1></div>
      <nav><a href="/navigation">Navigation</a></nav>
      <div class="content">
        <p>Correlate the diagnostic evidence before changing configuration.</p>
        <h2>Prerequisites</h2>
        <div class="alert important">Diagnostic logs must be enabled.</div>
        <p><a href="troubleshoot-high-cpu">High CPU guide</a></p>
        <div class="content"><p>Nested article content appears only once.</p></div>
      </div>
      <div class="content"><h2>Limitations</h2><p>Allow time for telemetry ingestion.</p></div>
    </main>
    """
    async with mock_service(lambda _: html_response(html)) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    assert result["success"]
    page = result["data"]
    assert page is not None
    assert page["title"] == "PostgreSQL troubleshooting guides"
    assert "Diagnostic logs must be enabled" in page["content"]
    assert "Allow time for telemetry ingestion" in page["content"]
    assert page["content"].count("Nested article content appears only once.") == 1
    assert page["sections"] == ["Prerequisites", "Limitations"]
    assert page["links"] == [
        {
            "text": "High CPU guide",
            "url": "https://learn.microsoft.com/en-us/azure/storage/troubleshoot-high-cpu",
            "section": "Prerequisites",
        }
    ]


@pytest.mark.asyncio
async def test_explicit_article_keeps_text_outside_nested_content_regions() -> None:
    html = (
        "<main><article><h1>Article</h1><p>Required outside text.</p>"
        '<div class="content"><p>Nested details.</p></div></article></main>'
    )
    async with mock_service(lambda _: html_response(html)) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    assert result["data"] is not None
    assert "Required outside text." in result["data"]["content"]
    assert "Nested details." in result["data"]["content"]


@pytest.mark.asyncio
async def test_heading_only_response_is_not_complete_documentation_evidence() -> None:
    async with mock_service(
        lambda _: html_response('<main><div class="content"><h1>Title only</h1></div></main>')
    ) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    assert not result["success"]
    assert "no article body" in result["error"]


@pytest.mark.parametrize(
    ("url", "base", "expected"),
    [
        (
            "HTTPS://LEARN.MICROSOFT.COM:443/azure?view=azure-2026&tabs=cli&version=2"
            "&q=a%26b%3Dc&utm_source=test&UTM_custom=x&ocid=123#details",
            "",
            "https://learn.microsoft.com/azure?view=azure-2026&tabs=cli&version=2&q=a%26b%3Dc",
        ),
        (
            "../network/connect?api-version=2026-09-01&tabs=cli%2Cportal#configure",
            PAGE_URL,
            "https://learn.microsoft.com/en-us/azure/network/connect"
            "?api-version=2026-09-01&tabs=cli%2Cportal",
        ),
        ("//other.example/article#part", PAGE_URL, "https://other.example/article"),
        ("#part", PAGE_URL, PAGE_URL),
        ("?view=v2&tabs=powershell", PAGE_URL, PAGE_URL + "?view=v2&tabs=powershell"),
        ("http://EXAMPLE.COM:80", "", "http://example.com/"),
        ("https://example.com/한글", "", "https://example.com/%ED%95%9C%EA%B8%80"),
        (
            "https://example.com/doc?data=required&cid=id&empty=&tabs=a&tabs=b",
            "",
            "https://example.com/doc?data=required&cid=id&empty=&tabs=a&tabs=b",
        ),
    ],
)
def test_normalize_documentation_url(url: str, base: str, expected: str) -> None:
    assert normalize_documentation_url(url, base) == expected
    assert normalize_documentation_url(expected) == expected


def test_safelinks_unwrap_once_and_preserve_encoded_functional_values() -> None:
    target = PAGE_URL + "/a%2Fb?view=2026&custom=a%26b%3Dc&utm_source=mail#heading"
    first = "https://nam06.safelinks.protection.outlook.com/?" + urlencode(
        {"url": target, "data": "bookkeeping"}
    )
    nested = "https://eur01.safelinks.protection.outlook.com/?" + urlencode({"url": first})
    assert normalize_documentation_url(nested) == (PAGE_URL + "/a%2Fb?view=2026&custom=a%26b%3Dc")
    spoof = "https://safelinks.protection.outlook.com.evil.example/?" + urlencode({"url": target})
    assert normalize_documentation_url(spoof).startswith(
        "https://safelinks.protection.outlook.com.evil.example/"
    )


@pytest.mark.parametrize(
    "url",
    [
        "",
        " ",
        "/relative-without-base",
        "javascript:alert(1)",
        "data:text/html,hello",
        "file:///etc/passwd",
        "https:learn.microsoft.com/article",
        "https:///article",
        "https://",
        "https://user:secret@learn.microsoft.com/doc",
        "https://@learn.microsoft.com/doc",
        "https://learn.microsoft.com:444/doc",
        "https://learn.microsoft.com:80/doc",
        "http://learn.microsoft.com:443/doc",
        "https://learn.microsoft.com:/doc",
        "https://learn.microsoft.com:not-a-port/doc",
        "https://learn.microsoft.com:99999/doc",
        "https://learn.microsoft.com\\@evil.example/doc",
        "https://learn.microsoft.com/\r\nLocation: x",
        "https://learn.micro\tsoft.com/doc",
        "https://learn.microsoft.com/space here",
        "https://learn%2emicrosoft.com/doc",
        "https://learn..microsoft.com/doc",
        "https://-invalid.example/doc",
        "https://[not-an-ip]/doc",
        "https://learn.microsoft.com/doc%invalid",
        "https://learn.microsoft.com/doc\x7f",
        "https://safelinks.protection.outlook.com/?url=javascript%3Aalert%281%29",
        "https://safelinks.protection.outlook.com/?url=https%3A%2F%2Flearn.microsoft.com&url=x",
    ],
)
def test_normalization_rejects_unsafe_or_malformed_urls(url: str) -> None:
    with pytest.raises(ValueError):
        normalize_documentation_url(url)


def test_normalization_rejects_unsafe_base_and_excessive_safelinks_nesting() -> None:
    with pytest.raises(ValueError):
        normalize_documentation_url("article", "https://user:secret@learn.microsoft.com/")
    nested = PAGE_URL
    for _ in range(MAX_DOCUMENTATION_REDIRECTS + 1):
        nested = "https://safe.safelinks.protection.outlook.com/?" + urlencode({"url": nested})
    with pytest.raises(ValueError, match="nesting"):
        normalize_documentation_url(nested)


@pytest.mark.asyncio
async def test_full_article_links_sections_warnings_tables_commands_and_visuals() -> None:
    long_text = "Documented configuration and limitations remain relevant. " * 100
    command = 'az resource list \\\n  --query "[].id"\n\n\nprintf "```"\n'
    wrapped_image = "https://safe.safelinks.protection.outlook.com/diagram.png?" + urlencode(
        {"url": "https://learn.microsoft.com/diagram.png"}
    )
    html = f"""
      <html><head><title>Fallback</title><base href="https://evil.example/"></head><body>
      <nav>Outer navigation<a href="/outer">Outside</a></nav>
      <main>
        <h1>Configure storage</h1>
        <div class="metadata">OUTSIDE BODY METADATA</div>
        <div class="content">
          <div class="toc"><h2>UI TABLE OF CONTENTS</h2><a href="/toc">TOC link</a></div>
          <h2>Overview</h2>
          <p>{long_text}</p>
          <div class="alert is-warning"><p>Important: keep the recovery key before migration.</p></div>
          <aside class="admonition" role="complementary">
            Technical caveat: a restart interrupts connections.
          </aside>
          <a href="./configure?view=v2&amp;tabs=cli&amp;utm_source=article#one">Configure CLI</a>
          <a href="{PAGE_URL.rsplit('/', 1)[0]}/configure?view=v2&amp;tabs=cli#two">Duplicate</a>
          <a href="#overview">Section anchor</a>
          <a href="{PAGE_URL}#another">Absolute section anchor</a>
          <a href="/ja-jp/azure/storage/overview">Japanese translation</a>
          <a href="/some-language" hreflang="en-us">Read in English</a>
          <a href="https://social.example/share">Share on Facebook</a>
          <a href="mailto:help@example.com">Mail</a>
          <a href="javascript:alert(1)">Script</a>
          <h3>Prerequisites</h3>
          <a href="../identity/requirements?api-version=2026-09-01">Identity requirements</a>
          <a href="https://external.example/specification#conditions">External specification</a>
          <a href="http://internal.example/legacy">HTTP reference</a>
          <a href="https://127.0.0.1/metadata">Untrusted address</a>
          <a href="https://aka.ms/storage">Short link</a>
          <table><caption>Service plans</caption><tr><th>Plan</th><th>Availability</th></tr>
            <tr><td>Basic</td><td>Not supported</td></tr></table>
          <pre><code class="lang-azurecli">{escape(command)}</code></pre>
          <figure><img src="media/example/portal.png" alt="Storage configuration pane">
            <figcaption>Keep this setting enabled.</figcaption></figure>
          <img src="https://evil.example/chart.png" alt="External image">
          <img src="https://user:secret@learn.microsoft.com/chart.png" alt="Credentialed image">
          <img src="https://learn.microsoft.com:444/chart.png" alt="Untrusted port">
          <img src="https://aka.ms/chart.png" alt="Shortener image">
          <img src="{escape(wrapped_image)}" alt="SafeLinks image">
          <img src="/media/icon.svg" alt="icon">
          <header>HEADER CHROME</header><footer>FOOTER CHROME</footer>
          <aside>SIDEBAR CHROME<a href="/side">Sidebar link</a></aside>
          <div class="feedback">FEEDBACK CHROME<a href="/feedback">Feedback</a></div>
          <button>COPY BUTTON</button><script>unsafe_script()</script>
          <h2>Next steps</h2><p>Tail evidence after the old 3000-character limit.</p>
          <a href="/en-us/azure/storage/next">Next operation</a>
        </div>
        <p>OUTSIDE BODY TEASER</p>
      </main></body></html>
    """
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return html_response(html)

    requested_url = PAGE_URL + "?utm_source=mail#overview"
    async with mock_service(handler) as service:
        result = await service.fetch_documentation_page(requested_url)

    assert result["success"] is True
    assert result["error"] == ""
    page = result["data"]
    assert page is not None
    assert page["url"] == PAGE_URL
    assert page["requested_url"] == requested_url
    assert page["title"] == "Configure storage"
    assert len(page["content"]) > 5000
    assert long_text.strip() in page["content"]
    assert "Tail evidence after the old 3000-character limit." in page["content"]
    assert "Important: keep the recovery key before migration." in page["content"]
    assert "Technical caveat: a restart interrupts connections." in page["content"]
    assert "| Plan | Availability |" in page["content"]
    assert "| Basic | Not supported |" in page["content"]
    assert "## Overview" in page["content"]
    assert "### Prerequisites" in page["content"]
    assert page["sections"] == ["Overview", "Prerequisites", "Next steps"]
    assert page["code_blocks"] == [f"````azurecli\n{command.rstrip(chr(10))}\n````"]
    assert page["code_blocks"][0] in page["content"]
    assert not page["links_truncated"]
    assert page["links"] == [
        {
            "text": "Configure CLI",
            "url": "https://learn.microsoft.com/en-us/azure/storage/configure?view=v2&tabs=cli",
            "section": "Overview",
        },
        {
            "text": "Identity requirements",
            "url": "https://learn.microsoft.com/en-us/azure/identity/requirements?api-version=2026-09-01",
            "section": "Prerequisites",
        },
        {
            "text": "External specification",
            "url": "https://external.example/specification",
            "section": "Prerequisites",
        },
        {
            "text": "HTTP reference",
            "url": "http://internal.example/legacy",
            "section": "Prerequisites",
        },
        {
            "text": "Untrusted address",
            "url": "https://127.0.0.1/metadata",
            "section": "Prerequisites",
        },
        {"text": "Short link", "url": "https://aka.ms/storage", "section": "Prerequisites"},
        {
            "text": "Next operation",
            "url": "https://learn.microsoft.com/en-us/azure/storage/next",
            "section": "Next steps",
        },
    ]
    assert page["visuals"] == [
        {
            "url": "https://learn.microsoft.com/en-us/azure/storage/media/example/portal.png",
            "alt": "Storage configuration pane",
            "caption": "Keep this setting enabled.",
            "source_url": PAGE_URL,
            "source_title": "Configure storage",
        }
    ]
    for noise in (
        "CHROME",
        "UI TABLE",
        "OUTSIDE BODY",
        "COPY BUTTON",
        "unsafe_script",
        "Read in English",
    ):
        assert noise not in page["content"]
    assert len(requests) == 1
    assert requests[0].headers["Accept"] == "text/html, application/xhtml+xml"


@pytest.mark.asyncio
async def test_learn_content_precedes_nested_article_and_preserves_versioned_locale_links() -> None:
    html = """
      <main><div class="content"><h1>Primary article</h1>
        <p>Primary evidence before an embedded example.</p>
        <article><h2>Example</h2><p>Embedded evidence.</p></article>
        <a href="/ja-jp/azure/storage/overview?view=v2">Version two evidence</a>
        <pre class="language-powershell"><code>Get-AzResource</code></pre>
        <code>first command
second command</code>
        <p>Primary evidence after the embedded example.</p>
      </div></main>
    """
    async with mock_service(lambda request: html_response(html)) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    page = result["data"]
    assert page is not None
    assert page["title"] == "Primary article"
    assert "Primary evidence before" in page["content"]
    assert "Primary evidence after" in page["content"]
    assert "Embedded evidence." in page["content"]
    assert page["links"] == [
        {
            "text": "Version two evidence",
            "url": "https://learn.microsoft.com/ja-jp/azure/storage/overview?view=v2",
            "section": "Example",
        }
    ]
    assert page["code_blocks"] == [
        "```powershell\nGet-AzResource\n```",
        "```\nfirst command\nsecond command\n```",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("host", sorted(ALLOWED_FETCH_DOMAINS))
async def test_all_original_terminal_hosts_remain_fetchable(host: str) -> None:
    async with mock_service(lambda request: html_response()) as service:
        result = await service.fetch_documentation_page(f"https://{host}/article")
    assert result["success"]
    assert result["data"] is not None
    assert result["data"]["url"] == f"https://{host}/article"


@pytest.mark.asyncio
@pytest.mark.parametrize("extra_unique", [0, 1])
async def test_link_limit_counts_only_unique_valid_article_candidates(extra_unique: int) -> None:
    links = "".join(
        f'<a href="/doc-{index}">Article {index}</a>'
        for index in range(MAX_DOCUMENTATION_LINKS + extra_unique)
    )
    html = (
        "<main><h1>References</h1>"
        + links
        + '<a href="/doc-0?utm_source=duplicate#part">Duplicate</a>'
        + '<a href="#local">Local</a><a href="mailto:someone@example.com">Mail</a>'
        + '<nav><a href="/not-an-article">Navigation</a></nav></main>'
    )
    async with mock_service(lambda request: html_response(html)) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    page = result["data"]
    assert page is not None
    assert len(page["links"]) == MAX_DOCUMENTATION_LINKS
    assert page["links_truncated"] is bool(extra_unique)
    assert page["links"][0]["section"] == ""
    assert f"Article {MAX_DOCUMENTATION_LINKS + extra_unique - 1}" in page["content"]


@pytest.mark.asyncio
async def test_shortener_chain_relative_redirects_and_final_link_base() -> None:
    requests: list[str] = []
    final_url = "https://learn.microsoft.com/en-us/azure/network/guide?view=v2&tabs=cli"
    responses = [
        httpx.Response(301, headers={"Location": "https://go.microsoft.com/fwlink/start"}),
        httpx.Response(307, headers={"Location": "../next?linkid=123"}),
        httpx.Response(302, headers={"Location": final_url + "&utm_source=short#section"}),
        html_response(
            "<article><h1>Network guide</h1><p>Evidence.</p>"
            '<a href="./details?view=v2&tabs=cli">Details</a></article>'
        ),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return responses[len(requests) - 1]

    async with mock_service(handler) as service:
        result = await service.fetch_documentation_page("https://aka.ms/network")
    page = result["data"]
    assert page is not None
    assert requests == [
        "https://aka.ms/network",
        "https://go.microsoft.com/fwlink/start",
        "https://go.microsoft.com/next?linkid=123",
        final_url,
    ]
    assert page["requested_url"] == "https://aka.ms/network"
    assert page["url"] == final_url
    assert page["links"][0]["url"] == (
        "https://learn.microsoft.com/en-us/azure/network/details?view=v2&tabs=cli"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_all_supported_redirect_statuses(status: int) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if len(requests) == 1:
            return httpx.Response(status, headers={"Location": "/final"})
        return html_response()

    async with mock_service(handler) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    assert result["success"] is True
    assert requests == [PAGE_URL, "https://learn.microsoft.com/final"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        "https://127.0.0.1/metadata",
        "http://169.254.169.254/metadata",
        "https://localhost/private",
        "https://[::1]/private",
        "https://evil.example/article",
        "//evil.example/article",
        "https://learn.microsoft.com.evil.example/article",
        "http://learn.microsoft.com/article",
        "https://user:secret@learn.microsoft.com/article",
        "https://learn.microsoft.com:444/article",
        "https://evil.example/?target=https://learn.microsoft.com",
    ],
)
async def test_redirect_is_validated_before_the_next_request(target: str) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": target})

    async with mock_service(handler) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    assert result["success"] is False
    assert result["data"] is None
    assert result["error"]
    assert requests == [PAGE_URL]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://learn.microsoft.com/article",
        "https://learn.microsoft.com.evil.example/",
        "https://evil.example/learn.microsoft.com/aka.ms",
        "https://127.0.0.1/",
        "https://user:secret@learn.microsoft.com/",
        "https://learn.microsoft.com:8443/",
        "javascript:alert(1)",
        "http://safe.safelinks.protection.outlook.com/?" + urlencode({"url": PAGE_URL}),
        "https://safe.safelinks.protection.outlook.com/?"
        + urlencode({"url": "http://learn.microsoft.com/article"}),
    ],
)
async def test_invalid_initial_url_never_creates_a_client(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = MicrosoftLearnService()
    get_client = AsyncMock(side_effect=AssertionError("No HTTP client may be created"))
    monkeypatch.setattr(service, "_get_client", get_client)
    with capture_logs() as logs:
        result = await service.fetch_documentation_page(url)
    assert result["success"] is False
    assert result["data"] is None
    assert result["error"]
    assert not get_client.called
    assert logs[-1]["event"] == "learn_documentation_fetch_failed"
    assert logs[-1]["error"] == result["error"]
    assert "secret" not in str(logs)


@pytest.mark.asyncio
async def test_safelinks_wrapper_is_cleaned_without_fetching_the_wrapper() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return html_response()

    wrapper = "https://safe.safelinks.protection.outlook.com/?" + urlencode({"url": PAGE_URL})
    async with mock_service(handler) as service:
        result = await service.fetch_documentation_page(wrapper)
    assert result["success"] is True
    assert requests == [PAGE_URL]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["https://aka.ms/article", "https://go.microsoft.com/article"])
async def test_shorteners_are_never_terminal_content(url: str) -> None:
    async with mock_service(lambda request: html_response()) as service:
        result = await service.fetch_documentation_page(url)
    assert result["success"] is False
    assert result["data"] is None
    assert "Redirect-only" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "redirects", [MAX_DOCUMENTATION_REDIRECTS, MAX_DOCUMENTATION_REDIRECTS + 1]
)
async def test_redirect_limit_includes_only_followed_hops(redirects: int) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        step = int(request.url.path.strip("/"))
        if step < redirects:
            return httpx.Response(302, headers={"Location": f"/{step + 1}"})
        return html_response()

    async with mock_service(handler) as service:
        result = await service.fetch_documentation_page("https://learn.microsoft.com/0")
    assert len(requests) == MAX_DOCUMENTATION_REDIRECTS + 1
    assert result["success"] is (redirects == MAX_DOCUMENTATION_REDIRECTS)
    if redirects > MAX_DOCUMENTATION_REDIRECTS:
        assert "redirect limit" in result["error"]
        assert result["data"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("location", [PAGE_URL + "#loop", "/second"])
async def test_redirect_loop_is_reported_without_refetching_a_page(location: str) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            302, headers={"Location": location if len(requests) == 1 else PAGE_URL}
        )

    async with mock_service(handler) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    assert not result["success"]
    assert "redirect loop" in result["error"]
    assert len(requests) == (1 if "#loop" in location else 2)


@pytest.mark.asyncio
async def test_redirect_without_location_is_explicit_failure() -> None:
    async with mock_service(lambda request: httpx.Response(302)) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    assert result["data"] is None
    assert "Location" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reported_url",
    ["https://127.0.0.1/internal", "http://learn.microsoft.com/final", "https://aka.ms/final"],
)
async def test_final_response_url_is_independently_validated(reported_url: str) -> None:
    class ReportedURLResponse(httpx.Response):
        @property
        def url(self) -> httpx.URL:
            return httpx.URL(reported_url)

    async with mock_service(
        lambda request: ReportedURLResponse(
            200, headers={"Content-Type": "text/html"}, text="<main>Must not be accepted</main>"
        )
    ) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    assert not result["success"]
    assert result["data"] is None
    assert result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["application/pdf", "application/json", "text/plain", ""])
async def test_non_html_response_fails_without_reading_body(content_type: str) -> None:
    stream = CountingStream([b"not HTML"])
    async with mock_service(
        lambda request: httpx.Response(200, headers={"Content-Type": content_type}, stream=stream)
    ) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    assert not result["success"]
    assert "not HTML" in result["error"]
    assert stream.reads == 0
    assert stream.closed


@pytest.mark.asyncio
async def test_xhtml_is_supported_and_empty_articles_fail() -> None:
    async with mock_service(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "application/xhtml+xml; charset=utf-8"},
            text="<article><h1>Title</h1><p>Valid XHTML evidence.</p></article>",
        )
    ) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    assert result["success"]
    async with mock_service(
        lambda request: html_response("<main><nav>Only navigation</nav></main>")
    ) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    assert not result["success"]
    assert "no article content" in result["error"]


@pytest.mark.asyncio
async def test_mislabeled_non_html_body_is_not_accepted_as_an_article() -> None:
    async with mock_service(
        lambda request: html_response('{"error": "not a document"}')
    ) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    assert not result["success"]
    assert result["data"] is None
    assert "no HTML article markup" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [204, 206, 400, 403, 404, 500])
async def test_unsuccessful_or_partial_http_statuses_never_look_complete(status: int) -> None:
    async with mock_service(
        lambda request: httpx.Response(
            status, headers={"Content-Type": "text/html"}, text="<main>Partial content</main>"
        )
    ) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    assert not result["success"]
    assert result["data"] is None
    assert f"HTTP {status}" in result["error"]


@pytest.mark.asyncio
async def test_declared_oversize_is_rejected_before_streaming() -> None:
    stream = CountingStream([b"<main>Unconsumed</main>"])
    async with mock_service(
        lambda request: httpx.Response(
            200,
            headers={
                "Content-Type": "text/html",
                "Content-Length": str(MAX_DOCUMENTATION_BYTES + 1),
            },
            stream=stream,
        )
    ) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    assert not result["success"]
    assert result["data"] is None
    assert "exceeds" in result["error"]
    assert stream.reads == 0
    assert stream.closed


@pytest.mark.asyncio
async def test_undeclared_oversize_stops_the_stream_and_never_returns_partial_data() -> None:
    chunk = b"x" * (64 * 1024)
    stream = CountingStream([chunk] * (MAX_DOCUMENTATION_BYTES // len(chunk) + 4))
    async with mock_service(
        lambda request: httpx.Response(200, headers={"Content-Type": "text/html"}, stream=stream)
    ) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    assert not result["success"]
    assert result["data"] is None
    assert "exceeds" in result["error"]
    assert stream.reads == MAX_DOCUMENTATION_BYTES // len(chunk) + 1
    assert stream.reads < len(stream.chunks)
    assert stream.closed


@pytest.mark.asyncio
async def test_compressed_html_is_limited_by_decoded_size() -> None:
    compressed = gzip.compress(b"<main>" + b"x" * MAX_DOCUMENTATION_BYTES + b"</main>")
    stream = CountingStream([compressed])
    async with mock_service(
        lambda request: httpx.Response(
            200,
            headers={
                "Content-Type": "text/html",
                "Content-Encoding": "gzip",
                "Content-Length": str(len(compressed)),
            },
            stream=stream,
        )
    ) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    assert not result["success"]
    assert result["data"] is None
    assert "exceeds" in result["error"]
    assert stream.closed


@pytest.mark.asyncio
async def test_exact_download_limit_is_not_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    html = "<main><h1>Title</h1><p>Exactly the permitted size.</p></main>"
    limit = len(html.encode("utf-8"))
    monkeypatch.setattr("src.services.microsoft_learn.MAX_DOCUMENTATION_BYTES", limit)
    async with mock_service(lambda request: html_response(html)) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    assert result["success"]
    assert result["data"] is not None
    assert "Exactly the permitted size." in result["data"]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError, RuntimeError])
async def test_timeout_and_transport_failures_are_logged(
    error_type: type[Exception],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_type("offline test failure")

    async with mock_service(handler) as service:
        with capture_logs() as logs:
            result = await service.fetch_documentation_page(PAGE_URL)
    assert not result["success"]
    assert result["data"] is None
    assert error_type.__name__ in result["error"]
    assert "offline test failure" in result["error"]
    assert logs[-1]["event"] == "learn_documentation_fetch_failed"


@pytest.mark.asyncio
async def test_mid_stream_failure_closes_response_without_partial_evidence() -> None:
    stream = CountingStream([b"<main>Partial evidence"], httpx.ReadError("interrupted"))
    async with mock_service(
        lambda request: httpx.Response(200, headers={"Content-Type": "text/html"}, stream=stream)
    ) as service:
        result = await service.fetch_documentation_page(PAGE_URL)
    assert not result["success"]
    assert result["data"] is None
    assert "interrupted" in result["error"]
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("during_stream", [False, True])
async def test_cancellation_propagates_and_closes_open_streams(during_stream: bool) -> None:
    stream = CountingStream([b"<main>Partial"], asyncio.CancelledError())

    async def handler(request: httpx.Request) -> httpx.Response:
        if not during_stream:
            raise asyncio.CancelledError()
        return httpx.Response(200, headers={"Content-Type": "text/html"}, stream=stream)

    async with mock_service(handler) as service:
        with pytest.raises(asyncio.CancelledError):
            await service.fetch_documentation_page(PAGE_URL)
    if during_stream:
        assert stream.closed


@pytest.mark.asyncio
async def test_legacy_adapter_preserves_required_keys_and_limits_without_mutating_full_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page: DocumentationPage = {
        "title": "Complete page",
        "url": PAGE_URL,
        "requested_url": PAGE_URL,
        "content": "Complete text " * 500,
        "sections": [f"Section {index}" for index in range(20)],
        "links": [],
        "code_blocks": [],
        "visuals": [],
        "links_truncated": False,
    }
    service = MicrosoftLearnService()
    fetch = AsyncMock(return_value={"success": True, "data": page, "error": ""})
    monkeypatch.setattr(service, "fetch_documentation_page", fetch)
    result = await service.fetch_page_content(PAGE_URL, max_chars=40)
    assert result is not None
    assert set(result) == {"title", "url", "content", "sections", "visuals"}
    assert result["content"] == page["content"][:40] + "\n... (truncated)"
    assert len(result["sections"]) == 15
    assert len(page["sections"]) == 20
    assert len(page["content"]) > 3000
    assert service._client is None
    fetch.assert_awaited_once_with(PAGE_URL)
    fetch.return_value = {"success": False, "data": None, "error": "unavailable"}
    assert await service.fetch_page_content(PAGE_URL) is None


@pytest.mark.asyncio
async def test_legacy_adapter_exercises_safe_transport_and_returns_short_content_whole() -> None:
    async with mock_service(lambda request: html_response()) as service:
        result = await service.fetch_page_content(PAGE_URL)
    assert result is not None
    assert "Complete evidence." in result["content"]
    assert "truncated" not in result["content"]


@pytest.mark.asyncio
async def test_learn_more_adapter_filters_exact_hosts_deduplicates_and_handles_shorteners() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.host == "aka.ms":
            return httpx.Response(302, headers={"Location": "https://learn.microsoft.com/second"})
        if request.url.path == "/failed":
            return httpx.Response(503)
        return html_response("<main><h1>Document</h1><p>" + "Evidence " * 50 + "</p></main>")

    links = [
        {"url": "https://evil.example/learn.microsoft.com"},
        {"url": "https://aka.ms.evil.example/guide"},
        {"url": "http://learn.microsoft.com/unsafe"},
        {"url": "https://learn.microsoft.com:444/unsafe"},
        {"url": "https://user:secret@learn.microsoft.com/unsafe"},
        {"url": PAGE_URL + "?utm_source=one#first"},
        {"url": PAGE_URL + "?utm_source=two#second"},
        {"url": "https://aka.ms/second"},
        {"url": "https://learn.microsoft.com/failed"},
        {"url": "https://learn.microsoft.com/not-selected"},
    ]
    async with mock_service(handler) as service:
        results = await service.fetch_learn_more_contents(links, max_links=3, max_chars_per_page=60)
    assert len(results) == 2
    assert [result["url"] for result in results] == [
        PAGE_URL,
        "https://learn.microsoft.com/second",
    ]
    assert all(result["content"].endswith("\n... (truncated)") for result in results)
    assert set(requests) == {
        PAGE_URL,
        "https://aka.ms/second",
        "https://learn.microsoft.com/second",
        "https://learn.microsoft.com/failed",
    }


@pytest.mark.asyncio
async def test_empty_or_nonpositive_batch_does_not_fetch_and_child_cancellation_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MicrosoftLearnService()
    fetch = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(service, "fetch_page_content", fetch)
    assert await service.fetch_learn_more_contents([]) == []
    assert await service.fetch_learn_more_contents([{"url": PAGE_URL}], max_links=0) == []
    assert await service.fetch_learn_more_contents([{"url": PAGE_URL}], max_links=-1) == []
    assert not fetch.called
    with pytest.raises(asyncio.CancelledError):
        await service.fetch_learn_more_contents([{"url": PAGE_URL}])
