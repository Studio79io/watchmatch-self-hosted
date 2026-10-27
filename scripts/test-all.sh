#!/bin/sh
set -eu

# This separate project has no fixed container name, published app port, or
# persistent database mount. It never reads an installation's .env or state.
compose_test() {
  docker compose --env-file test.env --project-name watchmatch-test \
    -f docker-compose.yml -f compose.test.yml "$@"
}

cleanup() {
  compose_test down --volumes --remove-orphans
}
trap cleanup EXIT

compose_test --profile test run --rm --build test
compose_test --profile browser-test run --rm --build browser-test
compose_test --profile test stop postgres-test
