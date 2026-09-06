#!/usr/bin/env python3
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
"""End-to-end test: drives lo_mcp_server.py over real MCP stdio JSON-RPC."""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.environ.get("LO_MCP_PYTHON", "/usr/bin/python3")
OUT = tempfile.mkdtemp(prefix="lo-mcp-test-")

p = subprocess.Popen([PY, os.path.join(HERE, "lo_mcp_server.py")],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True, bufsize=1)
_id = [0]
fails = []


def rpc(method, params=None):
    _id[0] += 1
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": _id[0],
                              "method": method, "params": params or {}}) + "\n")
    p.stdin.flush()
    return json.loads(p.stdout.readline())


def call(_tool, **args):
    name = _tool
    r = rpc("tools/call", {"name": name, "arguments": args})
    body = r["result"]["content"][0]["text"]
    if r["result"].get("isError"):
        fails.append("%s -> %s" % (name, body))
        print("  FAIL %-22s %s" % (name, body))
        return {}
    print("  ok   %-22s %s" % (name, body.replace("\n", " ")[:90]))
    return json.loads(body)


init = rpc("initialize", {"protocolVersion": "2025-06-18",
                          "capabilities": {}, "clientInfo": {"name": "test"}})
print("initialize:", init["result"]["serverInfo"], init["result"]["protocolVersion"])
print("tools:", len(rpc("tools/list")["result"]["tools"]))

print("\n-- Writer --")
d = call("lo_new", kind="writer")["doc_id"]
call("writer_append", doc_id=d, text="Quarterly Report", style="Heading 1")
call("writer_append", doc_id=d, text="Revenue grew by PLACEHOLDER percent.")
call("writer_replace", doc_id=d, search="PLACEHOLDER", replace="12")
call("writer_insert_table", doc_id=d,
     data=[["Region", "Q1", "Q2"], ["North", 100, 120], ["South", 90, 140]])
txt = call("writer_get_text", doc_id=d)["text"]
assert "12 percent" in txt and "North" in txt, txt
call("writer_get_paragraphs", doc_id=d)
call("lo_save", doc_id=d, path=os.path.join(OUT, "report.odt"))
call("lo_save", doc_id=d, path=os.path.join(OUT, "report.docx"))
call("lo_save", doc_id=d, path=os.path.join(OUT, "report.pdf"))
call("lo_close", doc_id=d)
r = call("lo_open", path=os.path.join(OUT, "report.docx"))
assert "Quarterly Report" in call("writer_get_text", doc_id=r["doc_id"])["text"]
call("lo_close", doc_id=r["doc_id"])

print("\n-- Calc --")
d = call("lo_new", kind="calc")["doc_id"]
call("calc_write", doc_id=d, start_cell="A1",
     values=[["Item", "Qty", "Price", "Total"],
             ["Widget", 3, 2.5, "=B2*C2"],
             ["Gadget", 10, 1.25, "=B3*C3"],
             ["", "", "Sum", "=SUM(D2:D3)"]])
call("calc_add_sheet", doc_id=d, name="Notes")
call("calc_list_sheets", doc_id=d)
vals = call("calc_read", doc_id=d, sheet="Sheet1")["values"]
assert vals[3][3] == 20.0, vals
call("calc_read", doc_id=d, sheet="Sheet1", range="D2:D4", formulas=True)
call("lo_save", doc_id=d, path=os.path.join(OUT, "sheet.xlsx"))
call("lo_save", doc_id=d, path=os.path.join(OUT, "sheet.ods"))
call("lo_close", doc_id=d)

print("\n-- Impress --")
d = call("lo_new", kind="impress")["doc_id"]
call("impress_add_slide", doc_id=d, title="Agenda",
     body="Numbers\nRisks\nNext steps")
call("impress_add_slide", doc_id=d, title="Numbers", body="Revenue up 12%")
call("impress_set_notes", doc_id=d, slide=1, text="Keep this short.")
sl = call("impress_list_slides", doc_id=d)["slides"]
assert any("Agenda" in s["text"] for s in sl[1]["shapes"]), sl
call("impress_delete_slide", doc_id=d, slide=0)
call("draw_add_shape", doc_id=d, page=0, type="rect", x_mm=20, y_mm=100,
     width_mm=60, height_mm=20, text="Callout", fill="#3366CC")
call("lo_save", doc_id=d, path=os.path.join(OUT, "deck.pptx"))
call("lo_save", doc_id=d, path=os.path.join(OUT, "deck.pdf"))
call("lo_close", doc_id=d)

print("\n-- Draw --")
d = call("lo_new", kind="draw")["doc_id"]
call("draw_add_shape", doc_id=d, type="rect", x_mm=20, y_mm=20,
     width_mm=60, height_mm=30, text="Client", fill="#E8F0FE",
     line_color="#3366CC")
call("draw_add_shape", doc_id=d, type="ellipse", x_mm=120, y_mm=20,
     width_mm=60, height_mm=30, text="Server", fill="#FDE8E8")
call("draw_add_shape", doc_id=d, type="line", x_mm=80, y_mm=35,
     width_mm=40, height_mm=0)
call("draw_set_shape", doc_id=d, page=0, shape=0, text="Browser")
pg = call("draw_list_pages", doc_id=d)["pages"][0]
assert len(pg["shapes"]) == 3 and pg["shapes"][0]["text"] == "Browser", pg
call("draw_add_page", doc_id=d)
call("lo_save", doc_id=d, path=os.path.join(OUT, "diagram.odg"))
call("lo_save", doc_id=d, path=os.path.join(OUT, "diagram.svg"))
call("lo_close", doc_id=d)

print("\n-- convert / guards --")
call("lo_convert", path=os.path.join(OUT, "sheet.xlsx"),
     out_path=os.path.join(OUT, "sheet.csv"))
bad = rpc("tools/call", {"name": "lo_open", "arguments": {"path": "/etc/passwd"}})
assert bad["result"]["isError"], "root guard did not fire"
print("  ok   root guard rejected /etc/passwd")
call("lo_status")

print("\n-- files produced --")
for f in sorted(os.listdir(OUT)):
    print("  %8d  %s" % (os.path.getsize(os.path.join(OUT, f)), f))
p.stdin.close(); p.wait(timeout=10)
print("\nRESULT:", "FAILED: %s" % fails if fails else "ALL PASS")
sys.exit(1 if fails else 0)
