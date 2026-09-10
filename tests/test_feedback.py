"""Feedback contract and collection tests."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.feedback.models import FeedbackCategory, FeedbackReceipt, FeedbackRequest
from src.feedback.page import render_feedback_page
from src.feedback.router import router
from src.feedback.service import FeedbackService
from src.services.feedback import FileFeedbackStore

UTC = timezone.utc


def test_feedback_request_normalizes_valid_input():
    request = FeedbackRequest(
        category="report_context",
        subject="  Add our maintenance window  ",
        details="  Apply the Sunday 02:00 UTC maintenance window to future reports.  ",
        contact_email=" OWNER@EXAMPLE.COM ",
        report_reference=" update:570120 ",
        language="ko",
    )

    assert request.category == FeedbackCategory.REPORT_CONTEXT
    assert request.subject == "Add our maintenance window"
    assert request.contact_email == "owner@example.com"
    assert request.report_reference == "update:570120"


@pytest.mark.parametrize(
    "field,value",
    [
        ("category", "other"),
        ("contact_email", "not-an-email"),
        ("subject", "line one\nline two"),
    ],
)
def test_feedback_request_rejects_invalid_fields(field, value):
    payload = {
        "category": "bug",
        "subject": "Something failed",
        "details": "The report page failed after I selected a report.",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(payload)


def test_feedback_request_rejects_unknown_fields():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        FeedbackRequest.model_validate(
            {
                "category": "improvement",
                "subject": "Improve filtering",
                "details": "Please add a filter for reports that need action.",
                "unexpected": True,
            }
        )


@pytest.mark.asyncio
async def test_file_feedback_store_persists_create_only_document(tmp_path):
    store = FileFeedbackStore(str(tmp_path))
    service = FeedbackService(
        store=store,
        settings=SimpleNamespace(feedback_ui_enabled=False, feedback_base_url=None),
        clock=lambda: datetime(2026, 9, 6, 1, 2, 3, tzinfo=UTC),
        id_factory=lambda: "0123456789abcdef0123456789abcdef",
    )

    receipt = await service.submit(
        FeedbackRequest(
            category="bug",
            subject="Digest link is broken",
            details="The first report link returns an unexpected not-found response.",
            contact_email="owner@example.com",
            report_reference="update:570120",
            language="ko",
        )
    )

    paths = list(tmp_path.rglob("*.json"))
    assert receipt.feedback_id.startswith("20260906T010203")
    assert len(paths) == 1
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1"
    assert payload["category"] == "bug"
    assert payload["report_reference"] == "update:570120"
    assert "website" not in payload


@pytest.mark.asyncio
async def test_maximum_length_multibyte_details_fit_storage_contract(tmp_path):
    service = FeedbackService(
        store=FileFeedbackStore(str(tmp_path)),
        settings=SimpleNamespace(feedback_ui_enabled=False, feedback_base_url=None),
    )

    receipt = await service.submit(
        FeedbackRequest(
            category="report_context",
            subject="한글 컨텍스트 경계 검증",
            details="가" * 8_000,
        )
    )

    assert receipt.accepted is True
    assert len(list(tmp_path.rglob("*.json"))) == 1


@pytest.mark.asyncio
async def test_feedback_is_persisted_before_notification(tmp_path):
    store = FileFeedbackStore(str(tmp_path))
    observed = []

    async def notify(submission):
        observed.append((submission, len(list(tmp_path.rglob("*.json")))))
        return True

    service = FeedbackService(
        store=store,
        settings=SimpleNamespace(feedback_ui_enabled=False, feedback_base_url=None),
        notifier=notify,
    )

    receipt = await service.submit(
        FeedbackRequest(
            category="improvement",
            subject="Add a compact comparison",
            details="Compare the current setting with the recommended setting.",
        )
    )

    assert receipt.notification_sent is True
    assert observed[0][0].feedback_id == receipt.feedback_id
    assert observed[0][1] == 1


@pytest.mark.asyncio
async def test_notification_failure_does_not_lose_persisted_feedback(tmp_path):
    async def fail(_submission):
        raise RuntimeError("mail unavailable")

    service = FeedbackService(
        store=FileFeedbackStore(str(tmp_path)),
        settings=SimpleNamespace(feedback_ui_enabled=False, feedback_base_url=None),
        notifier=fail,
    )

    receipt = await service.submit(
        FeedbackRequest(
            category="bug",
            subject="Report did not render",
            details="The report body was blank after opening the email message.",
        )
    )

    assert receipt.accepted is True
    assert receipt.notification_sent is False
    assert len(list(tmp_path.rglob("*.json"))) == 1


@pytest.mark.asyncio
async def test_honeypot_submission_is_acknowledged_without_storage():
    class Store:
        configured = True

        async def put(self, submission):
            raise AssertionError("honeypot submissions must not be stored")

    service = FeedbackService(
        store=Store(),
        settings=SimpleNamespace(feedback_ui_enabled=True, feedback_base_url=None),
    )

    receipt = await service.submit(
        FeedbackRequest(
            category="improvement",
            subject="Automated submission",
            details="This should be accepted without creating a stored document.",
            website="https://spam.example",
        )
    )

    assert receipt.accepted is True
    assert receipt.feedback_id == ""


def test_feedback_page_url_carries_language_and_report_reference():
    service = FeedbackService(
        store=FileFeedbackStore("unused"),
        settings=SimpleNamespace(
            feedback_ui_enabled=True,
            feedback_base_url="https://azbrief.example",
        ),
    )

    url = service.page_url(language="ko-KR", report_reference="update:570120 / digest")

    assert url == ("https://azbrief.example/feedback?lang=ko&" "report=update%3A570120+%2F+digest")


def test_feedback_page_defaults_to_english_and_uses_shared_shell():
    page = render_feedback_page("nonce-value")

    assert '<html lang="en">' in page
    assert "Send feedback" in page
    assert "brand-lockup" in page
    assert "Program bug" in page
    assert "Report context" in page


def test_feedback_form_supports_preserved_input_and_explicit_receipts():
    page = render_feedback_page("nonce", admin_enabled=True, archive_enabled=True)

    assert 'href="/admin"' in page
    assert 'href="/archive"' in page
    assert 'href="/feedback" aria-current="page"' in page
    assert 'aria-describedby="subject-error subject-count"' in page
    assert 'id="language"' in page
    assert "function setLanguage(code)" in page
    assert "window.addEventListener('beforeunload'" in page
    assert "if (submitting || !byId('receipt').hidden) return" in page
    assert "receipt.feedback_id" in page
    assert "receipt.notification_sent" in page
    assert "error.status === 429" in page
    assert "error.status === 503" in page
    assert "localStorage" not in page
    assert "sessionStorage" not in page
    assert "innerHTML" not in page
    assert 'href="/admin"' not in render_feedback_page("nonce")


def test_feedback_page_localizes_korean_and_escapes_report_reference():
    page = render_feedback_page(
        "nonce-value",
        language="ko-KR",
        report_reference='update:<script>alert("x")</script>',
    )

    assert '<html lang="ko">' in page
    assert "피드백 보내기" in page
    assert "프로그램 버그" in page
    assert "<script>alert" not in page
    assert "update:&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in page


def _feedback_client(monkeypatch, enabled=True, service=None):
    monkeypatch.setattr(
        "src.feedback.router.get_settings",
        lambda: SimpleNamespace(
            feedback_ui_enabled=enabled, admin_ui_enabled=True, archive_ui_enabled=True
        ),
    )
    if service is not None:
        monkeypatch.setattr("src.feedback.router.get_feedback_service", lambda: service)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_feedback_route_is_hidden_when_disabled(monkeypatch):
    response = _feedback_client(monkeypatch, enabled=False).get("/feedback")

    assert response.status_code == 404


def test_feedback_page_route_sets_strict_csp_and_prefills_reference(monkeypatch):
    response = _feedback_client(monkeypatch).get(
        "/feedback",
        params={"lang": "ko", "report": "update:570120"},
    )

    assert response.status_code == 200
    assert "피드백 보내기" in response.text
    assert 'value="update:570120"' in response.text
    assert "form-action 'self'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


def test_feedback_api_persists_and_reports_notification_status(monkeypatch):
    class Service:
        configured = True
        payload = None

        async def submit(self, payload):
            self.payload = payload
            return FeedbackReceipt(feedback_id="feedback-1", notification_sent=True)

    service = Service()
    client = _feedback_client(monkeypatch, service=service)

    response = client.post(
        "/api/feedback",
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
        json={
            "category": "report_context",
            "subject": "Use our maintenance window",
            "details": "Future reports should use Sunday 02:00 UTC as the maintenance window.",
            "contact_email": "owner@example.com",
            "report_reference": "update:570120",
            "language": "ko",
        },
    )

    assert response.status_code == 201
    assert response.json() == {
        "accepted": True,
        "feedback_id": "feedback-1",
        "notification_sent": True,
    }
    assert service.payload.category == FeedbackCategory.REPORT_CONTEXT


def test_feedback_api_rejects_cross_site_browser_post(monkeypatch):
    class Service:
        configured = True

    response = _feedback_client(monkeypatch, service=Service()).post(
        "/api/feedback",
        headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
        json={
            "category": "bug",
            "subject": "Cross-site post",
            "details": "This payload must not reach the feedback service.",
        },
    )

    assert response.status_code == 403
