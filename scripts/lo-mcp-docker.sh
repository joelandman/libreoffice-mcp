#!/usr/bin/env bash
#
# Copyright 2026 Joe Landman
# SPDX-License-Identifier: Apache-2.0
#
# Run the LibreOffice MCP server in a container and speak MCP over stdio.
# Point your agent's MCP config at this script instead of the Python server.
#
#   LO_MCP_ROOTS="$HOME/Documents:$HOME/Downloads" scripts/lo-mcp-docker.sh
#
# Environment:
#   LO_MCP_ROOTS      ':'-separated dirs to expose (default $HOME/Documents)
#   LO_MCP_IMAGE      image to run (default libreoffice-mcp)
#   LO_MCP_ENGINE     docker | podman (default: podman if present)
#   LO_MCP_ALLOW_EXEC 1 to enable the lo_run_uno escape hatch
set -euo pipefail

IMAGE="${LO_MCP_IMAGE:-libreoffice-mcp}"
ROOTS="${LO_MCP_ROOTS:-$HOME/Documents}"

ENGINE="${LO_MCP_ENGINE:-}"
if [ -z "$ENGINE" ]; then
  if command -v podman >/dev/null 2>&1; then ENGINE=podman
  elif command -v docker >/dev/null 2>&1; then ENGINE=docker
  else echo "lo-mcp-docker: neither podman nor docker found" >&2; exit 1; fi
fi

# Getting the user mapping wrong is the classic failure here, and it does not
# announce itself: writes fail with an opaque LibreOffice IO error, or land on
# the host owned by a subuid you cannot read.
#   rootful docker : the container's root is the host's root, so ask for our uid
#   rootless podman: our uid is ALREADY mapped to the container's root, and
#                    --user would push us into the subuid range instead
USERFLAGS=(--user "$(id -u):$(id -g)")
if "$ENGINE" --version 2>/dev/null | grep -qi podman; then
  if [ "$("$ENGINE" info --format '{{.Host.Security.Rootless}}' 2>/dev/null)" = "true" ]; then
    USERFLAGS=(--userns=keep-id)
  fi
fi

# Mount every root at the same path inside the container: the agent sends
# absolute host paths and nothing rewrites them.
MOUNTS=()
IFS=':' read -r -a _roots <<< "$ROOTS"
for r in "${_roots[@]}"; do
  [ -n "$r" ] || continue
  mkdir -p "$r"
  MOUNTS+=(-v "$r:$r")
done

ENVFLAGS=(-e "LO_MCP_ROOTS=$ROOTS")
[ "${LO_MCP_ALLOW_EXEC:-}" = 1 ] && ENVFLAGS+=(-e LO_MCP_ALLOW_EXEC=1)

# -i for stdio, no -t (a TTY would corrupt the JSON-RPC stream).
# --network none: the agent talks over stdin/stdout, so nothing needs egress,
# and a document cannot report home with what it read.
exec "$ENGINE" run -i --rm --network none \
  "${USERFLAGS[@]}" "${MOUNTS[@]}" "${ENVFLAGS[@]}" \
  "$IMAGE" "$@"
