#!/usr/bin/env bash
set -euo pipefail

# === CONFIGURATION ===
REPO="DmytroMosnenko/adagent"  # GitHub org/repo
TOKEN="${GITHUB_PAT:-}"                # export GITHUB_PAT="ghp_..."
TAG="${1:-latest}"                     # pass version tag as $1, default = latest
API="https://api.github.com/repos/$REPO/releases"

# === CHECKS ===
if [[ -z "$TOKEN" ]]; then
  echo "❌ ERROR: Please set environment variable GITHUB_PAT with a valid GitHub token."
  exit 1
fi

# === FETCH RELEASE METADATA ===
if [[ "$TAG" == "latest" ]]; then
  echo "🔍 Fetching latest release metadata..."
  META=$(curl -s -H "Authorization: token $TOKEN" "$API/latest")
else
  echo "🔍 Fetching release metadata for tag $TAG..."
  META=$(curl -s -H "Authorization: token $TOKEN" "$API/tags/$TAG")
fi

# === PARSE ASSETS ===
ASSETS=$(echo "$META" | jq -r '.assets[] | select(.name | endswith(".rpm")) | "\(.id) \(.name)"')

if [[ -z "$ASSETS" ]]; then
  echo "❌ No RPM assets found for release $TAG"
  exit 1
fi

# === DOWNLOAD ALL RPMs ===
echo "$ASSETS" | while read -r ID NAME; do
  echo "⬇️  Downloading $NAME ..."
  curl -sL \
    -H "Authorization: token $TOKEN" \
    -H "Accept: application/octet-stream" \
    "$API/assets/$ID" \
    -o "$NAME"
done

echo "✅ All RPMs downloaded successfully."
