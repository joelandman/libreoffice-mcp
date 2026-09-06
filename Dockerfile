# Copyright 2026 Joe Landman
# SPDX-License-Identifier: Apache-2.0
#
# LibreOffice MCP server in a container, for people who would rather use
# Docker/Podman than the bubblewrap confinement install.sh --sandbox builds.
#
# The agent speaks MCP over stdio, so the container is started per session with
# `docker run -i` and needs no ports and no network at all.
#
#   docker build -t libreoffice-mcp .
#
# Mount document directories at THE SAME PATH inside the container as outside.
# The agent passes absolute host paths, and nothing translates them:
#
#   docker run -i --rm --network none \
#     --user "$(id -u):$(id -g)" \
#     -v "$HOME/Documents:$HOME/Documents" \
#     -e LO_MCP_ROOTS="$HOME/Documents" \
#     libreoffice-mcp
#
# See scripts/lo-mcp-docker.sh, which builds that command for you.

FROM ubuntu:24.04

# --no-install-recommends keeps the Java stack and a desktop's worth of
# dependencies out; the "javaldx failed" warning it causes is harmless.
# The fonts are not optional: without them PDF export renders tofu.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        libreoffice-calc \
        libreoffice-impress \
        libreoffice-draw \
        python3-uno \
        fonts-dejavu-core \
        fonts-liberation2 \
    && rm -rf /var/lib/apt/lists/*

COPY lo_mcp_server.py /opt/lo-mcp/lo_mcp_server.py
RUN chmod 0755 /opt/lo-mcp/lo_mcp_server.py

# The container is run with an arbitrary --user, which may have no passwd
# entry and no home. Everything that needs to be writable lives under /tmp.
ENV HOME=/tmp \
    LO_MCP_PROFILE=/tmp/lo-mcp-profile \
    LO_MCP_ROOTS=/data \
    LO_MCP_SANDBOX=container \
    LO_MCP_OWN_SOFFICE_ONLY=1 \
    PYTHONUNBUFFERED=1

# A default for `-v somewhere:/data` if you would rather not mirror host paths.
RUN mkdir -p /data && chmod 1777 /data /tmp

# stdio transport: no EXPOSE, no ports, nothing listening outside the container.
ENTRYPOINT ["python3", "/opt/lo-mcp/lo_mcp_server.py"]
