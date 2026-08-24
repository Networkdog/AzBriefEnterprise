#!/usr/bin/env python
"""
AzBrief Report Quality Evaluator

Defines quality scoring metrics across multiple dimensions and evaluates
generated reports. Supports iterative generation-evaluation loops to
continuously improve report quality.

Usage:
    # Evaluate a single report (latest update)
    python -m scripts.evaluate_report --latest

    # Evaluate with a specific URL
    python -m scripts.evaluate_report --url "https://azure.microsoft.com/updates/..."

    # Iterative improvement loop (generate → evaluate → improve → repeat)
    python -m scripts.evaluate_report --latest --iterate 3

    # Evaluate HTML email output as well
    python -m scripts.evaluate_report --latest --with-html
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.analyzer import (
    ActionItem,
    AnalysisResult,
    AzureUpdateAnalyzer,
    ImpactSummary,
)
from src.agent.geval import GEvalJudge, GEvalReport
from src.agent.resilience import TOOL_RESULT_BUDGET_CHARS
from src.config import get_settings
from src.email.service import EmailService
from src.email.templates import markdown_to_html
from src.rss.parser import AzureUpdate, AzureUpdateParser

# ============================================================================
# Evaluation artifact output (gitignored — never committed)
# ============================================================================

# Generated reports and scores are written under this directory so they never
# pollute the repository. `eval_runs/` is listed in .gitignore. Override the
# root with the AZBRIEF_EVAL_DIR environment variable or the --out-dir flag.
EVAL_OUTPUT_ROOT = Path(
    os.environ.get(
        "AZBRIEF_EVAL_DIR",
        str(Path(__file__).resolve().parent.parent / "eval_runs"),
    )
)


def _create_run_dir(out_dir: Optional[Path] = None) -> Path:
    """Create and return a timestamped run directory for evaluation artifacts.

    Each evaluation run gets its own ``run_<timestamp>`` folder so iterations
    are grouped together and never clobber a previous run. The location is
    outside version control (see .gitignore).

    Args:
        out_dir: Optional override for the output root. Defaults to
            ``eval_runs/`` at the project root (or ``$AZBRIEF_EVAL_DIR``).

    Returns:
        The created run directory path.
    """
    root = Path(out_dir) if out_dir else EVAL_OUTPUT_ROOT
    run_dir = root / f"run_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


# ============================================================================
# Quality Scoring Model
# ============================================================================

# Categories that add a new capability instead of changing existing behaviour.
CAPABILITY_CATEGORIES = frozenset(
    {"new_feature", "new_service", "region_expansion", "preview", "sdk_tooling"}
)

# For a Capability update, "this does not affect your operations" is a tautology —
# a newly released capability cannot change existing behaviour. The same sentence is
# legitimate information for a Change update ("the other 19 accounts already use TLS
# 1.2"), so this net is only applied to Capability categories.
# Calibrated on 267 corpus reports: flags 156/240 Capability reports, and never fires
# on adoption-cost phrasings ("기존 구성 변경 없이 추가할 수 있습니다").
_RE_ABSENCE_TAUTOLOGY = re.compile(
    r"(?:운영|기존)[^.\n]{0,40}(?:영향|리스크|위험|변경|변화)[^.\n]{0,12}"
    r"(?:없습니다|없으며|없고|없지만|없음|않습니다|않으며)"
    r"|(?:도입|적용|채택|사용)하지\s*않아(?:도|서도)[^.\n]{0,40}"
    r"(?:없습니다|없으며|없고|없지만|않습니다|않으며)"
    r"|미도입[^.\n]{0,30}(?:없습니다|없으며|않습니다|않으며)"
    r"|운영\s*(?:항목|사항)[^.\n]{0,10}(?:없습니다|없으며|없고)"
)


@dataclass
class ScoreItem:
    """Individual scoring criterion."""

    name: str
    category: str
    max_score: int
    score: int = 0
    reason: str = ""
    deductions: list[str] = field(default_factory=list)


@dataclass
class QualityReport:
    """Complete quality evaluation report."""

    total_score: int = 0
    max_score: int = 0
    percentage: float = 0.0
    grade: str = ""
    items: list[ScoreItem] = field(default_factory=list)
    category_scores: dict[str, dict] = field(default_factory=dict)
    improvement_suggestions: list[str] = field(default_factory=list)
    critical_issues: list[str] = field(default_factory=list)

    def calculate(self):
        """Calculate totals from individual items."""
        self.max_score = sum(item.max_score for item in self.items)
        self.total_score = sum(item.score for item in self.items)
        self.percentage = (self.total_score / self.max_score * 100) if self.max_score > 0 else 0

        if self.percentage >= 95:
            self.grade = "S"
        elif self.percentage >= 90:
            self.grade = "A+"
        elif self.percentage >= 85:
            self.grade = "A"
        elif self.percentage >= 80:
            self.grade = "B+"
        elif self.percentage >= 75:
            self.grade = "B"
        elif self.percentage >= 65:
            self.grade = "C"
        elif self.percentage >= 50:
            self.grade = "D"
        else:
            self.grade = "F"

        # Category breakdown
        cats: dict[str, list[ScoreItem]] = {}
        for item in self.items:
            cats.setdefault(item.category, []).append(item)
        for cat, cat_items in cats.items():
            cat_max = sum(i.max_score for i in cat_items)
            cat_score = sum(i.score for i in cat_items)
            self.category_scores[cat] = {
                "score": cat_score,
                "max": cat_max,
                "percentage": (cat_score / cat_max * 100) if cat_max > 0 else 0,
            }


class ReportQualityEvaluator:
    """Evaluates the quality of an AzBrief analysis report across multiple dimensions."""

    def evaluate(
        self,
        result: AnalysisResult,
        update: AzureUpdate,
        html_content: str = "",
        language: str = "ko",
    ) -> QualityReport:
        """Run all quality checks and return a scored report.

        Args:
            result: The AnalysisResult to evaluate
            update: The original AzureUpdate
            html_content: Optional rendered HTML email content
            language: Report language

        Returns:
            QualityReport with detailed scoring
        """
        report = QualityReport()
        report.items = []

        # 1. Content Accuracy (25 points)
        report.items.extend(self._evaluate_content_accuracy(result, update))

        # 2. Structural Completeness (20 points)
        report.items.extend(self._evaluate_structure(result, update))

        # 3. Language Quality (20 points)
        report.items.extend(self._evaluate_language(result, language))

        # 4. Actionability (20 points)
        report.items.extend(self._evaluate_actionability(result, update))

        # 5. Scannability & Design (15 points)
        report.items.extend(self._evaluate_scannability(result, update, html_content))

        report.calculate()

        # Generate improvement suggestions
        report.improvement_suggestions = self._generate_suggestions(report)
        report.critical_issues = [
            item.reason
            for item in report.items
            if item.score < item.max_score * 0.5 and item.max_score >= 3
        ]

        return report

    # ------------------------------------------------------------------
    # 1. Content Accuracy (30 points)
    # ------------------------------------------------------------------
    def _evaluate_content_accuracy(
        self, result: AnalysisResult, update: AzureUpdate
    ) -> list[ScoreItem]:
        items = []

        # 1.1 Relevance classification correctness (5 pts)
        item = ScoreItem("relevance_classification", "content_accuracy", 5)
        rel = result.relevance.value
        has_resources = bool(result.affected_resources)
        if rel == "relevant" and has_resources:
            item.score = 5
            item.reason = "Relevance=relevant with affected resources identified"
        elif rel == "not_relevant" and not has_resources:
            item.score = 5
            item.reason = "Relevance=not_relevant with no affected resources"
        elif rel == "opportunity":
            item.score = 4
            item.reason = "Relevance=opportunity (acceptable)"
        elif rel == "unknown":
            item.score = 2
            item.reason = "Relevance=unknown — resource query may have failed"
            item.deductions.append("Could not determine relevance")
        else:
            # Mismatch cases
            if rel == "relevant" and not has_resources:
                item.score = 2
                item.reason = "Relevance=relevant but no affected resources listed"
                item.deductions.append("Relevant without resource evidence")
            elif rel == "not_relevant" and has_resources:
                item.score = 2
                item.reason = "Relevance=not_relevant but affected resources exist"
                item.deductions.append("Resources found but marked not relevant")
            else:
                item.score = 3
        items.append(item)

        # 1.2 One-line summary quality (3 pts)
        item = ScoreItem("one_line_summary", "content_accuracy", 3)
        summary = result.one_line_summary or ""
        if not summary:
            item.score = 0
            item.reason = "Missing one-line summary"
        else:
            item.score = 3
            deductions = []
            if len(summary) < 20:
                deductions.append("Too short (<20 chars)")
                item.score -= 1
            if len(summary) > 100:
                deductions.append("Too long (>100 chars)")
                item.score -= 1
            # Check for vague language
            vague = ["some resources", "may be affected", "a new feature", "an update"]
            for v in vague:
                if v.lower() in summary.lower():
                    deductions.append(f"Vague language: '{v}'")
                    item.score -= 1
                    break
            # Check for internal process exposure
            internal = ["resource graph", "tool call", "search result", "query"]
            for w in internal:
                if w.lower() in summary.lower():
                    deductions.append(f"Exposes internals: '{w}'")
                    item.score -= 1
                    break
            item.deductions = deductions
            item.reason = f"Summary ({len(summary)} chars): {summary[:60]}..."
            item.score = max(0, item.score)
        items.append(item)

        # 1.3 No fabricated URLs (3 pts)
        item = ScoreItem("no_fabricated_urls", "content_accuracy", 3)
        ref_docs = result.reference_docs or []
        if not ref_docs:
            item.score = 3
            item.reason = "No reference docs (acceptable)"
        else:
            item.score = 3
            bad_urls = []
            for doc in ref_docs:
                url = doc.get("url", "") if isinstance(doc, dict) else ""
                if url and not url.startswith("http"):
                    bad_urls.append(url)
            if bad_urls:
                item.score -= len(bad_urls) * 2
                item.deductions = [f"Invalid URL: {u}" for u in bad_urls]
            item.reason = f"{len(ref_docs)} reference docs checked"
            item.score = max(0, item.score)
        items.append(item)

        # 1.4 Relevance evidence quality (5 pts)
        item = ScoreItem("relevance_evidence", "content_accuracy", 5)
        evidence = result.relevance_evidence or ""
        if not evidence:
            item.score = 1
            item.reason = "Missing relevance_evidence"
            item.deductions.append("No evidence explaining why this update was selected")
        else:
            item.score = 5
            deductions = []
            # Should contain resource names or counts
            if not re.search(r"\d+", evidence):
                deductions.append("No resource counts in evidence")
                item.score -= 1
            # Should not be too short
            if len(evidence) < 30:
                deductions.append("Too brief (<30 chars)")
                item.score -= 1
            item.deductions = deductions
            item.reason = f"Evidence ({len(evidence)} chars)"
            item.score = max(0, item.score)
        items.append(item)

        # 1.5 No fabricated dates/deadlines (4 pts)
        item = ScoreItem("no_fabricated_dates", "content_accuracy", 4)
        deductions = []
        for ai in result.action_items:
            dl = ai.deadline or ""
            # Fabricated patterns: "within X weeks/months"
            if re.search(r"within \d+ (week|month|day)", dl, re.I):
                deductions.append(f"Possible fabricated deadline: '{dl}'")
        if deductions:
            item.score = max(0, 4 - len(deductions) * 2)
            item.deductions = deductions
        else:
            item.score = 4
        item.reason = f"Checked {len(result.action_items)} action items for deadline fabrication"
        items.append(item)

        # 1.6 Update category correctness and report frame (5 pts)
        # The category IS the frame: Change categories are reported as impact,
        # Capability categories as opportunity.
        item = ScoreItem("update_category", "content_accuracy", 5)
        category = getattr(result, "update_category", "")
        valid_categories = {
            "retirement",
            "feature_change",
            "new_feature",
            "new_service",
            "region_expansion",
            "preview",
            "sdk_tooling",
            "pricing",
        }
        if not category:
            item.score = 2
            item.reason = "Missing update_category"
        elif category in valid_categories:
            item.score = 5
            # Check category vs update_type alignment
            utype = (update.update_type or "").lower()
            if "retirement" in utype and category != "retirement":
                item.score = 3
                item.deductions.append(f"Update type '{utype}' but category='{category}'")
            elif "preview" in utype and category not in ("preview", "new_feature"):
                item.score = 3
                item.deductions.append(f"Update type '{utype}' but category='{category}'")
            item.reason = f"Category: {category}"
            hits = (
                _RE_ABSENCE_TAUTOLOGY.findall(self._frame_text(result))
                if category in CAPABILITY_CATEGORIES
                else []
            )
            if hits:
                item.score = max(0, item.score - 2 * len(hits))
                item.deductions += [
                    f"'{h[:40]}' — {category}에 영향 부재 서술 (→ 기회/도입 조건으로 서술)"
                    for h in hits[:4]
                ]
                item.reason = f"Category: {category}, {len(hits)} absence-of-impact statement(s)"
        else:
            item.score = 1
            item.reason = f"Invalid category: {category}"
        items.append(item)

        return items

    @staticmethod
    def _frame_text(result: AnalysisResult) -> str:
        """Collect the narrative fields where the report's framing is visible."""
        impact = result.impact_details
        return "\n".join(
            filter(
                None,
                [
                    result.relevance_reason or "",
                    getattr(result, "relevance_evidence", "") or "",
                    getattr(impact, "cost_impact", "") if impact else "",
                    getattr(impact, "security_impact", "") if impact else "",
                    getattr(impact, "performance_impact", "") if impact else "",
                    getattr(impact, "operational_impact", "") if impact else "",
                ],
            )
        )

    # ------------------------------------------------------------------
    # 2. Structural Completeness (25 points)
    # ------------------------------------------------------------------
    def _evaluate_structure(self, result: AnalysisResult, update: AzureUpdate) -> list[ScoreItem]:
        items = []
        category = getattr(result, "update_category", "new_feature")
        rel = result.relevance.value

        # 2.1 Detailed analysis presence and quality (6 pts)
        item = ScoreItem("detailed_analysis", "structure", 6)
        analysis = result.relevance_reason or ""
        if not analysis:
            item.score = 0
            item.reason = "Missing detailed analysis"
        else:
            item.score = 6
            deductions = []
            # Minimum length check
            if len(analysis) < 200:
                deductions.append(f"Too short ({len(analysis)} chars, min 200)")
                item.score -= 2
            # Concept boxes are need-based, not quota-based: penalize padding, not absence.
            # A box that defines an obvious term is filler and reads as machine-assembled.
            concept_boxes = len(re.findall(r"^>\s*\*\*", analysis, re.MULTILINE))
            if concept_boxes > 3:
                deductions.append(f"{concept_boxes} concept boxes — over-explaining (3 max)")
                item.score -= 2
            # Template-style subheadings are forbidden in the narrative body
            headings = len(re.findall(r"^#{1,4}\s+", analysis, re.MULTILINE))
            if headings:
                deductions.append(f"{headings} markdown heading(s) in the analysis body")
                item.score -= 2
            # Check for content duplication (resource names in analysis)
            if result.affected_resources:
                resource_names = [
                    r.get("name", "") for r in result.affected_resources if r.get("name")
                ]
                names_in_analysis = sum(1 for n in resource_names if n and n in analysis)
                if names_in_analysis > 2:
                    deductions.append(
                        f"{names_in_analysis} resource names appear in analysis body (should be in affected_resources only)"
                    )
                    item.score -= 1
            item.deductions = deductions
            item.reason = (
                f"Analysis: {len(analysis)} chars, {concept_boxes} concept boxes, "
                f"{headings} headings"
            )
            item.score = max(0, item.score)
        items.append(item)

        # 2.2 Impact summary substance (4 pts)
        # Rewarding the filled count alone pushed Capability reports to fill every
        # dimension with "영향 없음". For those categories a hollow dimension scores nothing.
        item = ScoreItem("impact_summary", "structure", 4)
        impact = result.impact_details
        if not impact:
            item.score = 1
            item.reason = "No impact_details object"
        else:
            values = [
                v.strip()
                for v in [
                    impact.cost_impact,
                    impact.security_impact,
                    impact.performance_impact,
                    impact.operational_impact,
                ]
                if v and v.strip()
            ]
            # An "unaffected" statement is real information for a Change update.
            hollow = (
                [v for v in values if _RE_ABSENCE_TAUTOLOGY.search(v)]
                if category in CAPABILITY_CATEGORIES
                else []
            )
            substantive = len(values) - len(hollow)
            if substantive == 0:
                item.score = 1
                item.reason = (
                    f"All {len(hollow)} dimension(s) state only an absence of impact"
                    if hollow
                    else "Impact details present but all empty"
                )
            elif category in CAPABILITY_CATEGORIES:
                item.score = max(1, 4 - len(hollow))
                item.reason = f"{substantive} opportunity dimension(s), {len(hollow)} hollow"
            else:
                item.score = min(4, 1 + substantive)
                item.reason = f"{substantive}/4 impact dimensions filled"
            if hollow:
                item.deductions = [f"영향 부재 서술: '{v[:50]}'" for v in hollow[:4]]
        items.append(item)

        # 2.3 Affected resources quality (5 pts)
        item = ScoreItem("affected_resources", "structure", 5)
        resources = result.affected_resources or []
        if category in ("new_service", "region_expansion", "sdk_tooling") and not resources:
            item.score = 5
            item.reason = f"Empty affected_resources correct for {category}"
        elif rel == "not_relevant" and not resources:
            item.score = 5
            item.reason = "No resources for not_relevant update"
        elif not resources and category in ("retirement", "feature_change"):
            item.score = 2
            item.reason = "Missing affected resources for retirement/feature_change"
            item.deductions.append("Retirement/feature_change should list affected resources")
        else:
            item.score = 5
            deductions = []
            for r in resources:
                if not r.get("reason"):
                    deductions.append(f"Resource '{r.get('name', '?')}' missing reason")
                    item.score -= 1
                else:
                    reason = r.get("reason", "")
                    # Reason should contain property evidence in parentheses
                    # e.g., "(nodeImageVersion: AKSUbuntu-2204gen2containerd)"
                    has_evidence = bool(re.search(r"\([A-Za-z]+[:.]\s*\S+\)", reason))
                    # Also accept inline property references without parens
                    has_inline = bool(re.search(r"[A-Za-z]+[:.=]\s*\S+", reason))
                    if not has_evidence and not has_inline:
                        deductions.append(
                            f"Resource '{r.get('name', '?')}' reason lacks property value evidence"
                        )
            item.deductions = deductions[:5]
            item.reason = f"{len(resources)} resources listed"
            item.score = max(0, item.score)
        items.append(item)

        # 2.4 Reference docs (3 pts)
        item = ScoreItem("reference_docs", "structure", 3)
        docs = result.reference_docs or []
        if not docs:
            item.score = 1
            item.reason = "No reference docs"
            item.deductions.append("Should have at least 1 reference doc")
        else:
            item.score = 3
            deductions = []
            for d in docs:
                if isinstance(d, dict):
                    if not d.get("url"):
                        deductions.append("Reference doc missing URL")
                        item.score -= 1
                    if not d.get("title"):
                        deductions.append("Reference doc missing title")
            item.deductions = deductions[:3]
            item.reason = f"{len(docs)} reference docs"
            item.score = max(0, item.score)
        items.append(item)

        # 2.5 Additional checks presence (2 pts)
        item = ScoreItem("additional_checks", "structure", 2)
        checks = result.additional_checks or []
        if checks:
            item.score = 2
            item.reason = f"{len(checks)} additional checks noted"
        else:
            item.score = 1
            item.reason = "No additional checks (acceptable for most updates)"
        items.append(item)

        return items

    # ------------------------------------------------------------------
    # 3. Language Quality (20 points)
    # ------------------------------------------------------------------
    def _evaluate_language(self, result: AnalysisResult, language: str) -> list[ScoreItem]:
        items = []
        analysis = result.relevance_reason or ""

        # 3.1 No internal process exposure (5 pts)
        item = ScoreItem("no_internal_exposure", "language", 5)
        internal_markers = [
            "resource graph",
            "tool call",
            "search result",
            "query result",
            "검색 결과",
            "쿼리 결과",
            "도구 호출",
            "tool output",
            "LLM",
        ]
        found = []
        for marker in internal_markers:
            if marker.lower() in analysis.lower():
                found.append(marker)
        if found:
            item.score = max(0, 5 - len(found) * 2)
            item.deductions = [f"Internal exposure: '{m}'" for m in found]
            item.reason = f"Found {len(found)} internal process references"
        else:
            item.score = 5
            item.reason = "No internal process exposure"
        items.append(item)

        # 3.2 Korean language quality checks (for ko)
        if language == "ko":
            items.extend(self._evaluate_korean_quality(analysis))
        elif language == "en":
            items.extend(self._evaluate_english_quality(analysis))
        else:
            # Default: basic language check
            item = ScoreItem("language_basic", "language", 15)
            item.score = 12
            item.reason = f"Basic check for language '{language}'"
            items.append(item)

        return items

    def _evaluate_korean_quality(self, text: str) -> list[ScoreItem]:
        """Evaluate Korean-specific language quality."""
        items = []

        # 3.2a Consistent speech level (합쇼체) (5 pts)
        item = ScoreItem("speech_level_consistency", "language", 5)
        # Check for 해요체 mixing
        haeyo_count = len(re.findall(r"해요|에요|예요|죠", text))
        if haeyo_count > 0:
            item.score = max(0, 5 - haeyo_count)
            item.deductions.append(f"해요체 혼용 {haeyo_count}회 발견")
        else:
            item.score = 5
        item.reason = f"합쇼체 일관성 검사 (해요체 {haeyo_count}회)"
        items.append(item)

        # 3.2b Translation-style avoidance (5 pts)
        item = ScoreItem("translation_avoidance", "language", 5)
        translation_patterns = [
            (r"하는 것을 권장", "~하는 것을 권장 (→ ~을 권장)"),
            (
                r"(?:하는|한|된다는|한다는|라는)\s*(?:내용|공지)입니다",
                "공지 자체를 서술 (~하는 내용/공지입니다) "
                "(→ '{날짜}부터 ~ 제공이 종료됩니다'처럼 사실 직접 서술)",
            ),
            (r"에 의해", "~에 의해 (→ ~(으)로)"),
            (r"수행하는 것이", "~수행하는 것이 (→ 직접 서술)"),
            (r"할 수 있게 됩니다", "~할 수 있게 됩니다 (주어 불일치 가능)"),
            (
                r"수 있게 (?:합니다|해 줍니다|해줍니다|만듭니다)",
                "사역형 ~할 수 있게 합니다 (→ ~할 수 있습니다 / ~할 수 있게 되었습니다)",
            ),
            (
                r"(?:이번|이) (?:업데이트|공지|발표|변경|릴리스|GA|preview)[는은][^.]{0,140}"
                r"(?:public preview|미리 보기|preview|GA|정식 출시|일반 공급|기능|변화)입니다",
                "공지 주어 + 분류어 서술어 — 출시 단계/기능/변화 "
                "(→ ~기능이 public preview로 추가되었습니다)",
            ),
            (
                r"이번\s*(?:GA|공지|발표|업데이트|preview|미리 보기|릴리스|출시|변경)\s*"
                r"(?:로|으로|에 따라)[^.]{0,120}(?:사용할 수 있|이용할 수 있|쓸 수 있)",
                "공지를 원인 부사구로 사용 (→ '이제 ~ 사용할 수 있습니다')",
            ),
            (
                r"은퇴(?:합니다|됩니다|한다|한다는|하는|되는|되며|될 예정)",
                "은퇴 (retire 직역 → 제공/지원 종료)",
            ),
            (
                r"[A-Za-z]{2,}\s*(?:되었습니다|됩니다|되어|되며|되면|된\s)",
                "영문 토큰을 동사 어간으로 사용 (GA되었습니다 → 정식 출시되었습니다)",
            ),
            (r"되어지", "이중 피동 (~되어지다)"),
        ]
        found_patterns = []
        for pattern, desc in translation_patterns:
            if re.search(pattern, text):
                found_patterns.append(desc)

        # Shape-based net for nominalized predicates. Concept boxes (`>` lines) are
        # excluded: "Term: ~하는 기능입니다" is the required form there.
        # Corpus median is 0 per report and p90 is 1, so 2+ marks the top ~9%.
        body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith(">"))
        nominal_endings = re.findall(
            r"(?:것|점|지점|방식|의미|내용|성격|형태|구조|부분|측면|수준|셈|차원)(?:입니다|이며)",
            body,
        )
        if len(nominal_endings) >= 2:
            found_patterns.append(
                f"명사화 종결 {len(nominal_endings)}회 "
                "(~점/방식/의미입니다 → 명사 안의 동사를 서술어로)"
            )

        # The opening must describe what changed, not the announcement.
        # `**bold**` starts a normal paragraph; only `- `/`* ` are list markers.
        opening = next(
            (
                ln.strip()
                for ln in text.splitlines()
                if ln.strip()
                and not ln.lstrip().startswith(("#", ">", "|"))
                and not re.match(r"[-*+]\s", ln.lstrip())
            ),
            "",
        )
        if re.match(
            r"(?:\*\*)?(?:이번|금번|이)\s*(?:\*\*)?\s*"
            r"(?:업데이트|공지|발표|변경|릴리스|릴리즈|출시|GA|preview|미리\s*보기)",
            opening,
        ):
            found_patterns.append("서두가 공지 프레임 (→ 바뀐 대상이나 시점으로 시작)")

        # The environment's verdict is section-2 content, never the first sentence.
        # Requires an absence predicate: a positive "현재 환경의 X 22개 중 3개가…" is a
        # different (allowed) shape, and matching it would be a false positive.
        if re.match(
            r"(?:\*\*)?(?:현재|금번|이|해당)?\s*(?:환경|테넌트|구독|인프라)"
            r"(?:\s*기준)?(?:에는|에서는|에도|으로는|로는|에|은|는|의)"
            r"[^.!?\n]{0,120}?(?:없습니다|없으며|없고|않습니다|아닙니다)",
            opening,
        ):
            found_patterns.append("서두가 환경 판정 (→ 바뀐 내용 먼저, 적용 여부는 뒤 문단)")

        if found_patterns:
            item.score = max(0, 5 - len(found_patterns))
            item.deductions = found_patterns[:5]
        else:
            item.score = 5
        item.reason = f"번역체 패턴 {len(found_patterns)}개 발견"
        items.append(item)

        # 3.2c Sentence ending variety (5 pts)
        item = ScoreItem("sentence_ending_variety", "language", 5)
        # Extract sentence-final verb endings (합쇼체 Korean)
        # Capture compound endings: "해야 합니다", "수 없습니다" etc. are functionally
        # different from bare "합니다" even though they share the suffix.
        # Use a broader pattern that includes auxiliary verb combinations.
        compound_endings = re.findall(
            r"((?:해야|할 수|할 필요가|으로|에서|하지|되지|수도)?\s*"
            r"(?:합니다|입니다|됩니다|않습니다|필요합니다|있습니다|없습니다|봅니다|줍니다|냅니다|옵니다))"
            r"[.。\s\n]",
            text,
        )
        # Normalize: strip leading whitespace from each match
        endings = [e.strip() for e in compound_endings]
        if len(endings) >= 3:
            # Check for 3+ consecutive IDENTICAL compound endings
            consecutive = 0
            max_consecutive = 0
            for i in range(1, len(endings)):
                if endings[i] == endings[i - 1]:
                    consecutive += 1
                    max_consecutive = max(max_consecutive, consecutive)
                else:
                    consecutive = 0
            # Korean 합쇼체 structurally ends most sentences with ~합니다/~입니다.
            # 3 consecutive is common and acceptable. Penalize 4+ consecutive.
            if max_consecutive >= 4:
                item.score = 2
                item.deductions.append(f"동일 종결어미 {max_consecutive+1}회 연속")
            elif max_consecutive >= 3:
                item.score = 4
                item.deductions.append(f"동일 종결어미 {max_consecutive+1}회 연속")
            else:
                item.score = 5
            # Check variety — Korean 합쇼체 structurally favors ~합니다/~입니다
            # so use a realistic threshold (20% unique is acceptable for Korean)
            unique_ratio = len(set(endings)) / len(endings) if endings else 1
            if unique_ratio < 0.15:
                item.score = min(item.score, 3)
                item.deductions.append(f"종결어미 다양성 부족 (유니크 비율: {unique_ratio:.0%})")
        else:
            item.score = 5
        item.reason = (
            f"종결어미 다양성 (유니크 비율: {len(set(endings))}/{len(endings) if endings else 0})"
        )
        items.append(item)

        return items

    def _evaluate_english_quality(self, text: str) -> list[ScoreItem]:
        """Evaluate English-specific language quality."""
        items = []

        # 3.2a Active voice preference (5 pts)
        item = ScoreItem("active_voice", "language", 5)
        passive_count = len(re.findall(r"\b(is|are|was|were|been|being)\s+\w+ed\b", text))
        total_sentences = len(re.findall(r"[.!?]", text)) or 1
        passive_ratio = passive_count / total_sentences
        if passive_ratio > 0.5:
            item.score = 2
            item.deductions.append(f"High passive voice ratio: {passive_ratio:.0%}")
        elif passive_ratio > 0.3:
            item.score = 3
        else:
            item.score = 5
        item.reason = f"Passive voice: {passive_count}/{total_sentences} sentences"
        items.append(item)

        # 3.2b No hedging (5 pts)
        item = ScoreItem("no_hedging", "language", 5)
        hedging = re.findall(
            r"\b(may potentially|might possibly|could perhaps|it seems|appears to)\b", text, re.I
        )
        if hedging:
            item.score = max(0, 5 - len(hedging))
            item.deductions = [f"Hedging: '{h}'" for h in hedging[:3]]
        else:
            item.score = 5
        item.reason = f"Hedging expressions: {len(hedging)}"
        items.append(item)

        # 3.2c Sentence variety (5 pts)
        item = ScoreItem("sentence_variety", "language", 5)
        sentences = re.split(r"[.!?]\s+", text)
        if len(sentences) >= 3:
            # Check for consecutive sentences starting with same word
            starts = [s.split()[0].lower() if s.split() else "" for s in sentences if s.strip()]
            consecutive_same = 0
            max_cons = 0
            for i in range(1, len(starts)):
                if starts[i] == starts[i - 1] and starts[i]:
                    consecutive_same += 1
                    max_cons = max(max_cons, consecutive_same)
                else:
                    consecutive_same = 0
            if max_cons >= 2:
                item.score = 3
                item.deductions.append(f"{max_cons+1} consecutive sentences start with same word")
            else:
                item.score = 5
        else:
            item.score = 5
        item.reason = f"Sentence start variety check"
        items.append(item)

        return items

    # ------------------------------------------------------------------
    # 4. Actionability (20 points)
    # ------------------------------------------------------------------
    def _evaluate_actionability(
        self, result: AnalysisResult, update: AzureUpdate
    ) -> list[ScoreItem]:
        items = []
        category = getattr(result, "update_category", "new_feature")
        rel = result.relevance.value

        # Categories where action items are mandatory
        action_required_categories = {"retirement", "feature_change"}

        # 4.1 Action items presence (5 pts)
        item = ScoreItem("action_items_presence", "actionability", 5)
        aitems = result.action_items or []
        if category in action_required_categories and rel in ("relevant", "opportunity"):
            if not aitems:
                item.score = 1
                item.reason = f"Missing action items for {category} (mandatory)"
                item.deductions.append("Action items required for retirement/feature_change")
            else:
                item.score = 5
                item.reason = f"{len(aitems)} action items for {category}"
        elif category in ("new_service", "region_expansion", "preview", "sdk_tooling"):
            if not aitems:
                item.score = 5
                item.reason = f"Empty action items correct for {category}"
            else:
                item.score = 4
                item.reason = f"{len(aitems)} action items for {category} (optional)"
        else:
            item.score = 4 if aitems else 3
            item.reason = f"{len(aitems)} action items for {category}"
        items.append(item)

        # 4.2 Action item quality (5 pts)
        item = ScoreItem("action_items_quality", "actionability", 5)
        if not aitems:
            item.score = 5 if category not in action_required_categories else 0
            item.reason = "No action items to evaluate"
        else:
            item.score = 5
            deductions = []
            has_ref_docs = bool(result.reference_docs)
            for ai in aitems:
                if not ai.task:
                    deductions.append("Action item missing task description")
                    item.score -= 1
                # procedure/cli_command is ideal, but if reference docs exist
                # the agent correctly avoided fabricating unverified commands.
                if not ai.procedure and not ai.cli_command:
                    if not has_ref_docs:
                        deductions.append(f"Step {ai.step}: no procedure or CLI command")
                        item.score -= 1
                    elif ai.step == 1 and len(aitems) > 1:
                        pass  # acceptable — ref docs provide the procedure
                if not ai.target_resources and result.affected_resources:
                    deductions.append(f"Step {ai.step}: no target resources specified")
                if not ai.why:
                    deductions.append(f"Step {ai.step}: missing 'why' explanation")
            item.deductions = deductions[:5]
            item.reason = f"Evaluated {len(aitems)} action items"
            item.score = max(0, item.score)
        items.append(item)

        # 4.3 Action items ordering (5 pts)
        item = ScoreItem("action_items_ordering", "actionability", 5)
        if len(aitems) <= 1:
            item.score = 5
            item.reason = "Single or no action items — ordering N/A"
        else:
            steps = [ai.step for ai in aitems]
            if steps == sorted(steps):
                item.score = 5
                item.reason = "Action items properly ordered by step"
            else:
                item.score = 3
                item.deductions.append("Action items not in step order")
                item.reason = f"Steps: {steps}"
        items.append(item)

        # 4.4 Numbers with context (5 pts) — CSA CISO guide principle
        # "3 affected" → "3 out of 22 affected (14%)"
        item = ScoreItem("numbers_with_context", "actionability", 5)
        evidence = result.relevance_evidence or ""
        analysis = result.relevance_reason or ""
        combined = evidence + " " + analysis
        deductions = []
        # Check if numbers include context ("X out of Y", "X/Y", "X개 중")
        has_contextual_numbers = bool(
            re.search(
                r"\d+\s*(?:개\s*)?(?:중|out of|/\s*\d+|of \d+)",
                combined,
            )
        )
        # Also check for ratio-style numbers like "22개 중 3개" or "3 of 22"
        has_ratio = bool(re.search(r"\d+\s*(?:개|건)?\s*중\s*\d+", combined))
        has_standalone_numbers = bool(re.search(r"\d+\s*(?:개|건|대)\s", combined))
        if has_contextual_numbers or has_ratio:
            item.score = 5
            item.reason = "Numbers presented with context (X out of Y)"
        elif has_standalone_numbers:
            item.score = 3
            item.reason = "Numbers present but lack comparative context"
            deductions.append("Use 'X개 중 Y개 영향' instead of just 'Y개 영향'")
        else:
            item.score = 4  # No numbers may be acceptable for some updates
            item.reason = "No numeric context to evaluate"
        item.deductions = deductions
        items.append(item)

        return items
        item = ScoreItem("action_items_ordering", "actionability", 5)
        if len(aitems) <= 1:
            item.score = 5
            item.reason = "Single or no action items — ordering N/A"
        else:
            steps = [ai.step for ai in aitems]
            if steps == sorted(steps):
                item.score = 5
                item.reason = "Action items properly ordered by step"
            else:
                item.score = 3
                item.deductions.append("Action items not in step order")
                item.reason = f"Steps: {steps}"
        items.append(item)

        return items

    # ------------------------------------------------------------------
    # 5. Scannability & Design (15 points)
    # ------------------------------------------------------------------
    def _evaluate_scannability(
        self, result: AnalysisResult, update: AzureUpdate, html_content: str
    ) -> list[ScoreItem]:
        items = []
        analysis = result.relevance_reason or ""

        # 5.1 3-second scan test — one_line_summary + urgency + relevance (5 pts)
        item = ScoreItem("three_second_scan", "scannability", 5)
        summary = result.one_line_summary or ""
        has_urgency = bool(result.urgency)
        has_relevance = bool(result.relevance)
        deductions = []
        if not summary:
            deductions.append("No one_line_summary for quick scan")
            item.score = 0
        else:
            item.score = 5
            # Summary should convey urgency and count
            if not re.search(r"\d", summary):
                deductions.append("Summary lacks resource count for quick scanning")
                item.score -= 1
            # Check for action indicator in summary (retirement = count, preview = feature name)
            category = getattr(result, "update_category", "")
            if (
                category in ("retirement", "feature_change")
                and "—" not in summary
                and "-" not in summary
            ):
                deductions.append(
                    "Summary for retirement/feature_change should use 'title — N resources need action' pattern"
                )
                item.score -= 1
        item.deductions = deductions
        item.reason = f"3-second scan: summary={len(summary)} chars, urgency={has_urgency}, relevance={has_relevance}"
        item.score = max(0, item.score)
        items.append(item)

        # 5.2 Text formatting quality (5 pts)
        item = ScoreItem("text_formatting", "scannability", 5)
        deductions = []
        bold_count = len(re.findall(r"\*\*[^*]+\*\*", analysis))
        if bold_count == 0 and len(analysis) > 200:
            deductions.append("No bold text (**) for emphasis")
            item.score = 3
        else:
            item.score = 5
        paragraphs = [p.strip() for p in analysis.split("\n\n") if p.strip()]
        if len(paragraphs) <= 1 and len(analysis) > 500:
            deductions.append("No paragraph breaks in long analysis")
            item.score -= 1
        item.deductions = deductions
        item.reason = f"Formatting: {bold_count} bold, {len(paragraphs)} paragraphs"
        item.score = max(0, item.score)
        items.append(item)

        # 5.3 HTML email quality (5 pts)
        item = ScoreItem("html_email_quality", "scannability", 5)
        if not html_content:
            item.score = 3
            item.reason = "HTML not evaluated (not provided)"
        else:
            item.score = 5
            deductions = []
            if "<table" not in html_content:
                deductions.append("Missing table-based layout")
                item.score -= 1
            if "AzBrief" not in html_content:
                deductions.append("Missing AzBrief branding")
                item.score -= 1
            # Check for broken template placeholders
            unresolved = re.findall(r"\{[a-z_]+\}", html_content)
            if unresolved:
                deductions.append(f"Unresolved template vars: {unresolved[:3]}")
                item.score -= 2
            # Check for urgency badge presence (status header)
            if (
                "urgency" not in html_content.lower()
                and "CRITICAL" not in html_content
                and "HIGH" not in html_content
                and "MEDIUM" not in html_content
                and "LOW" not in html_content
            ):
                deductions.append("No urgency status indicator in HTML")
                item.score -= 1
            item.deductions = deductions
            item.reason = f"HTML email ({len(html_content)} chars)"
            item.score = max(0, item.score)
        items.append(item)

        return items

    # ------------------------------------------------------------------
    # Improvement suggestions generator
    # ------------------------------------------------------------------
    def _generate_suggestions(self, report: QualityReport) -> list[str]:
        suggestions = []
        for item in report.items:
            if item.score < item.max_score:
                gap = item.max_score - item.score
                if gap >= 2:
                    for d in item.deductions[:2]:
                        suggestions.append(f"[{item.category}/{item.name}] {d}")
        return suggestions[:10]


# ============================================================================
# CLI & Iteration Logic
# ============================================================================


def _print_quality_report(qr: QualityReport, verbose: bool = True):
    """Print quality report to console."""
    print(f"\n{'=' * 70}")
    print(
        f"  QUALITY SCORE: {qr.total_score}/{qr.max_score} ({qr.percentage:.1f}%) — Grade: {qr.grade}"
    )
    print(f"{'=' * 70}")

    # Category breakdown
    print(f"\n  Category Breakdown:")
    for cat, scores in sorted(qr.category_scores.items()):
        bar_len = int(scores["percentage"] / 5)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(
            f"    {cat:<22} {scores['score']:>3}/{scores['max']:<3} {bar} {scores['percentage']:.0f}%"
        )

    if verbose:
        # Detailed items
        print(f"\n  Detailed Scoring:")
        for item in qr.items:
            status = (
                "✅"
                if item.score == item.max_score
                else "⚠️" if item.score >= item.max_score * 0.5 else "❌"
            )
            print(f"    {status} {item.name:<30} {item.score}/{item.max_score} — {item.reason}")
            for d in item.deductions[:2]:
                print(f"       ↳ {d}")

    if qr.critical_issues:
        print(f"\n  🚨 Critical Issues:")
        for issue in qr.critical_issues:
            print(f"    • {issue}")

    if qr.improvement_suggestions:
        print(f"\n  💡 Improvement Suggestions:")
        for s in qr.improvement_suggestions[:5]:
            print(f"    • {s}")

    print(f"{'=' * 70}\n")


def _print_geval_report(gr: GEvalReport):
    """Print the G-Eval (LLM-as-a-Judge) multi-dimensional score to console."""
    print(f"\n{'=' * 70}")
    print(f"  G-EVAL SCORE: {gr.weighted_score:.2f}/5.00 ({gr.percentage:.0f}%) — {gr.grade}")
    print(
        f"  Target: {gr.target_score:.1f}/5.00  |  "
        f"Verdict: {'PASSED' if gr.passed else 'NEEDS IMPROVEMENT'}  |  "
        f"{gr.elapsed_s:.1f}s"
    )
    print("  (5.0 = 이론적 이상향 / unreachable ideal, 4.0 = 프로덕션 우수 / production-excellent)")
    print(f"{'=' * 70}")
    print("\n  Dimension Scores:")
    for d in gr.dimension_scores:
        bar_len = int(d.score / 5 * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        norm = "≈" if d.normalized else " "
        status = "✅" if d.score >= 4.0 else "⚠️" if d.score >= 3.0 else "❌"
        print(f"    {status} {d.title:<38} {norm}{d.score:>4.2f}/5  {bar}  (int {d.integer_score})")
        if d.error:
            print(f"       ↳ ⚠️ eval error: {d.error}")

    if gr.critical_flaws:
        print("\n  🚨 Critical Flaws (dimension ≤ 2):")
        for flaw in gr.critical_flaws:
            print(f"    • {flaw}")

    weak = sorted(
        [d for d in gr.dimension_scores if d.feedback.strip() and d.score < 5.0],
        key=lambda x: x.score,
    )
    if weak:
        print("\n  💡 Improvement Feedback (weakest first):")
        for d in weak[:5]:
            fb = d.feedback.strip().replace("\n", " ")
            print(f"    • [{d.title}] {fb[:160]}")
    print(f"{'=' * 70}\n")


def _save_iteration_artifacts(
    run_dir: Path,
    iteration: int,
    report_markdown: str,
    geval_report: Optional[GEvalReport],
    quality_report: Optional[QualityReport],
) -> None:
    """Persist the rendered report and scores for inspection."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"report_iter{iteration}.md").write_text(report_markdown, encoding="utf-8")
    artifact: dict[str, Any] = {"iteration": iteration}
    if geval_report is not None:
        artifact["geval"] = geval_report.as_dict()
    if quality_report is not None:
        artifact["rule_based"] = {
            "total_score": quality_report.total_score,
            "max_score": quality_report.max_score,
            "percentage": round(quality_report.percentage, 1),
            "grade": quality_report.grade,
            "category_scores": quality_report.category_scores,
            "critical_issues": quality_report.critical_issues,
        }
    (run_dir / f"geval_iter{iteration}.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _build_feedback_prompt(qr: QualityReport) -> str:
    """Build a feedback prompt from quality report for iterative improvement.

    Returns text to inject into the report prompt to address quality gaps.
    """
    if qr.percentage >= 95:
        return ""

    lines = ["## Quality Improvement Feedback (from previous iteration)"]
    lines.append(f"Previous score: {qr.total_score}/{qr.max_score} ({qr.percentage:.1f}%)")
    lines.append("")

    if qr.critical_issues:
        lines.append("### Critical Issues to Fix:")
        for issue in qr.critical_issues:
            lines.append(f"- {issue}")
        lines.append("")

    if qr.improvement_suggestions:
        lines.append("### Specific Improvements Needed:")
        for s in qr.improvement_suggestions[:8]:
            lines.append(f"- {s}")
        lines.append("")

    # Category-specific feedback
    for cat, scores in qr.category_scores.items():
        if scores["percentage"] < 80:
            lines.append(f"### {cat} ({scores['percentage']:.0f}% — needs improvement)")
            cat_items = [i for i in qr.items if i.category == cat and i.score < i.max_score]
            for item in cat_items[:3]:
                lines.append(f"- **{item.name}**: {item.reason}")
                for d in item.deductions[:2]:
                    lines.append(f"  - Fix: {d}")
            lines.append("")

    return "\n".join(lines)


async def run_evaluation(
    url: str = None,
    latest: bool = False,
    with_html: bool = False,
    iterations: int = 1,
    use_geval: bool = True,
    target: float = None,
    out_dir: str = None,
) -> None:
    """Run report generation and quality evaluation with a self-improvement loop.

    Each iteration: generate a report from real data → run the rule-based
    mechanical pre-check → run the G-Eval LLM-as-a-Judge semantic scoring →
    build combined feedback → inject it into the next generation. Stops when the
    G-Eval weighted score reaches the target (no critical flaws) or iterations run out.

    Args:
        url: Specific Azure Update URL.
        latest: Use the latest update.
        with_html: Also evaluate HTML email rendering.
        iterations: Number of generate-evaluate-improve iterations.
        use_geval: Enable the G-Eval LLM-as-a-Judge (semantic scoring).
        target: G-Eval passing threshold on the 1-5 scale (defaults to settings).
    """
    from src.logging_config import setup_logging

    setup_logging(console_level="CRITICAL")

    settings = get_settings()

    # Fetch the update
    parser = AzureUpdateParser()
    if latest:
        print("\n📡 Fetching latest Azure Update...")
        updates = await parser.get_updates()
        if not updates:
            print("❌ No updates found.")
            return
        update = updates[0]
    elif url:
        print(f"\n📡 Fetching update: {url}")
        update = await parser.get_update_by_url(url)
        if not update:
            details = await parser.fetch_update_details(url)
            update = AzureUpdate(
                id=url,
                title=details.get("title", "Unknown"),
                description=details.get("content", ""),
                link=url,
                published_date=None,
                categories=[],
                azure_services=[],
                update_type=None,
                status=None,
            )
    else:
        print("❌ Specify --url or --latest")
        return

    print(f"\n📢 Update: {update.title}")
    print(f"   Type: {update.update_type or 'N/A'}")
    print(
        f"   Services: {', '.join(update.azure_services[:3]) if update.azure_services else 'N/A'}"
    )
    print(f"   Link: {update.link}")

    analyzer = AzureUpdateAnalyzer()
    evaluator = ReportQualityEvaluator()
    email_service = EmailService()
    language = settings.report_language

    run_dir = _create_run_dir(Path(out_dir) if out_dir else None)
    print(f"\n📁 Evaluation artifacts → {run_dir} (gitignored)")

    judge: Optional[GEvalJudge] = None
    if use_geval and settings.geval_enabled:
        judge = GEvalJudge(target_score=target)
        print(
            f"\n⚖️  G-Eval judge enabled — target {judge.target_score:.1f}/5.0, "
            f"logprob normalization: {judge.enable_logprob_normalization}"
        )

    best_score = -1.0  # G-Eval weighted (0-5) or rule percentage/20 fallback
    best_result = None
    best_geval: Optional[GEvalReport] = None
    feedback_prompt = ""
    history: list[str] = []

    for iteration in range(1, iterations + 1):
        print(f"\n{'#' * 70}")
        print(f"  ITERATION {iteration}/{iterations}")
        print(f"{'#' * 70}")

        # Generate report
        print("\n🤖 Generating report from live Azure data...")
        t0 = time.monotonic()

        original_custom = analyzer.settings.custom_system_prompt or ""
        if feedback_prompt:
            analyzer.settings.custom_system_prompt = original_custom + "\n\n" + feedback_prompt
        try:
            result = await analyzer.analyze_update(update)
        finally:
            if feedback_prompt:
                analyzer.settings.custom_system_prompt = original_custom

        elapsed = time.monotonic() - t0
        print(f"✅ Report generated in {elapsed:.1f}s")

        # Render the reader-facing markdown (also used as HTML source + judge input)
        report_markdown = (
            judge.render_report_markdown(result, update, language)
            if judge
            else result.relevance_reason or ""
        )

        # Optionally generate HTML
        html_content = ""
        if with_html:
            try:
                email_data = email_service.build_email_content(update, result, language)
                html_content = email_data.get("html_content", "")
                html_path = run_dir / f"report_iter{iteration}.html"
                html_path.parent.mkdir(parents=True, exist_ok=True)
                html_path.write_text(html_content, encoding="utf-8")
                print(f"📄 HTML saved: {html_path}")
            except Exception as e:
                print(f"⚠️ HTML generation failed: {e}")

        # 1) Rule-based mechanical pre-check (fast, deterministic)
        print("\n📊 Rule-based mechanical pre-check...")
        qr = evaluator.evaluate(result, update, html_content, language)
        _print_quality_report(qr, verbose=(iterations == 1 or iteration == iterations))

        # 2) G-Eval semantic scoring (LLM-as-a-Judge)
        geval_report: Optional[GEvalReport] = None
        if judge:
            print("⚖️  G-Eval semantic scoring (5 dimensions, parallel)...")
            # Give the judge the same ground truth the report was built from so
            # environment-specific claims are judged fairly (faithfulness dimension).
            evidence_context = analyzer.build_evidence_context()

            geval_report = await judge.evaluate(
                result,
                update,
                language=language,
                report_markdown=report_markdown,
                update_context=getattr(analyzer, "_last_update_context", None) or None,
                evidence_context=evidence_context,
            )
            _print_geval_report(geval_report)

        _save_iteration_artifacts(run_dir, iteration, report_markdown, geval_report, qr)

        # Track best + history
        if geval_report is not None:
            current_score = geval_report.weighted_score
            history.append(
                f"iter{iteration}: G-Eval {geval_report.weighted_score:.2f}/5 "
                f"({geval_report.grade.split()[0]}), rule {qr.percentage:.0f}%"
            )
        else:
            current_score = qr.percentage / 20.0  # map 0-100% into 0-5 space
            history.append(f"iter{iteration}: rule {qr.percentage:.0f}% ({qr.grade})")

        if current_score > best_score:
            best_score = current_score
            best_result = result
            best_geval = geval_report

        # Stopping conditions
        if geval_report is not None:
            if geval_report.passed and not geval_report.critical_flaws:
                print(
                    f"🏆 Target reached! G-Eval {geval_report.weighted_score:.2f}/5.0 "
                    f">= {geval_report.target_score:.1f} with no critical flaws."
                )
                break
        elif qr.percentage >= 95:
            print(f"🏆 Near-perfect rule-based score achieved! ({qr.percentage:.1f}%)")
            break

        # Build combined feedback for the next iteration
        if iteration < iterations:
            geval_feedback = (
                judge.build_feedback_prompt(geval_report) if (judge and geval_report) else ""
            )
            rule_feedback = _build_feedback_prompt(qr)
            feedback_prompt = "\n\n".join(p for p in (geval_feedback, rule_feedback) if p)
            if not feedback_prompt:
                print("✅ No significant improvements possible.")
                break
            print(f"📝 Feedback generated for next iteration ({len(feedback_prompt)} chars)")

    # Final summary
    print(f"\n{'=' * 70}")
    print("  FINAL RESULT")
    print(f"{'=' * 70}")
    for line in history:
        print(f"    {line}")
    if best_geval is not None:
        print(
            f"\n  🥇 Best G-Eval: {best_geval.weighted_score:.2f}/5.00 "
            f"({best_geval.percentage:.0f}%) — {best_geval.grade}"
        )
    print(f"{'=' * 70}\n")


def main():
    parser = argparse.ArgumentParser(description="AzBrief Report Quality Evaluator (G-Eval)")
    parser.add_argument("--url", help="Azure Update URL to analyze")
    parser.add_argument("--latest", action="store_true", help="Analyze latest update")
    parser.add_argument("--with-html", action="store_true", help="Also evaluate HTML email")
    parser.add_argument("--iterate", type=int, default=1, help="Number of iterations (default: 1)")
    parser.add_argument(
        "--no-geval",
        action="store_true",
        help="Disable the G-Eval LLM-as-a-Judge (use rule-based scoring only)",
    )
    parser.add_argument(
        "--target",
        type=float,
        default=None,
        help="G-Eval passing threshold on the 1-5 scale (default: settings, 4.5)",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Directory for generated report/score artifacts "
        "(default: eval_runs/ at project root, gitignored)",
    )
    args = parser.parse_args()

    if not args.url and not args.latest:
        parser.print_help()
        return

    asyncio.run(
        run_evaluation(
            url=args.url,
            latest=args.latest,
            with_html=args.with_html,
            iterations=args.iterate,
            use_geval=not args.no_geval,
            target=args.target,
            out_dir=args.out_dir,
        )
    )


if __name__ == "__main__":
    main()
