"""Crawl all Azure Update history from the Release Communications API.

Iterates through all pages by adjusting the 'skip' query parameter,
strips HTML from descriptions, and saves all records into a single
JSONL file (one JSON object per line) for easy reference.

Usage:
    python -m scripts.crawl_azure_updates
    python -m scripts.crawl_azure_updates --output data/azure_updates.jsonl
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

API_BASE = "https://www.microsoft.com/releasecommunications/api/v2/azure"
PAGE_SIZE = 100  # max items per request
REQUEST_TIMEOUT = 30  # seconds
DELAY_BETWEEN_REQUESTS = 0.3  # polite crawling delay


def clean_html(html: str) -> str:
    """Strip HTML tags and normalise whitespace."""
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def normalize_id(raw_id) -> str:
    """Coerce the API ``id`` to a clean string identifier.

    The Azure Release Communications API returns two id formats depending on
    record age: a numeric id (e.g. ``"466724"``) for records created on or
    after 2024-10-31, and a slug id (e.g. ``"2021-azure-hitrust"``) for older
    records. Both are valid canonical identifiers — slug records have no
    numeric equivalent in the source API. This normaliser only trims
    whitespace and guarantees a string; it does not alter the id format.

    Args:
        raw_id: The raw ``id`` value from the API record.

    Returns:
        The normalised id as a stripped string (empty string if missing).
    """
    if raw_id is None:
        return ""
    return str(raw_id).strip()


def fetch_page(client: httpx.Client, skip: int) -> dict:
    """Fetch a single page from the Azure Updates API."""
    params = {
        "$count": "true",
        "top": str(PAGE_SIZE),
        "skip": str(skip),
    }
    resp = client.get(API_BASE, params=params)
    resp.raise_for_status()
    return resp.json()


def simplify_record(raw: dict) -> dict:
    """Keep only useful fields and clean HTML in description."""
    return {
        "id": normalize_id(raw.get("id")),
        "title": raw.get("title", ""),
        "description": clean_html(raw.get("description", "")),
        "status": raw.get("status", ""),
        "created": raw.get("created", ""),
        "modified": raw.get("modified", ""),
        "products": raw.get("products", []),
        "productCategories": raw.get("productCategories", []),
        "tags": raw.get("tags", []),
        "generalAvailabilityDate": raw.get("generalAvailabilityDate"),
        "previewAvailabilityDate": raw.get("previewAvailabilityDate"),
        "availabilities": raw.get("availabilities", []),
    }


def main():
    parser = argparse.ArgumentParser(description="Crawl all Azure Update history")
    parser.add_argument(
        "--output",
        "-o",
        default="data/azure_updates_history.jsonl",
        help="Output file path (default: data/azure_updates_history.jsonl)",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Crawling Azure Updates API → {output_path}")

    headers = {
        "User-Agent": "AzBrief-Crawler/1.0 (Azure Update Intelligence Agent)",
        "Accept": "application/json",
    }

    total_count = None
    all_records: list[dict] = []
    skip = 0

    with httpx.Client(timeout=REQUEST_TIMEOUT, headers=headers) as client:
        while True:
            t0 = time.time()
            try:
                data = fetch_page(client, skip)
            except httpx.HTTPStatusError as e:
                print(f"\nHTTP error at skip={skip}: {e.response.status_code}")
                break
            except Exception as e:
                print(f"\nError at skip={skip}: {e}")
                break

            elapsed = time.time() - t0

            if total_count is None:
                total_count = data.get("@odata.count", 0)
                print(f"Total updates: {total_count}")

            page_items = data.get("value", [])
            if not page_items:
                break

            for raw in page_items:
                record = simplify_record(raw)
                if not record["id"]:
                    print(f"\n  WARNING: record with empty id " f"(title={record['title'][:60]!r})")
                all_records.append(record)

            fetched = len(all_records)
            pct = (fetched / total_count * 100) if total_count else 0
            print(
                f"\r  Fetched {fetched:>5}/{total_count} ({pct:5.1f}%) "
                f"skip={skip} [{elapsed:.1f}s]",
                end="",
                flush=True,
            )

            skip += PAGE_SIZE
            if total_count and skip >= total_count:
                break

            time.sleep(DELAY_BETWEEN_REQUESTS)

    print(f"\n\nTotal records fetched: {len(all_records)}")

    # Write as JSONL (one JSON object per line)
    with open(output_path, "w", encoding="utf-8") as f:
        for record in all_records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    file_size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Saved to {output_path} ({file_size_mb:.1f} MB, {len(all_records)} records)")

    # Also write a summary JSON for quick reference
    summary_path = output_path.with_suffix(".summary.json")
    # The API does not return records strictly newest-first, so derive the
    # date range from the actual min/max of the ``created`` field.
    created_dates = [rec["created"] for rec in all_records if rec.get("created")]
    summary = {
        "total_records": len(all_records),
        "crawled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_api": API_BASE,
        "date_range": {
            "earliest": min(created_dates) if created_dates else None,
            "latest": max(created_dates) if created_dates else None,
        },
        "id_format_distribution": {"numeric": 0, "slug": 0, "empty": 0},
        "status_distribution": {},
        "top_products": {},
    }
    for rec in all_records:
        rid = rec.get("id", "")
        if not rid:
            summary["id_format_distribution"]["empty"] += 1
        elif rid.isdigit():
            summary["id_format_distribution"]["numeric"] += 1
        else:
            summary["id_format_distribution"]["slug"] += 1
        status = rec.get("status", "Unknown")
        summary["status_distribution"][status] = summary["status_distribution"].get(status, 0) + 1
        for product in rec.get("products", []):
            summary["top_products"][product] = summary["top_products"].get(product, 0) + 1

    # Sort top products by count
    summary["top_products"] = dict(
        sorted(summary["top_products"].items(), key=lambda x: x[1], reverse=True)[:50]
    )

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"Summary saved to {summary_path}")


if __name__ == "__main__":
    main()
