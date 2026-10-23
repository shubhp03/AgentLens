#!/usr/bin/env bash
# Builds the dashboard in DEMO mode for static hosting (GitHub Pages, Netlify, Cloudflare Pages, S3...).
# No backend is involved: the app serves the synthetic data in public/demo.
#   BASE_HREF=/agentlens/ bash scripts/build_pages.sh      # GitHub project page: https://<user>.github.io/agentlens/
#   BASE_HREF=/           bash scripts/build_pages.sh      # custom domain or Netlify/Cloudflare
# Output: dashboard/dist/dashboard/browser  (index.html is also copied to 404.html so deep links work on Pages)
set -euo pipefail
BASE_HREF="${BASE_HREF:-/}"
case "$BASE_HREF" in /*/|/) ;; *) echo "BASE_HREF must start and end with '/', got '$BASE_HREF'" >&2; exit 1 ;; esac
cd "$(dirname "$0")/../dashboard"
npm ci
npx ng build --configuration production --base-href "$BASE_HREF"
OUT=dist/dashboard/browser
cp public/config.demo.json "$OUT/config.json"
cp "$OUT/index.html" "$OUT/404.html"
touch "$OUT/.nojekyll"
echo "built $OUT with base href $BASE_HREF"
