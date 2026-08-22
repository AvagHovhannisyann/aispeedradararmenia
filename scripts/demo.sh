#!/usr/bin/env bash
#
# Run the entire RoadEye pipeline on a synthetic drive, start to finish.
#
#   ./scripts/demo.sh
#
# Needs no phone, no trained model, no footage, no GPU and no network. It exists so
# that "does this thing work?" is one command rather than a setup exercise.
#
# IMPORTANT: the detector used here is FAKE and the survey has no imagery. This
# demonstrates that the plumbing is correct — timing, positioning, deduplication,
# storage, export. It demonstrates nothing about detecting road damage.

set -euo pipefail

cd "$(dirname "$0")/.."

# Prefer a local venv if one exists; otherwise assume roadeye is already on PATH
# (which is the case in the Codespace, where postCreateCommand installs it).
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
    ROADEYE=".venv/bin/roadeye"
elif command -v roadeye >/dev/null 2>&1; then
    PY="python3"
    ROADEYE="roadeye"
else
    echo "RoadEye is not installed yet. Run:"
    echo "    python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
    exit 1
fi

OUT="${1:-demo_output}"
rule() { printf '\n\033[1m%s\033[0m\n' "── $1 ─────────────────────────────────────────"; }

rm -rf "$OUT"
mkdir -p "$OUT"

rule "1/5  Run the test suite"
$PY -m pytest -q

rule "2/5  Generate a synthetic Yerevan drive"
$PY scripts/make_demo_survey.py "$OUT/survey"

rule "3/5  Validate the survey bundle"
$ROADEYE validate "$OUT/survey"

rule "4/5  Process it into defects"
$ROADEYE process "$OUT/survey" --db "$OUT/demo.db"

rule "5/5  Export for a municipality"
$ROADEYE export --db "$OUT/demo.db" --geojson "$OUT/demo.geojson" --csv "$OUT/demo.csv"

rule "Done"
cat <<EOF
Output is in ./$OUT/

  demo.geojson   open at https://geojson.io to see the defects on a real map
                 (they should trace an 800m x 400m rectangle in central Yerevan)
  demo.csv       the same data as a spreadsheet
  demo.db        SQLite database, inspect with:
                   sqlite3 $OUT/demo.db "SELECT defect_id, damage_class, confidence, uncertainty_m, status FROM defects LIMIT 5;"

Reminder: the detector is FAKE. Those markers are synthetic noise placed on a
real map. This run proves the pipeline works; it proves nothing about finding
potholes. A real detector arrives at milestone M3 (see docs/MILESTONES.md).
EOF
