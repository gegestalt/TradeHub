#!/usr/bin/env bash
# Run the full backend test suite.
# Usage: ./run_tests.sh [pytest args]
# Examples:
#   ./run_tests.sh                        # all tests
#   ./run_tests.sh -k test_concurrency    # filter by name
#   ./run_tests.sh -v                     # verbose
#   ./run_tests.sh --tb=long              # full tracebacks

set -e
cd "$(dirname "$0")/backend"
exec uv run pytest "$@"
