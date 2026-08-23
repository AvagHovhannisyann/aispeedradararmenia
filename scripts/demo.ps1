# Run the entire RoadEye pipeline on a synthetic drive, start to finish — on Windows.
#
#   .\scripts\demo.ps1
#
# The PowerShell twin of demo.sh. It exists because the founder's laptop runs Windows
# and every instruction in this repository was written for a POSIX shell, so "does this
# thing work?" was a translation exercise on the one machine where it matters most.
#
# Needs no phone, no trained model, no footage, no GPU and no network.
#
# IMPORTANT: the detector used here is FAKE and the survey has no imagery. This
# demonstrates that the plumbing is correct — timing, positioning, deduplication,
# storage, export. It demonstrates nothing about detecting road damage.

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

# Windows venvs put executables in Scripts\, not bin/. That one difference is what
# every copied-from-the-README command trips over.
if (Test-Path ".venv\Scripts\python.exe") {
    $PY = ".venv\Scripts\python.exe"
    $ROADEYE = ".venv\Scripts\roadeye.exe"
} elseif (Get-Command roadeye -ErrorAction SilentlyContinue) {
    $PY = "python"
    $ROADEYE = "roadeye"
} else {
    Write-Host "RoadEye is not installed yet. Run:"
    Write-Host "    python -m venv .venv"
    Write-Host "    .venv\Scripts\pip install -e '.[dev]'"
    exit 1
}

$OUT = if ($args.Count -ge 1) { $args[0] } else { "demo_output" }
$MARKER = ".roadeye-demo"

function Rule($text) {
    Write-Host ""
    Write-Host "-- $text ---------------------------------------" -ForegroundColor White
}

# This script wipes its output directory so each run starts clean, and it takes that
# directory as an argument. Those two facts together mean `demo.ps1 C:\Users\You\Documents`
# would recursively delete Documents — so a directory is only ever deleted if a previous
# run left its marker in it. Anything else is refused rather than assumed to be ours.
if (Test-Path $OUT) {
    if (-not (Test-Path (Join-Path $OUT $MARKER))) {
        Write-Host "Refusing to touch '$OUT': it exists and this script did not create it." -ForegroundColor Red
        Write-Host ""
        Write-Host "This script deletes its output directory before each run. It will only do"
        Write-Host "that to a directory containing its own $MARKER file."
        Write-Host ""
        Write-Host "If this is a demo_output from before this check existed, deleting it is"
        Write-Host "safe - everything in it is regenerated. Otherwise pick a different path."
        exit 1
    }
    Remove-Item -Recurse -Force $OUT
}
New-Item -ItemType Directory -Path $OUT | Out-Null
Set-Content -Path (Join-Path $OUT $MARKER) `
    -Value "Created by scripts/demo.ps1. Its presence lets the next run delete this directory."

Rule "1/6  Run the test suite"
& $PY -m pytest -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Rule "2/6  Generate a synthetic Yerevan drive"
& $PY scripts\make_demo_survey.py "$OUT\survey"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Rule "3/6  Validate the survey bundle"
& $ROADEYE validate "$OUT\survey"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Rule "4/6  Process it into defects"
& $ROADEYE process "$OUT\survey" --db "$OUT\demo.db"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Rule "5/6  Assign each defect to a street"
# Synthetic streets, not OpenStreetMap: OSM is ODbL and share-alike applies to data,
# so no real geometry is committed here (docs/LICENSE_AUDIT.md, L-3).
& $PY scripts\make_demo_roads.py --output "$OUT\roads.json"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $ROADEYE match-roads --db "$OUT\demo.db" --roads "$OUT\roads.json"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Rule "6/6  Export for a municipality"
& $ROADEYE export --db "$OUT\demo.db" --geojson "$OUT\demo.geojson" --csv "$OUT\demo.csv"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Rule "Done"
Write-Host @"
>>> TO SEE THE DEFECTS ON A MAP, RUN THIS NOW:

        $PY scripts\view_map.py

    Then open the address it prints in your browser.

Everything else is in .\$OUT\ — note this folder is git-ignored, so VS Code greys
it out or hides it in the file explorer. That is normal; the files are there.

  index.html     the map page (served by the command above)
  demo.geojson   raw defect data - also opens at https://geojson.io
  demo.csv       the same data as a spreadsheet
  roads.json     the synthetic street network the defects were matched against
  demo.db        SQLite database

Reminder: the detector is FAKE and those street names are INVENTED. The markers
are synthetic noise placed on a real map, matched against streets that do not
exist. This run proves the pipeline works end to end; it proves nothing about
finding potholes or about Yerevan. See docs/MILESTONES.md.
"@
