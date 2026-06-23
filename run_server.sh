#!/bin/bash
# Script to launch the SynEPD Mechanistic Web Service

# Navigate to the project root directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "================================================"
echo "Starting SynEPD Mechanistic Web Service..."
export SYNEPD_DATABASE_URL="${SYNEPD_DATABASE_URL:-data/epdb.sqlite}"
echo "Database location: $SYNEPD_DATABASE_URL"
echo "URL: http://127.0.0.1:8000"
echo "================================================"

# Launch the server via Python module syntax
python3 -m uvicorn synepd.web.server:app --host 127.0.0.1 --port 8000 --reload
