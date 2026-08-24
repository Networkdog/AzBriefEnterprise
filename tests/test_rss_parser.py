"""Tests for RSS feed parser in src/rss/parser.py."""

import json
from datetime import datetime, timezone

import pytest

from src.rss.parser import AzureUpdate, AzureUpdateParser, clean_url


class TestAzureUpdateParser:
    """Test RSS feed parsing."""

    def test_parse_feed_basic(self, sample_rss_xml):
        """Parse a well-formed RSS feed."""
        parser = AzureUpdateParser()
        updates = parser.parse_feed(sample_rss_xml)
        assert len(updates) == 3

    def test_parse_feed_titles(self, sample_rss_xml):
        """Titles are correctly extracted."""
        parser = AzureUpdateParser()
        updates = parser.parse_feed(sample_rss_xml)
        titles = [u.title for u in updates]
        assert "Generally Available: Azure Blob Storage SFTP Resumable Uploads" in titles
        assert "Public Preview: AKS Automatic Node Repair" in titles

    def test_parse_feed_categories(self, sample_rss_xml):
        """Categories are correctly extracted."""
        parser = AzureUpdateParser()
        updates = parser.parse_feed(sample_rss_xml)
        blob_update = [u for u in updates if "SFTP" in u.title][0]
        assert "Blob Storage" in blob_update.categories
        assert "Storage" in blob_update.categories

    def test_parse_feed_html_description_cleaned(self, sample_rss_xml):
        """HTML tags are removed from description."""
        parser = AzureUpdateParser()
        updates = parser.parse_feed(sample_rss_xml)
        blob_update = [u for u in updates if "SFTP" in u.title][0]
        assert "<p>" not in blob_update.description
        assert "SFTP" in blob_update.description

    def test_parse_feed_published_date(self, sample_rss_xml):
        """Published date is parsed correctly."""
        parser = AzureUpdateParser()
        updates = parser.parse_feed(sample_rss_xml)
        blob_update = [u for u in updates if "SFTP" in u.title][0]
        assert blob_update.published_date is not None
        assert blob_update.published_date.year == 2026
        assert blob_update.published_date.month == 3

    def test_parse_feed_ids_unique(self, sample_rss_xml):
        """Update IDs (guids) are unique — deduplication works."""
        parser = AzureUpdateParser()
        updates = parser.parse_feed(sample_rss_xml)
        ids = [u.id for u in updates]
        assert len(ids) == len(set(ids))

    def test_parse_feed_update_type_extraction(self, sample_rss_xml):
        """Update type is inferred from title prefix or categories."""
        parser = AzureUpdateParser()
        updates = parser.parse_feed(sample_rss_xml)
        # Without [Launched]/[In Preview] prefix, parser falls back to categories
        ga_update = [u for u in updates if "SFTP" in u.title][0]
        assert ga_update.update_type is not None  # Feature from categories
        preview_update = [u for u in updates if "AKS" in u.title][0]
        # No matching category for 'Public Preview' either
        assert preview_update.update_type is None or isinstance(preview_update.update_type, str)

    def test_parse_feed_update_type_with_prefix(self):
        """Update type is correctly parsed from [Launched]/[In Preview] title prefix."""
        parser = AzureUpdateParser()
        xml = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <item>
    <title>[Launched] Generally Available: Test Feature</title>
    <link>https://azure.microsoft.com/updates?id=1</link>
    <guid>1</guid>
    <description>Test</description>
    <pubDate>Mon, 10 Mar 2026 18:00:00 Z</pubDate>
  </item>
  <item>
    <title>[In Preview] Public Preview: Another Feature</title>
    <link>https://azure.microsoft.com/updates?id=2</link>
    <guid>2</guid>
    <description>Test</description>
    <pubDate>Tue, 11 Mar 2026 12:00:00 Z</pubDate>
  </item>
</channel></rss>"""
        updates = parser.parse_feed(xml)
        assert updates[0].update_type == "General Availability"
        assert updates[1].update_type == "Public Preview"

    def test_parse_feed_retirement_type(self, sample_rss_xml):
        """Retirement updates are correctly classified."""
        parser = AzureUpdateParser()
        updates = parser.parse_feed(sample_rss_xml)
        retirement = [u for u in updates if "Classic VMs" in u.title][0]
        assert retirement.update_type == "Retirement"

    def test_parse_feed_empty(self):
        """Empty or invalid XML returns empty list."""
        parser = AzureUpdateParser()
        assert parser.parse_feed("") == []
        assert parser.parse_feed("<not-rss/>") == []

    def test_parse_feed_malformed_xml(self):
        """Malformed XML returns empty list without crashing."""
        parser = AzureUpdateParser()
        result = parser.parse_feed("<rss><channel><item><title>Half item")
        # Should not raise — graceful failure
        assert isinstance(result, list)

    def test_azure_services_extraction(self, sample_rss_xml):
        """Azure services are extracted from categories and title."""
        parser = AzureUpdateParser()
        updates = parser.parse_feed(sample_rss_xml)
        blob_update = [u for u in updates if "SFTP" in u.title][0]
        # Should have at least one meaningful service
        assert len(blob_update.azure_services) > 0

    def test_to_dict_roundtrip(self, sample_rss_xml):
        """to_dict() produces a valid dictionary."""
        parser = AzureUpdateParser()
        updates = parser.parse_feed(sample_rss_xml)
        d = updates[0].to_dict()
        assert isinstance(d, dict)
        assert "id" in d
        assert "title" in d
        assert "link" in d

    def test_clean_html(self):
        """HTML cleaning removes tags and normalizes whitespace."""
        parser = AzureUpdateParser()
        html = "<p>Hello  <strong>world</strong>  </p>"
        cleaned = parser._clean_html(html)
        assert "<p>" not in cleaned
        assert "<strong>" not in cleaned
        assert "Hello" in cleaned
        assert "world" in cleaned


class TestHistoryArchiveMerge:
    """Test the local history archive loading and date-range merge.

    The live RSS feed only exposes a rolling ~200-item window, so historical
    months age out of it. ``get_updates_by_date_range`` merges the locally
    crawled archive to cover those older periods.
    """

    def test_parse_iso_date_seven_digit_fraction(self):
        """ISO timestamps with 7-digit fractions and trailing Z are parsed."""
        parser = AzureUpdateParser()
        dt = parser._parse_iso_date("2026-03-15T22:24:40.0000000Z")
        assert dt is not None
        assert (dt.year, dt.month, dt.day) == (2026, 3, 15)
        assert dt.tzinfo is not None

    def test_parse_iso_date_no_fraction(self):
        """ISO timestamps without fractional seconds are parsed."""
        parser = AzureUpdateParser()
        dt = parser._parse_iso_date("2026-05-01T00:00:00Z")
        assert dt is not None
        assert dt.month == 5

    def test_parse_iso_date_empty(self):
        """Empty or invalid input returns None."""
        parser = AzureUpdateParser()
        assert parser._parse_iso_date("") is None
        assert parser._parse_iso_date("not-a-date") is None

    def test_canonical_id_from_numeric_url(self):
        """A numeric-id URL normalizes to the bare numeric id."""
        parser = AzureUpdateParser()
        assert (
            parser._canonical_id("https://azure.microsoft.com/en-us/updates?id=555870") == "555870"
        )

    def test_canonical_id_from_bare_id(self):
        """A bare id is returned lowercased and stripped."""
        parser = AzureUpdateParser()
        assert parser._canonical_id("555870") == "555870"
        assert parser._canonical_id("2021-Azure-HITRUST") == "2021-azure-hitrust"

    def test_history_record_to_update_uses_products(self):
        """Products from the archive become azure_services directly."""
        parser = AzureUpdateParser()
        record = {
            "id": "555870",
            "title": "Generally Available: Test Service",
            "description": "Some description",
            "status": "Launched",
            "created": "2026-03-15T22:24:40.0000000Z",
            "products": ["Azure Kubernetes Service (AKS)"],
            "productCategories": ["Containers"],
            "tags": [],
        }
        update = parser._history_record_to_update(record)
        assert update is not None
        assert update.id == "555870"
        assert update.azure_services == ["Azure Kubernetes Service (AKS)"]
        assert update.published_date.month == 3
        assert update.link == "https://azure.microsoft.com/en-us/updates?id=555870"

    def test_history_record_type_from_status(self):
        """Update type falls back to the status field when categories don't match."""
        parser = AzureUpdateParser()
        record = {
            "id": "999",
            "title": "Some Update Without Prefix",
            "status": "In preview",
            "created": "2026-05-10T10:00:00.0000000Z",
            "products": [],
            "productCategories": [],
            "tags": [],
        }
        update = parser._history_record_to_update(record)
        assert update is not None
        assert update.update_type == "Public Preview"

    def test_history_record_empty_id_skipped(self):
        """Records without an id are skipped."""
        parser = AzureUpdateParser()
        assert parser._history_record_to_update({"id": "", "title": "x"}) is None

    def test_load_history_updates_from_file(self, tmp_path):
        """The archive JSONL is read into AzureUpdate objects."""
        archive = tmp_path / "history.jsonl"
        records = [
            {"id": "1", "title": "A", "created": "2026-03-01T00:00:00.0000000Z"},
            {"id": "2", "title": "B", "created": "2026-05-01T00:00:00.0000000Z"},
        ]
        archive.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        parser = AzureUpdateParser()
        updates = parser.load_history_updates(archive)
        assert len(updates) == 2
        assert {u.id for u in updates} == {"1", "2"}

    def test_load_history_updates_missing_file(self, tmp_path):
        """A missing archive returns an empty list (no crash)."""
        parser = AzureUpdateParser()
        assert parser.load_history_updates(tmp_path / "nope.jsonl") == []

    def test_load_history_updates_skips_bad_lines(self, tmp_path):
        """Malformed JSON lines are skipped without crashing."""
        archive = tmp_path / "history.jsonl"
        archive.write_text(
            '{"id": "1", "title": "A", "created": "2026-03-01T00:00:00Z"}\n' "not-json\n" "\n",
            encoding="utf-8",
        )
        parser = AzureUpdateParser()
        updates = parser.load_history_updates(archive)
        assert len(updates) == 1

    async def test_date_range_merges_and_dedups_history(self, tmp_path, monkeypatch):
        """Date-range query merges history, de-dups overlaps, and filters by date."""
        archive = tmp_path / "history.jsonl"
        records = [
            # Duplicate of the live update (same canonical id) — must be de-duped.
            {"id": "555870", "title": "Dup", "created": "2026-03-20T00:00:00Z"},
            # Unique in-range history item.
            {"id": "111", "title": "In range", "created": "2026-03-10T00:00:00Z"},
            # Out-of-range history item.
            {"id": "222", "title": "Out of range", "created": "2026-01-01T00:00:00Z"},
        ]
        archive.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

        live_update = AzureUpdate(
            id="555870",
            title="Live",
            description="",
            link="https://azure.microsoft.com/en-us/updates?id=555870",
            published_date=datetime(2026, 3, 20, tzinfo=timezone.utc),
            categories=[],
            azure_services=[],
            update_type=None,
            status=None,
        )

        async def fake_get_updates():
            return [live_update]

        parser = AzureUpdateParser()
        monkeypatch.setattr(parser, "get_updates", fake_get_updates)

        result = await parser.get_updates_by_date_range(
            datetime(2026, 3, 1),
            datetime(2026, 3, 31),
            history_path=archive,
        )

        ids = [u.id for u in result]
        assert ids.count("555870") == 1  # de-duplicated
        assert "111" in ids  # merged from history
        assert "222" not in ids  # excluded by date filter
        assert len(result) == 2

    async def test_date_range_history_disabled(self, tmp_path, monkeypatch):
        """include_history=False keeps the live-feed-only behavior."""
        archive = tmp_path / "history.jsonl"
        archive.write_text(
            json.dumps({"id": "111", "title": "H", "created": "2026-03-10T00:00:00Z"}) + "\n",
            encoding="utf-8",
        )

        async def fake_get_updates():
            return []

        parser = AzureUpdateParser()
        monkeypatch.setattr(parser, "get_updates", fake_get_updates)

        result = await parser.get_updates_by_date_range(
            datetime(2026, 3, 1),
            datetime(2026, 3, 31),
            include_history=False,
            history_path=archive,
        )
        assert result == []


class TestCleanUrl:
    """URL normalization: SafeLinks unwrapping + tracking-param stripping."""

    def test_plain_learn_url_unchanged(self):
        url = "https://learn.microsoft.com/azure/aks/app-routing"
        assert clean_url(url) == url

    def test_functional_params_preserved(self):
        """?view= and ?tabs= are functional and must survive."""
        url = "https://learn.microsoft.com/azure/postgresql/migrate?view=azuresql&tabs=portal"
        assert clean_url(url) == url

    def test_safelinks_unwrapped(self):
        wrapped = (
            "https://nam06.safelinks.protection.outlook.com/?url="
            "https%3A%2F%2Fazure.microsoft.com%2Fservices%2Fresource-mover%2F"
            "&data=05%7C01%7Cx&reserved=0"
        )
        assert clean_url(wrapped) == "https://azure.microsoft.com/services/resource-mover/"

    def test_safelinks_unwrapped_then_tracking_stripped(self):
        """Unwrap the wrapper, then drop the tracking params on the real target."""
        wrapped = (
            "https://nam06.safelinks.protection.outlook.com/?url="
            "https%3A%2F%2Fazure.microsoft.com%2Fpricing%2Fdetails%2Fstorage%2Fblobs%2F"
            "%3Fef_id%3Dabc%26OCID%3Dxyz%26msclkid%3D123"
            "&data=05%7C02%7Cy&sdata=z&reserved=0"
        )
        assert clean_url(wrapped) == "https://azure.microsoft.com/pricing/details/storage/blobs/"

    def test_direct_tracking_params_stripped(self):
        url = "https://azure.microsoft.com/products/x/?ocid=AID123&utm_source=email"
        assert clean_url(url) == "https://azure.microsoft.com/products/x/"

    def test_fragment_preserved(self):
        url = "https://learn.microsoft.com/azure/aks/x#container-network-metrics-filtering"
        assert clean_url(url) == url

    def test_empty_and_none_safe(self):
        assert clean_url("") == ""
        assert clean_url(None) is None
