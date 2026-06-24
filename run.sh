#!/usr/bin/env bash
# Launch AEON. Usage: ./run.sh
set -euo pipefail
cd "$(dirname "$0")"
exec python -m aeon "$@"
