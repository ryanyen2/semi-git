#!/usr/bin/env bash
# Rebuild the workbench fixtures from real `ugh` output on the bikecount study project:
# names on most requests, one request taken out, a two-version choice left by a sync, a declared
# plan, an unsaved edit. Usage: dev/fixtures/build.sh [scratch-dir]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
UGH="$ROOT/.venv/bin/ugh"
W="${1:-$(mktemp -d)}"
rm -rf "$W" && mkdir -p "$W"
cd "$W"

tar -xzf "$ROOT/scripts/research/removal-sets/exp-bikecount.tgz" --exclude '._*'
mv exp-bikecount repo
cd repo
rm -rf .sgt/local
git config user.name "Mira Okafor" && git config user.email mira@example.org
"$UGH" init . >/dev/null
printf '{"tiers": [{"name": "check", "command": "python3 check.py"}]}\n' > .sgt/oracle.json
"$UGH" save -m "run the page check on every save" >/dev/null

name() { "$UGH" name "$1" "$2" >/dev/null; }
name 196f7b6 "hourly profile"
name b9f66c7 "weekday split"
name 66ca5b9 "event days"
name 0c56354 "event days"
name c360a3d "event days"
name 977e808 "monthly trend"
name ca89fc2 "side by side"
name e36a2d2 "year table"
name a656515 "csv export"
name 22496d8 "date window"

"$UGH" revert 0c56354 --yes >/dev/null

git init -q --bare "$W/remote.git"
git remote remove origin 2>/dev/null || true
git remote add origin "$W/remote.git"
git push -q origin HEAD:main
"$UGH" push origin main >/dev/null 2>&1 || true
git clone -q "$W/remote.git" "$W/other" 2>/dev/null
(
  cd "$W/other"
  git config user.name "Teo Brandt" && git config user.email teo@example.org
  "$UGH" init . >/dev/null
  sed -i '' 's|avg = round(sum(normal_days) / len(normal_days))|avg = round(sum(normal_days) / len(normal_days), 1)|' bikecount/metrics.py
  "$UGH" save -m "one decimal in the normal-day average" >/dev/null
  "$UGH" push origin main >/dev/null 2>&1
)
sed -i '' 's|avg = round(sum(normal_days) / len(normal_days))|avg = round(sum(normal_days) / max(len(normal_days), 1))|' bikecount/metrics.py
"$UGH" save -m "no division by zero in the normal-day average" >/dev/null
"$UGH" sync origin main >/dev/null

"$UGH" plan "rider counts" "weather overlay" >/dev/null
printf '\n\ndef rider_counts(rows):\n    return len(rows)\n' >> bikecount/metrics.py
"$UGH" status --json >/dev/null || true

OUT="$HERE"
CHOICE="$("$UGH" status --json | python3 -c 'import json,sys; c=json.load(sys.stdin)["choices"]; print(c[0]["address"] if c else "")')"
STEP="$("$UGH" log --json | python3 -c '
import json, sys
for s in json.load(sys.stdin)["saves"]:
    for st in s["steps"]:
        if st["kind"] == "change" and st["name"] and "::" in st["address"]:
            print(st["address"]); raise SystemExit')"
"$UGH" log --json > "$OUT/log.json"
"$UGH" status --json > "$OUT/status.json"
"$UGH" plan --json > "$OUT/plan.json"
"$UGH" show "event days" --json > "$OUT/show-name.json"
"$UGH" show "$STEP" --json > "$OUT/show-step.json"
"$UGH" show bikecount/metrics.py --json > "$OUT/show-file.json"
"$UGH" revert "hourly profile" --emit --json > "$OUT/revert-name.json"
"$UGH" revert "hourly profile" --emit --whole --json > "$OUT/revert-whole.json"
"$UGH" restore 0c56354 --emit --json > "$OUT/restore-out.json"
"$UGH" find event --json > "$OUT/find.json"
[ -n "$CHOICE" ] && "$UGH" resolve "$CHOICE" --json > "$OUT/resolve.json"
echo "fixtures in $OUT from $W/repo"
