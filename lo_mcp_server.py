#!/usr/bin/env python3
"""
LibreOffice MCP server.

A dependency-free MCP (Model Context Protocol) stdio server that drives a
headless LibreOffice instance through the UNO bridge, exposing Writer, Calc,
Impress and Draw to agents (Claude Code, opencode, grok, etc.).

Requires: a Python interpreter that can `import uno` (Debian/Ubuntu/Mint:
python3-uno, Fedora: libreoffice-pyuno, Arch: libreoffice-fresh).

Environment:
  LO_MCP_PORT      UNO socket port (default 2002)
  LO_MCP_PROFILE   LibreOffice user profile dir (default ~/.cache/lo-mcp-profile)
  LO_MCP_SOFFICE   path to soffice binary (default: found on PATH)
  LO_MCP_ROOTS     ':'-separated dirs documents may be read/written under
                   (default: $HOME:/tmp). Set to '/' to disable the check.
  LO_MCP_ALLOW_EXEC=1  enable the lo_run_uno escape-hatch tool.
"""

import json
import os
import subprocess
import sys
import time
import traceback

VERSION = "1.0.0"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

PORT = int(os.environ.get("LO_MCP_PORT", "2002"))
PROFILE = os.path.expanduser(
    os.environ.get("LO_MCP_PROFILE", "~/.cache/lo-mcp-profile"))
SOFFICE = os.environ.get("LO_MCP_SOFFICE", "")
ALLOW_EXEC = os.environ.get("LO_MCP_ALLOW_EXEC", "") == "1"
ROOTS = [os.path.realpath(os.path.expanduser(p))
         for p in os.environ.get("LO_MCP_ROOTS",
                                 os.path.expanduser("~") + ":/tmp").split(":") if p]


def log(*a):
    print("[lo-mcp]", *a, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# UNO plumbing
# --------------------------------------------------------------------------

import uno            # noqa: E402
import unohelper      # noqa: E402
from com.sun.star.beans import PropertyValue          # noqa: E402
from com.sun.star.awt import Point, Size              # noqa: E402


class LO:
    """Lazily started headless LibreOffice + document registry."""

    def __init__(self):
        self.ctx = None
        self.desktop = None
        self.proc = None
        self.docs = {}       # doc_id -> component
        self._next = 1

    # -- connection ---------------------------------------------------
    def _resolve(self):
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver", local)
        return resolver.resolve(
            "uno:socket,host=127.0.0.1,port=%d;urp;StarOffice.ComponentContext"
            % PORT)

    def _soffice_bin(self):
        if SOFFICE:
            return SOFFICE
        for name in ("soffice", "libreoffice"):
            for d in os.environ.get("PATH", "").split(":"):
                p = os.path.join(d, name)
                if os.path.isfile(p) and os.access(p, os.X_OK):
                    return p
        raise RuntimeError("soffice binary not found; set LO_MCP_SOFFICE")

    def _spawn(self):
        os.makedirs(PROFILE, exist_ok=True)
        cmd = [
            self._soffice_bin(),
            "-env:UserInstallation=%s" % unohelper.systemPathToFileUrl(PROFILE),
            "--headless", "--invisible", "--nodefault", "--nologo",
            "--nolockcheck", "--norestore", "--nofirststartwizard",
            "--accept=socket,host=127.0.0.1,port=%d;urp;StarOffice.ServiceManager"
            % PORT,
        ]
        log("starting:", " ".join(cmd))
        devnull = open(os.devnull, "wb")
        self.proc = subprocess.Popen(
            cmd, stdout=devnull, stderr=devnull, stdin=subprocess.DEVNULL,
            start_new_session=True)

    def connect(self, timeout=60):
        if self.desktop is not None:
            try:                      # cheap liveness probe
                self.desktop.getCurrentComponent
                return self.desktop
            except Exception:
                self.ctx = self.desktop = None
        try:
            self.ctx = self._resolve()
        except Exception:
            self._spawn()
            deadline = time.time() + timeout
            last = None
            while time.time() < deadline:
                time.sleep(0.5)
                try:
                    self.ctx = self._resolve()
                    break
                except Exception as e:
                    last = e
            else:
                raise RuntimeError("could not connect to LibreOffice: %s" % last)
        self.desktop = self.ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", self.ctx)
        return self.desktop

    def svc(self, name):
        self.connect()
        return self.ctx.ServiceManager.createInstanceWithContext(name, self.ctx)

    # -- documents ----------------------------------------------------
    def register(self, doc, kind):
        did = "%s%d" % (kind, self._next)
        self._next += 1
        self.docs[did] = doc
        return did

    def get(self, doc_id):
        doc = self.docs.get(doc_id)
        if doc is None:
            raise RuntimeError("unknown doc_id %r (use lo_list_docs)" % doc_id)
        return doc


LO_ = LO()


def props(**kw):
    out = []
    for k, v in kw.items():
        p = PropertyValue()
        p.Name = k
        p.Value = v
        out.append(p)
    return tuple(out)


def check_path(path, write=False):
    p = os.path.realpath(os.path.expanduser(path))
    if ROOTS and "/" not in [r for r in ROOTS]:
        base = p if write else p
        if not any(base == r or base.startswith(r.rstrip("/") + "/")
                   for r in ROOTS):
            raise RuntimeError(
                "path %s is outside allowed roots %s (set LO_MCP_ROOTS)"
                % (p, ":".join(ROOTS)))
    return p


def to_url(path, write=False):
    return unohelper.systemPathToFileUrl(check_path(path, write))


def doc_kind(doc):
    s = doc.supportsService
    if s("com.sun.star.text.TextDocument"):
        return "writer"
    if s("com.sun.star.sheet.SpreadsheetDocument"):
        return "calc"
    if s("com.sun.star.presentation.PresentationDocument"):
        return "impress"
    if s("com.sun.star.drawing.DrawingDocument"):
        return "draw"
    return "other"


FACTORY = {"writer": "private:factory/swriter",
           "calc": "private:factory/scalc",
           "impress": "private:factory/simpress",
           "draw": "private:factory/sdraw"}

# extension -> (filter, kind) ; kind None = any
FILTERS = {
    ".odt": "writer8", ".docx": "MS Word 2007 XML", ".doc": "MS Word 97",
    ".rtf": "Rich Text", ".txt": "Text", ".html": "HTML (StarWriter)",
    ".ods": "calc8", ".xlsx": "Calc MS Excel 2007 XML", ".xls": "MS Excel 97",
    ".csv": "Text - txt - csv (StarCalc)",
    ".odp": "impress8", ".pptx": "Impress MS PowerPoint 2007 XML",
    ".ppt": "MS PowerPoint 97",
    ".odg": "draw8", ".svg": "draw_svg_Export", ".png": "draw_png_Export",
}
PDF_FILTER = {"writer": "writer_pdf_Export", "calc": "calc_pdf_Export",
              "impress": "impress_pdf_Export", "draw": "draw_pdf_Export"}


def filter_for(doc, path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return PDF_FILTER[doc_kind(doc)]
    f = FILTERS.get(ext)
    if not f:
        raise RuntimeError("unsupported extension %r" % ext)
    return f


# --------------------------------------------------------------------------
# Tool implementations
# --------------------------------------------------------------------------

TOOLS = []          # list of dicts for tools/list
HANDLERS = {}       # name -> callable(args) -> jsonable


def tool(name, description, schema):
    def deco(fn):
        TOOLS.append({"name": name, "description": description,
                      "inputSchema": {"type": "object", **schema}})
        HANDLERS[name] = fn
        return fn
    return deco


def obj(properties, required=()):
    return {"properties": properties, "required": list(required)}


S = lambda d="": {"type": "string", "description": d}          # noqa: E731
I = lambda d="": {"type": "integer", "description": d}          # noqa: E731
N = lambda d="": {"type": "number", "description": d}           # noqa: E731
B = lambda d="": {"type": "boolean", "description": d}          # noqa: E731
A = lambda d="", items=None: {"type": "array", "description": d,               # noqa: E731
                              "items": items or {}}

# ---- session / lifecycle -------------------------------------------------


@tool("lo_status", "Report LibreOffice bridge status, version and open documents.",
      obj({}))
def t_status(a):
    try:
        LO_.connect()
        cfg = LO_.svc("com.sun.star.configuration.ConfigurationProvider")
        ver = "unknown"
        try:
            node = cfg.createInstanceWithArguments(
                "com.sun.star.configuration.ConfigurationAccess",
                props(nodepath="/org.openoffice.Setup/Product"))
            ver = "%s %s" % (node.getByName("ooName"),
                             node.getByName("ooSetupVersionAboutBox"))
        except Exception:
            pass
        return {"connected": True, "port": PORT, "libreoffice": ver,
                "profile": PROFILE, "allowed_roots": ROOTS,
                "exec_enabled": ALLOW_EXEC,
                "open_docs": t_list_docs({})["docs"]}
    except Exception as e:
        return {"connected": False, "error": str(e)}


@tool("lo_list_docs", "List documents currently open in this session.", obj({}))
def t_list_docs(a):
    out = []
    for did, doc in list(LO_.docs.items()):
        try:
            url = doc.getURL()
            out.append({"doc_id": did, "kind": doc_kind(doc),
                        "path": unohelper.fileUrlToSystemPath(url) if url else None,
                        "modified": bool(doc.isModified())})
        except Exception:
            LO_.docs.pop(did, None)      # died underneath us
    return {"docs": out}


@tool("lo_new", "Create a new empty document and return its doc_id.",
      obj({"kind": {"type": "string", "enum": list(FACTORY),
                    "description": "writer | calc | impress | draw"}},
          ["kind"]))
def t_new(a):
    kind = a["kind"]
    desktop = LO_.connect()
    doc = desktop.loadComponentFromURL(FACTORY[kind], "_blank", 0,
                                       props(Hidden=True))
    return {"doc_id": LO_.register(doc, kind), "kind": kind}


@tool("lo_open", "Open an existing document file and return its doc_id.",
      obj({"path": S("Absolute path to the document"),
           "read_only": B("Open read-only (default false)")}, ["path"]))
def t_open(a):
    desktop = LO_.connect()
    url = to_url(a["path"])
    if not os.path.exists(check_path(a["path"])):
        raise RuntimeError("no such file: %s" % a["path"])
    doc = desktop.loadComponentFromURL(
        url, "_blank", 0, props(Hidden=True,
                                ReadOnly=bool(a.get("read_only", False))))
    if doc is None:
        raise RuntimeError("LibreOffice could not load %s" % a["path"])
    kind = doc_kind(doc)
    return {"doc_id": LO_.register(doc, kind), "kind": kind}


@tool("lo_save", "Save a document. Without path, saves in place. The output "
                 "format is chosen from the extension (.odt .docx .ods .xlsx "
                 ".odp .pptx .odg .pdf .csv .txt .html .svg .png).",
      obj({"doc_id": S(), "path": S("Optional target path (save-as)")},
          ["doc_id"]))
def t_save(a):
    doc = LO_.get(a["doc_id"])
    path = a.get("path")
    if not path:
        if not doc.getURL():
            raise RuntimeError("document has no path yet; pass 'path'")
        doc.store()
        return {"saved": unohelper.fileUrlToSystemPath(doc.getURL())}
    target = check_path(path, write=True)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    doc.storeToURL(unohelper.systemPathToFileUrl(target),
                   props(FilterName=filter_for(doc, target), Overwrite=True))
    return {"saved": target, "filter": filter_for(doc, target)}


@tool("lo_close", "Close a document, optionally saving it first.",
      obj({"doc_id": S(), "save": B("Save before closing (default false)")},
          ["doc_id"]))
def t_close(a):
    doc = LO_.get(a["doc_id"])
    if a.get("save") and doc.getURL():
        doc.store()
    doc.close(True)
    LO_.docs.pop(a["doc_id"], None)
    return {"closed": a["doc_id"]}


@tool("lo_convert", "Convert a file to another format without keeping it open "
                    "(e.g. docx -> pdf). Returns the output path.",
      obj({"path": S("Source file"),
           "out_path": S("Destination file; extension picks the format")},
          ["path", "out_path"]))
def t_convert(a):
    r = t_open({"path": a["path"], "read_only": True})
    try:
        res = t_save({"doc_id": r["doc_id"], "path": a["out_path"]})
    finally:
        t_close({"doc_id": r["doc_id"]})
    return res


@tool("lo_shutdown", "Terminate the headless LibreOffice backend (it restarts "
                     "automatically on the next call). Unsaved changes are lost.",
      obj({}))
def t_shutdown(a):
    try:
        LO_.connect().terminate()
    except Exception:
        pass
    LO_.docs.clear()
    LO_.ctx = LO_.desktop = None
    return {"terminated": True}


# ---- Writer --------------------------------------------------------------

def _writer(doc_id):
    doc = LO_.get(doc_id)
    if doc_kind(doc) != "writer":
        raise RuntimeError("%s is a %s document, not writer"
                           % (doc_id, doc_kind(doc)))
    return doc


@tool("writer_get_text", "Read a Writer document as plain text.",
      obj({"doc_id": S()}, ["doc_id"]))
def t_w_get(a):
    doc = _writer(a["doc_id"])
    return {"text": doc.getText().getString()}


@tool("writer_get_paragraphs",
      "List the paragraphs of a Writer document with their index and "
      "paragraph style (useful for locating a place to edit).",
      obj({"doc_id": S(), "start": I("First index (default 0)"),
           "limit": I("Max paragraphs (default 200)")}, ["doc_id"]))
def t_w_paras(a):
    doc = _writer(a["doc_id"])
    start, limit = int(a.get("start", 0)), int(a.get("limit", 200))
    out, i = [], 0
    it = doc.getText().createEnumeration()
    while it.hasMoreElements() and len(out) < limit:
        el = it.nextElement()
        if el.supportsService("com.sun.star.text.Paragraph"):
            if i >= start:
                out.append({"index": i, "style": el.ParaStyleName,
                            "text": el.getString()})
            i += 1
        elif el.supportsService("com.sun.star.text.TextTable"):
            if i >= start:
                out.append({"index": i, "style": "<table>",
                            "text": "table %s" % el.getName()})
            i += 1
    return {"paragraphs": out, "total_scanned": i}


@tool("writer_append",
      "Append one or more paragraphs to the end of a Writer document.",
      obj({"doc_id": S(),
           "text": S("Text to append; '\\n' starts a new paragraph"),
           "style": S("Paragraph style, e.g. 'Heading 1', 'Default Paragraph "
                      "Style', 'List Bullet'")}, ["doc_id", "text"]))
def t_w_append(a):
    doc = _writer(a["doc_id"])
    text = doc.getText()
    cur = text.createTextCursorByRange(text.getEnd())
    style = a.get("style")
    first = True
    for line in a["text"].split("\n"):
        if not first or text.getString():
            text.insertControlCharacter(cur, 0, False)   # PARAGRAPH_BREAK
        first = False
        if style:
            try:
                cur.ParaStyleName = style
            except Exception:
                raise RuntimeError("unknown paragraph style %r" % style)
        text.insertString(cur, line, False)
    return {"appended_chars": len(a["text"])}


@tool("writer_replace",
      "Search and replace throughout a Writer document. Returns the number of "
      "replacements.",
      obj({"doc_id": S(), "search": S(), "replace": S(),
           "regex": B("Treat 'search' as an ICU regular expression"),
           "match_case": B("Case sensitive (default false)")},
          ["doc_id", "search", "replace"]))
def t_w_replace(a):
    doc = _writer(a["doc_id"])
    d = doc.createReplaceDescriptor()
    d.SearchString = a["search"]
    d.ReplaceString = a["replace"]
    d.SearchRegularExpression = bool(a.get("regex", False))
    d.SearchCaseSensitive = bool(a.get("match_case", False))
    return {"replaced": doc.replaceAll(d)}


@tool("writer_set_paragraph",
      "Replace the text (and optionally the style) of one paragraph, "
      "addressed by the index from writer_get_paragraphs.",
      obj({"doc_id": S(), "index": I(), "text": S(), "style": S()},
          ["doc_id", "index", "text"]))
def t_w_setpara(a):
    doc = _writer(a["doc_id"])
    idx, i = int(a["index"]), 0
    it = doc.getText().createEnumeration()
    while it.hasMoreElements():
        el = it.nextElement()
        if el.supportsService("com.sun.star.text.Paragraph") or \
           el.supportsService("com.sun.star.text.TextTable"):
            if i == idx:
                el.setString(a["text"])
                if a.get("style"):
                    el.ParaStyleName = a["style"]
                return {"index": idx, "text": a["text"]}
            i += 1
    raise RuntimeError("paragraph index %d out of range (%d found)" % (idx, i))


@tool("writer_insert_table",
      "Append a table to a Writer document. 'data' is a list of rows; the "
      "first row is used as the header row.",
      obj({"doc_id": S(),
           "data": A("Rows of cell values (strings or numbers)",
                     {"type": "array", "items": {}}),
           "name": S("Optional table name")}, ["doc_id", "data"]))
def t_w_table(a):
    doc = _writer(a["doc_id"])
    data = a["data"]
    if not data or not data[0]:
        raise RuntimeError("'data' must be a non-empty list of rows")
    rows, cols = len(data), max(len(r) for r in data)
    tbl = doc.createInstance("com.sun.star.text.TextTable")
    tbl.initialize(rows, cols)
    text = doc.getText()
    cur = text.createTextCursorByRange(text.getEnd())
    text.insertTextContent(cur, tbl, False)
    if a.get("name"):
        tbl.setName(a["name"])
    for r, row in enumerate(data):
        for c, val in enumerate(row):
            cell = tbl.getCellByName("%s%d" % (chr(ord("A") + c), r + 1))
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                cell.setValue(float(val))
            else:
                cell.setString("" if val is None else str(val))
    return {"table": tbl.getName(), "rows": rows, "cols": cols}


@tool("writer_insert_image",
      "Append an image file to a Writer document.",
      obj({"doc_id": S(), "path": S("Image file path"),
           "width_mm": N("Width in mm (default 100)"),
           "height_mm": N("Height in mm (default: proportional guess, 75)")},
          ["doc_id", "path"]))
def t_w_image(a):
    doc = _writer(a["doc_id"])
    g = doc.createInstance("com.sun.star.text.GraphicObject")
    g.GraphicURL = to_url(a["path"])
    g.Width = int(float(a.get("width_mm", 100)) * 100)
    g.Height = int(float(a.get("height_mm", 75)) * 100)
    text = doc.getText()
    text.insertTextContent(text.createTextCursorByRange(text.getEnd()), g, False)
    return {"inserted": a["path"]}


@tool("writer_list_styles",
      "List available paragraph style names for a Writer document.",
      obj({"doc_id": S()}, ["doc_id"]))
def t_w_styles(a):
    doc = _writer(a["doc_id"])
    fam = doc.getStyleFamilies().getByName("ParagraphStyles")
    return {"styles": sorted(fam.getElementNames())}


# ---- Calc ----------------------------------------------------------------

def _calc(doc_id):
    doc = LO_.get(doc_id)
    if doc_kind(doc) != "calc":
        raise RuntimeError("%s is a %s document, not calc"
                           % (doc_id, doc_kind(doc)))
    return doc


def _sheet(doc, ref):
    sheets = doc.getSheets()
    if ref is None or ref == "":
        return sheets.getByIndex(0)
    if isinstance(ref, int):
        return sheets.getByIndex(ref)
    if sheets.hasByName(ref):
        return sheets.getByName(ref)
    raise RuntimeError("no sheet named %r (have %s)"
                       % (ref, list(sheets.getElementNames())))


def _cellname(col, row):
    s = ""
    c = col
    while True:
        s = chr(ord("A") + c % 26) + s
        c = c // 26 - 1
        if c < 0:
            break
    return "%s%d" % (s, row + 1)


@tool("calc_list_sheets", "List the sheets of a Calc document with their used "
                          "range size.",
      obj({"doc_id": S()}, ["doc_id"]))
def t_c_sheets(a):
    doc = _calc(a["doc_id"])
    out = []
    sheets = doc.getSheets()
    for i in range(sheets.getCount()):
        sh = sheets.getByIndex(i)
        cur = sh.createCursor()
        cur.gotoEndOfUsedArea(False)
        ra = cur.getRangeAddress()
        out.append({"index": i, "name": sh.getName(),
                    "used_rows": ra.EndRow + 1, "used_cols": ra.EndColumn + 1,
                    "used_range": "A1:%s" % _cellname(ra.EndColumn, ra.EndRow)})
    return {"sheets": out}


@tool("calc_add_sheet", "Add a new sheet to a Calc document.",
      obj({"doc_id": S(), "name": S(),
           "index": I("Position (default: last)")}, ["doc_id", "name"]))
def t_c_addsheet(a):
    doc = _calc(a["doc_id"])
    sheets = doc.getSheets()
    idx = int(a.get("index", sheets.getCount()))
    sheets.insertNewByName(a["name"], idx)
    return {"added": a["name"], "index": idx}


@tool("calc_read",
      "Read a cell range from a Calc sheet as a 2D array. Omit 'range' to read "
      "the whole used area.",
      obj({"doc_id": S(), "sheet": S("Sheet name or index (default first)"),
           "range": S("A1-style range, e.g. 'A1:D20'"),
           "formulas": B("Return formulas instead of computed values")},
          ["doc_id"]))
def t_c_read(a):
    doc = _calc(a["doc_id"])
    sh = _sheet(doc, a.get("sheet"))
    if a.get("range"):
        rng = sh.getCellRangeByName(a["range"])
    else:
        cur = sh.createCursor()
        cur.gotoEndOfUsedArea(False)
        ra = cur.getRangeAddress()
        rng = sh.getCellRangeByPosition(0, 0, ra.EndColumn, ra.EndRow)
    if a.get("formulas"):
        rows = [list(r) for r in rng.getFormulaArray()]
    else:
        rows = []
        for r in rng.getDataArray():
            rows.append([("" if v == "" else v) for v in r])
    return {"sheet": sh.getName(), "rows": len(rows),
            "cols": len(rows[0]) if rows else 0, "values": rows}


@tool("calc_write",
      "Write a 2D array of values into a Calc sheet starting at a cell. "
      "Strings beginning with '=' are entered as formulas; numbers as numbers.",
      obj({"doc_id": S(), "sheet": S("Sheet name or index (default first)"),
           "start_cell": S("Top-left cell, e.g. 'A1' (default A1)"),
           "values": A("Rows of values", {"type": "array", "items": {}})},
          ["doc_id", "values"]))
def t_c_write(a):
    doc = _calc(a["doc_id"])
    sh = _sheet(doc, a.get("sheet"))
    start = a.get("start_cell", "A1")
    anchor = sh.getCellRangeByName(start).getCellAddress()
    n = 0
    for dr, row in enumerate(a["values"]):
        for dc, val in enumerate(row):
            cell = sh.getCellByPosition(anchor.Column + dc, anchor.Row + dr)
            if val is None:
                cell.setString("")
            elif isinstance(val, bool):
                cell.setValue(1 if val else 0)
            elif isinstance(val, (int, float)):
                cell.setValue(float(val))
            elif isinstance(val, str) and val.startswith("="):
                cell.setFormula(val)
            else:
                cell.setString(str(val))
            n += 1
    return {"sheet": sh.getName(), "start": start, "cells_written": n}


@tool("calc_clear", "Clear the contents of a cell range.",
      obj({"doc_id": S(), "sheet": S(), "range": S("e.g. 'A1:D20'")},
          ["doc_id", "range"]))
def t_c_clear(a):
    doc = _calc(a["doc_id"])
    sh = _sheet(doc, a.get("sheet"))
    # VALUE|DATETIME|STRING|ANNOTATION|FORMULA
    sh.getCellRangeByName(a["range"]).clearContents(1 + 2 + 4 + 8 + 16)
    return {"cleared": a["range"]}


@tool("calc_recalculate", "Force a full recalculation of the document.",
      obj({"doc_id": S()}, ["doc_id"]))
def t_c_recalc(a):
    _calc(a["doc_id"]).calculateAll()
    return {"recalculated": True}


# ---- Impress -------------------------------------------------------------

def _impress(doc_id):
    doc = LO_.get(doc_id)
    if doc_kind(doc) != "impress":
        raise RuntimeError("%s is a %s document, not impress"
                           % (doc_id, doc_kind(doc)))
    return doc


def _shape_text(shape):
    try:
        return shape.getString()
    except Exception:
        return ""


@tool("impress_list_slides",
      "List the slides of a presentation with their name, layout and the text "
      "of each shape.",
      obj({"doc_id": S()}, ["doc_id"]))
def t_i_list(a):
    doc = _impress(a["doc_id"])
    pages = doc.getDrawPages()
    out = []
    for i in range(pages.getCount()):
        p = pages.getByIndex(i)
        shapes = [{"index": j, "type": p.getByIndex(j).getShapeType(),
                   "text": _shape_text(p.getByIndex(j))}
                  for j in range(p.getCount())]
        out.append({"index": i, "name": p.getName(),
                    "layout": getattr(p, "Layout", None), "shapes": shapes})
    return {"slides": out}


@tool("impress_add_slide",
      "Add a slide and optionally fill its title and body. Layout 1 = title + "
      "content (default), 0 = title only, 20 = blank.",
      obj({"doc_id": S(), "title": S(), "body": S("Body text; '\\n' per bullet"),
           "layout": I("AutoLayout number (default 1)"),
           "index": I("Insert position (default: at the end)")}, ["doc_id"]))
def t_i_add(a):
    doc = _impress(a["doc_id"])
    pages = doc.getDrawPages()
    idx = int(a.get("index", pages.getCount()))
    pages.insertNewByIndex(idx)
    page = pages.getByIndex(idx)
    page.Layout = int(a.get("layout", 1))
    if a.get("title") is not None and page.getCount() > 0:
        page.getByIndex(0).setString(a["title"])
    if a.get("body") is not None and page.getCount() > 1:
        page.getByIndex(1).setString(a["body"])
    return {"index": idx, "name": page.getName(), "shapes": page.getCount()}


@tool("impress_set_text",
      "Set the text of one shape on a slide (use impress_list_slides for the "
      "shape indexes).",
      obj({"doc_id": S(), "slide": I(), "shape": I(), "text": S()},
          ["doc_id", "slide", "shape", "text"]))
def t_i_settext(a):
    doc = _impress(a["doc_id"])
    page = doc.getDrawPages().getByIndex(int(a["slide"]))
    if int(a["shape"]) >= page.getCount():
        raise RuntimeError("slide %s has only %d shapes"
                           % (a["slide"], page.getCount()))
    page.getByIndex(int(a["shape"])).setString(a["text"])
    return {"slide": a["slide"], "shape": a["shape"]}


@tool("impress_delete_slide", "Delete a slide by index.",
      obj({"doc_id": S(), "slide": I()}, ["doc_id", "slide"]))
def t_i_del(a):
    doc = _impress(a["doc_id"])
    pages = doc.getDrawPages()
    pages.remove(pages.getByIndex(int(a["slide"])))
    return {"deleted": a["slide"], "remaining": pages.getCount()}


@tool("impress_set_notes", "Set the speaker notes of a slide.",
      obj({"doc_id": S(), "slide": I(), "text": S()},
          ["doc_id", "slide", "text"]))
def t_i_notes(a):
    doc = _impress(a["doc_id"])
    page = doc.getDrawPages().getByIndex(int(a["slide"]))
    notes = page.getNotesPage()
    for i in range(notes.getCount()):
        sh = notes.getByIndex(i)
        if "Notes" in sh.getShapeType() or "Outliner" in sh.getShapeType():
            sh.setString(a["text"])
            return {"slide": a["slide"], "notes_set": True}
    raise RuntimeError("no notes placeholder on slide %s" % a["slide"])


# ---- Draw ----------------------------------------------------------------

def _drawdoc(doc_id, allow_impress=True):
    doc = LO_.get(doc_id)
    k = doc_kind(doc)
    if k == "draw" or (allow_impress and k == "impress"):
        return doc
    raise RuntimeError("%s is a %s document, not draw" % (doc_id, k))


SHAPES = {"rect": "RectangleShape", "ellipse": "EllipseShape",
          "line": "LineShape", "text": "TextShape"}


@tool("draw_list_pages",
      "List the pages of a Draw document and the shapes on each "
      "(position/size are in mm).",
      obj({"doc_id": S()}, ["doc_id"]))
def t_d_pages(a):
    doc = _drawdoc(a["doc_id"])
    pages = doc.getDrawPages()
    out = []
    for i in range(pages.getCount()):
        p = pages.getByIndex(i)
        shapes = []
        for j in range(p.getCount()):
            sh = p.getByIndex(j)
            pos, sz = sh.getPosition(), sh.getSize()
            shapes.append({"index": j, "type": sh.getShapeType(),
                           "x_mm": pos.X / 100.0, "y_mm": pos.Y / 100.0,
                           "w_mm": sz.Width / 100.0, "h_mm": sz.Height / 100.0,
                           "text": _shape_text(sh)})
        out.append({"index": i, "name": p.getName(),
                    "width_mm": p.Width / 100.0, "height_mm": p.Height / 100.0,
                    "shapes": shapes})
    return {"pages": out}


@tool("draw_add_page", "Add a page to a Draw document.",
      obj({"doc_id": S(), "index": I("Insert position (default: at the end)")},
          ["doc_id"]))
def t_d_addpage(a):
    doc = _drawdoc(a["doc_id"])
    pages = doc.getDrawPages()
    idx = int(a.get("index", pages.getCount()))
    pages.insertNewByIndex(idx)
    return {"index": idx, "name": pages.getByIndex(idx).getName()}


@tool("draw_add_shape",
      "Add a shape to a Draw (or Impress) page. Coordinates and sizes are in "
      "mm from the top-left of the page. Colors are hex like '#3366CC'.",
      obj({"doc_id": S(), "page": I("Page/slide index (default 0)"),
           "type": {"type": "string", "enum": list(SHAPES),
                    "description": "rect | ellipse | line | text"},
           "x_mm": N(), "y_mm": N(), "width_mm": N(), "height_mm": N(),
           "text": S("Optional text inside the shape"),
           "fill": S("Fill color, e.g. '#3366CC', or 'none'"),
           "line_color": S("Outline color, e.g. '#000000'")},
          ["doc_id", "type", "x_mm", "y_mm", "width_mm", "height_mm"]))
def t_d_addshape(a):
    doc = _drawdoc(a["doc_id"])
    page = doc.getDrawPages().getByIndex(int(a.get("page", 0)))
    shape = doc.createInstance("com.sun.star.drawing.%s" % SHAPES[a["type"]])
    page.add(shape)
    shape.setPosition(Point(int(float(a["x_mm"]) * 100),
                            int(float(a["y_mm"]) * 100)))
    shape.setSize(Size(int(float(a["width_mm"]) * 100),
                       int(float(a["height_mm"]) * 100)))
    if a.get("text"):
        shape.setString(a["text"])
    fill = a.get("fill")
    if fill:
        if fill.lower() == "none":
            shape.FillStyle = uno.Enum("com.sun.star.drawing.FillStyle", "NONE")
        else:
            shape.FillStyle = uno.Enum("com.sun.star.drawing.FillStyle", "SOLID")
            shape.FillColor = int(fill.lstrip("#"), 16)
    if a.get("line_color"):
        shape.LineStyle = uno.Enum("com.sun.star.drawing.LineStyle", "SOLID")
        shape.LineColor = int(a["line_color"].lstrip("#"), 16)
    return {"page": int(a.get("page", 0)), "shape_index": page.getCount() - 1,
            "type": shape.getShapeType()}


@tool("draw_set_shape",
      "Modify an existing shape: its text, position, size or colors.",
      obj({"doc_id": S(), "page": I(), "shape": I(), "text": S(),
           "x_mm": N(), "y_mm": N(), "width_mm": N(), "height_mm": N(),
           "fill": S(), "line_color": S()}, ["doc_id", "page", "shape"]))
def t_d_setshape(a):
    doc = _drawdoc(a["doc_id"])
    page = doc.getDrawPages().getByIndex(int(a["page"]))
    sh = page.getByIndex(int(a["shape"]))
    if a.get("text") is not None:
        sh.setString(a["text"])
    pos, sz = sh.getPosition(), sh.getSize()
    if "x_mm" in a or "y_mm" in a:
        sh.setPosition(Point(int(float(a.get("x_mm", pos.X / 100.0)) * 100),
                             int(float(a.get("y_mm", pos.Y / 100.0)) * 100)))
    if "width_mm" in a or "height_mm" in a:
        sh.setSize(Size(int(float(a.get("width_mm", sz.Width / 100.0)) * 100),
                        int(float(a.get("height_mm", sz.Height / 100.0)) * 100)))
    if a.get("fill"):
        sh.FillStyle = uno.Enum("com.sun.star.drawing.FillStyle", "SOLID")
        sh.FillColor = int(a["fill"].lstrip("#"), 16)
    if a.get("line_color"):
        sh.LineStyle = uno.Enum("com.sun.star.drawing.LineStyle", "SOLID")
        sh.LineColor = int(a["line_color"].lstrip("#"), 16)
    return {"page": a["page"], "shape": a["shape"], "updated": True}


@tool("draw_delete_shape", "Delete a shape from a page.",
      obj({"doc_id": S(), "page": I(), "shape": I()},
          ["doc_id", "page", "shape"]))
def t_d_delshape(a):
    doc = _drawdoc(a["doc_id"])
    page = doc.getDrawPages().getByIndex(int(a["page"]))
    page.remove(page.getByIndex(int(a["shape"])))
    return {"deleted": True, "remaining": page.getCount()}


# ---- escape hatch (opt-in) -----------------------------------------------

if ALLOW_EXEC:
    @tool("lo_run_uno",
          "Run arbitrary Python UNO code against the live LibreOffice instance. "
          "Available names: uno, desktop, ctx, docs (doc_id -> document), doc "
          "(if doc_id given), props(**kw). Assign to 'result' to return a value.",
          obj({"code": S("Python source"),
               "doc_id": S("Optional document to bind as 'doc'")}, ["code"]))
    def t_exec(a):
        desktop = LO_.connect()
        env = {"uno": uno, "unohelper": unohelper, "desktop": desktop,
               "ctx": LO_.ctx, "smgr": LO_.ctx.ServiceManager,
               "docs": LO_.docs, "props": props, "Point": Point, "Size": Size,
               "result": None}
        if a.get("doc_id"):
            env["doc"] = LO_.get(a["doc_id"])
        exec(compile(a["code"], "<lo_run_uno>", "exec"), env)
        r = env.get("result")
        try:
            json.dumps(r)
        except TypeError:
            r = repr(r)
        return {"result": r}


# --------------------------------------------------------------------------
# MCP stdio transport
# --------------------------------------------------------------------------

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def result(mid, payload):
    send({"jsonrpc": "2.0", "id": mid, "result": payload})


def error(mid, code, message):
    send({"jsonrpc": "2.0", "id": mid, "error": {"code": code,
                                                 "message": message}})


def handle(msg):
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        want = params.get("protocolVersion")
        ver = want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        result(mid, {
            "protocolVersion": ver,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "libreoffice", "version": VERSION},
            "instructions": (
                "Drives a headless LibreOffice. Open or create a document "
                "(lo_open / lo_new) to get a doc_id, edit it with the "
                "writer_* / calc_* / impress_* / draw_* tools, then lo_save "
                "and lo_close. Changes only reach disk on lo_save."),
        })
    elif method in ("notifications/initialized", "notifications/cancelled"):
        pass
    elif method == "ping":
        result(mid, {})
    elif method == "tools/list":
        result(mid, {"tools": TOOLS})
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = HANDLERS.get(name)
        if fn is None:
            error(mid, -32602, "unknown tool: %s" % name)
            return
        try:
            payload = fn(args)
            text = json.dumps(payload, indent=2, default=str)
            result(mid, {"content": [{"type": "text", "text": text}],
                         "isError": False})
        except Exception as e:
            log("tool %s failed: %s" % (name, traceback.format_exc()))
            result(mid, {"content": [{"type": "text",
                                      "text": "%s: %s" % (type(e).__name__, e)}],
                         "isError": True})
    elif method in ("resources/list", "prompts/list"):
        result(mid, {"resources": [], "prompts": []})
    elif mid is not None:
        error(mid, -32601, "method not found: %s" % method)


def main():
    if "--selftest" in sys.argv:
        print(json.dumps({"tools": [t["name"] for t in TOOLS],
                          "status": t_status({})}, indent=2, default=str))
        return
    log("libreoffice-mcp %s ready (%d tools, port %d)"
        % (VERSION, len(TOOLS), PORT))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            log("bad JSON: %s" % e)
            continue
        try:
            handle(msg)
        except Exception:
            log(traceback.format_exc())
            if msg.get("id") is not None:
                error(msg["id"], -32603, "internal error")


if __name__ == "__main__":
    main()
