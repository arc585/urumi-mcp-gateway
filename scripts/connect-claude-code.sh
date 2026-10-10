#!/bin/bash
# Register the gateway in Claude Code as an HTTP MCP server named "urumi", authenticated with one
# member's API key. The key is typed at a hidden prompt: it is not echoed, not in shell history,
# and not passed on this script's command line. Claude Code stores it in your ~/.claude.json.
#
# Scope "local" = only Claude Code sessions opened in this project folder see the server.
#
# Usage: bash scripts/connect-claude-code.sh [gateway-mcp-url]
set -euo pipefail

URL="${1:-http://app.urumi.test:8880/mcp}"
cd "$(dirname "$0")/.."

# Use `claude` from PATH, or the copy bundled with the Claude desktop app.
CLAUDE="$(command -v claude || true)"
if [ -z "$CLAUDE" ]; then
  CLAUDE="$(ls -t "$HOME/Library/Application Support/Claude/claude-code"/*/*/claude.app/Contents/MacOS/claude 2>/dev/null | head -1 || true)"
fi
if [ -z "$CLAUDE" ] || [ ! -x "$CLAUDE" ]; then
  echo "Claude Code CLI not found. Install it, or add the server from the dashboard's 'Keys & clients' page instructions." >&2
  exit 1
fi

read -r -s -p "Paste the member's gateway API key (urm_...): " KEY
echo
case "$KEY" in
  urm_*) ;;
  *) echo "That does not look like a gateway key (it should start with urm_)." >&2; exit 1 ;;
esac

"$CLAUDE" mcp remove urumi -s local >/dev/null 2>&1 || true
"$CLAUDE" mcp add --transport http --scope local urumi "$URL" --header "Authorization: Bearer $KEY" \
  | sed -E 's/urm_[A-Za-z0-9_-]+/urm_<redacted>/g'
unset KEY
echo "Health check:"
"$CLAUDE" mcp list 2>/dev/null | grep '^urumi' || echo "urumi not listed — check the URL and key"
