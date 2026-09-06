# libreoffice-mcp

An MCP server that lets an agent — Claude Code, opencode, grok, Cursor, Zed, or
anything else speaking the Model Context Protocol — read and write
**Writer, Calc, Impress and Draw** documents through a headless LibreOffice
instance driven over the UNO bridge.

Copyright © 2026 Joe Landman. Licensed under the [Apache License, Version 2.0](LICENSE).

Design goals: **no pip installs, no npm, no network**. It is a single Python
file using only the standard library plus the `uno` module that ships with
LibreOffice, so it works on a PEP-668 "externally managed" system Python and
survives distro upgrades.

## Install

```bash
./install.sh            # detect, install deps if needed, register with clients
./install.sh --check    # report only, change nothing
./install.sh --uninstall
```

Useful flags: `--sandbox` or `--docker` (see [Security perimeter](#security-perimeter)),
`--port 2003`, `--roots "$HOME/Documents"`, `--allow-exec`, `--no-claude`,
`--no-opencode`, `--prefix DIR`. `./install.sh --help` lists them all.

The installer:

1. finds or installs LibreOffice + the Python-UNO bindings
   (apt / dnf / pacman / zypper / brew),
2. finds a Python that can `import uno` (usually `/usr/bin/python3`, *not*
   a pyenv/conda one),
3. copies the server to `~/.local/share/libreoffice-mcp/`,
4. smoke-tests the UNO bridge,
5. registers with Claude Code (`claude mcp add --scope user`) and opencode
   (`~/.config/opencode/opencode.json`, backed up first), and writes
   `mcp-config-snippet.json` in the standard `mcpServers` shape for every
   other client.

It is idempotent — re-running it re-registers cleanly rather than duplicating.

## How it works

```
agent ──MCP stdio JSON-RPC──▶ lo_mcp_server.py ──UNO/URP socket──▶ soffice --headless
```

The server starts `soffice` on first use with its **own user profile**
(`~/.cache/lo-mcp-profile`), so it never collides with an interactive
LibreOffice session. It stays up between calls; open documents keep their
`doc_id` across tool calls in a session.

## Tools (32)

| Group | Tools |
| --- | --- |
| Session | `lo_status` `lo_list_docs` `lo_new` `lo_open` `lo_save` `lo_close` `lo_convert` `lo_shutdown` |
| Writer | `writer_get_text` `writer_get_paragraphs` `writer_append` `writer_replace` `writer_set_paragraph` `writer_insert_table` `writer_insert_image` `writer_list_styles` |
| Calc | `calc_list_sheets` `calc_add_sheet` `calc_read` `calc_write` `calc_clear` `calc_recalculate` |
| Impress | `impress_list_slides` `impress_add_slide` `impress_set_text` `impress_delete_slide` `impress_set_notes` |
| Draw | `draw_list_pages` `draw_add_page` `draw_add_shape` `draw_set_shape` `draw_delete_shape` |
| Opt-in | `lo_run_uno` (arbitrary UNO Python; only with `--allow-exec`) |

Typical flow: `lo_new`/`lo_open` → edit → **`lo_save`** → `lo_close`.
Nothing reaches disk until `lo_save`. The output format follows the
extension: `.odt .docx .doc .rtf .txt .html .ods .xlsx .xls .csv .odp .pptx
.ppt .odg .svg .png .pdf`.

`draw_*` also works on Impress slides, so an agent can drop shapes onto a deck.
Positions and sizes are in **mm**; colors are `#RRGGBB`.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `LO_MCP_PORT` | `2002` | UNO socket port (bind one per user/machine) |
| `LO_MCP_PROFILE` | `~/.cache/lo-mcp-profile` | dedicated LibreOffice profile |
| `LO_MCP_SOFFICE` | auto | path to `soffice` |
| `LO_MCP_ROOTS` | `$HOME:/tmp` | `:`-separated dirs documents may live under; `/` disables the check |
| `LO_MCP_ALLOW_EXEC` | unset | `1` enables `lo_run_uno` |
| `LO_MCP_OWN_SOFFICE_ONLY` | unset | `1` refuses to attach to a LibreOffice this server did not start |
| `LO_MCP_SANDBOX` | unset | label for the confinement in use; reported by `lo_status` |

By itself `LO_MCP_ROOTS` is a guard rail, not a sandbox: it stops an agent from
wandering into `/etc` by accident, but the server runs with your full user
rights. Leave `lo_run_uno` off unless you want the escape hatch. For an actual
boundary, see below.

## Security perimeter

```bash
./install.sh --sandbox --roots "$HOME/Documents"
```

This registers a generated wrapper (`lo-mcp-sandboxed.sh`) with your agent
instead of the bare server, and that wrapper runs **both** the MCP server and
the headless LibreOffice it starts inside one [bubblewrap](https://github.com/containers/bubblewrap)
namespace. Inside it: `/usr` and `/etc` read-only, a private `/tmp`, `/home`
replaced by an empty tmpfs, and nothing bound writable except your `--roots`
directories and the LibreOffice profile. `--unshare-net` means the confined
process has no network at all — the agent reaches it over inherited stdio.

A sandboxed install uses its own LibreOffice profile
(`~/.cache/lo-mcp-profile-sandbox`). Two `soffice` processes sharing one
profile is unsupported and hangs on the profile lock, so a confined instance
must not share with an unconfined one that may still be running.

### Why the perimeter has to enclose LibreOffice, not just the server

`LO_MCP_ROOTS` is checked in the Python process, but the file I/O happens in
`soffice`, a **separate process**. Anything that makes LibreOffice touch a file
without passing through a tool argument goes around that check entirely:

* a linked image or embedded OLE object in a document you open,
* a `WEBSERVICE()` / DDE formula in a spreadsheet,
* a document macro,
* `lo_run_uno`, if you enabled it.

Confining `soffice` is what closes those. `--unshare-net` also stops a hostile
document from phoning home with what it read. Relatedly, `--sandbox` sets
`LO_MCP_OWN_SOFFICE_ONLY=1`: without it the server will happily attach to a
LibreOffice already listening on the port, which would run your documents in
*that* process's confinement rather than its own.

### Keep both layers

Under `--sandbox` the `LO_MCP_ROOTS` check stays on, and it should. Tested with
the app-level check disabled and only bubblewrap standing, a write to
`~/escaped.odt` **reported success** — and no such file existed afterwards. It
had gone into the sandbox's tmpfs and evaporated. The host was protected, but
the agent believed it had saved your document.

So the two layers do different jobs: the path check turns an out-of-bounds
write into an honest error, and bubblewrap is the backstop for everything the
path check cannot see. Neither one replaces the other.

### Running it in a container instead

If you would rather use Docker or Podman than bubblewrap:

```bash
./install.sh --docker --roots "$HOME/Documents:$HOME/Downloads"
```

That builds the image from the `Dockerfile` if it is missing, installs
`lo-mcp-docker.sh`, and registers that with your agents. The container carries
LibreOffice and the UNO bindings, so **the host needs no LibreOffice at all** —
only an engine. It runs with `--network none` and mounts nothing but your
roots. `--image NAME` selects a different image.

To drive it by hand, or from a client you configure yourself:

```bash
docker build -t libreoffice-mcp .
LO_MCP_ROOTS="$HOME/Documents" scripts/lo-mcp-docker.sh
```

Two things about containers that are easy to get wrong, both handled by the
runner script:

* **Mount document directories at the same path inside as outside**
  (`-v "$HOME/Documents:$HOME/Documents"`). The agent sends absolute host paths
  and nothing rewrites them; mounting at `/data` means every path the agent
  knows is wrong.
* **The user mapping differs between engines.** Under rootful Docker you want
  `--user "$(id -u):$(id -g)"`, or files land on the host owned by root. Under
  rootless Podman that same flag pushes you into the subuid range and writes
  fail with an opaque LibreOffice IO error — there you want `--userns=keep-id`.
  `lo-mcp-docker.sh` detects the engine and picks for you.

Bubblewrap starts in about a second and shares the host's LibreOffice; the
container is ~600 MB and starts more slowly, but needs nothing installed on the
host and is easier to pin to a known LibreOffice version. Both confine
LibreOffice itself, which is the part that matters, and everything in the next
section applies to both.

### Caveat emptor

**No technological barrier is impervious to manipulation.** What is here raises
the cost and narrows the blast radius of a mistake; it does not make one
impossible, and it is not a defence against a determined attacker with local
access. Known limits, so you can judge for yourself:

* Kernel and bubblewrap bugs, and namespace escapes, are real and periodic.
* `/etc` and `/usr` are readable inside the sandbox; treat anything there as
  disclosed to whatever you open.
* Everything in `--roots` is fully writable — an agent can still corrupt or
  destroy documents it was legitimately given. Version control or backups are
  the answer to that, not a sandbox.
* The confinement runs as **you**, not as a lesser user. It restricts reach,
  not privilege.
* An agent that can edit the wrapper, the server, or your MCP config can
  disable all of this. Nothing here defends against a client you have already
  given write access to those files.
* The container is not stronger than the bubblewrap confinement by virtue of
  being a container. Both rely on the same kernel namespaces. A rootful Docker
  daemon adds a root-owned attack surface the bubblewrap path does not have.
* Documents are still parsed by LibreOffice's full format stack, which is a
  large attack surface. The sandbox contains the outcome of a parser bug; it
  does not prevent one.

Decide what you are comfortable pointing an agent at, and assume anything
inside `--roots` may be read, rewritten, or deleted.

## Testing

```bash
/usr/bin/python3 lo_mcp_server.py --selftest   # bridge + tool inventory
/usr/bin/python3 test_e2e.py                   # 40+ real MCP calls, all 4 apps
```

Under `--sandbox` the suite needs its scratch directory inside the perimeter —
run it with `--roots` including `/tmp`, or test unsandboxed.

`test_e2e.py` builds a Writer report (styles, replace, table), a Calc sheet with
live formulas, an Impress deck with notes, and a Draw diagram; round-trips
through `.docx`/`.xlsx`/`.pptx`/`.odg`/`.pdf`/`.svg`/`.csv`; and checks the
path guard.

## Troubleshooting

* **`could not connect to LibreOffice`** — a stale headless instance may hold
  the profile: `pkill -f lo-mcp-profile`, then retry.
* **`import uno` fails** — the agent is using the wrong interpreter. Point the
  MCP config at `/usr/bin/python3` (Debian family) or
  `/usr/lib/libreoffice/program/python`, never a pyenv/conda Python.
* **Port already in use** — reinstall with `--port 2003`.
* **Server logs** go to stderr; Claude Code shows them under `/mcp`.
* **`--sandbox` fails with "cannot create a namespace"** — unprivileged user
  namespaces are disabled (`sysctl kernel.unprivileged_userns_clone`). Either
  enable them or drop `--sandbox`.
* **A sandboxed save "succeeds" but the file is nowhere** — the target was
  outside `--roots` and landed in the sandbox's tmpfs. Re-run `install.sh
  --sandbox --roots` with that directory included.
* **`something is already listening on port NNNN`** — under `--sandbox` the
  server refuses to adopt a LibreOffice it did not start. Use `--port`.
* **The confined server starts but cannot reach LibreOffice** — usually a
  profile shared with another running instance. Give it its own
  `LO_MCP_PROFILE`, or stop the other instance.
* **Container writes fail with `Error Area:Io Class:Write`, or files appear
  owned by root or by a high-numbered uid** — the user mapping is wrong for
  your engine. See the container notes above; `LO_MCP_ENGINE=docker|podman`
  overrides the detection.

## License

```
Copyright 2026 Joe Landman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

This project drives LibreOffice as a separate process over its UNO IPC bridge
and bundles no LibreOffice code. LibreOffice itself is licensed under the
[MPL-2.0](https://www.libreoffice.org/download/license/) by The Document
Foundation and must be installed separately.
