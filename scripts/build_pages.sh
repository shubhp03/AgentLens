#!/usr/bin/env bash
# Builds the dashboard in read-only static mode for hosting (GitHub Pages, Netlify, Cloudflare Pages, S3...).
# No backend is involved. DATASET picks what is served:
#   DATASET=recorded  (default) real runs captured from a live stack, public/replay (scripts/replay.py). No synthetic data is shipped.
#   DATASET=synthetic           fictional companies, public/demo (scripts/gen_demo_data.py), for exploring the UI.
#   BASE_HREF=/agentlens/ bash scripts/build_pages.sh      # GitHub project page: https://<user>.github.io/agentlens/
#   BASE_HREF=/           bash scripts/build_pages.sh      # custom domain or Netlify/Cloudflare
# Output: dashboard/dist/dashboard/browser  (index.html is also copied to 404.html so deep links work on Pages)
set -euo pipefail
BASE_HREF="${BASE_HREF:-/}"
DATASET="${DATASET:-recorded}"
case "$DATASET" in recorded|synthetic) ;; *) echo "DATASET must be recorded or synthetic, got '$DATASET'" >&2; exit 1 ;; esac
case "$BASE_HREF" in /*/|/) ;; *) echo "BASE_HREF must start and end with '/', got '$BASE_HREF'" >&2; exit 1 ;; esac
cd "$(dirname "$0")/../dashboard"
npm ci
npx ng build --configuration production --base-href "$BASE_HREF"
OUT=dist/dashboard/browser
if [ "$DATASET" = recorded ]; then
  [ -f public/replay/runs.json ] || { echo "no public/replay data: run scripts/replay.py export first" >&2; exit 1; }
  cp public/config.replay.json "$OUT/config.json"
  rm -rf "$OUT/demo"   # never ship the synthetic data next to the real replay
else
  cp public/config.demo.json "$OUT/config.json"
  rm -rf "$OUT/replay"
fi
cp "$OUT/index.html" "$OUT/404.html"
touch "$OUT/.nojekyll"
echo "built $OUT with base href $BASE_HREF"
