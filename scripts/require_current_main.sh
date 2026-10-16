#!/usr/bin/env bash
# The caller holds the serialized production lease. Never deliver an older
# queued SHA after a newer main, even when Actions start jobs out of order.
set -euo pipefail
: "${EXPECTED_SHA:?Missing exact SHA}"
: "${GITHUB_REPOSITORY:?Missing repository}"
: "${GH_TOKEN:?Missing read token}"
[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$GITHUB_REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]
body=$(curl --fail --silent --show-error --max-time 8 \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $GH_TOKEN" \
  "https://api.github.com/repos/${GITHUB_REPOSITORY}/git/ref/heads/main")
current_main=$(printf '%s' "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin)["object"]["sha"])')
[[ "$current_main" =~ ^[0-9a-f]{40}$ ]]
if [ "$current_main" != "$EXPECTED_SHA" ]; then
  echo "::error::SUPERSEDED_PRODUCTION_SHA expected=$EXPECTED_SHA current_main=$current_main"
  exit 1
fi
echo "CURRENT_MAIN_VERIFIED expected_sha=$EXPECTED_SHA"
