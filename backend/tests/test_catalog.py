from backend.catalog import CatalogItemRef, PlexCatalogProvider, selected_catalog_provider, sync_catalog
from backend.config import validate_installation_config


def test_installation_defaults_to_the_plex_provider_for_v1_compatibility():
    assert validate_installation_config({}) == {"catalog_provider": "plex"}


def test_installation_rejects_an_unsupported_catalog_provider():
    try:
        validate_installation_config({"installation": {"catalog_provider": "netflix"}})
    except ValueError as exc:
        assert "Unsupported catalog provider" in str(exc)
    else:
        raise AssertionError("unsupported provider should fail validation")


def test_plex_provider_exposes_provider_scoped_external_identifiers():
    assert PlexCatalogProvider().item_ref("123") == CatalogItemRef(
        provider="plex", external_id="123"
    )


def test_selected_provider_is_plex_and_preserves_the_existing_sync_path(monkeypatch):
    expected = {"mode": "demo", "count": 5}
    monkeypatch.setattr("backend.catalog.sync_plex", lambda config: expected)
    config = {"installation": {"catalog_provider": "plex"}}

    assert selected_catalog_provider(config).provider == "plex"
    assert sync_catalog(config) == expected
