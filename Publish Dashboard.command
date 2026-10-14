#!/bin/zsh

# Double-click this file in Finder to publish the current dashboard to
# Cloudflare without importing statements, for example after editing
# categories.
set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$SCRIPT_DIR"

./deploy_dashboard.sh
STATUS=$?
echo
if [ "$STATUS" -eq 0 ]; then
    echo "Dashboard published."
else
    echo "Publishing failed (exit code $STATUS)."
fi
read -r "?Press Return to close this window..."
exit "$STATUS"
