"""Serve synthetic control surfaces on loopback without Azure calls or email delivery."""

from __future__ import annotations

import argparse
import secrets
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from structlog import get_logger

from scripts.preview_email import build_demo_items
from src.admin.page import render_admin_page
from src.archive.models import (
    ArchiveAnalysisResultV1,
    ArchiveDocumentV1,
    ArchiveQuery,
    ArchiveSummary,
    ArchiveUpdateV1,
)
from src.archive.page import render_archive_page
from src.feedback.models import FeedbackRequest
from src.feedback.page import render_feedback_page
from src.web_fonts import WEB_FONT_CSP_SOURCE

logger = get_logger()
_NOW = datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)


def _documents() -> list[ArchiveDocumentV1]:
    examples = {language: build_demo_items(language)[:3] for language in ("en", "ko", "ja")}
    documents = []
    for index in range(30):
        language = ("en", "ko", "ja")[index % 3]
        example = examples[language][(index // 3) % 3]
        raw = example["result"].model_dump(mode="json")
        analyzed_at = _NOW - timedelta(hours=index * 6)
        documents.append(
            ArchiveDocumentV1(
                archive_id=f"{int(analyzed_at.timestamp() * 1000):013d}-{index + 1:032x}",
                analyzed_at=analyzed_at,
                source="admin_run" if index % 2 else "scheduled_digest",
                report_language=language,
                update=ArchiveUpdateV1.model_validate(
                    {
                        key: value
                        for key, value in asdict(example["update"]).items()
                        if key in ArchiveUpdateV1.model_fields
                    }
                ),
                result=ArchiveAnalysisResultV1.model_validate(
                    {
                        key: value
                        for key, value in raw.items()
                        if key in ArchiveAnalysisResultV1.model_fields
                    }
                ),
            )
        )
    return documents


def _matches(document: ArchiveDocumentV1, query: ArchiveQuery) -> bool:
    summary = ArchiveSummary.from_document(document)
    searchable = " ".join([summary.title, summary.one_line_summary, *summary.azure_services])
    if query.q.casefold() not in searchable.casefold():
        return False
    if query.service.casefold() not in ", ".join(summary.azure_services).casefold():
        return False
    for field in ("importance", "impact_level", "relevance", "source"):
        if getattr(query, field) and getattr(query, field) != getattr(summary, field):
            return False
    if query.category and query.category != summary.update_category:
        return False
    if query.analyzed_after and document.analyzed_at < query.analyzed_after:
        return False
    return not (query.analyzed_before and document.analyzed_at > query.analyzed_before)


def create_app() -> FastAPI:
    """Create a disposable preview with schema-validated reports and in-memory edits."""
    app = FastAPI(title="AzBrief synthetic web preview", docs_url=None, redoc_url=None)
    documents = _documents()
    subscribers = [
        {
            "email": "platform@example.com",
            "name": "Platform team",
            "role": "Cloud Architect",
            "language": "ko",
            "alert_level": "all",
            "managed": True,
            "focus_services": ["AKS", "Storage"],
        },
        {
            "email": "security@example.com",
            "name": "Security operations",
            "role": "Security Engineer",
            "language": "en",
            "alert_level": "important_and_above",
            "managed": True,
        },
        {
            "email": "operations@example.com",
            "name": "Deployment subscriber",
            "role": "Operations",
            "language": "ja",
            "alert_level": "all",
            "managed": False,
        },
    ]
    administrators = [
        {"principal": "owner@example.com", "managed": False},
        {"principal": "platform-admin@example.com", "managed": True},
    ]
    schedules = [
        {
            "time_utc": "00:00",
            "cron_expression": "0 0 * * *",
            "managed": False,
            "next_run_at": "2026-09-11T00:00:00Z",
        },
        {
            "time_utc": "09:00",
            "cron_expression": "0 9 * * *",
            "managed": True,
            "next_run_at": "2026-09-11T09:00:00Z",
        },
    ]
    runs = []
    for index, status in enumerate(("running", "completed", "failed", "completed")):
        runs.append(
            {
                "run_id": f"preview{index}-{index + 1:032x}",
                "status": status,
                "source": "admin_run",
                "selection": {"mode": "recent", "recent_count": 10},
                "total": 10,
                "analyzed": 4 if status == "running" else 9,
                "failed": int(status == "failed"),
                "deferred": 0,
                "pending": 6 if status == "running" else 0,
                "relevant": 6,
                "archived": 4 if status == "running" else 9,
                "archive_failed": 0,
                "elapsed_seconds": 128 + index * 50,
                "started_at": (_NOW - timedelta(hours=index)).isoformat(),
                "finished_at": None if status == "running" else _NOW.isoformat(),
                "dry_run": False,
                "send_email": False,
                "email_sent": False,
                "commit_checkpoint": False,
                "checkpoint_committed": False,
                "watermark": None,
                "error": "Synthetic upstream timeout" if status == "failed" else None,
            }
        )

    @app.get("/")
    async def root():
        return RedirectResponse("/admin")

    @app.get("/admin")
    @app.get("/archive")
    @app.get("/archive/{archive_id}")
    @app.get("/feedback")
    async def page(request: Request):
        nonce = secrets.token_urlsafe(16)
        language = request.query_params.get("lang", "en")
        if request.url.path == "/admin":
            content = render_admin_page(
                nonce, "SYNTHETIC PREVIEW", "operator@example.com", True, True
            )
        elif request.url.path == "/feedback":
            content = render_feedback_page(
                nonce=nonce,
                language=language,
                report_reference=request.query_params.get("report", ""),
            )
            content = content.replace(
                '<span class="brand-area">Feedback</span>',
                '<span class="brand-area">SYNTHETIC PREVIEW</span>',
            )
        else:
            content = render_archive_page(
                nonce, "SYNTHETIC PREVIEW", "reader@example.com", language, True, True
            )
        return HTMLResponse(
            content,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    f"default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; "
                    f"connect-src 'self'; font-src 'self' {WEB_FONT_CSP_SOURCE}; "
                    "img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
                ),
            },
        )

    @app.get("/api/archive/analyses")
    async def archive_list(request: Request):
        try:
            query = ArchiveQuery.model_validate(dict(request.query_params))
            offset = int(query.cursor or "0")
            if offset < 0:
                raise ValueError("Negative cursor")
        except (ValidationError, ValueError) as error:
            raise HTTPException(422, "Invalid preview query") from error
        matches = [document for document in documents if _matches(document, query)]
        items = matches[offset : offset + query.limit]
        more = offset + len(items) < len(matches)
        return {
            "items": [
                ArchiveSummary.from_document(document).model_dump(mode="json") for document in items
            ],
            "has_more": more,
            "next_cursor": str(offset + len(items)) if more else "",
            "scanned": len(items),
        }

    @app.get("/api/archive/analyses/{archive_id}")
    async def archive_detail(archive_id: str):
        document = next((item for item in documents if item.archive_id == archive_id), None)
        if not document:
            raise HTTPException(404, "Synthetic record not found")
        return document.model_dump(mode="json")

    @app.get("/api/admin/{area}")
    async def admin_data(area: str):
        if area == "status":
            groups = {
                "Runtime": ["Hosted analysis", "Specialist roster", "Model deployment"],
                "Access & evidence": ["Entra identity", "Resource Graph", "Azure API"],
                "Delivery & storage": ["Email transport", "Archive storage", "Checkpoint"],
                "Scheduling": ["Dispatcher", "Daily schedule", "Subscriber configuration"],
            }
            return {
                "ok": True,
                "ready": 12,
                "total": 12,
                "sections": [
                    {
                        "title": title,
                        "checks": [
                            {
                                "name": name,
                                "ok": True,
                                "detail": "Configured (synthetic)",
                                "action": "",
                            }
                            for name in names
                        ],
                    }
                    for title, names in groups.items()
                ],
            }
        data = {
            "runs": runs,
            "subscribers": subscribers,
            "administrators": administrators,
            "schedules": schedules,
            "updates": [document.update.model_dump(mode="json") for document in documents[:8]],
        }
        if area not in data:
            raise HTTPException(404)
        return {area: data[area], "configuration_writable": True}

    @app.get("/api/admin/runs/{run_id}")
    async def run_detail(run_id: str):
        result = next((run for run in runs if run["run_id"] == run_id), None)
        if result is None:
            raise HTTPException(404)
        return result

    @app.api_route("/api/admin/{area}", methods=["POST"])
    @app.api_route("/api/admin/{area}/{identifier:path}", methods=["PUT", "DELETE"])
    async def edit_preview(area: str, request: Request, identifier: str = ""):
        if area == "runs":
            raise HTTPException(
                409, "Synthetic preview: live analyses and email delivery are disabled."
            )
        targets = {
            "subscribers": (subscribers, "email"),
            "administrators": (administrators, "principal"),
            "schedules": (schedules, "time_utc"),
        }
        if area not in targets:
            raise HTTPException(404)
        collection, key = targets[area]
        existing = next((item for item in collection if item[key] == identifier), None)
        if existing and not existing["managed"]:
            raise HTTPException(403, "Deployment entries are immutable.")
        if request.method in {"PUT", "DELETE"} and existing is None:
            raise HTTPException(404)
        if request.method == "DELETE":
            collection.remove(existing)
            return {"deleted": True}
        data = await request.json()
        if not data.get(key):
            raise HTTPException(422, "Required value missing")
        if existing:
            collection.remove(existing)
        item = {**data, "managed": True}
        if area == "schedules":
            hour, minute = item["time_utc"].split(":")
            item.update(
                cron_expression=f"{int(minute)} {int(hour)} * * *",
                next_run_at=f"2026-09-11T{item['time_utc']}:00Z",
            )
        collection.append(item)
        return item

    @app.post("/api/feedback", status_code=201)
    async def feedback(payload: FeedbackRequest):
        return {
            "accepted": True,
            "feedback_id": "SYNTHETIC-" + uuid4().hex[:12],
            "notification_sent": False,
        }

    return app


def main() -> None:
    """Start the disposable preview on the local interface only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    logger.info("synthetic_web_preview", url=f"http://127.0.0.1:{args.port}", live_services=False)
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
