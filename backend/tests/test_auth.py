from backend.auth import PlexAuthorizationProvider, ProviderIdentity, selected_authorization_provider


def test_plex_authorization_provider_returns_a_provider_scoped_identity():
    assert PlexAuthorizationProvider().identity({"uuid": "user-123"}) == ProviderIdentity("plex", "user-123")


def test_plex_authorization_provider_requires_configured_server_access():
    provider = PlexAuthorizationProvider()
    assert provider.has_configured_server_access(
        [{"clientIdentifier": "server-1", "provides": "server,client"}], "server-1"
    )
    assert not provider.has_configured_server_access(
        [{"clientIdentifier": "other", "provides": "server"}], "server-1"
    )


def test_authorization_provider_uses_the_installation_selection():
    assert selected_authorization_provider({"installation": {"catalog_provider": "plex"}}).provider == "plex"
