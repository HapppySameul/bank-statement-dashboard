#!/bin/sh
# Bank statement dashboard launcher for macOS and Linux.
#
# Double-click this file in Finder for a menu, or run it with a command:
#   ./Dashboard.command update    import new statements, publish, then open the editor
#   ./Dashboard.command build     import new statements and rebuild the dashboard only
#   ./Dashboard.command publish   publish the current dashboard to Cloudflare
#   ./Dashboard.command edit      open the dashboard with editable categories
#
# Statements, the ledger and the generated dashboard live in data/, which is
# git-ignored.
set -u

ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
DATA="$ROOT/data"
cd "$ROOT" || exit 1

PYTHON=python3
if [ -x "$ROOT/.venv/bin/python" ]; then
    PYTHON="$ROOT/.venv/bin/python"
fi

build() {
    mkdir -p "$DATA"
    echo "Updating bank statement dashboard..."
    echo
    "$PYTHON" src/bank_statement_dashboard.py \
        --statement-dir "$DATA" \
        --history-file "$DATA/bank_transactions.csv" \
        --categories-file "$DATA/merchant_categories.json" \
        --reconciliation-file "$DATA/statement_reconciliation.json" \
        --import-log-file "$DATA/statement_import_log.json" \
        --output-html "$DATA/index.html"
}

# Upload data/index.html to Cloudflare. The dashboard embeds every transaction,
# so it is published from this machine and never committed. Needs Node.js; the
# first run opens a browser to sign in to Cloudflare.
publish() {
    if [ ! -f "$DATA/index.html" ]; then
        echo "data/index.html not found. Run the dashboard update first." >&2
        return 1
    fi
    if ! command -v npx >/dev/null 2>&1; then
        echo "Node.js (npx) is not installed, so the dashboard was not published." >&2
        echo "Install it from https://nodejs.org and run this again." >&2
        return 1
    fi
    # Publish from a folder that holds nothing but the page and its headers.
    rm -rf deploy/site
    mkdir deploy/site
    cp "$DATA/index.html" deploy/_headers deploy/site/
    npx --yes wrangler@4 deploy --config deploy/wrangler.jsonc
}

# Serve the dashboard on localhost so category edits save back to data/.
edit() {
    if [ ! -f "$DATA/index.html" ]; then
        echo "data/index.html not found. Run the dashboard update first." >&2
        return 1
    fi
    # Ask the OS for a free local port so an old dashboard process cannot block
    # a new launch. The server is still bound to localhost only.
    PORT=$("$PYTHON" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()' 2>/dev/null)
    if [ -z "$PORT" ]; then
        echo "Could not find an available local port for the dashboard server." >&2
        return 1
    fi

    "$PYTHON" src/dashboard_server.py --directory "$DATA" --port "$PORT" &
    SERVER_PID=$!
    trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT INT TERM
    sleep 1
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        wait "$SERVER_PID"
        STATUS=$?
        echo "Dashboard server failed to start (exit code $STATUS)." >&2
        return "$STATUS"
    fi

    URL="http://127.0.0.1:${PORT}/index.html"
    if command -v open >/dev/null 2>&1 && open "$URL"; then
        echo "Dashboard opened in your browser at $URL. Category edits save automatically."
    elif command -v xdg-open >/dev/null 2>&1 && xdg-open "$URL"; then
        echo "Dashboard opened in your browser at $URL. Category edits save automatically."
    else
        echo "Open $URL in your browser. Category edits save automatically."
    fi
    echo
    echo "Leave this window open while using the editable dashboard. Press Ctrl+C to stop."
    wait "$SERVER_PID"
}

update() {
    build
    STATUS=$?
    if [ "$STATUS" -ne 0 ]; then
        echo
        echo "Dashboard update failed (exit code $STATUS)."
        echo "Review the message above, correct the issue, and run the update again."
        return "$STATUS"
    fi
    echo
    echo "Dashboard update completed successfully."
    # A publishing failure only affects the online copy; the local editor still opens.
    echo "Publishing the dashboard to Cloudflare..."
    if ! publish; then
        echo "Publishing failed; the online dashboard was not updated."
        echo "Choose Publish from the menu to try again."
    fi
    echo
    echo "Starting the local dashboard server with editable categories..."
    edit
}

COMMAND="${1:-}"
PAUSE=0
if [ -z "$COMMAND" ]; then
    # Started by double-clicking: offer a menu and keep the window open at the end.
    PAUSE=1
    echo "Bank statement dashboard"
    echo
    echo "  1) Update   import new statements, publish, and open the editor"
    echo "  2) Publish  publish the current dashboard to Cloudflare"
    echo "  3) Edit     open the dashboard with editable categories"
    echo
    printf "Choose 1, 2 or 3 [1]: "
    read -r CHOICE
    case "${CHOICE:-1}" in
        1) COMMAND=update ;;
        2) COMMAND=publish ;;
        3) COMMAND=edit ;;
        *) echo "Unknown choice: $CHOICE"; COMMAND=none ;;
    esac
fi

case "$COMMAND" in
    update|build|publish|edit) "$COMMAND"; STATUS=$? ;;
    none) STATUS=2 ;;
    *)
        echo "Usage: $0 [update|build|publish|edit]" >&2
        STATUS=2
        ;;
esac

if [ "$COMMAND" = publish ] && [ "$STATUS" -eq 0 ]; then
    echo
    echo "Dashboard published."
fi
if [ "$PAUSE" -eq 1 ]; then
    echo
    printf "Press Return to close this window..."
    read -r _
fi
exit "$STATUS"
