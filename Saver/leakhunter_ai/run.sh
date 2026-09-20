#!/usr/bin/env bash
set -euo pipefail
export LEAKHUNTER_DATA_DIR="${LEAKHUNTER_DATA_DIR:-./data}"
export LEAKHUNTER_DB_PATH="${LEAKHUNTER_DB_PATH:-$LEAKHUNTER_DATA_DIR/leakhunter.db}"
mkdir -p "$LEAKHUNTER_DATA_DIR"
streamlit run app.py --server.address=0.0.0.0 --server.port="${PORT:-8501}"
