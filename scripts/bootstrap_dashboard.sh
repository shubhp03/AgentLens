#!/usr/bin/env bash
# Generates the Angular app (standalone, routing, SCSS, no SSR) into dashboard/ without
# overwriting files that already exist (PLAN.md, Dockerfile, anything you wrote).
#
# Angular major is pinned (default 21). Angular 22's CLI needs Node >= 22.22.3 / 24.15 / 26; v21 runs on
# Node >= 20.19 / 22.12 / 24. Upgrade later with `ng update` when your Node is new enough.
set -euo pipefail
cd "$(dirname "$0")/.."
NG_MAJOR="${ANGULAR_MAJOR:-21}"

node -e 'const [a,b]=process.versions.node.split(".").map(Number);const ok=(a==20&&b>=19)||(a==22&&b>=12)||a>=24;if(!ok){console.error("Node "+process.versions.node+" is too old for Angular 21 (need >=20.19, >=22.12 or >=24)");process.exit(1)}'

ROOT="$PWD"
TMP="$(mktemp -d)"
# `ng new` creates the directory relative to the CWD, so run it inside the temp dir.
(cd "$TMP" && npx -y "@angular/cli@${NG_MAJOR}" new dashboard --routing --style=scss \
  --ssr=false --skip-git --skip-install --defaults)
cp -a --update=none "$TMP/dashboard/." "$ROOT/dashboard/"
rm -rf "$TMP"
# npm 10 can crash resolving jsdom's optional peers ("reading 'edgesOut'"); npm 11 does not.
(cd dashboard && (npm install || npx -y npm@11 install) && npm install chart.js && npm install -D openapi-typescript)
echo "Dashboard generated. Plan is in dashboard/PLAN.md"
