#!/bin/zsh

# Double-click this file in Finder to import the latest statement and rebuild
# the offline dashboard.
set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Updating bank statement dashboard..."
echo

./run_dashboard.sh
STATUS=$?

if [ "$STATUS" -ne 0 ]; then
    echo
    echo "Dashboard update failed (exit code $STATUS)."
    echo "Review the message above, correct the issue, and double-click this file again."
    echo
    read -r "?Press Return to close this window..."
    exit "$STATUS"
fi

echo
echo "Dashboard update completed successfully."

# Publish the refreshed dashboard to Cloudflare. A failure here only affects
# the online copy; the local dashboard still opens below.
echo "Publishing the dashboard to Cloudflare..."
if ! ./deploy_dashboard.sh; then
    echo "Publishing failed; the online dashboard was not updated."
    echo "Double-click Publish Dashboard.command to try again."
fi
echo
echo "Starting the local dashboard server with editable categories..."

DASHBOARD_PYTHON=python3
if [ -x ".venv/bin/python" ]; then
    DASHBOARD_PYTHON=.venv/bin/python
fi

# Ask the OS for a free local port so an old dashboard process cannot block a
# new launch. The server is still bound to localhost only.
if ! DASHBOARD_PORT=$(
    "$DASHBOARD_PYTHON" -c 'import socket; sock = socket.socket(); sock.bind(("127.0.0.1", 0)); print(sock.getsockname()[1]); sock.close()' 2>/dev/null
); then
    echo "Could not find an available local port for the dashboard server."
    read -r "?Press Return to close this window..."
    exit 1
fi
if [ -z "$DASHBOARD_PORT" ]; then
    echo "Could not find an available local port for the dashboard server."
    read -r "?Press Return to close this window..."
    exit 1
fi

"$DASHBOARD_PYTHON" dashboard_server.py --directory "$SCRIPT_DIR" --port "$DASHBOARD_PORT" &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT
sleep 1

if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    wait "$SERVER_PID"
    STATUS=$?
    echo
    echo "Dashboard server failed to start (exit code $STATUS)."
    echo "The dashboard was updated, but no browser window was opened."
    read -r "?Press Return to close this window..."
    exit "$STATUS"
fi

DASHBOARD_URL="http://127.0.0.1:${DASHBOARD_PORT}/index.html"
if ! open "$DASHBOARD_URL"; then
    echo
    echo "Dashboard server started at $DASHBOARD_URL, but the browser could not be opened."
    echo "Open that address manually while leaving this window open."
else
    echo "Dashboard opened in your browser at $DASHBOARD_URL. Category edits save automatically."
fi

echo ""
echo "Leave this window open while using the editable dashboard."
echo
wait "$SERVER_PID"
