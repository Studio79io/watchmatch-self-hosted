"""Provider-neutral catalog synchronization boundary for WatchMatch V2."""

from dataclasses import dataclass
from typing import Mapping, Protocol

from .config import validate_installation_config
from .plex import sync_plex


@dataclass(frozen=True)
class CatalogItemRef:
    """A provider-scoped catalog identifier.

    Existing V1 persistence continues to use the current Plex-derived media IDs.
    This V2 foundation establishes the explicit provider namespace before a
    later, separately reviewed persistence migration.
    """

    provider: str
    external_id: str


class CatalogProvider(Protocol):
    provider: str

    def sync(self, config: Mapping) -> dict:
        """Synchronize this provider's catalog into the existing local store."""

    def item_ref(self, external_id: str) -> CatalogItemRef:
        """Return the provider-scoped identifier for an upstream catalog item."""


class PlexCatalogProvider:
    provider = "plex"

    def sync(self, config: Mapping) -> dict:
        return sync_plex(config)

    def item_ref(self, external_id: str) -> CatalogItemRef:
        return CatalogItemRef(provider=self.provider, external_id=str(external_id))


_PROVIDERS: dict[str, CatalogProvider] = {"plex": PlexCatalogProvider()}


def selected_catalog_provider(config: Mapping) -> CatalogProvider:
    installation = validate_installation_config(config)
    return _PROVIDERS[installation["catalog_provider"]]


def sync_catalog(config: Mapping) -> dict:
    """Run the selected single-installation catalog provider."""
    return selected_catalog_provider(config).sync(config)
