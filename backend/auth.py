"""Provider-neutral authorization boundary for the Plex-first WatchMatch V2 server."""
from dataclasses import dataclass
from typing import Mapping, Protocol

from .config import validate_installation_config


@dataclass(frozen=True)
class ProviderIdentity:
    provider: str
    external_id: str


class AuthorizationProvider(Protocol):
    provider: str

    def identity(self, user_data: Mapping) -> ProviderIdentity: ...
    def has_configured_server_access(self, resources: object, machine_id: str) -> bool: ...


class PlexAuthorizationProvider:
    provider = "plex"

    def identity(self, user_data: Mapping) -> ProviderIdentity:
        external_id = str(user_data.get("uuid") or user_data.get("id") or "").strip()
        if not external_id:
            raise ValueError("Plex did not return a user identity")
        return ProviderIdentity(provider=self.provider, external_id=external_id)

    def has_configured_server_access(self, resources: object, machine_id: str) -> bool:
        resource_list = resources if isinstance(resources, list) else resources.get("resources", [])
        return any(
            item.get("clientIdentifier") == machine_id
            and "server" in item.get("provides", "")
            for item in resource_list
        )


_PROVIDERS: dict[str, AuthorizationProvider] = {"plex": PlexAuthorizationProvider()}


def selected_authorization_provider(config: Mapping) -> AuthorizationProvider:
    installation = validate_installation_config(config)
    return _PROVIDERS[installation["catalog_provider"]]
