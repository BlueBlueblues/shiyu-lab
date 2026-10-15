#!/usr/bin/env bash
# Local/new-environment startup only; performs no systemd or deployment changes.
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}"
