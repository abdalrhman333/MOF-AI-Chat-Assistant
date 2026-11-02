"""Generate the UC3 Enterprise Assistant ERD as a two-page A3 PDF.

Matches the house style of the UC2 Lessons Learned ERD: a dark cover page
with at-a-glance figures, then one landscape diagram page with entity cards,
crow's-foot connectors, a legend strip and numbered design notes.

Field data is transcribed from `NCGR_AI_SPM_Input_Data_Schema_per_UseCase
_v0.3`, sheet "UC3 Enterprise Assistant", section 3 — 119 fields across 11
entities, 67 of them required. The two entities marked `built=True` are ours,
not SPM's: the OCR cache and the vector collection the assistant retrieves
from.

    python Documentation/diagrams/uc3_erd.py

Writes `Documentation/UC3_Enterprise_Assistant_ERD.pdf`. Needs Chrome or Edge
for the PDF step; without one it still writes the HTML beside it.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent
PDF = OUT_DIR / "UC3_Enterprise_Assistant_ERD.pdf"
HTML = Path(__file__).resolve().parent / "uc3_erd.html"

ISSUED = "2 November 2026"
SCHEMA_VERSION = "v0.3"

# --------------------------------------------------------------------------
# Palette — one colour per entity group
# --------------------------------------------------------------------------
GROUPS = {
    "core":     ("#1e3a8a", "#eef2ff", "Core project"),
    "schedule": ("#0f766e", "#ecfdf5", "Schedule and delivery"),
    "register": ("#c2410c", "#fff7ed", "Registers"),
    "document": ("#7c3aed", "#f5f3ff", "Documents and retrieval"),
    "platform": ("#475569", "#f1f5f9", "Platform and access"),
}

# --------------------------------------------------------------------------
# Entities.  fields = (key, name, type, required)
#   key: "PK" | "FK" | "UK" | ""        required: True = bold, False = grey
# --------------------------------------------------------------------------
ENTITIES = [
    dict(code="SCH", name="Schedule phase", ar="الخطة الزمنية المعتمدة والمراحل",
         src="Project Server (PWA) · PWA project (ProjectId = ProjectGUID)",
         group="schedule", col=0, y=86, note=3, fields=[
             ("FK", "projectGuid", "guid", False),
             ("", "phaseId", "string", False),
             ("", "phaseName", "string", False),
             ("", "percentComplete", "decimal", False),
         ]),
    dict(code="MIL", name="Milestone", ar="المعالم الرئيسية",
         src="SharePoint list · Milestones", group="schedule", col=0, y=240,
         fields=[
             ("PK", "milestoneId", "int", False),
             ("FK", "projectCode", "string", True),
             ("", "milestoneName", "string", True),
             ("", "phaseName", "string", False),
             ("", "baselineDate", "date", False),
             ("", "forecastDate", "date", False),
             ("", "actualDate", "date", False),
             ("", "status", "choice", True),
             ("", "deletionStatus", "string", True),
         ]),
    dict(code="DEL", name="Deliverable", ar="مخرجات المشروع",
         src="SharePoint list · TBC (Deliverables)", group="schedule",
         col=0, y=470, fields=[
             ("PK", "deliverableId", "int", True),
             ("FK", "projectCode", "string", True),
             ("", "deliverableName", "string", True),
             ("", "deliverableType", "choice", False),
             ("FK", "milestoneId", "lookup", False),
             ("", "weightPct", "decimal", False),
             ("", "committedDate", "date", True),
             ("", "delivered", "bool", True),
             ("", "actualDeliveryDate", "date", False),
             ("", "status", "choice", True),
             ("", "providerRequirements", "text", False),
             ("", "acceptanceStatus", "choice", False),
             ("", "reviewerNotes", "text", False),
             ("", "modifiedAt", "datetime", True),
             ("", "deletionStatus", "string", True),
         ]),
    dict(code="PRJ", name="Project", ar="المشروع",
         src="SharePoint list · Projects", group="core", col=1, y=86, fields=[
             ("UK", "projectId", "int", True),
             ("PK", "projectCode", "string", True),
             ("", "projectNameAr", "string", True),
             ("", "projectNameEn", "string", False),
             ("", "projectType", "choice", False),
             ("", "department", "lookup", False),
             ("", "beneficiaryEntity", "string", False),
             ("", "status", "choice", True),
             ("", "currentPhase", "string", True),
             ("", "projectManager", "person", True),
             ("", "projectOwner", "person", False),
             ("", "projectSponsor", "person", False),
             ("", "programManager", "person", False),
             ("", "portfolioManager", "person", False),
             ("", "baselineStartDate", "date", False),
             ("", "baselineEndDate", "date", False),
             ("", "actualStartDate", "date", False),
             ("", "forecastEndDate", "date", False),
             ("", "plannedProgressPct", "decimal", False),
             ("", "actualProgressPct", "decimal", False),
             ("", "progressStatus", "choice", False),
             ("", "hasApprovedBaseline", "bool", True),
             ("", "deletionStatus", "string", True),
             ("", "projectUrl", "url", True),
             ("", "modifiedAt", "datetime", True),
         ]),
    dict(code="USR", name="User access scope", ar="صلاحيات المستخدم",
         src="AD + SharePoint groups + project person fields",
         group="platform", col=1, y=560, note=4, fields=[
             ("PK", "userUpn", "string", True),
             ("", "displayName", "string", False),
             ("", "email", "string", False),
             ("", "spGroups", "string[]", True),
             ("", "platformRole", "choice", True),
             ("FK", "assignedProjectCodes", "string[]", True),
             ("", "departmentScope", "string[]", False),
         ]),
    dict(code="RSK", name="Risk", ar="المخاطر",
         src="SharePoint list · Risks", group="register", col=2, y=86,
         note=8, fields=[
             ("PK", "riskId", "int", True),
             ("FK", "projectCode", "string", True),
             ("", "riskTitle", "string", True),
             ("", "description", "text", True),
             ("", "category", "choice", False),
             ("", "probability", "choice", False),
             ("", "impact", "choice", False),
             ("", "riskLevel", "choice", True),
             ("", "riskOwner", "person", True),
             ("", "dueDate", "date", True),
             ("", "status", "choice", True),
             ("", "mitigationPlan", "text", True),
             ("", "closureNotes", "text", False),
             ("", "source", "choice", False),
         ]),
    dict(code="ISS", name="Issue", ar="العوائق / القضايا",
         src="SharePoint list · Issues", group="register", col=2, y=384,
         fields=[
             ("PK", "issueId", "int", True),
             ("FK", "projectCode", "string", True),
             ("", "issueTitle", "string", True),
             ("", "description", "text", True),
             ("", "priority", "choice", True),
             ("", "owner", "person", True),
             ("", "status", "choice", True),
             ("", "resolution", "text", True),
             ("", "raisedDate", "date", False),
             ("", "resolvedDate", "date", False),
         ]),
    dict(code="CR", name="Change request", ar="طلبات التغيير",
         src="SharePoint list · TBC", group="register", col=2, y=622, fields=[
             ("PK", "crId", "int", True),
             ("FK", "projectCode", "string", True),
             ("", "crTitle", "string", True),
             ("", "crType", "choice", True),
             ("", "justification", "text", True),
             ("", "impactDescription", "text", False),
             ("", "scheduleImpactDays", "int", False),
             ("", "status", "choice", True),
             ("", "requestedDate", "date", False),
             ("", "decisionDate", "date", False),
         ]),
    dict(code="DOC", name="Project document", ar="وثائق المشروع",
         src="SharePoint libraries · TBC (approved libraries, DEP-03)",
         group="document", col=3, y=86, note=2, fields=[
             ("PK", "documentId", "string", True),
             ("FK", "projectCode", "string", True),
             ("", "fileName", "string", True),
             ("", "title", "string", True),
             ("", "libraryName", "string", True),
             ("", "folderPath", "string", False),
             ("", "documentCategory", "choice", False),
             ("", "fileExtension", "string", True),
             ("", "fileSizeBytes", "int", False),
             ("", "documentUrl", "url", True),
             ("", "version", "string", True),
             ("", "createdAt", "datetime", False),
             ("", "modifiedAt", "datetime", True),
             ("", "modifiedBy", "person", False),
             ("", "content", "binary", True),
             ("", "hasUniquePermissions", "bool", True),
         ]),
    dict(code="DCN", name="Document content", ar="النص المستخرج",
         src="Built by the assistant · PostgreSQL · OCR cache",
         group="document", col=3, y=422, built=True, fields=[
             ("PK", "document_link_id", "int", True),
             ("", "markdown", "text", True),
             ("", "raw_response", "jsonb", False),
             ("", "page_count", "int", False),
             ("", "ocr_model", "string", False),
             ("", "ocr_api_version", "string", False),
             ("", "extracted_at", "datetime", True),
         ]),
    dict(code="EVT", name="Change event", ar="حدث تغيير",
         src="Webhook / delta feed · TBC", group="platform", col=4, y=86,
         note=5, fields=[
             ("PK", "eventId", "string", True),
             ("", "entityType", "choice", True),
             ("FK", "itemId", "string", True),
             ("FK", "projectCode", "string", True),
             ("", "changeType", "choice", True),
             ("", "changedAt", "datetime", True),
             ("", "changedBy", "person", False),
         ]),
    dict(code="REF", name="Reference lookups", ar="القوائم المرجعية",
         src="SharePoint choice fields / lookup lists · TBC",
         group="platform", col=4, y=288, note=7, fields=[
             ("", "projectStatusValues", "list", True),
             ("", "departments", "list", False),
             ("", "documentCategories", "list", False),
         ]),
    dict(code="VEC", name="Vector chunk", ar="مقاطع البحث الدلالي",
         src="Built by the assistant · Qdrant · one collection per project",
         group="document", col=4, y=430, built=True, fields=[
             ("PK", "point_id", "uuid", True),
             ("", "embedding", "vector(1536)", True),
             ("", "text", "text", True),
             ("", "page", "int", False),
             ("", "section", "string", False),
             ("", "chunk_index", "int", True),
             ("", "char_start", "int", False),
             ("", "char_end", "int", False),
             ("", "has_table", "bool", False),
             ("FK", "document_link_id", "int", True),
             ("", "project_code", "string", True),
             ("", "filename", "string", True),
             ("", "doc_type", "string", True),
         ]),
]

DESIGN_NOTES = [
    "Budget is absent from UC3 but required by the BRD.",
    "Per-document permissions break project-level isolation.",
    "Schedule phase has no key in UC3.",
    "User \u2194 Project is many-to-many.",
    "Change events are polymorphic.",
    "Event sync replaces the nightly re-scan.",
    "Around 60 field names are still unconfirmed.",
    "Registers are rows, not prose \u2014 never embedded.",
]

# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------
PAGE_W, PAGE_H = 1587, 1123          # 420mm x 297mm at 96dpi
COL_X = [32, 348, 664, 980, 1296]    # 258 wide, 58 apart
COL_W = 258
HEAD_H, SRC_H, ROW_H, PAD_B = 44, 22, 15, 7
# Vertical routing lanes, one per column gap.
BUS = [319, 635, 951, 1267]


def box(e: dict) -> tuple[int, int, int, int]:
    x = COL_X[e["col"]]
    h = HEAD_H + SRC_H + len(e["fields"]) * ROW_H + PAD_B
    return x, e["y"], COL_W, h


BY_CODE = {e["code"]: e for e in ENTITIES}


def edge(code: str, side: str, frac: float = 0.5) -> tuple[float, float]:
    x, y, w, h = box(BY_CODE[code])
    if side == "l":
        return x, y + h * frac
    if side == "r":
        return x + w, y + h * frac
    if side == "t":
        return x + w * frac, y
    return x + w * frac, y + h


# Crow's foot (many) and single bar (one) markers, drawn at a line end.
def foot(x: float, y: float, facing: str, many: bool) -> str:
    d = 1 if facing == "r" else -1
    if many:
        return (f'<path class="ck" d="M{x} {y} l{7*d} -6 M{x} {y} l{7*d} 0 '
                f'M{x} {y} l{7*d} 6"/>')
    return f'<path class="ck" d="M{x+4*d} {y-6} l0 12"/>'


def ortho(a: tuple[float, float], b: tuple[float, float], mid: float | None,
          vertical_first: bool = False,
          pts: list[tuple[float, float]] | None = None) -> str:
    (x1, y1), (x2, y2) = a, b
    if pts:
        # Explicit waypoints, for the few routes that have to go round
        # something rather than straight between two columns.
        legs = " ".join(f"L{x} {y}" for x, y in pts)
        return f"M{x1} {y1} {legs} L{x2} {y2}"
    if mid is None:
        return f"M{x1} {y1} L{x2} {y2}"
    if vertical_first:
        return f"M{x1} {y1} L{x1} {mid} L{x2} {mid} L{x2} {y2}"
    return f"M{x1} {y1} L{mid} {y1} L{mid} {y2} L{x2} {y2}"


def rel(a: str, b: str, *, label: str = "", mid: float | None = None,
        dashed: bool = False, vfirst: bool = False,
        a_side: str = "r", b_side: str = "l",
        a_frac: float = 0.5, b_frac: float = 0.5,
        a_many: bool = False, b_many: bool = True,
        pts: list[tuple[float, float]] | None = None,
        lx: float | None = None, ly: float | None = None) -> dict:
    return dict(a=a, b=b, label=label, mid=mid, dashed=dashed, vfirst=vfirst,
                a_side=a_side, b_side=b_side, a_frac=a_frac, b_frac=b_frac,
                a_many=a_many, b_many=b_many, pts=pts, lx=lx, ly=ly)


RELATIONSHIPS = [
    # Project is the hub. Every child carries projectCode as its FK, so the
    # lane gets ONE shared label rather than the same word three times.
    rel("PRJ", "SCH", a_side="l", b_side="r", a_frac=0.10, b_frac=0.55,
        mid=BUS[0], dashed=True, label="projectGuid", lx=286, ly=168),
    rel("PRJ", "MIL", a_side="l", b_side="r", a_frac=0.30, b_frac=0.26,
        mid=BUS[0], label="projectCode", lx=283, ly=322),
    rel("PRJ", "DEL", a_side="l", b_side="r", a_frac=0.66, b_frac=0.14,
        mid=BUS[0]),
    rel("DEL", "MIL", a_side="t", b_side="b", a_frac=0.30, b_frac=0.30,
        mid=459, vfirst=True, b_many=False,
        label="milestoneId", lx=112, ly=452),
    rel("PRJ", "RSK", a_side="r", b_side="l", a_frac=0.14, b_frac=0.30,
        mid=BUS[1]),
    rel("PRJ", "ISS", a_side="r", b_side="l", a_frac=0.52, b_frac=0.22,
        mid=BUS[1], label="projectCode", lx=600, ly=408),
    rel("PRJ", "CR", a_side="r", b_side="l", a_frac=0.88, b_frac=0.18,
        mid=BUS[1]),
    # Documents hang off the top so the line never crosses the registers.
    rel("PRJ", "DOC", a_side="t", b_side="t", a_frac=0.55, b_frac=0.34,
        mid=56, vfirst=True,
        label="projectCode → Project document", lx=642, ly=44),
    # The two stores the assistant adds, chained off the document.
    rel("DOC", "DCN", a_side="b", b_side="t", a_frac=0.20, b_frac=0.20,
        mid=410, vfirst=True, b_many=False,
        label="extracted", lx=1042, ly=402),
    rel("DCN", "VEC", a_side="r", b_side="l", a_frac=0.55, b_frac=0.14,
        mid=BUS[3], label="chunked", lx=1236, ly=510),
    rel("VEC", "DOC", a_side="t", b_side="b", a_frac=0.86, b_frac=0.84,
        mid=414, vfirst=True, dashed=True, b_many=False,
        label="document_link_id", lx=1372, ly=406),
    # Access scope: an array in SPM, a join table here.
    rel("USR", "PRJ", a_side="t", b_side="b", a_frac=0.50, b_frac=0.50,
        mid=547, vfirst=True, a_many=True, b_many=True,
        label="N : M", lx=456, ly=540),
    # Lookups constrain values rather than owning rows, so they are dashed.
    rel("REF", "DOC", a_side="l", b_side="r", a_frac=0.70, b_frac=0.86,
        mid=BUS[3], dashed=True, b_many=False,
        label="categories", lx=1234, ly=408),
    # Routed over the top: along the bottom it would cross the registers and
    # then the legend strip.
    rel("REF", "PRJ", a_side="t", b_side="r", a_frac=0.26, b_frac=0.965,
        dashed=True, b_many=False,
        pts=[(1363, 72), (647, 72)],
        label="projectStatusValues → status", lx=700, ly=60),
    # Change events are polymorphic: itemId points at whichever entity moved.
    rel("EVT", "DOC", a_side="l", b_side="r", a_frac=0.52, b_frac=0.12,
        mid=BUS[3], dashed=True,
        label="itemId", lx=1243, ly=148),
]

SCOPE_LINES = [
    ("DeletionStatus", "ne 'Deleted'"),
    ("status", "= Active"),
    ("hasApprovedBaseline", "= true"),
    ("projectCode", "in the user's authorised list"),
]


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def entity_html(e: dict) -> str:
    x, y, w, h = box(e)
    dark, tint, _ = GROUPS[e["group"]]
    note = (f'<span class="note">{e["note"]}</span>' if e.get("note") else "")
    built = ('<span class="built">BUILT</span>' if e.get("built") else "")
    rows = []
    for key, name, typ, req in e["fields"]:
        kc = f'<span class="k k-{key.lower()}">{key}</span>' if key else \
             '<span class="k k-none"></span>'
        cls = "req" if req else "opt"
        rows.append(f'<div class="row {cls}">{kc}'
                    f'<span class="fn">{name}</span>'
                    f'<span class="ft">{typ}</span></div>')
    return f'''<div class="ent" style="left:{x}px;top:{y}px;width:{w}px;
  height:{h}px;border-color:{dark}">
  <div class="eh" style="background:{dark}">
    <span class="code">{e["code"]}</span>
    <span class="en">{e["name"]}</span>{built}
    <span class="ea">{e["ar"]}</span>{note}
  </div>
  <div class="es" style="background:{tint};color:{dark}">{e["src"]}</div>
  <div class="ef">{"".join(rows)}</div>
</div>'''


def connectors_html() -> tuple[str, str]:
    paths, labels = [], []
    for r in RELATIONSHIPS:
        a = edge(r["a"], r["a_side"], r["a_frac"])
        b = edge(r["b"], r["b_side"], r["b_frac"])
        d = ortho(a, b, r["mid"], r["vfirst"], r.get("pts"))
        cls = "ln dash" if r["dashed"] else "ln"
        paths.append(f'<path class="{cls}" d="{d}"/>')
        if r["b_side"] in ("l", "r"):
            paths.append(foot(b[0], b[1], "r" if r["b_side"] == "l" else "l",
                              r["b_many"]))
        if r["a_side"] in ("l", "r"):
            paths.append(foot(a[0], a[1], "r" if a[0] < b[0] else "l",
                              r["a_many"]))
        if r["label"] and r["lx"] is not None:
            labels.append(
                f'<div class="rl" style="left:{r["lx"]}px;top:{r["ly"]}px">'
                f'{r["label"]}</div>')
    # Labels are returned separately so the caller can paint them AFTER the
    # entity cards. Emitted before, every pill that sits over a card edge is
    # covered by it — which is what hid half of them.
    return (f'<svg class="wires" width="{PAGE_W}" height="1000">'
            f'{"".join(paths)}</svg>', "".join(labels))


def cover() -> str:
    pills = "".join(
        f'<span class="pill"><i style="background:{c[0]}"></i>{c[2]}</span>'
        for c in GROUPS.values())
    meta = [
        ("Use case", "UC3: Enterprise assistant, per project"),
        ("Source schema", f"SPM input data schema per use case, draft {SCHEMA_VERSION}"),
        ("Sheet", "\u201cUC3 Enterprise Assistant\u201d, section 3"),
        ("Systems", "SharePoint lists and libraries, Project Server (PWA), AD, "
                    "Azure OpenAI, Azure Document Intelligence, Qdrant"),
        ("Write-back", "None \u2014 the assistant is read-only"),
        ("Issued", ISSUED),
    ]
    rows = "".join(f'<tr><th>{k}</th><td>{v}</td></tr>' for k, v in meta)
    stats = [("13", "Entities (11 SPM \u00b7 2 built)"),
             ("15", "Relationships drawn"),
             ("119", "SPM fields (67 required)"),
             ("8", "Design notes to confirm")]
    cards = "".join(f'<div class="stat"><b>{n}</b><span>{t}</span></div>'
                    for n, t in stats)
    return f'''<div class="page cover">
  <div class="cl">
    <div class="eyebrow">NCGR / MOF &nbsp;·&nbsp; AI PROJECT INTELLIGENCE PLATFORM</div>
    <h1>UC3 Enterprise Assistant</h1>
    <h2>Entity Relationship Model</h2>
    <div class="arabic">المساعد الرقمي للمؤسسة (لكل مشروع)</div>
    <p class="lede">The SPM (Enjaz) data entities the per-project assistant
    reads: their keys, their relationships and cardinality, the two stores the
    assistant adds, and the open design points to confirm with the Enjaz
    team.</p>
    <svg class="mark" width="430" height="300" viewBox="0 0 430 300">
      <rect x="1" y="52" width="128" height="92"/>
      <rect x="166" y="1" width="150" height="128"/>
      <rect x="1" y="196" width="128" height="92"/>
      <rect x="166" y="168" width="150" height="128"/>
      <rect x="352" y="96" width="76" height="106"/>
      <path d="M129 92 L148 92 L148 56 L166 56"/>
      <path d="M241 129 L241 150 L241 168"/>
      <path d="M129 236 L148 236 L148 220 L166 220"/>
      <path d="M316 64 L334 64 L334 128 L352 128"/>
      <path d="M316 232 L334 232 L334 170 L352 170"/>
    </svg>
    <div class="pills">{pills}</div>
  </div>
  <div class="cr">
    <div class="lbl">AT A GLANCE</div>
    <div class="stats">{cards}</div>
    <table class="meta">{rows}</table>
    <div class="lbl mt">CONTENTS</div>
    <div class="toc"><b>01</b><span>Entity relationship diagram</span>
      <i></i><em>2</em></div>
  </div>
</div>'''


def scope_card() -> str:
    x, y = COL_X[3], 676
    items = "".join(f'<div class="sr"><code>{a}</code><span>{b}</span></div>'
                    for a, b in SCOPE_LINES)
    return f'''<div class="scope" style="left:{x}px;top:{y}px;width:{COL_W}px">
  <div class="sh">SCOPE FILTER \u00b7 every read</div>
  {items}
  <div class="sn">A project failing any line is invisible to the assistant,
  enforced by the access gate and row-level security \u2014 not by the query.</div>
</div>'''


def diagram() -> str:
    wires, labels = connectors_html()
    ents = "".join(entity_html(e) for e in ENTITIES)
    groups = "".join(
        f'<span class="lg"><i style="background:{c[0]}"></i>{c[2]}</span>'
        for c in GROUPS.values())
    notes = "".join(
        f'<span class="dn"><b>{i}</b>{t}</span>'
        for i, t in enumerate(DESIGN_NOTES, 1))
    return f'''<div class="page diag">
  <div class="phead"><i></i><span class="pn">01</span>
    <h3>Entity relationship diagram</h3>
    <div class="pcap">UC3 Enterprise Assistant · all fields listed for UC3 ·
      bold = required, grey = optional</div>
  </div>
  <div class="canvas">{wires}{ents}{scope_card()}{labels}</div>
  <div class="legend">
    <div class="lbox"><div class="lt">ENTITY GROUPS</div>
      <div class="lgs">{groups}</div></div>
    <div class="lbox"><div class="lt">RELATIONSHIPS</div>
      <div class="lrs">
        <span><svg width="46" height="10"><path class="ln" d="M2 5 L44 5"/></svg>
          FK field in the schema</span>
        <span><svg width="46" height="10"><path class="ln dash" d="M2 5 L44 5"/></svg>
          Inferred, soft or polymorphic</span>
        <span><svg width="46" height="14"><path class="ln" d="M2 7 L30 7"/>
          <path class="ck" d="M26 1 l0 12"/><path class="ck" d="M34 7 l7 -5 M34 7 l7 0 M34 7 l7 5"/></svg>
          One · zero or many</span>
      </div></div>
    <div class="lbox"><div class="lt">KEYS</div>
      <div class="lks">
        <span><i class="k k-pk">PK</i> Primary / business key</span>
        <span><i class="k k-fk">FK</i> Foreign key</span>
        <span><i class="k k-uk">UK</i> Alternate unique key</span>
        <span><i class="note">1</i> Design note (right)</span>
        <span><i class="built">BUILT</i> Added by the assistant</span>
      </div></div>
    <div class="lbox wide"><div class="lt">DESIGN NOTES</div>
      <div class="dns">{notes}</div></div>
  </div>
  <div class="pfoot"><span>UC3 Enterprise Assistant · Entity Relationship
    Model · schema {SCHEMA_VERSION}</span><b>2</b></div>
</div>'''


CSS = f'''
@page {{ size: A3 landscape; margin: 0; }}
* {{ box-sizing: border-box; -webkit-print-color-adjust: exact;
     print-color-adjust: exact; }}
body {{ margin: 0; font-family: "Segoe UI", system-ui, sans-serif;
        color: #0f172a; }}
.page {{ width: {PAGE_W}px; height: {PAGE_H}px; position: relative;
         overflow: hidden; page-break-after: always; background: #fff; }}
.page:last-child {{ page-break-after: auto; }}

/* ---------- cover ---------- */
.cover {{ display: grid; grid-template-columns: 62% 38%; }}
.cl {{ background: linear-gradient(150deg,#1d4ed8 0%,#17336f 42%,#0b1733 100%);
       padding: 74px 66px; position: relative; color: #fff; }}
.eyebrow {{ font-size: 13px; letter-spacing: 2.6px; font-weight: 600;
            color: #a9c3f0; }}
.cl h1 {{ font-size: 68px; margin: 118px 0 0; font-weight: 800;
          letter-spacing: -1.6px; line-height: 1; }}
.cl h2 {{ font-size: 50px; margin: 6px 0 0; font-weight: 300;
          color: #b9cdf2; letter-spacing: -1px; line-height: 1.1; }}
.arabic {{ font-size: 27px; margin-top: 34px; direction: rtl;
           width: fit-content;
           font-family: "Segoe UI", Tahoma, sans-serif; }}
.lede {{ font-size: 16.5px; line-height: 1.68; color: #cfddf6;
         max-width: 650px; margin-top: 30px; }}
.mark {{ position: absolute; right: 56px; bottom: 132px;
         opacity: .13; pointer-events: none; }}
  .mark rect {{ fill: none; stroke: #cfe0ff; stroke-width: 1.6; rx: 9; }}
  .mark path {{ fill: none; stroke: #cfe0ff; stroke-width: 1.6; }}
.pills {{ position: absolute; bottom: 54px; left: 66px; display: flex;
          gap: 9px; flex-wrap: wrap; }}
.pill {{ background: rgba(255,255,255,.1); border: 1px solid rgba(255,255,255,.2);
         border-radius: 999px; padding: 7px 14px; font-size: 12.5px;
         display: inline-flex; align-items: center; gap: 8px; }}
.pill i {{ width: 9px; height: 9px; border-radius: 3px; display: block; }}
.cr {{ background: #f8fafc; padding: 74px 56px; }}
.lbl {{ font-size: 12px; letter-spacing: 2.2px; font-weight: 700;
        color: #64748b; }}
.lbl.mt {{ margin-top: 44px; }}
.stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px;
          margin: 18px 0 38px; }}
.stat {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 12px;
         padding: 20px 22px; }}
.stat b {{ display: block; font-size: 40px; color: #1e3a8a; line-height: 1;
           letter-spacing: -1.2px; }}
.stat span {{ display: block; font-size: 12.5px; color: #64748b;
              margin-top: 9px; }}
table.meta {{ width: 100%; border-collapse: collapse; }}
table.meta th {{ text-align: left; font-size: 13px; color: #64748b;
                 font-weight: 400; padding: 13px 0; width: 36%;
                 vertical-align: top; border-top: 1px solid #e2e8f0; }}
table.meta td {{ font-size: 13.5px; padding: 13px 0; border-top: 1px solid #e2e8f0;
                 vertical-align: top; }}
table.meta tr:first-child th, table.meta tr:first-child td {{ border-top: 0; }}
.toc {{ display: flex; align-items: baseline; gap: 12px; margin-top: 18px;
        font-size: 14px; }}
.toc b {{ color: #7c3aed; }}
.toc i {{ flex: 1; border-bottom: 1px dotted #cbd5e1; }}
.toc em {{ font-style: normal; color: #64748b; }}

/* ---------- diagram ---------- */
.phead {{ display: flex; align-items: center; gap: 14px; padding: 26px 46px 0; }}
.phead i {{ width: 5px; height: 26px; background: #7c3aed; border-radius: 3px; }}
.pn {{ font-size: 12px; letter-spacing: 2px; color: #64748b; font-weight: 700; }}
.phead h3 {{ font-size: 25px; margin: 0; font-weight: 700;
             letter-spacing: -.4px; }}
.pcap {{ margin-left: auto; font-size: 11.5px; color: #64748b;
         text-align: right; max-width: 430px; }}
.canvas {{ position: absolute; top: 72px; left: 0; width: {PAGE_W}px;
           height: 1000px; }}
.wires {{ position: absolute; inset: 0; overflow: visible; }}
.ln {{ fill: none; stroke: #334980; stroke-width: 1.3; }}
.ln.dash {{ stroke-dasharray: 5 4; stroke: #6b7fb3; }}
.ck {{ fill: none; stroke: #334980; stroke-width: 1.3; }}
.rl {{ position: absolute; background: #fff; border: 1px solid #dbe2f0;
       border-radius: 5px; padding: 2px 7px; font-size: 10px; color: #334980;
       white-space: nowrap; z-index: 6;
       box-shadow: 0 1px 2px rgba(15,23,42,.08); }}

.ent {{ position: absolute; background: #fff; border: 1.4px solid;
        border-radius: 10px; overflow: hidden;
        box-shadow: 0 1px 3px rgba(15,23,42,.07); }}
.eh {{ height: {HEAD_H}px; display: flex; align-items: center; gap: 7px;
       padding: 0 10px; color: #fff; position: relative;
       white-space: nowrap; }}
.code {{ background: rgba(255,255,255,.22); border-radius: 4px;
         padding: 3px 6px; font-size: 9.5px; font-weight: 700;
         letter-spacing: .5px; flex: none; }}
.en {{ font-size: 13px; font-weight: 700; letter-spacing: -.2px; }}
.ea {{ margin-left: auto; font-size: 10.5px; direction: rtl; opacity: .9;
       font-family: "Segoe UI", Tahoma, sans-serif; flex: none; }}
.built {{ background: #ecfdf5; color: #047857; border-radius: 4px;
          padding: 2px 5px; font-size: 7.5px; font-weight: 800;
          letter-spacing: .5px; flex: none; }}
.note {{ position: absolute; right: 7px; top: {HEAD_H}px; width: 15px;
         height: 15px; border-radius: 50%; background: #dc2626; color: #fff;
         font-size: 9px; font-weight: 700; display: flex; z-index: 3;
         align-items: center; justify-content: center;
         box-shadow: 0 0 0 2px #fff; }}
.es {{ height: {SRC_H}px; display: flex; align-items: center; padding: 0 10px;
       font-size: 8.5px; font-weight: 600; white-space: nowrap;
       overflow: hidden; text-overflow: ellipsis; }}
.ef {{ padding: 3px 0 {PAD_B}px; }}
.row {{ height: {ROW_H}px; display: flex; align-items: center; gap: 7px;
        padding: 0 10px; font-size: 10px; white-space: nowrap;
        font-family: Consolas, "Courier New", monospace; }}
.row.req .fn {{ color: #0f172a; font-weight: 700; }}
.row.opt .fn {{ color: #94a3b8; }}
.row .ft {{ margin-left: auto; color: #94a3b8; font-size: 9.5px; }}
.row.opt .ft {{ color: #cbd5e1; }}
.k {{ width: 19px; font-size: 7.5px; font-weight: 800; text-align: center;
      border-radius: 3px; padding: 1.5px 0; letter-spacing: .3px;
      font-family: "Segoe UI", sans-serif; }}
.k-pk {{ background: #fef3c7; color: #92400e; }}
.k-fk {{ background: #dbeafe; color: #1e40af; }}
.k-uk {{ background: #e0e7ff; color: #4338ca; }}
.k-none {{ background: transparent; }}

.scope {{ position: absolute; background: #fff7ed; border: 1.4px dashed #c2410c;
          border-radius: 10px; padding: 12px 13px; }}
.sh {{ font-size: 10px; font-weight: 800; letter-spacing: 1.2px;
       color: #c2410c; margin-bottom: 9px; }}
.sr {{ display: flex; gap: 7px; align-items: baseline; margin-bottom: 5px;
       font-size: 10px; }}
.sr code {{ font-family: Consolas, monospace; font-weight: 700;
            color: #7c2d12; }}
.sr span {{ color: #9a3412; }}
.sn {{ font-size: 9.5px; color: #9a3412; line-height: 1.45; margin-top: 9px;
       border-top: 1px solid #fed7aa; padding-top: 8px; }}

.legend {{ position: absolute; left: 46px; right: 46px; bottom: 52px;
           display: grid; grid-template-columns: 1.05fr .95fr .8fr 1.5fr;
           gap: 14px; }}
.lbox {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px;
         padding: 13px 15px; }}
.lt {{ font-size: 10px; letter-spacing: 1.6px; font-weight: 700;
       color: #64748b; margin-bottom: 9px; }}
.lgs, .lrs, .lks {{ display: flex; flex-direction: column; gap: 5px; }}
.lgs {{ display: grid; grid-template-columns: 1fr 1fr; gap: 5px 10px; }}
.lg, .lrs span, .lks span {{ font-size: 10.5px; color: #334155;
       display: flex; align-items: center; gap: 7px; }}
.lg i {{ width: 9px; height: 9px; border-radius: 3px; display: block; }}
.lks .k, .lks .built, .lks .note {{ position: static; flex: none; }}
.lks .note {{ width: 15px; height: 15px; }}
.dns {{ display: grid; grid-template-columns: 1fr 1fr; gap: 5px 16px; }}
.dn {{ font-size: 10.5px; color: #334155; display: flex; gap: 7px;
       align-items: flex-start; }}
.dn b {{ flex: none; width: 15px; height: 15px; border-radius: 50%;
         background: #dc2626; color: #fff; font-size: 9px; display: flex;
         align-items: center; justify-content: center; margin-top: 1px; }}
.pfoot {{ position: absolute; left: 46px; right: 46px; bottom: 22px;
          display: flex; font-size: 10.5px; color: #94a3b8; }}
.pfoot b {{ margin-left: auto; color: #334155; }}
'''


def build_html() -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>UC3 Enterprise Assistant — ERD</title>'
            f'<style>{CSS}</style></head><body>'
            f'{cover()}{diagram()}</body></html>')


def main() -> int:
    HTML.write_text(build_html(), encoding="utf-8")
    print(f"html  -> {HTML}")

    browser = next((p for p in [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        shutil.which("chrome"), shutil.which("msedge"),
    ] if p and Path(p).exists()), None)
    if not browser:
        print("no Chrome/Edge found; HTML written, PDF skipped")
        return 1

    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([
            browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
            f"--user-data-dir={profile}", "--no-first-run",
            f"--print-to-pdf={PDF}", HTML.as_uri(),
        ], check=True, capture_output=True, timeout=180)
    print(f"pdf   -> {PDF}  ({PDF.stat().st_size/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
