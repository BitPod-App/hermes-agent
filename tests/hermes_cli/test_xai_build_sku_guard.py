"""T01 pin (BIT-1411): Grok Build / code-fast SKUs never reach the xAI or xAI-OAuth pickers.

SuperGrok OAuth bills Build SKUs separately; merely being selectable leaks subscription usage.
"""
from hermes_cli import models
from hermes_cli.models_catalog_static import _XAI_STATIC_FALLBACK, _drop_xai_build_skus, _xai_finalize_catalog

BUILD_IDS = ["grok-build-0.1", "grok-build-latest", "grok-code-fast-2", "x-ai/grok-build-0.1"]


def test_finalize_drops_build_skus():
    ids = _xai_finalize_catalog(BUILD_IDS + ["grok-4.6", "grok-4.5"])
    assert not [m for m in ids if "build" in m or "code-fast" in m]
    assert ids[0] == "grok-4.6"


def test_static_fallback_has_no_build_sku():
    assert _drop_xai_build_skus(list(_XAI_STATIC_FALLBACK)) == list(_XAI_STATIC_FALLBACK)


def test_web_search_default_is_not_a_build_sku():
    from plugins.web.xai.provider import DEFAULT_MODEL

    assert "build" not in DEFAULT_MODEL and "code-fast" not in DEFAULT_MODEL


def test_live_fetcher_path_is_filtered(monkeypatch):
    for provider in ("xai", "xai-oauth"):
        monkeypatch.setattr(models, "_configured_relay_base_url", lambda _p: "")
        monkeypatch.setitem(models._PROVIDER_CATALOG_FETCHERS, provider, lambda _n, _f: list(BUILD_IDS) + ["grok-4.6"])
        ids = models.provider_model_ids(provider)
        assert ids and not [m for m in ids if "build" in m or "code-fast" in m], (provider, ids)


def test_cached_hit_and_stale_rows_are_filtered(monkeypatch):
    for provider in ("xai", "xai-oauth"):
        monkeypatch.setattr(models, "_cached_provider_model_ids_unguarded",
                            lambda *a, **k: ["grok-build-0.1", "grok-code-fast-1", "grok-4.6"])
        assert models.cached_provider_model_ids(provider) == ["grok-4.6"]
        assert models.cached_provider_model_ids(provider, non_blocking=True) == ["grok-4.6"]


def test_other_providers_untouched_by_cached_guard(monkeypatch):
    monkeypatch.setattr(models, "_cached_provider_model_ids_unguarded", lambda *a, **k: ["x-ai/grok-build-0.1"])
    assert models.cached_provider_model_ids("openrouter") == ["x-ai/grok-build-0.1"]


def test_cold_picker_models_dev_merge_is_filtered(monkeypatch):
    from hermes_cli import model_switch_providers as msp

    monkeypatch.setattr(models, "_cached_provider_model_ids_unguarded", lambda *a, **k: [])
    monkeypatch.setattr(models, "_merge_with_models_dev", lambda slug, ids: list(ids) + ["grok-build-0.1"])
    for slug in ("xai", "xai-oauth"):
        ids = msp._live_or_curated_ids(slug, {slug: ["grok-4.6"]}, non_blocking=True)
        assert "grok-build-0.1" not in ids and "grok-4.6" in ids, (slug, ids)


def test_final_picker_rows_drop_build_skus():
    from hermes_cli import model_switch_providers as msp

    rows = [{"slug": "xai-oauth", "is_current": True, "models": ["grok-build-0.1", "grok-4.6"], "total_models": 2},
            {"slug": "openrouter", "is_current": False, "models": ["x-ai/grok-build-0.1"], "total_models": 1}]
    out = {r["slug"]: r for r in msp._finalize_picker_rows(rows, {}, "")}
    assert out["xai-oauth"]["models"] == ["grok-4.6"] and out["xai-oauth"]["total_models"] == 1
    assert out["openrouter"]["models"] == ["x-ai/grok-build-0.1"]
