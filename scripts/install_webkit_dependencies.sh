#!/usr/bin/env bash
# GitHub ubuntu-22.04 only; no production use. Keep signed APT admission.
set -euo pipefail

mirror=''
for candidate in \
  https://archive.ubuntu.com/ubuntu \
  http://azure.archive.ubuntu.com/ubuntu \
  http://archive.ubuntu.com/ubuntu; do
  if curl -4 --connect-timeout 3 --max-time 8 --retry 0 \
    --fail --silent --show-error --output /dev/null \
    "$candidate/dists/jammy/InRelease"; then
    mirror="$candidate"
    break
  fi
done
if [[ -z "$mirror" ]]; then
  echo 'CI_UBUNTU_MIRRORS_UNAVAILABLE' >&2
  exit 1
fi
echo "CI_UBUNTU_MIRROR=$mirror"

{
  for suite in jammy jammy-updates jammy-backports jammy-security; do
    echo "deb $mirror $suite main restricted universe multiverse"
  done
} | sudo tee /etc/apt/sources.list >/dev/null
sudo rm -f /etc/apt/sources.list.d/ubuntu.sources
sudo tee /etc/apt/apt.conf.d/99-ci-network >/dev/null <<'EOF'
Acquire::Retries "1";
Acquire::http::Timeout "10";
Acquire::https::Timeout "10";
Acquire::ForceIPv4 "true";
APT::Update::Error-Mode "any";
EOF
sudo rm -rf /var/lib/apt/lists/*
# A reachable InRelease is only a network probe. APT must still verify signed
# metadata and install every actual dependency; failures remain CI failures.
npx playwright install-deps webkit
