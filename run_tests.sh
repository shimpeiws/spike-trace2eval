#!/bin/sh
# Run the t2e test suite (stdlib unittest; no external dependencies).
set -eu
cd "$(dirname "$0")"
PYTHONPATH=tools:tests exec python3 -m unittest discover -s tests -p 'test_*.py' "$@"
