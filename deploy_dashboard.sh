#!/usr/bin/env sh
# Upload the locally generated index.html to Cloudflare. The dashboard embeds
# every transaction, so it is published from this machine and never committed.
# Needs Node.js; the first run opens a browser to sign in to Cloudflare.
set -eu
cd "$(dirname "$0")"

if [ ! -f index.html ]; then
  echo "index.html not found. Run the dashboard update first." >&2
  exit 1
fi
if ! command -v npx >/dev/null 2>&1; then
  echo "Node.js (npx) is not installed, so the dashboard was not published." >&2
  echo "Install it from https://nodejs.org and run this again." >&2
  exit 1
fi

# Publish from a folder that holds nothing but the page and its headers.
rm -rf site
mkdir site
cp index.html _headers site/

npx --yes wrangler@4 deploy
