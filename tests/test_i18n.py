"""Tests for the language registry and its dependent loaders."""

import pytest

from src.agent.prompts import build_system_prompt, get_language_guide, get_translation_notes
from src.agent.prompts.languages import has_curated_guide
from src.config import Subscriber
from src.i18n import (
    DEFAULT_LANGUAGE,
    LanguageSpec,
    fallback_chain,
    get_language,
    is_supported,
    language_display,
    language_name,
    normalize_language,
    register_language,
    resolve,
    supported_language_codes,
)
from src.i18n.labels import get_labels, label_keys, missing_label_keys

CURATED = ("ko", "en", "ja")


class TestNormalization:
    def test_regional_tag_collapses_to_base(self):
        assert normalize_language("ko-KR") == "ko"
        assert normalize_language("ko_KR") == "ko"
        assert normalize_language("EN") == "en"

    def test_empty_falls_back_to_default(self):
        assert normalize_language("") == DEFAULT_LANGUAGE
        assert normalize_language("   ") == DEFAULT_LANGUAGE

    def test_unknown_code_is_preserved(self):
        assert normalize_language("fr-FR") == "fr"


class TestRegistry:
    def test_curated_languages_are_registered(self):
        assert set(CURATED) <= set(supported_language_codes())

    def test_unknown_code_synthesizes_a_spec(self):
        spec = get_language("fr")
        assert spec.code == "fr"
        assert spec.registered is False
        assert spec.fallback == DEFAULT_LANGUAGE
        assert is_supported("fr") is False

    def test_iso_name_is_used_for_unregistered_code(self):
        assert language_name("vi") == "Vietnamese"

    def test_display_name_includes_native_form(self):
        assert language_display("ko") == "Korean (한국어)"
        assert language_display("en") == "English"

    def test_default_language_is_the_chain_root(self):
        assert get_language(DEFAULT_LANGUAGE).fallback is None
        assert fallback_chain(DEFAULT_LANGUAGE) == (DEFAULT_LANGUAGE,)

    def test_chain_always_terminates_at_default(self):
        for code in (*CURATED, "fr", "zz"):
            assert fallback_chain(code)[-1] == DEFAULT_LANGUAGE

    def test_lang_attr_defaults_to_code(self):
        assert get_language("ja").lang_attr == "ja"


class TestResolve:
    def test_picks_the_requested_language(self):
        assert resolve({"ko": "가", "en": "a"}, "en") == "a"

    def test_falls_through_to_default(self):
        assert resolve({"ko": "가"}, "ja") == "가"

    def test_returns_default_when_nothing_matches(self):
        assert resolve({"de": "x"}, "fr", default="fallback") == "fallback"


class TestLabels:
    def test_curated_languages_translate_every_key(self):
        for code in CURATED:
            assert missing_label_keys(code) == frozenset(), f"{code} has untranslated labels"

    def test_unknown_language_gets_a_complete_bundle(self):
        labels = get_labels("fr")
        assert set(labels) == set(label_keys())
        assert labels["update_type"] == get_labels(DEFAULT_LANGUAGE)["update_type"]

    def test_regional_tag_resolves_to_base_bundle(self):
        assert get_labels("ja-JP") == get_labels("ja")


class TestStyleGuides:
    def test_curated_languages_ship_their_own_guide(self):
        for code in CURATED:
            assert has_curated_guide(code)

    def test_unknown_language_gets_a_synthesized_guide(self):
        assert not has_curated_guide("fr")
        guide = get_language_guide("fr")
        assert "French" in guide
        assert "Style Guide" in guide

    def test_guide_is_never_empty(self):
        for code in (*CURATED, "fr", "zz"):
            assert get_language_guide(code).strip()

    def test_report_phase_embeds_the_language_guide(self):
        prompt = build_system_prompt(phase="report", language="ja")
        assert get_language_guide("ja") in prompt

    def test_planning_phase_omits_the_language_guide(self):
        prompt = build_system_prompt(phase="planning", language="ja")
        assert get_language_guide("ja") not in prompt

    def test_translation_notes_are_optional(self):
        assert get_translation_notes("ko")
        assert get_translation_notes("fr") == ""


class TestRuntimeRegistration:
    def test_registering_a_language_invalidates_caches(self):
        code = "xq"
        assert not is_supported(code)
        before = get_language_guide(code)
        try:
            register_language(
                LanguageSpec(code=code, english_name="Testish", native_name="Tëstish")
            )
            assert is_supported(code)
            after = get_language_guide(code)
            assert after != before
            assert "Testish (Tëstish)" in after
        finally:
            from src.i18n import _REGISTRY

            _REGISTRY.pop(code, None)
            register_language(get_language(DEFAULT_LANGUAGE))


class TestRendersWithNewLanguage:
    """A language nobody has translated yet must still render, not crash."""

    def test_template_helpers_accept_an_unregistered_language(self):
        from src.email.templates import (
            format_additional_checks_html,
            get_importance_colors,
            get_relevance_colors,
        )

        assert get_relevance_colors("relevant", "fr")["label"]
        assert get_importance_colors("high", "fr")["label"]
        assert "check" in format_additional_checks_html(["check"], "fr")

    def test_html_lang_attribute_uses_the_registry(self):
        assert get_language("ja-JP").lang_attr == "ja"
        assert get_language("fr").lang_attr == "fr"


class TestConfigValidation:
    def test_report_language_accepts_registered_codes(self):
        from src.config import Settings

        for code in CURATED:
            assert Settings(azure_tenant_id="t", report_language=code).report_language == code

    def test_report_language_normalizes_regional_tags(self):
        from src.config import Settings

        assert Settings(azure_tenant_id="t", report_language="ko-KR").report_language == "ko"

    def test_report_language_rejects_unregistered_codes(self):
        from src.config import Settings

        with pytest.raises(ValueError, match="report_language must be one of"):
            Settings(azure_tenant_id="t", report_language="fr")

    def test_subscriber_language_normalizes_without_rejecting(self):
        assert Subscriber(email="a@b.c", name="A", language="ja-JP").language == "ja"
        assert Subscriber(email="a@b.c", name="A", language="fr").language == "fr"
