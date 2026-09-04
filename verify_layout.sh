#!/usr/bin/env bash
# Confirms the repo is in publishable shape. Run from the project root.
ok(){   printf '\033[32m  ✓\033[0m %s\n' "$*"; }
bad(){  printf '\033[31m  ✗\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
warn(){ printf '\033[33m  !\033[0m %s\n' "$*"; }
FAIL=0

echo "── files that must exist ──"
for f in .gitignore .github/workflows/deploy.yml w3id/iitb/.htaccess \
         README.md LICENSE requirements.txt \
         pipeline/run_all.py pipeline/config.yml pipeline/wdnkit/__init__.py \
         pipeline/scripts/05_export_web.py \
         site/_quarto.yml site/index.qmd site/assets/explorer.js \
         site/assets/styles.css site/assets/custom.scss \
         site/data/manifest.json site/data/graph.ttl site/data/ontology.owl; do
  [ -e "$f" ] && ok "$f" || bad "$f  MISSING"
done

echo; echo "── site pages ──"
for n in index 01-source-data 02-hydraulic-model 03-sensor-placement \
         04-iot-node 05-ontology 06-knowledge-graph 07-explorer 08-reproduce; do
  [ -f "site/$n.qmd" ] && ok "site/$n.qmd" || bad "site/$n.qmd  MISSING"
done

echo; echo "── things that should NOT be here ──"
for f in deploy.yml gitignore.txt _site files code temp.bin temp.inp temp.rpt \
         site/06-explorer.qmd site/07-reproduce.qmd; do
  [ -e "$f" ] && bad "$f  should be moved or deleted" || ok "no $f"
done

echo; echo "── stale links ──"
if grep -rqn "06-explorer\|07-reproduce" site/*.qmd 2>/dev/null; then
  bad "stale page links:"; grep -rn "06-explorer\|07-reproduce" site/*.qmd
else ok "no stale page links"; fi

echo; echo "── placeholder username ──"
if grep -rqn "manishbilore" site/_quarto.yml README.md w3id/iitb/.htaccess 2>/dev/null; then
  warn "replace 'manishbilore' with your GitHub username in:"
  grep -rln "manishbilore" site/_quarto.yml README.md w3id/iitb/.htaccess 2>/dev/null | sed 's/^/      /'
else ok "username set"; fi

echo; echo "── build config ──"
grep -q "output-dir: _site" site/_quarto.yml 2>/dev/null \
  && ok "output-dir is site/_site" || bad "output-dir not updated in site/_quarto.yml"
grep -q "path: site/_site" .github/workflows/deploy.yml 2>/dev/null \
  && ok "workflow artifact path matches" || bad "workflow still points at the old _site"

echo; echo "── payload ──"
if [ -f site/data/manifest.json ]; then
  python3 - <<'PY'
import json
m=json.load(open("site/data/manifest.json")); c=m["counts"]
print(f"      {c['pipes']} pipes · {c['junctions']} junctions · "
      f"{c['sensors']} sensors · {c['triples']:,} triples")
PY
fi
du -sh site/data 2>/dev/null | sed 's/^/      /'

echo
[ "$FAIL" -eq 0 ] && printf '\033[32mLayout clean.\033[0m\n' \
                  || printf '\033[31m%d problem(s).\033[0m\n' "$FAIL"
