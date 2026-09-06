#!/usr/bin/env bash
#
# Copyright 2026 Joe Landman
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0
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
ALLOW_EXEC=0; SANDBOX=0
DO_CLAUDE=1; DO_OPENCODE=1; CHECK_ONLY=0; UNINSTALL=0; ASSUME_YES=0

usage() {
  cat <<'USAGE'
Install the LibreOffice MCP server and register it with local agent clients.

  ./install.sh                  install + register with every client found
  ./install.sh --check          only report what is present, change nothing
  ./install.sh --uninstall      remove the server and its registrations

  --no-claude                   skip Claude Code registration
  --no-opencode                 skip opencode registration
  --sandbox                     run the server inside a bubblewrap confinement
                                that can only see the --roots directories
  --allow-exec                  enable the lo_run_uno escape-hatch tool
  --port PORT                   UNO socket port (default 2002)
  --roots "DIR:DIR"             dirs documents may be read/written under
  --prefix DIR                  install location (default ~/.local/share/libreoffice-mcp)
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK_ONLY=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --no-claude) DO_CLAUDE=0 ;;
    --no-opencode) DO_OPENCODE=0 ;;
    --allow-exec) ALLOW_EXEC=1 ;;
    --sandbox) SANDBOX=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    --port) PORT="$2"; shift ;;
    --roots) ROOTS="$2"; shift ;;
    --prefix) PREFIX="$2"; SERVER="$PREFIX/lo_mcp_server.py"; shift ;;
    -h|--help) usage; exit 0 ;;
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

# What the MCP client will actually launch. Without --sandbox that is the
# interpreter and the server; with it, the wrapper that confines both.
CMD_BIN="$PYBIN"; CMD_ARGS=("$SERVER")

if [ "$SANDBOX" = 1 ]; then
  say "Building the bubblewrap confinement"
  command -v bwrap >/dev/null 2>&1 || die \
    "bwrap not found. Install it (Debian/Ubuntu: bubblewrap, Fedora: bubblewrap, Arch: bubblewrap) or drop --sandbox."
  # Mirrors the wrapper's own mounts: without the lib symlinks the dynamic
  # loader is missing and every exec fails with a misleading ENOENT.
  if ! bwrap --ro-bind /usr /usr \
             --symlink usr/lib /lib --symlink usr/lib64 /lib64 \
             --symlink usr/bin /bin --symlink usr/sbin /sbin \
             --proc /proc --dev /dev --unshare-net \
             --die-with-parent /usr/bin/true 2>/dev/null; then
    die "bwrap is installed but cannot create a namespace here (unprivileged user namespaces may be disabled: sysctl kernel.unprivileged_userns_clone). Drop --sandbox or use another mechanism."
  fi
  ok "bwrap works"

  # A confinement that can see all of $HOME confines nothing. Say so.
  IFS=':' read -r -a _roots <<< "$ROOTS"
  for r in "${_roots[@]}"; do
    case "$r" in
      "$HOME"|/|/home) warn "root '$r' is your whole home or the filesystem root — the confinement will not meaningfully limit reach. Consider --roots \"\$HOME/Documents\"." ;;
    esac
  done

  SBTMP="$HOME/.cache/lo-mcp-sandbox-tmp"
  WRAPPER="$PREFIX/lo-mcp-sandboxed.sh"
  {
    printf '%s\n' '#!/usr/bin/env bash'
    printf '%s\n' '#'
    printf '%s\n' '# Generated by install.sh --sandbox. Runs the MCP server and the'
    printf '%s\n' '# headless LibreOffice it starts inside one bubblewrap namespace, so'
    printf '%s\n' '# both see only the directories bound below. The agent speaks to it'
    printf '%s\n' '# over inherited stdio, which is why no network is needed at all.'
    printf '%s\n' '#'
    printf '%s\n' '# This raises the cost of a mistake. It does not make one impossible.'
    printf '%s\n' 'set -euo pipefail'
    echo
    printf 'SBTMP=%q
' "$SBTMP"
    printf 'PROFILE=%q
' "$PROFILE"
    printf '%s\n' 'mkdir -p "$SBTMP" "$PROFILE"'
    for r in "${_roots[@]}"; do printf 'mkdir -p %q
' "$r"; done
    echo
    printf '%s\n' 'exec bwrap \'
    printf '%s\n' '  --ro-bind /usr /usr --ro-bind /etc /etc \'
    printf '%s\n' '  --symlink usr/lib /lib --symlink usr/lib64 /lib64 \'
    printf '%s\n' '  --symlink usr/bin /bin --symlink usr/sbin /sbin \'
    printf '%s\n' '  --proc /proc --dev /dev \'
    printf '%s\n' '  --tmpfs /run --tmpfs /var --tmpfs /home --tmpfs /root \'
    [ -d /opt ] && printf '%s\n' '  --ro-bind-try /opt /opt \'
    printf '  --ro-bind %q %q \
' "$PREFIX" "$PREFIX"
    case "$PYBIN" in /usr/*) ;; *) printf '  --ro-bind-try %q %q \
' "$(dirname "$(dirname "$PYBIN")")" "$(dirname "$(dirname "$PYBIN")")" ;; esac
    printf '  --bind "$SBTMP" /tmp \
'
    printf '  --bind "$PROFILE" "$PROFILE" \
'
    for r in "${_roots[@]}"; do printf '  --bind %q %q \
' "$r" "$r"; done
    printf '  --setenv HOME %q \
' "$PROFILE"
    printf '  --setenv LO_MCP_PORT %q \
' "$PORT"
    printf '  --setenv LO_MCP_PROFILE %q \
' "$PROFILE"
    printf '  --setenv LO_MCP_ROOTS %q \
' "$ROOTS"
    printf '%s\n' '  --setenv LO_MCP_SANDBOX bwrap \'
    printf '%s\n' '  --setenv LO_MCP_OWN_SOFFICE_ONLY 1 \'
    [ "$ALLOW_EXEC" = 1 ] && printf '%s\n' '  --setenv LO_MCP_ALLOW_EXEC 1 \'
    printf '%s\n' '  --unshare-net --unshare-pid --unshare-ipc --unshare-uts \'
    printf '%s\n' '  --die-with-parent --new-session \'
    printf '  %q %q "$@"
' "$PYBIN" "$SERVER"
  } > "$WRAPPER"
  chmod 0755 "$WRAPPER"
  ok "$WRAPPER"

  say "Smoke-testing the confinement"
  if printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"install"}}}' \
     | timeout 90 "$WRAPPER" 2>/dev/null | grep -q '"serverInfo"'; then
    ok "server starts inside the confinement"
  else
    die "the confined server did not respond; run it by hand to see why: $WRAPPER"
  fi

  CMD_BIN="$WRAPPER"; CMD_ARGS=()
  # The wrapper carries the settings itself; nothing to leak through the client.
  ENVARGS=()
fi

# ----------------------------------------------------- 4. register clients
if [ "$DO_CLAUDE" = 1 ] && command -v claude >/dev/null 2>&1; then
  say "Registering with Claude Code (user scope)"
  claude mcp remove "$NAME" --scope user >/dev/null 2>&1 || true
  EARGS=(); for e in ${ENVARGS[@]+"${ENVARGS[@]}"}; do EARGS+=(-e "$e"); done
  claude mcp add "$NAME" --scope user "${EARGS[@]}" -- "$CMD_BIN" "${CMD_ARGS[@]}" \
    && ok "claude mcp: $NAME" || warn "claude registration failed"
elif [ "$DO_CLAUDE" = 1 ]; then
  warn "claude CLI not found — skipping"
fi

if [ "$DO_OPENCODE" = 1 ] && [ -d "$HOME/.config/opencode" ]; then
  say "Registering with opencode"
  OC="$HOME/.config/opencode/opencode.json"
  [ -f "$OC" ] || echo '{"$schema":"https://opencode.ai/config.json"}' > "$OC"
  cp "$OC" "$OC.bak.$(date +%Y%m%d%H%M%S)"
  "$PYBIN" - "$OC" "$NAME" "$CMD_BIN" "${#CMD_ARGS[@]}" ${CMD_ARGS[@]+"${CMD_ARGS[@]}"} ${ENVARGS[@]+"${ENVARGS[@]}"} <<'PY'
import json, sys
path, name, py, nargs = sys.argv[1:5]
nargs = int(nargs)
argv = sys.argv[5:5 + nargs]
env = dict(kv.split("=", 1) for kv in sys.argv[5 + nargs:])
d = json.load(open(path))
d.setdefault("mcp", {})[name] = {
    "type": "local", "enabled": True,
    "command": [py] + argv, "environment": env,
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
"$PYBIN" - "$SNIP" "$NAME" "$CMD_BIN" "${#CMD_ARGS[@]}" ${CMD_ARGS[@]+"${CMD_ARGS[@]}"} ${ENVARGS[@]+"${ENVARGS[@]}"} <<'PY'
import json, sys
path, name, py, nargs = sys.argv[1:5]
nargs = int(nargs)
argv = sys.argv[5:5 + nargs]
env = dict(kv.split("=", 1) for kv in sys.argv[5 + nargs:])
json.dump({"mcpServers": {name: {"command": py, "args": argv, "env": env}}},
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
  sandbox   $([ "$SANDBOX" = 1 ] && echo "bubblewrap ($WRAPPER)" || echo "none — the server has your full user rights")

Next steps
  * Claude Code: restart it, then run /mcp — you should see "$NAME" with 32 tools.
  * opencode:    restart it; the server appears as a local MCP.
  * Other clients (grok, Cursor, Zed, ...): paste $SNIP
    into their MCP config (they all use the mcpServers command/args/env shape).
  * Full test:   $PYBIN $PREFIX/test_e2e.py
EOF
