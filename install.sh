#!/usr/bin/env bash
# Install the LibreOffice MCP server and register it with local agent clients.
#
#   ./install.sh                  install + register with every client found
#   ./install.sh --check          only report what is present, change nothing
#   ./install.sh --no-claude      skip Claude Code registration
#   ./install.sh --no-opencode    skip opencode registration
#   ./install.sh --allow-exec     enable the lo_run_uno escape-hatch tool
#   ./install.sh --port 2003      use a different UNO socket port
#   ./install.sh --roots "$HOME:/srv/docs"   dirs documents may live in
#   ./install.sh --uninstall      remove the server and its registrations
set -euo pipefail

NAME="libreoffice"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${LO_MCP_PREFIX:-$HOME/.local/share/libreoffice-mcp}"
SERVER="$PREFIX/lo_mcp_server.py"
PORT="${LO_MCP_PORT:-2002}"
ROOTS="${LO_MCP_ROOTS:-$HOME:/tmp}"
PROFILE="${LO_MCP_PROFILE:-$HOME/.cache/lo-mcp-profile}"
ALLOW_EXEC=0
DO_CLAUDE=1; DO_OPENCODE=1; CHECK_ONLY=0; UNINSTALL=0; ASSUME_YES=0

while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK_ONLY=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --no-claude) DO_CLAUDE=0 ;;
    --no-opencode) DO_OPENCODE=0 ;;
    --allow-exec) ALLOW_EXEC=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    --port) PORT="$2"; shift ;;
    --roots) ROOTS="$2"; shift ;;
    --prefix) PREFIX="$2"; SERVER="$PREFIX/lo_mcp_server.py"; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m   %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m %s\n' "$*"; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- uninstall
if [ "$UNINSTALL" = 1 ]; then
  say "Uninstalling"
  command -v claude >/dev/null 2>&1 && \
    { claude mcp remove "$NAME" --scope user >/dev/null 2>&1 && ok "unregistered from Claude Code" || warn "not registered with Claude Code"; }
  OC="$HOME/.config/opencode/opencode.json"
  if [ -f "$OC" ]; then
    python3 - "$OC" "$NAME" <<'PY'
import json,sys
p,n=sys.argv[1],sys.argv[2]
d=json.load(open(p))
if d.get("mcp",{}).pop(n,None) is not None:
    json.dump(d,open(p,"w"),indent=2); open(p,"a").write("\n")
PY
    ok "unregistered from opencode"
  fi
  pkill -f "UserInstallation=.*lo-mcp-profile" 2>/dev/null && ok "stopped headless LibreOffice" || true
  :
  rm -rf "$PREFIX" && ok "removed $PREFIX"
  echo "LibreOffice itself and its profile ($PROFILE) were left in place."
  exit 0
fi

# ------------------------------------------------------- 1. LibreOffice
say "Checking LibreOffice"
install_lo() {
  if   command -v apt-get >/dev/null 2>&1; then
    echo "  sudo apt-get install -y libreoffice-writer libreoffice-calc libreoffice-impress libreoffice-draw python3-uno"
    [ "$CHECK_ONLY" = 1 ] && return 1
    sudo apt-get update -qq && sudo apt-get install -y \
      libreoffice-writer libreoffice-calc libreoffice-impress libreoffice-draw python3-uno
  elif command -v dnf >/dev/null 2>&1; then
    [ "$CHECK_ONLY" = 1 ] && { echo "  sudo dnf install -y libreoffice-writer libreoffice-calc libreoffice-impress libreoffice-draw libreoffice-pyuno"; return 1; }
    sudo dnf install -y libreoffice-writer libreoffice-calc libreoffice-impress libreoffice-draw libreoffice-pyuno
  elif command -v pacman >/dev/null 2>&1; then
    [ "$CHECK_ONLY" = 1 ] && { echo "  sudo pacman -S --needed libreoffice-fresh"; return 1; }
    sudo pacman -S --needed --noconfirm libreoffice-fresh
  elif command -v zypper >/dev/null 2>&1; then
    [ "$CHECK_ONLY" = 1 ] && { echo "  sudo zypper install -y libreoffice libreoffice-pyuno"; return 1; }
    sudo zypper install -y libreoffice libreoffice-pyuno
  elif command -v brew >/dev/null 2>&1; then
    [ "$CHECK_ONLY" = 1 ] && { echo "  brew install --cask libreoffice"; return 1; }
    brew install --cask libreoffice
  else
    die "no supported package manager found; install LibreOffice and its Python-UNO bindings manually"
  fi
}

SOFFICE=""
for c in soffice libreoffice /Applications/LibreOffice.app/Contents/MacOS/soffice; do
  command -v "$c" >/dev/null 2>&1 && { SOFFICE="$(command -v "$c")"; break; }
  [ -x "$c" ] && { SOFFICE="$c"; break; }
done
if [ -n "$SOFFICE" ]; then
  ok "$SOFFICE — $("$SOFFICE" --version 2>/dev/null | head -1)"
else
  warn "LibreOffice not found"
  install_lo || { [ "$CHECK_ONLY" = 1 ] && warn "would install LibreOffice (see command above)"; }
  SOFFICE="$(command -v soffice || true)"
fi

# --------------------------------------------- 2. a Python that has 'uno'
say "Looking for a Python with UNO bindings"
PYBIN=""
for c in /usr/bin/python3 "$(command -v python3 || true)" \
         /usr/lib/libreoffice/program/python \
         /opt/libreoffice*/program/python \
         /Applications/LibreOffice.app/Contents/Resources/python; do
  [ -n "$c" ] && [ -x "$c" ] || continue
  if "$c" -c "import uno, unohelper" >/dev/null 2>&1; then PYBIN="$c"; break; fi
done
if [ -z "$PYBIN" ]; then
  warn "no Python can 'import uno' — installing the bindings"
  install_lo || true
  for c in /usr/bin/python3 "$(command -v python3 || true)"; do
    [ -n "$c" ] && "$c" -c "import uno" >/dev/null 2>&1 && { PYBIN="$c"; break; }
  done
fi
[ -n "$PYBIN" ] || die "still no Python with UNO bindings (Debian/Ubuntu: python3-uno, Fedora: libreoffice-pyuno)"
ok "$PYBIN ($("$PYBIN" --version 2>&1)) can import uno"

if [ "$CHECK_ONLY" = 1 ]; then
  say "Check only — nothing was changed."
  exit 0
fi

# --------------------------------------------------- 3. install the server
say "Installing server to $PREFIX"
mkdir -p "$PREFIX"
install -m 0755 "$SRC_DIR/lo_mcp_server.py" "$SERVER"
[ -f "$SRC_DIR/test_e2e.py" ] && install -m 0755 "$SRC_DIR/test_e2e.py" "$PREFIX/test_e2e.py"
ok "$SERVER"

say "Smoke-testing the UNO bridge (starts headless LibreOffice, may take ~20s)"
if LO_MCP_PORT="$PORT" LO_MCP_PROFILE="$PROFILE" LO_MCP_ROOTS="$ROOTS" \
   "$PYBIN" "$SERVER" --selftest 2>/dev/null | grep -q '"connected": true'; then
  ok "bridge up on port $PORT"
else
  die "could not talk to LibreOffice; run: $PYBIN $SERVER --selftest"
fi

ENVARGS=(LO_MCP_PORT="$PORT" LO_MCP_PROFILE="$PROFILE" LO_MCP_ROOTS="$ROOTS")
[ "$ALLOW_EXEC" = 1 ] && ENVARGS+=(LO_MCP_ALLOW_EXEC=1)

# ----------------------------------------------------- 4. register clients
if [ "$DO_CLAUDE" = 1 ] && command -v claude >/dev/null 2>&1; then
  say "Registering with Claude Code (user scope)"
  claude mcp remove "$NAME" --scope user >/dev/null 2>&1 || true
  EARGS=(); for e in "${ENVARGS[@]}"; do EARGS+=(-e "$e"); done
  claude mcp add "$NAME" --scope user "${EARGS[@]}" -- "$PYBIN" "$SERVER" \
    && ok "claude mcp: $NAME" || warn "claude registration failed"
elif [ "$DO_CLAUDE" = 1 ]; then
  warn "claude CLI not found — skipping"
fi

if [ "$DO_OPENCODE" = 1 ] && [ -d "$HOME/.config/opencode" ]; then
  say "Registering with opencode"
  OC="$HOME/.config/opencode/opencode.json"
  [ -f "$OC" ] || echo '{"$schema":"https://opencode.ai/config.json"}' > "$OC"
  cp "$OC" "$OC.bak.$(date +%Y%m%d%H%M%S)"
  "$PYBIN" - "$OC" "$NAME" "$PYBIN" "$SERVER" "${ENVARGS[@]}" <<'PY'
import json, sys
path, name, py, server = sys.argv[1:5]
env = dict(kv.split("=", 1) for kv in sys.argv[5:])
d = json.load(open(path))
d.setdefault("mcp", {})[name] = {
    "type": "local", "enabled": True,
    "command": [py, server], "environment": env,
}
json.dump(d, open(path, "w"), indent=2)
open(path, "a").write("\n")
PY
  ok "opencode mcp: $NAME (backup written alongside opencode.json)"
elif [ "$DO_OPENCODE" = 1 ]; then
  warn "no ~/.config/opencode — skipping"
fi

# ------------------------------------- 5. generic config for other clients
SNIP="$PREFIX/mcp-config-snippet.json"
"$PYBIN" - "$SNIP" "$NAME" "$PYBIN" "$SERVER" "${ENVARGS[@]}" <<'PY'
import json, sys
path, name, py, server = sys.argv[1:5]
env = dict(kv.split("=", 1) for kv in sys.argv[5:])
json.dump({"mcpServers": {name: {"command": py, "args": [server], "env": env}}},
          open(path, "w"), indent=2)
open(path, "a").write("\n")
PY
ok "generic client snippet: $SNIP"

cat <<EOF

$(say "Done")
  server    $SERVER
  python    $PYBIN
  port      $PORT      profile $PROFILE
  roots     $ROOTS
  exec tool $([ "$ALLOW_EXEC" = 1 ] && echo enabled || echo disabled)

Next steps
  * Claude Code: restart it, then run /mcp — you should see "$NAME" with 32 tools.
  * opencode:    restart it; the server appears as a local MCP.
  * Other clients (grok, Cursor, Zed, ...): paste $SNIP
    into their MCP config (they all use the mcpServers command/args/env shape).
  * Full test:   $PYBIN $PREFIX/test_e2e.py
EOF
