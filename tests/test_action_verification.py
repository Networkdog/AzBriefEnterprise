"""Tests for the multi-layer action-item safety gate."""

import json

import pytest

from src.agent.action_verification import (
    STATUS_BLOCKED,
    STATUS_CAUTION,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
    ActionItemVerifier,
    apply_static_verification,
    build_evidence,
    build_source_evidence,
    verify_static,
)
from src.agent.analyzer import ActionItem

EVIDENCE = build_evidence(
    "Storage accounts: stgprod (rg-prod, koreacentral), stgarchive (rg-data)",
    {
        "t1": (
            "name=stgprod, minimumTlsVersion=TLS1_0, publicNetworkAccess=Enabled\n"
            "retirement date 2026-09-30 announced"
        )
    },
)


def _codes(findings) -> set:
    return {f.code for f in findings}


class FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeLLM:
    """Minimal stand-in for a chat model that returns a canned review."""

    def __init__(self, payload, *, raise_error: bool = False) -> None:
        self._payload = payload
        self._raise_error = raise_error
        self.calls: list[list] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if self._raise_error:
            raise RuntimeError("model unavailable")
        content = self._payload if isinstance(self._payload, str) else json.dumps(self._payload)
        return FakeResponse(content)


# ---------------------------------------------------------------------------
# Layer 1 — static safety gate
# ---------------------------------------------------------------------------


class TestStaticGate:
    def test_unattended_destructive_is_blocking(self):
        item = ActionItem(
            task="delete", cli_command="az storage account delete -n stgprod -g rg-prod --yes"
        )
        findings = verify_static(item, EVIDENCE)
        assert "unattended_destructive" in _codes(findings)
        assert any(f.severity == "blocking" for f in findings)

    def test_destructive_without_rollback_is_blocking(self):
        item = ActionItem(
            task="delete", cli_command="az storage account delete -n stgprod -g rg-prod"
        )
        assert "destructive_no_rollback" in _codes(verify_static(item, EVIDENCE))

    def test_destructive_with_rollback_and_scope_passes(self):
        item = ActionItem(
            task="delete",
            cli_command="az storage account delete -n stgprod -g rg-prod",
            rollback="Restore from soft-delete within 14 days",
        )
        assert verify_static(item, EVIDENCE) == []

    def test_unscoped_destructive_is_blocking(self):
        item = ActionItem(task="purge", cli_command="az keyvault purge", rollback="n/a")
        assert "unscoped_destructive" in _codes(verify_static(item, EVIDENCE))

    def test_unresolved_placeholder_is_blocking(self):
        item = ActionItem(
            task="update",
            cli_command="az storage account update -n <your-account> -g rg-prod",
            rollback="revert",
        )
        findings = verify_static(item, EVIDENCE)
        assert "unresolved_placeholder" in _codes(findings)
        assert any(f.severity == "blocking" for f in findings)

    def test_delete_retention_flag_is_not_destructive(self):
        """--delete-retention-days contains the verb but is not a delete operation."""
        item = ActionItem(
            task="retention",
            cli_command=(
                "az storage blob service-properties update --account-name stgprod "
                "--delete-retention-days 7"
            ),
            rollback="set to 0",
        )
        assert verify_static(item, EVIDENCE) == []

    def test_list_deleted_is_not_destructive(self):
        item = ActionItem(
            task="list", cli_command="az keyvault key list-deleted --vault-name kvprod"
        )
        assert verify_static(item, EVIDENCE) == []

    def test_jmespath_braces_are_not_placeholders(self):
        item = ActionItem(
            task="query",
            cli_command='az storage account list --query "[].{name:name,tls:minimumTlsVersion}"',
        )
        assert verify_static(item, EVIDENCE) == []

    def test_create_command_is_exempt_from_grounding(self):
        """A resource being created legitimately does not exist in the evidence yet."""
        item = ActionItem(
            task="create",
            cli_command="az storage account create -n stgnew -g rg-prod",
            rollback="delete the new account",
        )
        assert "ungrounded_command_target" not in _codes(verify_static(item, EVIDENCE))

    def test_ungrounded_mutation_target_is_flagged(self):
        item = ActionItem(
            task="update",
            cli_command="az storage account update -n stgghost -g rg-prod",
            rollback="revert",
        )
        assert "ungrounded_command_target" in _codes(verify_static(item, EVIDENCE))

    def test_read_only_ungrounded_target_is_not_flagged(self):
        """A wrong name on a read-only command just errors out; it is not a risk."""
        item = ActionItem(task="show", cli_command="az storage account show -n stgghost -g rg")
        assert verify_static(item, EVIDENCE) == []

    def test_prose_target_is_not_treated_as_identifier(self):
        item = ActionItem(task="review", target_resources=["모든 스토리지 계정", "stgprod"])
        assert verify_static(item, EVIDENCE) == []

    def test_ungrounded_target_resource_is_flagged(self):
        item = ActionItem(task="review", target_resources=["stgghost"])
        assert "ungrounded_target" in _codes(verify_static(item, EVIDENCE))

    def test_grounded_deadline_passes(self):
        item = ActionItem(task="migrate", deadline="2026-09-30")
        assert verify_static(item, EVIDENCE) == []

    def test_deadline_from_the_announcement_is_grounded(self):
        """A retirement date legitimately comes from the notice, not the tenant."""
        item = ActionItem(task="migrate", deadline="2027-03-31 (공지된 기능 종료일)")
        env = build_evidence("Storage accounts: stgprod", {})
        source = build_source_evidence(env, "This feature will be retired on 2027-03-31.")
        assert "fabricated_deadline" in _codes(verify_static(item, env))
        assert verify_static(item, env, source) == []

    @pytest.mark.parametrize(
        "announcement",
        [
            "will be retired on September 30, 2026.",
            "will be retired on 30 September 2026.",
            "retirement is planned for September 2026.",
            "2026년 9월 30일에 종료됩니다.",
            "sunset date: 2026-09-30.",
        ],
    )
    def test_iso_deadline_matches_any_source_date_format(self, announcement: str):
        """The notice writes prose, the report writes ISO — the same date must match."""
        item = ActionItem(task="migrate", deadline="2026-09-30")
        env = build_evidence("Storage accounts: stgprod", {})
        source = build_source_evidence(env, announcement)
        assert verify_static(item, env, source) == []

    def test_wrong_month_is_still_flagged(self):
        """Normalization must not turn the check into a rubber stamp."""
        item = ActionItem(task="migrate", deadline="2026-11-30")
        env = build_evidence("Storage accounts: stgprod", {})
        source = build_source_evidence(env, "will be retired on September 30, 2026.")
        assert "fabricated_deadline" in _codes(verify_static(item, env, source))

    def test_korean_deadline_matches_english_source(self):
        item = ActionItem(task="migrate", deadline="2026년 9월 30일")
        env = build_evidence("Storage accounts: stgprod", {})
        source = build_source_evidence(env, "will be retired on September 30, 2026.")
        assert verify_static(item, env, source) == []

    def test_fabricated_deadline_is_flagged(self):
        item = ActionItem(task="migrate", deadline="2027-01-15")
        assert "fabricated_deadline" in _codes(verify_static(item, EVIDENCE))

    def test_relative_deadline_is_flagged(self):
        item = ActionItem(task="migrate", deadline="within 2 weeks")
        assert "fabricated_deadline" in _codes(verify_static(item, EVIDENCE))

    def test_missing_rollback_on_mutation(self):
        item = ActionItem(
            task="enable", cli_command="az storage account update -n stgprod -g rg-prod --set x=1"
        )
        assert "missing_rollback" in _codes(verify_static(item, EVIDENCE))

    def test_no_evidence_skips_grounding_checks(self):
        """Without evidence there is nothing to ground against — do not guess."""
        item = ActionItem(
            task="update",
            cli_command="az storage account update -n whatever -g rg",
            target_resources=["whatever"],
            deadline="2030-01-01",
            rollback="revert",
        )
        assert verify_static(item, evidence="") == []

    def test_findings_are_sorted_blocking_first(self):
        item = ActionItem(
            task="delete",
            cli_command="az storage account delete -n <name> -g rg-prod --yes",
            deadline="within 3 days",
        )
        findings = verify_static(item, EVIDENCE)
        severities = [f.severity for f in findings]
        assert severities == sorted(severities, key=lambda s: 0 if s == "blocking" else 1)


class TestFindingMessages:
    def test_message_is_localized_in_all_three_languages(self):
        findings = verify_static(
            ActionItem(task="x", cli_command="az group delete -n rg-prod --yes"), EVIDENCE
        )
        assert findings
        for lang in ("ko", "en", "ja"):
            for f in findings:
                assert f.message(lang), f"{f.code} has no {lang} message"

    def test_unknown_language_falls_back_to_korean(self):
        f = verify_static(ActionItem(task="x", deadline="within 2 weeks"), EVIDENCE)[0]
        assert f.message("de") == f.message("ko")


# ---------------------------------------------------------------------------
# Layer 2 + 3 — cross-check and policy gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestVerifier:
    async def test_clean_item_is_verified_when_cross_check_passes(self):
        item = ActionItem(
            step=1, task="check", cli_command="az storage account show -n stgprod -g rg-prod"
        )
        llm = FakeLLM({"reviews": [{"step": 1, "verdict": "safe", "defect": "", "correction": ""}]})
        summary = await ActionItemVerifier(llm=llm).verify([item], evidence=EVIDENCE)

        assert item.verification_status == STATUS_VERIFIED
        assert item.verification_notes == []
        assert summary.verified == 1
        assert summary.cross_check_ran is True
        assert summary.passed is True

    async def test_unsafe_cross_check_blocks_and_withholds_command(self):
        item = ActionItem(
            step=1, task="check", cli_command="az storage account show -n stgprod -g rg-prod"
        )
        llm = FakeLLM(
            {
                "reviews": [
                    {
                        "step": 1,
                        "verdict": "unsafe",
                        "defect": "리소스 이름이 증거에 없습니다",
                        "correction": "실제 계정명을 확인하세요",
                    }
                ]
            }
        )
        summary = await ActionItemVerifier(llm=llm).verify([item], evidence=EVIDENCE)

        assert item.verification_status == STATUS_BLOCKED
        assert item.cli_command == ""  # never copy-pasteable
        assert summary.withheld_commands == 1
        assert summary.passed is False
        assert any("리소스 이름이 증거에 없습니다" in n for n in item.verification_notes)

    async def test_caution_cross_check_downgrades_without_withholding(self):
        item = ActionItem(step=1, task="check", cli_command="az storage account list")
        llm = FakeLLM(
            {
                "reviews": [
                    {"step": 1, "verdict": "caution", "defect": "범위 확인 필요", "correction": ""}
                ]
            }
        )
        await ActionItemVerifier(llm=llm).verify([item], evidence=EVIDENCE)

        assert item.verification_status == STATUS_CAUTION
        assert item.cli_command == "az storage account list"

    async def test_missing_llm_marks_unverified_not_verified(self):
        """A cross-check that never ran must not look like a passed one."""
        item = ActionItem(step=1, task="check", cli_command="az storage account list")
        summary = await ActionItemVerifier(llm=None).verify([item], evidence=EVIDENCE)

        assert item.verification_status == STATUS_UNVERIFIED
        assert summary.cross_check_ran is False
        assert item.verification_notes  # tells the reader the check was skipped

    async def test_llm_failure_degrades_to_unverified(self):
        item = ActionItem(step=1, task="check", cli_command="az storage account list")
        llm = FakeLLM(None, raise_error=True)
        summary = await ActionItemVerifier(llm=llm).verify([item], evidence=EVIDENCE)

        assert item.verification_status == STATUS_UNVERIFIED
        assert summary.cross_check_ran is False

    async def test_unparsable_review_degrades_to_unverified(self):
        item = ActionItem(step=1, task="check", cli_command="az storage account list")
        llm = FakeLLM("I refuse to answer in JSON.")
        summary = await ActionItemVerifier(llm=llm).verify([item], evidence=EVIDENCE)

        assert item.verification_status == STATUS_UNVERIFIED
        assert summary.cross_check_ran is False

    async def test_static_blocking_wins_even_if_llm_says_safe(self):
        """Layer 1 is deterministic; a model opinion cannot override it."""
        item = ActionItem(
            step=1,
            task="delete",
            cli_command="az storage account delete -n stgprod -g rg-prod --yes",
        )
        llm = FakeLLM({"reviews": [{"step": 1, "verdict": "safe", "defect": "", "correction": ""}]})
        await ActionItemVerifier(llm=llm).verify([item], evidence=EVIDENCE)

        assert item.verification_status == STATUS_BLOCKED
        assert item.cli_command == ""

    async def test_evidence_and_items_reach_the_reviewer_prompt(self):
        item = ActionItem(step=1, task="rotate keys", cli_command="az storage account keys renew")
        llm = FakeLLM({"reviews": [{"step": 1, "verdict": "safe"}]})
        await ActionItemVerifier(llm=llm).verify(
            [item], update_context="Storage TLS retirement", evidence=EVIDENCE
        )

        prompt = llm.calls[0][1].content
        assert "rotate keys" in prompt
        assert "stgprod" in prompt
        assert "Storage TLS retirement" in prompt

    async def test_empty_items_short_circuits(self):
        llm = FakeLLM({"reviews": []})
        summary = await ActionItemVerifier(llm=llm).verify([], evidence=EVIDENCE)
        assert summary.total == 0
        assert llm.calls == []


class TestStaticReverification:
    def test_rewritten_item_is_reverified(self):
        """A subscriber rewrite must not inherit the base report's verdict."""
        item = ActionItem(
            step=1,
            task="delete",
            cli_command="az storage account delete -n stgprod -g rg-prod --yes",
            verification_status=STATUS_VERIFIED,
        )
        summary = apply_static_verification([item], EVIDENCE, language="ko")

        assert item.verification_status == STATUS_BLOCKED
        assert item.cli_command == ""
        assert summary.withheld_commands == 1

    def test_clean_item_stays_unverified_without_cross_check(self):
        item = ActionItem(step=1, task="check", cli_command="az storage account list")
        apply_static_verification([item], EVIDENCE)
        assert item.verification_status == STATUS_UNVERIFIED


class TestBuildEvidence:
    def test_evidence_is_lowercased_and_merged(self):
        ev = build_evidence("Account STGPROD", {"t1": "VNet HubNet"})
        assert "stgprod" in ev
        assert "hubnet" in ev

    def test_handles_missing_task_results(self):
        assert build_evidence("summary", None) == "summary"

    def test_announcement_text_never_grounds_a_resource_name(self):
        """Only the tenant's own data may prove a resource exists."""
        env = build_evidence("Account stgprod", {})
        source = build_source_evidence(env, "Sample account contoso999 is affected.")
        item = ActionItem(
            task="update",
            cli_command="az storage account update -n contoso999 -g rg",
            rollback="revert",
        )
        assert "ungrounded_command_target" in _codes(verify_static(item, env, source))
