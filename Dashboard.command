#!/bin/sh
# Bank statement dashboard launcher for macOS and Linux.
#
# Double-click this file in Finder for a menu, or run it with a command:
#   ./Dashboard.command update    import new statements, publish, then open the editor
#   ./Dashboard.command build     import new statements, rebuild, and publish if anything changed
#   ./Dashboard.command publish   publish the current dashboard to Cloudflare
#   ./Dashboard.command edit      open the dashboard with editable categories; edits publish themselves
#   ./Dashboard.command sync      only sync the code with GitHub
#
# Every run first syncs the code with GitHub (see sync below), so this Mac
# always has the latest version.
#
# The online copy follows the data on its own: build, update and edit publish
# whenever the ledger (or the code that draws it) changed since the last publish.
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

# Keep this Mac's copy of the code in step with GitHub before every run: offer
# to commit and push local code changes, then bring in anything new from GitHub.
# data/ is git-ignored, so statements, the ledger and the dashboard never leave
# this machine. Any problem only prints a warning; the dashboard still runs.
sync() {
    # The editing server publishes in the background and must never stop to ask.
    if [ "${DASHBOARD_SKIP_SYNC:-0}" = 1 ]; then
        return 0
    fi
    if ! command -v git >/dev/null 2>&1 || ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        return 0
    fi
    if ! git rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
        echo "Code sync skipped: this branch does not track a GitHub branch."
        echo
        return 0
    fi
    echo "Syncing the code with GitHub..."

    if [ -n "$(git status --porcelain)" ]; then
        echo "Local code changes:"
        git status --short
        printf "Commit and push them to GitHub? [Y/n]: "
        read -r ANSWER
        case "${ANSWER:-y}" in
            [Yy]*)
                printf "Describe the change [Update from Mac]: "
                read -r MESSAGE
                git add -A
                # Belt and braces for a public repo: nothing private may be committed.
                if git diff --cached --name-only | grep -Eq '^data/|\.pdf$|(^|/)index\.html$|\.csv$|statement_password'; then
                    git reset -q
                    echo "Stopped: a private file was about to be committed. Nothing was committed." >&2
                else
                    git commit -q -m "${MESSAGE:-Update from Mac}" && echo "Committed."
                fi
                ;;
            *) echo "Left uncommitted." ;;
        esac
    fi

    if ! git fetch -q 2>/dev/null; then
        echo "Could not reach GitHub, so the code was not synced. Continuing with this copy."
        echo
        return 0
    fi
    BEHIND=$(git rev-list --count 'HEAD..@{u}')
    AHEAD=$(git rev-list --count '@{u}..HEAD')
    if [ "$BEHIND" -gt 0 ]; then
        if [ -n "$(git status --porcelain)" ]; then
            echo "GitHub has $BEHIND new commit(s), but they were not pulled because of the uncommitted changes above."
        elif git merge -q --ff-only '@{u}' 2>/dev/null || git merge -q --no-edit '@{u}' 2>/dev/null; then
            echo "Pulled $BEHIND new commit(s) from GitHub."
        else
            git merge --abort 2>/dev/null
            echo "GitHub's changes conflict with this copy, so nothing was pulled. Ask for help merging them." >&2
        fi
        AHEAD=$(git rev-list --count '@{u}..HEAD')
    fi
    if [ "$AHEAD" -gt 0 ]; then
        if git push -q 2>/dev/null; then
            echo "Pushed $AHEAD commit(s) to GitHub."
        else
            echo "Could not push to GitHub; the commits stay on this Mac and will be pushed next time." >&2
        fi
    fi
    if [ "$BEHIND" -eq 0 ] && [ "$AHEAD" -eq 0 ]; then
        echo "Code is already up to date with GitHub."
    fi
    echo
}

rebuild() {
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
    FINGERPRINT=$(fingerprint)
    npx --yes wrangler@4 deploy --config deploy/wrangler.jsonc || return $?
    echo "$FINGERPRINT" > "$PUBLISHED_FINGERPRINT"
}

# What the online dashboard is drawn from: the ledger, the statement checks and
# the code that turns them into the page. The page itself carries a build time,
# so hashing it would make every rebuild look new.
PUBLISHED_FINGERPRINT="$DATA/.published_fingerprint"
fingerprint() {
    cat "$DATA/bank_transactions.csv" "$DATA/statement_reconciliation.json" \
        src/bank_statement_dashboard.py src/dashboard_template.html deploy/_headers 2>/dev/null |
        "$PYTHON" -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
}

# Publish only when something changed since the last successful publish. A
# failure only affects the online copy, so it warns and carries on.
publish_if_changed() {
    if [ -f "$PUBLISHED_FINGERPRINT" ] && [ "$(fingerprint)" = "$(cat "$PUBLISHED_FINGERPRINT")" ]; then
        echo "The online dashboard is already up to date."
        return 0
    fi
    echo "The dashboard has new data. Publishing it to Cloudflare..."
    if publish; then
        echo "Online dashboard updated."
    else
        echo "Publishing failed; the online dashboard was not updated." >&2
        echo "Choose Publish from the menu to try again." >&2
        return 1
    fi
}

build() {
    rebuild || return $?
    echo
    publish_if_changed
    return 0
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

    # The server publishes on its own shortly after the last category edit.
    DASHBOARD_SKIP_SYNC=1 "$PYTHON" src/dashboard_server.py --directory "$DATA" --port "$PORT" \
        --publish-after-edits 30 --publish-command "$ROOT/Dashboard.command" &
    SERVER_PID=$!
    trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT TERM HUP
    # Ctrl+C stops the server (it receives the signal too); carry on below so an
    # edit made just before stopping is still published.
    trap ':' INT
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
    echo "Category edits are published online about 30 seconds after the last change."
    wait "$SERVER_PID"
    wait "$SERVER_PID" 2>/dev/null
    trap - INT
    echo
    publish_if_changed
}

update() {
    rebuild
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
    publish_if_changed
    echo
    echo "Starting the local dashboard server with editable categories..."
    edit
}

COMMAND="${1:-}"
PAUSE=0
sync
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
    publish-if-changed) publish_if_changed; STATUS=$? ;;
    sync) STATUS=0 ;;
    none) STATUS=2 ;;
    *)
        echo "Usage: $0 [update|build|publish|edit|sync]" >&2
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
