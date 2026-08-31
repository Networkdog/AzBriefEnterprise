"""Tests for immutable archive persistence and query behavior."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.agent.analyzer import AnalysisResult, RelevanceStatus, UrgencyLevel
from src.archive.models import (
    ArchiveAnalysisResultV1,
    ArchiveDocumentV1,
    ArchiveQuery,
    ArchiveSource,
)
from src.archive.service import ArchiveService, create_archive_id
from src.config import Settings
from src.rss.parser import AzureUpdate
from src.services.archive import (
    ArchiveConflictError,
    ArchiveIntegrityError,
    ArchiveStore,
    BlobArchiveStore,
    FileArchiveStore,
    _BlobListItem,
    _metadata_from_document,
    _summary_from_metadata,
    build_archive_store,
)

UTC = timezone.utc


def _settings(**overrides) -> Settings:
    values = {
        "azure_tenant_id": "00000000-0000-0000-0000-000000000000",
        "foundry_hosted_agent_name": "azbrief-analysis-hosted",
        "report_language": "ko",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _update(update_id: str = "570120", service: str = "Azure Kubernetes Service") -> AzureUpdate:
    return AzureUpdate(
        id=update_id,
        title=f"Update {update_id}",
        description="Description",
        link=f"https://azure.microsoft.com/updates?id={update_id}",
        published_date=datetime(2026, 8, 29, tzinfo=UTC),
        categories=["Compute"],
        azure_services=[service],
        update_type="General Availability",
        status=None,
    )


def _result(update_id: str = "570120", importance: str = "high") -> AnalysisResult:
    return AnalysisResult(
        update_id=update_id,
        update_title=f"Update {update_id}",
        relevance=RelevanceStatus.RELEVANT,
        urgency=UrgencyLevel.HIGH,
        importance=importance,
        impact_level="medium",
        job_relevance="high",
        one_line_summary=f"Summary {update_id}",
        relevance_reason="Relevant to the current environment.",
        affected_resources=[],
        impact_summary="Review required.",
        recommendations=[],
        reference_docs=[],
        should_notify=True,
    )


def _document(archive_id: str, update_id: str = "570120") -> ArchiveDocumentV1:
    return ArchiveDocumentV1(
        archive_id=archive_id,
        analyzed_at=datetime(2026, 8, 30, 12, tzinfo=UTC),
        source=ArchiveSource.API_ANALYZE,
        update=_update(update_id).to_dict(),
        result=ArchiveAnalysisResultV1.model_validate(
            _result(update_id).model_dump(mode="json", exclude={"job_relevance"})
        ),
    )


class TestArchiveIds:
    def test_newer_timestamp_sorts_first(self):
        older = create_archive_id(datetime(2026, 8, 29, tzinfo=UTC), "1" * 32)
        newer = create_archive_id(datetime(2026, 8, 30, tzinfo=UTC), "2" * 32)
        assert newer < older


class TestStoreSelection:
    def test_blob_wins_over_file(self, monkeypatch):
        monkeypatch.setattr(
            "src.config.get_settings",
            lambda: _settings(
                archive_blob_container_url="https://acct.blob.core.windows.net/archive",
                archive_file_path="data/archive",
            ),
        )
        assert isinstance(build_archive_store(), BlobArchiveStore)

    def test_file_is_local_fallback(self, monkeypatch):
        monkeypatch.setattr(
            "src.config.get_settings",
            lambda: _settings(archive_file_path="data/archive"),
        )
        assert isinstance(build_archive_store(), FileArchiveStore)


class TestInertStore:
    @pytest.mark.asyncio
    async def test_unconfigured_archive_is_an_explicit_noop(self):
        store = ArchiveStore()
        assert store.configured is False
        assert (
            await store.put(_document(create_archive_id(datetime.now(UTC), "1" * 32)))
        ).archived is False
        assert await store.get("8211694095999-0123456789abcdef0123456789abcdef") is None
        assert (await store.list(ArchiveQuery())).items == []


class TestFileArchiveStore:
    @pytest.mark.asyncio
    async def test_put_get_and_idempotent_retry(self, tmp_path):
        store = FileArchiveStore(str(tmp_path))
        document = _document("8211694095999-0123456789abcdef0123456789abcdef")

        first = await store.put(document)
        second = await store.put(document)

        assert first == second
        assert await store.get(document.archive_id) == document

    @pytest.mark.asyncio
    async def test_same_id_with_different_content_is_rejected(self, tmp_path):
        store = FileArchiveStore(str(tmp_path))
        document = _document("8211694095999-0123456789abcdef0123456789abcdef")
        await store.put(document)
        changed = ArchiveDocumentV1.model_validate(
            {
                **document.model_dump(mode="json"),
                "result": {
                    **document.result.model_dump(mode="json"),
                    "one_line_summary": "Changed",
                },
            }
        )

        with pytest.raises(ArchiveConflictError):
            await store.put(changed)

    @pytest.mark.asyncio
    async def test_list_is_newest_first_and_cursor_has_no_duplicates(self, tmp_path):
        store = FileArchiveStore(str(tmp_path))
        moments = [datetime(2026, 8, 28, tzinfo=UTC) + timedelta(days=index) for index in range(3)]
        for index, moment in enumerate(moments):
            archive_id = create_archive_id(moment, f"{index + 1:032x}")
            await store.put(_document(archive_id, update_id=f"u{index}"))

        first = await store.list(ArchiveQuery(limit=2))
        second = await store.list(ArchiveQuery(limit=2, cursor=first.next_cursor))

        assert [item.update_id for item in first.items] == ["u2", "u1"]
        assert [item.update_id for item in second.items] == ["u0"]
        assert first.has_more is True
        assert second.has_more is False

    @pytest.mark.asyncio
    async def test_list_filters_without_loading_results_into_the_api(self, tmp_path):
        store = FileArchiveStore(str(tmp_path))
        first_id = create_archive_id(datetime(2026, 8, 30, tzinfo=UTC), "1" * 32)
        second_id = create_archive_id(datetime(2026, 8, 29, tzinfo=UTC), "2" * 32)
        await store.put(_document(first_id, update_id="aks"))
        sql_document = _document(second_id, update_id="sql")
        sql_payload = sql_document.model_dump(mode="json")
        sql_payload["update"]["azure_services"] = ["Azure SQL Database"]
        await store.put(ArchiveDocumentV1.model_validate(sql_payload))

        page = await store.list(ArchiveQuery(q="sql", service="SQL"))

        assert [item.update_id for item in page.items] == ["sql"]

    @pytest.mark.asyncio
    async def test_listing_cache_sees_new_writes_without_rescanning(self, tmp_path):
        store = FileArchiveStore(str(tmp_path))
        first = _document(
            create_archive_id(datetime(2026, 8, 29, tzinfo=UTC), "1" * 32),
            "first",
        )
        second = _document(
            create_archive_id(datetime(2026, 8, 30, tzinfo=UTC), "2" * 32),
            "second",
        )
        await store.put(first)
        assert [item.update_id for item in (await store.list(ArchiveQuery())).items] == ["first"]

        await store.put(second)
        page = await store.list(ArchiveQuery())

        assert [item.update_id for item in page.items] == ["second", "first"]


class TestArchiveService:
    @pytest.mark.asyncio
    async def test_service_builds_canonical_document_without_subscriber_data(self, tmp_path):
        store = FileArchiveStore(str(tmp_path))
        moment = datetime(2026, 8, 30, 12, tzinfo=UTC)
        service = ArchiveService(
            store=store,
            settings=_settings(),
            clock=lambda: moment,
            id_factory=lambda: "a" * 32,
        )

        receipt = await service.archive_analysis(
            _update(),
            _result(),
            ArchiveSource.SCHEDULED_DIGEST,
            run_id="run-1",
        )
        document = await service.get(receipt.archive_id)

        assert receipt.archived is True
        assert document is not None
        assert document.run_id == "run-1"
        assert document.hosted_agent_name == "azbrief-analysis-hosted"
        assert not ({"subscriber", "recipient", "email"} & set(document.model_dump()))

    def test_detail_url_requires_the_archive_ui_to_be_enabled(self):
        disabled = ArchiveService(
            store=ArchiveStore(),
            settings=_settings(
                archive_base_url="https://azbrief.example",
                archive_ui_enabled=False,
            ),
        )
        enabled = ArchiveService(
            store=ArchiveStore(),
            settings=_settings(
                archive_base_url="https://azbrief.example",
                archive_ui_enabled=True,
            ),
        )
        archive_id = "8211694095999-0123456789abcdef0123456789abcdef"
        assert disabled.detail_url(archive_id) == ""
        assert enabled.detail_url(archive_id).endswith(f"/archive/{archive_id}")


class TestBlobMetadata:
    def test_metadata_round_trip_preserves_search_projection(self):
        document = _document("8211694095999-0123456789abcdef0123456789abcdef")
        payload = document.model_dump_json().encode("utf-8")

        summary = _summary_from_metadata(_metadata_from_document(document, payload))
        metadata = _metadata_from_document(document, payload)

        assert summary.archive_id == document.archive_id
        assert summary.title == document.update.title
        assert summary.azure_services == document.update.azure_services
        assert summary.one_line_summary == document.result.one_line_summary
        assert "job_relevance" not in metadata
        assert "job_relevance" not in summary.model_dump()

    def test_services_metadata_remains_valid_json_when_projection_is_truncated(self):
        document = _document("8211694095999-0123456789abcdef0123456789abcdef")
        payload = document.model_dump(mode="json")
        payload["update"]["azure_services"] = [
            f"Service {index} " + "x" * 100 for index in range(30)
        ]
        document = ArchiveDocumentV1.model_validate(payload)

        metadata = _metadata_from_document(document, document.model_dump_json().encode("utf-8"))
        summary = _summary_from_metadata(metadata)

        assert metadata["projection_truncated"] == "true"
        assert summary.azure_services
        assert len(summary.azure_services) < len(document.update.azure_services)


class TestBlobArchiveStore:
    @pytest.mark.asyncio
    async def test_put_is_create_only_and_writes_search_metadata(self):
        store = BlobArchiveStore("https://acct.blob.core.windows.net/azbrief-archive")
        document = _document("8211694095999-0123456789abcdef0123456789abcdef")
        captured = {}

        async def _put_once(object_name, payload, metadata):
            captured.update(object_name=object_name, payload=payload, metadata=metadata)
            return httpx.Response(201, request=httpx.Request("PUT", "https://example.test"))

        store._put_once = _put_once  # type: ignore[method-assign]
        receipt = await store.put(document)

        assert receipt.archived is True
        assert captured["object_name"].endswith(f"{document.archive_id}.json")
        assert captured["metadata"]["archive_id"] == document.archive_id
        assert captured["metadata"]["payload_sha256"]

    @pytest.mark.asyncio
    async def test_precondition_failure_is_idempotent_when_bytes_match(self):
        store = BlobArchiveStore("https://acct.blob.core.windows.net/azbrief-archive")
        document = _document("8211694095999-0123456789abcdef0123456789abcdef")
        payload = document.model_dump_json().encode("utf-8")

        async def _put_once(*_args, **_kwargs):
            return httpx.Response(412, request=httpx.Request("PUT", "https://example.test"))

        async def _read_raw(_object_name):
            return payload, {}

        store._put_once = _put_once  # type: ignore[method-assign]
        store._read_raw = _read_raw  # type: ignore[method-assign]

        assert (await store.put(document)).archived is True

    @pytest.mark.asyncio
    async def test_get_rejects_payload_hash_mismatch(self):
        store = BlobArchiveStore("https://acct.blob.core.windows.net/azbrief-archive")
        document = _document("8211694095999-0123456789abcdef0123456789abcdef")

        async def _read_raw(_object_name):
            return document.model_dump_json().encode("utf-8"), {"payload_sha256": "wrong"}

        store._read_raw = _read_raw  # type: ignore[method-assign]

        with pytest.raises(ArchiveIntegrityError, match="hash"):
            await store.get(document.archive_id)

    @pytest.mark.asyncio
    async def test_list_uses_metadata_without_fetching_full_documents(self):
        store = BlobArchiveStore("https://acct.blob.core.windows.net/azbrief-archive")
        documents = [
            _document(create_archive_id(datetime(2026, 8, 30, tzinfo=UTC), "1" * 32), "aks"),
            _document(create_archive_id(datetime(2026, 8, 29, tzinfo=UTC), "2" * 32), "sql"),
        ]
        sql_payload = documents[1].model_dump(mode="json")
        sql_payload["update"]["azure_services"] = ["Azure SQL Database"]
        documents[1] = ArchiveDocumentV1.model_validate(sql_payload)
        listed = [
            _BlobListItem(
                name=f"entries/{document.archive_id}.json",
                metadata=_metadata_from_document(
                    document,
                    document.model_dump_json().encode("utf-8"),
                ),
            )
            for document in documents
        ]

        async def _list_once(_marker, _start_from):
            return listed, ""

        store._list_once = _list_once  # type: ignore[method-assign]
        page = await store.list(ArchiveQuery(service="sql"))

        assert [item.update_id for item in page.items] == ["sql"]

    @pytest.mark.asyncio
    async def test_truncated_projection_loads_document_for_complete_search(self):
        store = BlobArchiveStore("https://acct.blob.core.windows.net/azbrief-archive")
        document = _document(
            create_archive_id(datetime(2026, 8, 30, tzinfo=UTC), "3" * 32),
            "long",
        )
        payload = document.model_dump(mode="json")
        payload["update"]["title"] = "x" * 900 + " hidden-needle"
        document = ArchiveDocumentV1.model_validate(payload)
        raw = document.model_dump_json().encode("utf-8")
        metadata = _metadata_from_document(document, raw)
        get_calls = []

        async def _list_once(_marker, _start_from):
            return [
                _BlobListItem(
                    name=f"entries/{document.archive_id}.json",
                    metadata=metadata,
                )
            ], ""

        async def _get(archive_id):
            get_calls.append(archive_id)
            return document

        store._list_once = _list_once  # type: ignore[method-assign]
        store.get = _get  # type: ignore[method-assign]
        page = await store.list(ArchiveQuery(q="hidden-needle"))

        assert [item.archive_id for item in page.items] == [document.archive_id]
        assert page.items[0].title.endswith("hidden-needle")
        assert get_calls == [document.archive_id]

    @pytest.mark.asyncio
    async def test_last_storage_page_does_not_leave_a_false_next_cursor(self):
        store = BlobArchiveStore("https://acct.blob.core.windows.net/azbrief-archive")
        first = _document(
            create_archive_id(datetime(2026, 8, 30, tzinfo=UTC), "1" * 32),
            "first",
        )
        second = _document(
            create_archive_id(datetime(2026, 8, 29, tzinfo=UTC), "2" * 32),
            "second",
        )
        calls = []

        async def _list_once(marker, _start_from):
            calls.append(marker)
            document = first if not marker else second
            next_marker = "page-2" if not marker else ""
            return [
                _BlobListItem(
                    name=f"entries/{document.archive_id}.json",
                    metadata=_metadata_from_document(
                        document,
                        document.model_dump_json().encode("utf-8"),
                    ),
                )
            ], next_marker

        store._list_once = _list_once  # type: ignore[method-assign]
        page = await store.list(ArchiveQuery(limit=10))

        assert [item.update_id for item in page.items] == ["first", "second"]
        assert calls == ["", "page-2"]
        assert page.has_more is False
        assert page.next_cursor == ""
