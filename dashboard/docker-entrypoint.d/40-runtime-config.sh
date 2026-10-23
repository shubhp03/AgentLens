#!/bin/sh
# Runs before nginx starts (official nginx image convention). Writes the SPA's runtime config.
set -eu
demo="${DEMO:-false}"
case "$demo" in true|false) ;; *) echo "DEMO must be true or false" >&2; exit 1 ;; esac
printf '{ "apiBaseUrl": "%s", "demo": %s }\n' "${API_BASE_URL:-}" "$demo" > /usr/share/nginx/html/config.json
