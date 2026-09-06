# libreoffice-mcp

An MCP server that lets an agent (Claude Code, opencode, grok, Cursor, Zed, …)
read and write **Writer, Calc, Impress and Draw** documents through a headless
LibreOffice instance driven over the UNO bridge.

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

Useful flags: `--port 2003`, `--roots "$HOME:/srv/docs"`, `--allow-exec`,
`--no-claude`, `--no-opencode`, `--prefix DIR`.

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

`LO_MCP_ROOTS` is a guard rail, not a sandbox: it stops an agent from wandering
into `/etc` by accident, but the server runs with your full user rights.
Leave `lo_run_uno` off unless you want the escape hatch.

## Testing

```bash
/usr/bin/python3 lo_mcp_server.py --selftest   # bridge + tool inventory
/usr/bin/python3 test_e2e.py                   # 40+ real MCP calls, all 4 apps
```

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
