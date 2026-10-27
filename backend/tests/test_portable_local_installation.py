from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_portable_setup_creates_private_local_configuration_without_secrets_in_source():
    script = (ROOT / "scripts" / "prepare-local-install.sh").read_text()

    assert "umask 077" in script
    assert "POSTGRES_PASSWORD=$database_password" in script
    assert "config.example.yaml config.yaml" in script
    assert "docker compose --env-file .env config --quiet" in script
    assert "PLEX_TOKEN=" in script
    assert "POSTGRES_PASSWORD=watchmatch-dev-only" not in script


def test_compose_requires_a_password_but_keeps_local_state_isolated():
    compose = (ROOT / "docker-compose.yml").read_text()

    assert compose.count("${POSTGRES_PASSWORD:-}") == 2
    assert "${POSTGRES_DATA_PATH:-./postgres-data}:/var/lib/postgresql" in compose
    assert "5432:" not in compose
    assert "host.docker.internal:host-gateway" in compose
    assert "container_name:" not in compose
    assert "${WATCHMATCH_PORT:-8080}:8080" in compose


def test_browser_suite_uses_a_disposable_environment_without_reading_local_env():
    test_script = (ROOT / "scripts" / "test-all.sh").read_text()
    test_environment = (ROOT / "test.env").read_text()

    assert "--env-file test.env --project-name watchmatch-test" in test_script
    assert "-f docker-compose.yml -f compose.test.yml" in test_script
    assert "down --volumes --remove-orphans" in test_script
    assert "POSTGRES_DATA_PATH=./test-postgres-data" in test_environment
    assert "POSTGRES_PASSWORD=watchmatch-test-only" in test_environment
    assert "--env-file .env" not in test_script
