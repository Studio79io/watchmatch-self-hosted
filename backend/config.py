import os
import re
from pathlib import Path

import yaml


_ENV = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")
SUPPORTED_CATALOG_PROVIDERS = {"plex"}


def _expand(value):
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.getenv(m.group(1), m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def validate_installation_config(config):
    """Validate the one-installation V2 boundary without changing V1 defaults."""
    installation = config.get("installation", {})
    if not isinstance(installation, dict):
        raise ValueError("installation configuration must be an object")
    provider = str(installation.get("catalog_provider", "plex")).strip().lower()
    if provider not in SUPPORTED_CATALOG_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_CATALOG_PROVIDERS))
        raise ValueError(
            f"Unsupported catalog provider {provider!r}; supported providers: {supported}"
        )
    return {"catalog_provider": provider}


def load_config():
    path = Path(os.getenv("CONFIG_PATH", "config.yaml"))
    if not path.exists():
        path = Path("config.example.yaml")
    config = _expand(yaml.safe_load(path.read_text()))
    config["installation"] = validate_installation_config(config)
    return config
