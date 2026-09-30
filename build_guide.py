"""Builds OpsFlow_Guide.pdf — the detailed, reader-friendly guide to the project.

    python docs/guide/build_guide.py            # writes ./OpsFlow_Guide.pdf at the repo root

Requires: reportlab (pip install reportlab) and the DejaVu fonts (fonts-dejavu on Debian/Ubuntu).
Screenshots are read from docs/screenshots/.
"""

from __future__ import annotations

import sys
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs" / "screenshots"
OUT = ROOT / "OpsFlow_Guide.pdf"

# ---------------------------------------------------------------- fonts & palette
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
try:
    pdfmetrics.registerFont(TTFont("Body", str(FONT_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("Body-Bold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("Body-Italic", str(FONT_DIR / "DejaVuSans-Oblique.ttf")))
    pdfmetrics.registerFont(TTFont("Mono", str(FONT_DIR / "DejaVuSansMono.ttf")))
    from reportlab.pdfbase.pdfmetrics import registerFontFamily

    registerFontFamily("Body", normal="Body", bold="Body-Bold", italic="Body-Italic", boldItalic="Body-Bold")
except Exception:  # noqa: BLE001
    sys.exit("DejaVu fonts not found (apt install fonts-dejavu-core)")

INK = colors.HexColor("#151a25")
INK2 = colors.HexColor("#353d4e")
MUTED = colors.HexColor("#636d80")
LINE = colors.HexColor("#dde1e8")
SOFT = colors.HexColor("#f4f5f8")
ACCENT = colors.HexColor("#0e8573")
ACCENT_SOFT = colors.HexColor("#e3f6f1")
WARN_SOFT = colors.HexColor("#fff6e0")
RED = colors.HexColor("#b42318")

# ---------------------------------------------------------------- styles
S = {
    "title": ParagraphStyle("title", fontName="Body-Bold", fontSize=30, leading=36, textColor=INK),
    "subtitle": ParagraphStyle("subtitle", fontName="Body", fontSize=13, leading=19, textColor=MUTED),
    "h1": ParagraphStyle("h1", fontName="Body-Bold", fontSize=19, leading=24, textColor=INK, spaceBefore=4, spaceAfter=10),
    "h2": ParagraphStyle("h2", fontName="Body-Bold", fontSize=13.5, leading=18, textColor=INK, spaceBefore=12, spaceAfter=5),
    "h3": ParagraphStyle("h3", fontName="Body-Bold", fontSize=11, leading=15, textColor=INK2, spaceBefore=8, spaceAfter=3),
    "p": ParagraphStyle("p", fontName="Body", fontSize=9.6, leading=14.2, textColor=INK2, spaceAfter=5),
    "small": ParagraphStyle("small", fontName="Body", fontSize=8.2, leading=11.5, textColor=MUTED),
    "cell": ParagraphStyle("cell", fontName="Body", fontSize=8.4, leading=11.2, textColor=INK2),
    "cellb": ParagraphStyle("cellb", fontName="Body-Bold", fontSize=8.4, leading=11.2, textColor=INK),
    "code": ParagraphStyle("code", fontName="Mono", fontSize=7.9, leading=10.6, textColor=INK),
    "bullet": ParagraphStyle("bullet", fontName="Body", fontSize=9.6, leading=14, textColor=INK2, leftIndent=12, bulletIndent=2, spaceAfter=2),
    "q": ParagraphStyle("q", fontName="Body-Bold", fontSize=9.8, leading=14, textColor=INK, spaceBefore=7, spaceAfter=2),
    "a": ParagraphStyle("a", fontName="Body", fontSize=9.4, leading=13.8, textColor=INK2, leftIndent=10, spaceAfter=3),
    "caption": ParagraphStyle("caption", fontName="Body-Italic", fontSize=8, leading=11, textColor=MUTED, alignment=TA_CENTER, spaceAfter=8),
    "toc1": ParagraphStyle("toc1", fontName="Body-Bold", fontSize=10.5, leading=17, textColor=INK),
    "toc2": ParagraphStyle("toc2", fontName="Body", fontSize=9.2, leading=14, leftIndent=14, textColor=INK2),
}


# ---------------------------------------------------------------- document with TOC hooks
class GuideDoc(BaseDocTemplate):
    def __init__(self, filename: str):
        super().__init__(
            filename,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=18 * mm,
            title="OpsFlow — Project Guide",
            author="Alloy Das",
            subject="Newtonite Software Engineering Challenge",
        )
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="body")
        self.addPageTemplates(
            [
                PageTemplate(id="cover", frames=[frame], onPage=self._cover),
                PageTemplate(id="body", frames=[frame], onPage=self._decorate),
            ]
        )

    def afterFlowable(self, flowable):  # noqa: N802 — reportlab hook
        if isinstance(flowable, Paragraph) and hasattr(flowable, "_toc_level"):
            text = flowable.getPlainText()
            key = f"h{id(flowable)}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=flowable._toc_level)
            self.notify("TOCEntry", (flowable._toc_level, text, self.page, key))

    @staticmethod
    def _cover(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(INK)
        canvas.rect(0, A4[1] - 70 * mm, A4[0], 70 * mm, stroke=0, fill=1)
        canvas.setFillColor(ACCENT)
        canvas.rect(0, A4[1] - 72 * mm, A4[0], 2 * mm, stroke=0, fill=1)
        canvas.restoreState()

    @staticmethod
    def _decorate(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(doc.leftMargin, A4[1] - 13 * mm, A4[0] - doc.rightMargin, A4[1] - 13 * mm)
        canvas.setFont("Body", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(doc.leftMargin, A4[1] - 11 * mm, "OpsFlow — Project Guide")
        canvas.drawRightString(A4[0] - doc.rightMargin, A4[1] - 11 * mm, "Newtonite Software Engineering Challenge")
        canvas.drawRightString(A4[0] - doc.rightMargin, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()


story: list = []


def h1(text: str) -> None:
    story.append(CondPageBreak(60 * mm))
    p = Paragraph(escape(text), S["h1"])
    p._toc_level = 0
    story.append(p)


def h2(text: str, toc: bool = True) -> None:
    p = Paragraph(escape(text), S["h2"])
    if toc:
        p._toc_level = 1
    story.append(KeepTogether([p]))


def h3(text: str) -> None:
    story.append(Paragraph(escape(text), S["h3"]))


def p(markup: str) -> None:
    """Paragraph with light inline markup: **bold**, `code`."""
    story.append(Paragraph(md(markup), S["p"]))


def md(text: str) -> str:
    out = escape(text)
    parts = out.split("`")
    for i in range(1, len(parts), 2):
        parts[i] = f'<font face="Mono" size="8.4" color="#0d6b5e">{parts[i]}</font>'
    out = "".join(parts)
    bold = out.split("**")
    for i in range(1, len(bold), 2):
        bold[i] = f"<b>{bold[i]}</b>"
    return "".join(bold)


def bullets(items: list[str]) -> None:
    for it in items:
        story.append(Paragraph(md(it), S["bullet"], bulletText="•"))
    story.append(Spacer(1, 3))


def numbered(items: list[str]) -> None:
    for i, it in enumerate(items, 1):
        story.append(Paragraph(md(it), S["bullet"], bulletText=f"{i}."))
    story.append(Spacer(1, 3))


def code(text: str) -> None:
    pre = Preformatted(text.strip("\n"), S["code"])
    t = Table([[pre]], colWidths=[doc_width()])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(t)
    story.append(Spacer(1, 6))


def callout(markup: str, tone: str = "accent") -> None:
    bg = {"accent": ACCENT_SOFT, "warn": WARN_SOFT}[tone]
    t = Table([[Paragraph(md(markup), S["p"])]], colWidths=[doc_width()])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LINEBEFORE", (0, 0), (0, -1), 2.2, ACCENT if tone == "accent" else colors.HexColor("#d99a1e")),
            ]
        )
    )
    story.append(t)
    story.append(Spacer(1, 7))


def table(rows: list[list[str]], widths: list[float], header: bool = True) -> None:
    data = []
    for r, row in enumerate(rows):
        style = S["cellb"] if header and r == 0 else S["cell"]
        data.append([Paragraph(md(c), style) for c in row])
    total = sum(widths)
    t = Table(data, colWidths=[w / total * doc_width() for w in widths], repeatRows=1 if header else 0)
    cmds = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
    ]
    if header:
        cmds += [("BACKGROUND", (0, 0), (-1, 0), SOFT), ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK2)]
    t.setStyle(TableStyle(cmds))
    story.append(t)
    story.append(Spacer(1, 8))


def shot(name: str, caption: str, width_frac: float = 1.0) -> None:
    path = SHOTS / name
    if not path.exists():
        return
    from PIL import Image as PILImage

    w, h = PILImage.open(path).size
    width = doc_width() * width_frac
    height = width * h / w
    max_h = 150 * mm
    if height > max_h:
        width, height = width * max_h / height, max_h
    img = Image(str(path), width=width, height=height)
    frame = Table([[img]], colWidths=[width + 2])
    frame.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, LINE), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    story.append(KeepTogether([frame, Spacer(1, 3), Paragraph(escape(caption), S["caption"])]))


def qa(question: str, answer: str) -> None:
    story.append(KeepTogether([Paragraph(md("Q. " + question), S["q"]), Paragraph(md(answer), S["a"])]))


def doc_width() -> float:
    return A4[0] - 40 * mm


# ---------------------------------------------------------------- diagrams
def box(d: Drawing, x, y, w, h, label, sub="", fill=colors.white, stroke=INK2, bold=True):
    d.add(Rect(x, y, w, h, rx=5, ry=5, fillColor=fill, strokeColor=stroke, strokeWidth=0.9))
    d.add(String(x + w / 2, y + h / 2 + (4 if sub else -3), label, fontName="Body-Bold" if bold else "Body",
                 fontSize=8.4, textAnchor="middle", fillColor=INK))
    if sub:
        for i, line in enumerate(sub.split("\n")):
            d.add(String(x + w / 2, y + h / 2 - 7 - i * 9, line, fontName="Body", fontSize=6.8,
                         textAnchor="middle", fillColor=MUTED))


def arrow(d: Drawing, x1, y1, x2, y2, label="", color=INK2):
    d.add(Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=0.9))
    import math

    ang = math.atan2(y2 - y1, x2 - x1)
    s = 5
    pts = [x2, y2, x2 - s * math.cos(ang - 0.4), y2 - s * math.sin(ang - 0.4), x2 - s * math.cos(ang + 0.4),
           y2 - s * math.sin(ang + 0.4)]
    d.add(Polygon(pts, fillColor=color, strokeColor=color))
    if label:
        d.add(String((x1 + x2) / 2, (y1 + y2) / 2 + 4, label, fontName="Body", fontSize=6.6, textAnchor="middle",
                     fillColor=MUTED))


def architecture_diagram() -> Drawing:
    W = doc_width()  # ~482 pt
    d = Drawing(W, 270)
    gap = 18
    bw = (W - 2 * gap) / 3
    # top row: browser → proxy → API
    box(d, 0, 200, bw, 52, "Browser", "React SPA\nTanStack Query cache", fill=SOFT)
    box(d, bw + gap, 200, bw, 52, "nginx / Vite proxy", "static files · /api proxy\nCSP & security headers", fill=SOFT)
    ax = 2 * (bw + gap)
    box(d, ax, 150, bw, 102, "", fill=colors.white)
    d.add(String(ax + bw / 2, 240, "FastAPI modular monolith", fontName="Body-Bold", fontSize=8, textAnchor="middle", fillColor=INK))
    layers = [("api/v1 — routes", 214), ("auth — sessions, policy", 193), ("services — use cases", 172), ("domain — workflow rules", 151)]
    for label, y in layers:
        d.add(Rect(ax + 8, y + 2, bw - 16, 16, rx=3, ry=3, fillColor=ACCENT_SOFT, strokeColor=ACCENT, strokeWidth=0.6))
        d.add(String(ax + bw / 2, y + 7, label, fontName="Body", fontSize=6.8, textAnchor="middle", fillColor=INK))
    # bottom row: IdP, PostgreSQL, worker
    box(d, 0, 30, bw, 62, "Identity provider", "OIDC: Okta, Entra ID…\nor the bundled dev IdP", fill=WARN_SOFT)
    box(d, bw + gap, 30, bw, 62, "PostgreSQL 16", "constraints · row locks · FTS\ntriggers · sessions · throttle", fill=SOFT)
    box(d, ax, 30, bw, 62, "Outbox worker", "SKIP LOCKED · retries\ndead letter · notifications", fill=SOFT)
    arrow(d, bw, 226, bw + gap, 226)
    arrow(d, 2 * bw + gap, 226, ax, 226)
    arrow(d, ax + bw * 0.3, 150, bw + gap + bw * 0.75, 92, "")
    d.add(String(ax + bw * 0.3 - 6, 112, "one transaction", fontName="Body", fontSize=6.5, textAnchor="start", fillColor=MUTED))
    d.add(String(ax + bw * 0.3 - 6, 104, "per request", fontName="Body", fontSize=6.5, textAnchor="start", fillColor=MUTED))
    arrow(d, ax, 61, 2 * bw + gap, 61, "")
    d.add(String(ax - gap / 2, 66, "poll", fontName="Body", fontSize=6.3, textAnchor="middle", fillColor=MUTED))
    arrow(d, bw / 2, 200, bw / 2, 92, "")
    d.add(String(bw / 2 + 4, 146, "SSO redirect", fontName="Body", fontSize=6.5, textAnchor="start", fillColor=MUTED))
    arrow(d, ax + bw * 0.12, 150, bw - 2, 88, "")
    d.add(String(bw + 6, 136, "code → token", fontName="Body", fontSize=6.3, textAnchor="start", fillColor=MUTED))
    d.add(String(bw + 6, 128, "(server-to-server)", fontName="Body", fontSize=6.3, textAnchor="start", fillColor=MUTED))
    return d


def workflow_diagram() -> Drawing:
    W = doc_width()
    d = Drawing(W, 160)
    bw, bh = 82, 28
    col = [0, (W - bw) * 0.34, (W - bw) * 0.68, W - bw]
    pos = {
        "open": (col[0], 96),
        "in_progress": (col[1], 96),
        "resolved": (col[2], 96),
        "closed": (col[3], 96),
        "cancelled": (col[0], 12),
        "blocked": (col[1], 12),
    }
    fills = {"closed": ACCENT_SOFT, "cancelled": SOFT}
    for k, (x, y) in pos.items():
        box(d, x, y, bw, bh, k, fill=fills.get(k, colors.white))

    def c(k, side, dx=0):
        x, y = pos[k]
        return {"r": (x + bw, y + bh / 2), "l": (x, y + bh / 2), "t": (x + bw / 2 + dx, y + bh), "b": (x + bw / 2 + dx, y)}[side]

    def lbl(x, y, text, anchor="middle"):
        d.add(String(x, y, text, fontName="Body", fontSize=6.6, textAnchor=anchor, fillColor=MUTED))

    arrow(d, *c("open", "r"), *c("in_progress", "l"))
    lbl((col[0] + bw + col[1]) / 2, 114, "start")
    arrow(d, *c("in_progress", "r"), *c("resolved", "l"))
    lbl((col[1] + bw + col[2]) / 2, 114, "resolve")
    arrow(d, *c("resolved", "r"), *c("closed", "l"))
    lbl((col[2] + bw + col[3]) / 2, 114, "approve")
    lbl((col[2] + bw + col[3]) / 2, 86, "(manager)")
    arrow(d, *c("in_progress", "b", -14), *c("blocked", "t", -14))
    lbl(col[1] + bw / 2 - 18, 64, "block*", "end")
    arrow(d, *c("blocked", "t", 14), *c("in_progress", "b", 14))
    lbl(col[1] + bw / 2 + 18, 64, "unblock", "start")
    arrow(d, *c("open", "b"), *c("cancelled", "t"))
    lbl(col[0] + bw / 2 + 4, 64, "cancel*", "start")
    arrow(d, *c("blocked", "l"), *c("cancelled", "r"))
    lbl((col[0] + bw + col[1]) / 2, 30, "cancel* (mgr)")
    top = 96 + bh
    rx, ix = col[2] + bw / 2, col[1] + bw / 2
    d.add(Line(rx, top, rx, top + 18, strokeColor=INK2, strokeWidth=0.9))
    d.add(Line(rx, top + 18, ix + 16, top + 18, strokeColor=INK2, strokeWidth=0.9))
    arrow(d, ix + 16, top + 18, ix + 16, top)
    lbl((rx + ix) / 2 + 8, top + 22, "reject* (reporter / manager)")
    d.add(String(W, 2, "* reason required", fontName="Body-Italic", fontSize=6.8, textAnchor="end", fillColor=MUTED))
    return d


# =============================================================================== CONTENT
def cover() -> None:
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph('<font color="#ffffff">OpsFlow</font>', S["title"]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph('<font color="#c3c9d4">Operational work management — Project Guide</font>', S["subtitle"]))
    story.append(Spacer(1, 50 * mm))
    story.append(Paragraph("What it is · Why it exists · How to install and run it · How it was built · Questions and answers", S["subtitle"]))
    story.append(Spacer(1, 10 * mm))
    table(
        [
            ["Prepared for", "Newtonite Software Engineering Challenge — “Operations Under Pressure”"],
            ["Author", "Alloy Das"],
            ["Date", "30 September 2026"],
            ["Stack", "FastAPI · SQLAlchemy 2 · PostgreSQL 16 · React 19 · TypeScript · Tailwind · Docker Compose"],
            ["Verified", "146 backend tests · 4 frontend unit tests · 32-check multi-browser end-to-end run — all passing"],
        ],
        [1, 4],
        header=False,
    )
    story.append(Spacer(1, 6 * mm))
    callout(
        "**How to read this guide.** Sections 1–3 explain the product in plain language. Sections 4–6 are a "
        "step-by-step installation and run book. Sections 7–11 explain the engineering approach. Section 14 "
        "answers the questions people usually ask — about usage, setup, design, security and scaling."
    )
    story.append(NextPageTemplate("body"))
    story.append(PageBreak())


def contents() -> None:
    story.append(Paragraph("Contents", S["h1"]))
    toc = TableOfContents()
    toc.levelStyles = [S["toc1"], S["toc2"]]
    toc.dotsMinLevel = 0
    story.append(toc)
    story.append(PageBreak())


def section_what() -> None:
    h1("1. What is OpsFlow?")
    p(
        "OpsFlow is an internal web application in which a company’s teams record, own, progress and approve "
        "**operational work**: a customer escalation, a payment that needs investigating, a production incident, "
        "a compliance request, a task that needs a manager’s sign-off. Each request becomes a **work item** with "
        "a single owner, a clear state, a priority, an optional due date, a discussion thread and a complete, "
        "tamper-proof history."
    )
    p(
        "It replaces the mix of chat threads, spreadsheets and email that stops working once a company has "
        "hundreds of employees — where requests get lost, two people unknowingly work on the same thing, "
        "decisions cannot be traced, and nobody can see what needs attention."
    )
    h2("In one paragraph, for engineers")
    p(
        "A modular FastAPI monolith on PostgreSQL with a React/TypeScript single-page app. The interesting part is "
        "not CRUD but **correctness under concurrent and repeated use**: atomic claims (exactly one winner), "
        "optimistic versioning with a conflict-resolution UI, idempotency keys, resource-level authorization, "
        "database-enforced workflow and audit invariants, a transactional outbox for notifications — plus identity "
        "and access management: revocable server-side sessions, PostgreSQL-backed login rate limiting, OIDC single "
        "sign-on, user administration and an append-only security log."
    )
    h2("What a user can do")
    table(
        [
            ["Area", "Capabilities"],
            ["Work items", "Create, edit, claim, assign/reassign, release, comment; human-readable keys such as `PAY-42`"],
            ["Workflow", "Open → In progress → Blocked / Awaiting approval → Closed (manager approval) or Cancelled; reasons required for block, cancel and reject"],
            ["Overview", "Dashboard of what needs attention (blocked, overdue, awaiting approval, unassigned urgent, my work), per-team breakdown"],
            ["Finding work", "Full-text search, jump-to-key, filters (status, priority, team, assignee, overdue), four sort orders, pagination"],
            ["History", "Timeline of every change with who, when, before/after values and reasons; cannot be edited or deleted"],
            ["Collaboration", "Comments, in-app notifications (assignment, status changes, comments, priority changes)"],
            ["Teams", "Teams with managers and members; managers manage membership; admins create teams"],
            ["Accounts", "Password or single sign-on; see and sign out your devices; change password"],
            ["Administration", "Create/deactivate/reactivate users, reset passwords, grant admin, sign users out everywhere, security log, notification-delivery health"],
        ],
        [1, 4],
    )
    shot("02-dashboard.png", "Figure 1 — Dashboard: the attention tiles link to pre-filtered lists.")


def section_why() -> None:
    h1("2. Why use it — the problem it solves")
    table(
        [
            ["Pain today (chat, spreadsheets, email)", "How OpsFlow addresses it"],
            ["Requests disappear in long threads", "Every request is a work item with a state; the dashboard surfaces blocked, overdue and unowned work"],
            ["Nobody knows who owns what", "Exactly one assignee; claiming is atomic, so two people can never both believe they own an item"],
            ["Two people start the same work", "A second claimer is told immediately who already owns it"],
            ["People act on outdated information", "Every change carries a version; stale edits are refused and the user sees what changed"],
            ["Repeating an action “just in case” duplicates it", "Idempotency keys: a retried request returns the original result instead of repeating it"],
            ["Decisions cannot be explained later", "Immutable history with before/after values and mandatory reasons for block/cancel/reject"],
            ["Approvals happen informally", "Resolution → manager approval is part of the workflow and enforced by the server"],
            ["Anyone can see or change anything", "Team-based access; other teams’ items are invisible; actions are checked on the server"],
            ["Former employees keep access; weak passwords", "Deactivation ends sessions instantly; sign-in throttling; SSO with the company identity provider"],
        ],
        [1, 1.3],
    )
    h2("Who uses it")
    table(
        [
            ["Role", "Typical person", "What they can do"],
            ["Team member", "Support agent, engineer, analyst", "See their teams’ work, create items, claim unowned items, work them through the workflow, comment"],
            ["Team manager", "Team lead", "Everything a member can, plus assign/reassign, edit any item, approve or reject resolutions, cancel, manage team membership"],
            ["Reporter / assignee", "Whoever raised or owns an item", "Edit it; the reporter can cancel it while open or reject a resolution; the assignee moves it through the workflow"],
            ["Administrator", "IT / operations lead", "All of the above in every team; create teams and users; deactivate users; reset passwords; view the security log and system health"],
        ],
        [0.9, 1.1, 2.6],
    )
    h2("Why this design is worth trusting")
    bullets(
        [
            "**Rules are enforced by the server and the database**, never only by hiding buttons.",
            "**Every critical behaviour has an automated test** against a real PostgreSQL database, including tests that "
            "race many simultaneous requests against each other.",
            "**Nothing important can silently disappear**: state changes and their audit records are written in one "
            "transaction, and the audit tables reject edits and deletes at the database level.",
        ]
    )


def section_requirements() -> None:
    h1("3. Requirements to install")
    h2("Option A — Docker (recommended)")
    table(
        [
            ["Requirement", "Version / notes"],
            ["Docker Engine + Docker Compose v2", "Docker Desktop on Windows/macOS, or docker-ce on Linux"],
            ["Free ports", "8080 (app), 8000 (API, localhost only), 5432 (PostgreSQL), 9000 (optional dev SSO provider)"],
            ["Hardware", "2 CPU cores, 2 GB free RAM, ~1.5 GB disk for images"],
            ["Browser", "Any current Chrome, Edge, Firefox or Safari"],
        ],
        [1, 2],
    )
    h2("Option B — Run the parts locally (for development)")
    table(
        [
            ["Requirement", "Version / notes"],
            ["Python", "3.11 or newer (3.11 used)"],
            ["Node.js + npm", "20 or newer (22 used)"],
            ["PostgreSQL", "14 or newer (16 used) with the standard `pg_trgm` extension (package `postgresql-contrib` on some distributions)"],
            ["A PostgreSQL superuser", "Only once, to create the `opsflow` role and the two databases"],
            ["Optional: Playwright", "Only to run the end-to-end browser test (`npm i -D playwright && npx playwright install chromium`)"],
        ],
        [1, 2],
    )
    h2("Python and JavaScript dependencies")
    p("Installed automatically by the commands in section 4. For reference:")
    table(
        [
            ["Backend (backend/pyproject.toml)", "Frontend (frontend/package.json)"],
            ["fastapi, uvicorn, sqlalchemy 2, psycopg 3, alembic, pydantic 2, pydantic-settings, pyjwt, bcrypt, cryptography, httpx, python-multipart; dev: pytest, ruff, mypy",
             "react 19, react-dom, react-router-dom 7, @tanstack/react-query 5; dev: vite 8, typescript, tailwindcss 4, eslint, vitest, testing-library"],
        ],
        [1, 1],
    )


def section_run() -> None:
    h1("4. Step-by-step: installing and running")
    h2("4.1 With Docker (one command)")
    numbered(
        [
            "Unzip the project and open a terminal in the `opsflow` folder.",
            "Optional: `cp .env.example .env` to change settings (defaults work locally).",
            "Start everything: `docker compose up --build` — the first build takes a few minutes.",
            "Open **http://localhost:8080** and sign in with a demo account (section 5).",
            "API documentation (Swagger UI) is at **http://localhost:8080/api/docs**.",
            "Stop with `Ctrl+C`; remove everything including the database with `docker compose down -v`.",
        ]
    )
    p(
        "What happens on start: the database starts; the **backend** container waits for it, runs the migrations "
        "(`alembic upgrade head`), seeds demo data, and starts the API; the **worker** starts once the backend is "
        "healthy; **nginx** serves the web app and forwards `/api` to the backend."
    )
    h2("4.2 With single sign-on (development identity provider)")
    code("docker compose --env-file .env.sso.example --profile sso up --build")
    p(
        "The login page now shows **Continue with Dev SSO**. The development provider (port 9000) only asks for an "
        "email — it stands in for Okta / Microsoft Entra ID / Google Workspace and must never be deployed. Type a "
        "seeded address such as `daniel@opsflow.dev`; unknown emails are refused (unless auto-provisioning is enabled)."
    )
    h2("4.3 Without Docker (local development)")
    p("**Step 1 — create the database role and databases** (once; adjust `-U`/`-h` for your superuser):")
    code(
        """psql -h localhost -U postgres -c "CREATE USER opsflow WITH PASSWORD 'opsflow' CREATEDB;"
psql -h localhost -U postgres -c "CREATE DATABASE opsflow OWNER opsflow;"
psql -h localhost -U postgres -c "CREATE DATABASE opsflow_test OWNER opsflow;\""""
    )
    p("**Step 2 — backend API** (terminal 1):")
    code(
        """cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\\Scripts\\activate
pip install -e ".[dev]"
alembic upgrade head               # create / upgrade the schema
python -m app.seed --reset         # demo data (development only)
uvicorn app.main:app --reload --port 8000"""
    )
    p("**Step 3 — notification worker** (terminal 2):")
    code("cd backend && source .venv/bin/activate && python -m app.worker")
    p("**Step 4 — web app** (terminal 3):")
    code("cd frontend\nnpm ci\nnpm run dev                        # http://localhost:5173 (proxies /api to :8000)")
    p("**Step 5 — check the installation:**")
    code("cd backend && python scripts/verify_setup.py")
    p("It verifies the database connection, migrations, the audit trigger, seed data, the API health endpoint and a demo login.")
    p("**Optional step 6 — SSO locally:** run `python -m app.devtools.dev_oidc_provider` (port 9000) and start the API with:")
    code(
        """OPSFLOW_OIDC_ISSUER=http://localhost:9000 OPSFLOW_OIDC_CLIENT_ID=opsflow-dev \\
OPSFLOW_OIDC_CLIENT_SECRET=opsflow-dev-secret \\
OPSFLOW_OIDC_REDIRECT_URI=http://localhost:5173/api/v1/auth/oidc/callback \\
uvicorn app.main:app --reload --port 8000"""
    )
    callout("A `Makefile` wraps the same commands: `make install migrate seed api worker web idp test lint verify`.")
    h2("4.4 Running the tests")
    code(
        """cd backend && pytest                    # 146 tests, needs the opsflow_test database
cd frontend && npm test                  # frontend unit tests
cd backend && ruff check app tests scripts && mypy app
cd frontend && npm run typecheck && npm run lint
# end-to-end (stack running on a fresh seed, worker running):
cd frontend && npm i -D playwright && npx playwright install chromium
BASE_URL=http://localhost:5173 npm run e2e"""
    )
    callout(
        "**Warning:** the backend tests empty every table of the test database between tests. Never point "
        "`OPSFLOW_TEST_DATABASE_URL` at a database with real data.",
        "warn",
    )
    h2("4.5 Scale check (optional)")
    code("cd backend && python -m app.seed --bulk 50000 && python scripts/benchmark.py")


def section_first_steps() -> None:
    h1("5. Demo accounts and a 15-minute tour")
    p("All demo users share the password **opsflow-demo**. They exist only in the development seed script.")
    table(
        [
            ["Email", "Role"],
            ["admin@opsflow.dev", "Administrator (all teams, users, security log)"],
            ["priya@opsflow.dev", "Manager of Payments; member of Customer Support"],
            ["lena@opsflow.dev", "Manager of Customer Support; member of Compliance"],
            ["grace@opsflow.dev", "Manager of Platform Engineering"],
            ["ravi@opsflow.dev", "Manager of Compliance; member of Payments"],
            ["omar@opsflow.dev", "Member of Payments and Customer Support"],
            ["daniel@opsflow.dev", "Member of Payments and Platform Engineering"],
            ["tom@opsflow.dev", "Member of Platform Engineering"],
        ],
        [1, 2],
    )
    h2("Guided tour")
    numbered(
        [
            "**Dashboard** — sign in as Priya. Click “Overdue” to jump to a filtered list; search “refund”; type `PAY-3` to jump to an item by key.",
            "**Claim race** — open a private window as Omar. Both open the same unassigned Payments item and click **Claim**: one wins, the other is told who owns it.",
            "**Stale edit** — Priya clicks **Edit** and changes the title; Omar changes the priority and saves; Priya saves → a side-by-side comparison appears with “Apply my changes on top of latest”.",
            "**Workflow** — Omar: Start work → Mark blocked (a reason is required) → Unblock → Resolve. Omar has no approve button; Priya approves → Closed. Open the **History** tab.",
            "**Access control** — as Omar, open a Platform Engineering item URL → “Work item not found” (the server returns 404).",
            "**User administration** — as admin: Admin → New user → copy the one-time password. Sign in as the new user: only “Choose a new password” is reachable. Change it.",
            "**Sessions** — sign that user in on a second browser; Account → “Sign out other devices”; the second browser is logged out on its next click. Admin → Deactivate → the user is thrown out immediately.",
            "**Rate limiting** — five wrong passwords for tom@opsflow.dev; the sixth attempt (even correct) is refused for 15 minutes.",
            "**Single sign-on** — with the `sso` profile: Continue with Dev SSO → `daniel@opsflow.dev`.",
            "**Security log** — Admin → Security log shows every sign-in, lockout, password change and deactivation.",
        ]
    )
    shot("05-conflict.png", "Figure 2 — A stale edit is refused; the user compares versions and decides.", 0.92)
    shot("06-history.png", "Figure 3 — The immutable history: who changed what, when, from what to what, and why.", 0.92)
    shot("12-admin-users.png", "Figure 4 — User administration.", 0.92)
    shot("11-admin-temp-password.png", "Figure 5 — A temporary password is shown exactly once.", 0.8)
    shot("13-forced-password-change.png", "Figure 6 — Temporary passwords must be replaced before anything else works.", 0.8)
    shot("14-account-sessions.png", "Figure 7 — Where you are signed in; revoke any session instantly.", 0.9)
    shot("15-security-log.png", "Figure 8 — Append-only security log.", 0.92)
    shot("16-rate-limited.png", "Figure 9 — Sign-in paused after repeated failures; SSO button above.", 0.7)
    shot("17-sso-provider.png", "Figure 10 — The bundled development identity provider (demo only).", 0.7)


def section_config() -> None:
    h1("6. Configuration reference")
    p("All settings are environment variables with the prefix `OPSFLOW_` (see `.env.example`).")
    table(
        [
            ["Variable", "Default", "Purpose"],
            ["DATABASE_URL", "postgresql+psycopg://opsflow:opsflow@localhost:5432/opsflow", "Database connection"],
            ["ENV", "development", "`production` refuses the default secret and refuses to seed"],
            ["JWT_SECRET", "dev placeholder", "Signs session tokens — **must** be set to a long random value in production"],
            ["ACCESS_TOKEN_TTL_MINUTES", "480", "Session lifetime (8 hours)"],
            ["COOKIE_SECURE", "false", "`true` when served over HTTPS"],
            ["TRUST_PROXY_HEADERS", "false (true in compose)", "Use X-Forwarded-For from your own proxy for rate limiting"],
            ["LOGIN_MAX_FAILURES_PER_ACCOUNT / _PER_IP", "5 / 30", "Failures allowed per window before a lockout"],
            ["LOGIN_FAILURE_WINDOW_MINUTES / LOGIN_LOCKOUT_MINUTES", "15 / 15", "Window and lock length"],
            ["PASSWORD_MIN_LENGTH", "10", "Password policy"],
            ["PASSWORD_LOGIN_ENABLED", "true", "`false` = SSO-only (admins keep password sign-in)"],
            ["OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET", "unset", "Enable single sign-on"],
            ["OIDC_REDIRECT_URI", "http://localhost:5173/api/v1/auth/oidc/callback", "Must match the IdP registration"],
            ["OIDC_DISCOVERY_URL", "unset", "Internal discovery URL if the backend reaches the IdP differently"],
            ["OIDC_AUTO_PROVISION / OIDC_ALLOWED_DOMAINS", "false / []", "Create accounts on first SSO sign-in for listed domains"],
            ["IDEMPOTENCY_TTL_HOURS", "24", "How long retry keys are remembered"],
            ["OUTBOX_MAX_ATTEMPTS / WORKER_POLL_SECONDS", "5 / 2", "Notification retries and polling"],
            ["DB_POOL_SIZE / DB_MAX_OVERFLOW / DB_DISABLE_JIT", "10 / 20 / true", "Connection pool; PostgreSQL JIT off for short queries"],
        ],
        [1.6, 1.4, 2],
    )


def section_approach() -> None:
    h1("7. The approach — how the problem was solved")
    h2("7.1 From brief to engineering problem")
    p(
        "The brief is deliberately open. The first step was to turn it into **actors** (members, managers, "
        "reporters, assignees, administrators), **resources** (teams, memberships, work items, history, comments), "
        "**operations** and — most importantly — **invariants that must always hold**:"
    )
    table(
        [
            ["#", "Invariant", "Enforced by"],
            ["I1", "At most one assignee, who is an active member of the item’s team", "service + membership row lock"],
            ["I2", "In progress / blocked / awaiting approval ⇒ an assignee exists", "service + database CHECK"],
            ["I3", "Status changes follow the workflow graph; closed and cancelled are final", "pure state machine"],
            ["I4", "Every change produces exactly one history record, atomically", "single transaction"],
            ["I5", "History and security records are never modified or deleted", "database triggers"],
            ["I6", "A write based on stale data is refused", "version check (409)"],
            ["I7", "Repeating a request with the same key has the effect of one request", "idempotency table + unique index"],
            ["I8", "Item keys (PAY-42) never repeat", "unique constraint + atomic counter"],
            ["I9", "Teams keep a manager; nobody who owns active work is removed; ≥1 active admin", "row locks + checks"],
        ],
        [0.3, 3, 1.6],
    )
    p(
        "Five situations were identified where simple create/read/update/delete is not enough — each is implemented "
        "on the server and has tests: **(A)** two users claim the same item; **(B)** two users edit from different "
        "versions; **(C)** a user repeats a request after a timeout; **(D)** a user attempts an action on a resource "
        "they may not touch; **(E)** a status change that breaks the workflow — plus **(F)** notification delivery "
        "that fails, repeats or is delayed."
    )
    h2("7.2 Architecture")
    story.append(architecture_diagram())
    story.append(Paragraph("Figure 11 — Components and request flow.", S["caption"]))
    p(
        "A **modular monolith**: one API process with strict layers — HTTP routes → services (use cases) → domain "
        "rules and authorization policy → database models — and a separate **worker** process for notifications. "
        "All the critical rules span several tables and are trivially atomic inside one PostgreSQL transaction; "
        "splitting services would have turned each into a distributed-consistency problem with no benefit at this scale."
    )
    h2("7.3 Workflow")
    story.append(workflow_diagram())
    story.append(Paragraph("Figure 12 — Work item lifecycle.", S["caption"]))
    table(
        [
            ["From → To", "Who may do it", "Reason"],
            ["open → in progress", "assignee or manager (item must be assigned)", "—"],
            ["open → cancelled", "reporter or manager", "required"],
            ["in progress → blocked", "assignee or manager", "required"],
            ["in progress → awaiting approval (resolved)", "assignee or manager", "—"],
            ["in progress → open", "assignee or manager", "—"],
            ["blocked → in progress", "assignee or manager", "—"],
            ["blocked → cancelled", "manager", "required"],
            ["resolved → closed (approval)", "manager only", "—"],
            ["resolved → in progress (reject)", "reporter or manager", "required"],
        ],
        [1.6, 2, 0.6],
    )
    h2("7.4 The critical mechanisms")
    table(
        [
            ["Situation", "Mechanism", "Result"],
            ["Simultaneous claims", "One atomic statement: `UPDATE … SET assignee = me WHERE id = … AND assignee IS NULL RETURNING version`", "Exactly one winner; others get 409 “already claimed”; one history entry"],
            ["Stale edits", "Row lock (`SELECT … FOR UPDATE`) + compare the client’s `version`; success increments it", "409 with the current item; UI shows both versions and lets the user re-apply"],
            ["Retries / double clicks", "`Idempotency-Key` stored in the same transaction as the change; unique per user", "Retry replays the first response; same key with a different body → 422"],
            ["History integrity", "History row written in the same transaction; trigger blocks UPDATE/DELETE", "No change without its record; records immutable"],
            ["Notifications", "Transactional outbox + worker with `FOR UPDATE SKIP LOCKED`, retries with back-off, dead letter", "Events exist iff the change committed; no duplicates; failures visible to admins"],
            ["Membership vs assignment", "Assignee’s membership read `FOR SHARE`; removal deletes first, then checks", "Nobody can be assigned while being removed"],
        ],
        [1, 2.2, 1.8],
    )
    h2("7.5 Identity and access")
    bullets(
        [
            "**Server-side sessions.** A signed token names a session row; every request checks the row is active. Logout, "
            "“sign out other devices”, admin revocation, deactivation and password resets therefore take effect on the next request, on every server.",
            "**Rate limiting in PostgreSQL.** Two counters per attempt — per account (5 failures / 15 min) and per client "
            "address (30 / 15 min) — updated by one atomic upsert, so it is correct with many API replicas. Counters exist "
            "for any email, so a lockout never reveals whether an account exists.",
            "**Single sign-on (OIDC).** Authorization-code flow with PKCE; `state` in a signed short-lived cookie; `nonce` "
            "checked; ID-token signature verified against the provider’s keys, plus issuer, audience, expiry and verified email. "
            "Identities link to accounts by provider subject or verified email; optional domain-restricted auto-provisioning.",
            "**Administration.** Admins create users with a one-time temporary password; the API refuses everything except "
            "account endpoints until it is changed. Admins cannot lock themselves out and there is always an active admin.",
            "**Audit.** Every identity event is written to an append-only security log.",
        ]
    )
    h2("7.6 Search, pagination and the dashboard")
    p(
        "Search uses PostgreSQL full-text search (a generated, weighted `tsvector` with a GIN index) plus trigram "
        "substring matching on titles and exact key lookup. Lists use **keyset (cursor) pagination**, so page 40 costs "
        "the same as page 1 and items moving between pages are never skipped or repeated. The dashboard is one SQL "
        "aggregate (`COUNT(*) FILTER (…)`) — the browser never downloads the dataset."
    )
    h2("7.7 Frontend")
    p(
        "React + TypeScript with TanStack Query as the only store of server data. The UI **never assumes a click "
        "succeeded**: it shows the server’s response, refreshes dependent views, and handles 409/422/429 explicitly. "
        "Filters live in the URL. Idempotent actions are retried automatically with the same key if the network fails."
    )


def section_testing() -> None:
    h1("8. Testing and results")
    p(
        "Testing is **risk-based**: the tests target behaviours that would corrupt data, leak data or mislead "
        "decisions. Integration tests use a real PostgreSQL database created by the real migrations, because the "
        "protections under test (row locks, unique-index waits, SKIP LOCKED, constraints, triggers) are database behaviour."
    )
    table(
        [
            ["Suite", "Executed", "Passed", "Failed"],
            ["Backend unit (workflow state machine)", "24", "24", "0"],
            ["Backend integration (work items, concurrency, idempotency, authorization, audit, outbox)", "74", "74", "0"],
            ["Backend identity and SSO (sessions, throttling, admin, OIDC flow and token forgery)", "48", "48", "0"],
            ["Frontend unit (API client)", "4", "4", "0"],
            ["End-to-end, multi-browser (dev server and production build behind nginx)", "32 checks", "32", "0"],
            ["Static checks (ruff, mypy, TypeScript strict, ESLint)", "—", "clean", "—"],
        ],
        [3.2, 0.8, 0.7, 0.7],
    )
    p(
        "Concurrency is tested two ways: **races** (up to 8 threads released together, repeated) and **deterministic "
        "interleavings** (one database session holds a lock, the second is asserted to be blocked, then the first commits)."
    )
    h2("Performance (50,000 work items, single client)")
    table(
        [
            ["Request", "Median", "p95"],
            ["List, first page / page 41 (keyset)", "11.7 / 13.5 ms", "13.5 / 17.5 ms"],
            ["Full-text search “refund” (4,169 hits)", "15.7 ms", "20.5 ms"],
            ["Filter by status × priority", "18.5 ms", "25.1 ms"],
            ["Dashboard summary", "26.6 ms", "34.4 ms"],
        ],
        [2.5, 1, 1],
    )
    p(
        "A finding during measurement: PostgreSQL’s JIT compiler added up to 87 ms to short queries; it is now disabled "
        "for application connections, which brought the teams list from 126 ms to 11 ms."
    )


def section_decisions() -> None:
    h1("9. Key engineering decisions (summary)")
    table(
        [
            ["Decision", "Chosen", "Main alternative", "Why"],
            ["Architecture", "Modular monolith + worker", "Microservices", "Invariants span tables; one transaction is simplest and correct"],
            ["Invariants", "In the database as well as code", "Code only", "Constraints and triggers protect against future bugs"],
            ["Authorization", "Admin + team role + reporter/assignee, one policy module", "Generic ACLs", "Small, explainable, fully tested; 404 hides other teams’ items"],
            ["Concurrency", "Atomic claim; row lock + version for edits", "Last write wins / SERIALIZABLE", "Precise, explainable 409s; no lost updates"],
            ["Retries", "Idempotency keys in the same transaction", "Redis cache / de-dup by content", "Key and effect commit together"],
            ["Async", "Transactional outbox", "Celery + broker", "No extra infrastructure; events iff commit"],
            ["Identity", "DB sessions, PG throttling, OIDC", "Stateless JWT, Redis, SAML/SaaS", "Instant revocation, correct across replicas, works with any IdP"],
            ["AI / RAG", "Not built; PostgreSQL FTS", "Vector DB (Milvus, Weaviate, Pinecone)", "Needs are lexical and permission-sensitive; FTS is current and ACL-correct"],
        ],
        [0.9, 1.3, 1.1, 1.8],
    )


def section_structure() -> None:
    h1("10. Project structure")
    code(
        """opsflow/
├── README.md · ENGINEERING_DECISIONS.md · OpsFlow_Guide.pdf
├── docker-compose.yml · Makefile · .env.example · .env.sso.example
├── backend/
│   ├── app/
│   │   ├── main.py            FastAPI app, middleware, error handling, security headers
│   │   ├── core/              settings, security (bcrypt, tokens, password policy), errors
│   │   ├── models/ schemas/   database models, request/response contracts
│   │   ├── domain/workflow.py the state machine (pure, unit-tested)
│   │   ├── auth/              authentication dependency + authorization policy
│   │   ├── services/          work items, teams, dashboard, idempotency, outbox, pagination,
│   │   │                      auth, sessions, throttle, users, oidc, security_log
│   │   ├── api/v1/            HTTP routes
│   │   ├── devtools/          development-only OIDC identity provider
│   │   ├── worker.py          notification worker
│   │   └── seed.py            demo data + bulk generator
│   ├── alembic/versions/      0001 work management schema, 0002 identity & security
│   ├── scripts/               verify_setup.py, benchmark.py
│   └── tests/                 unit/ and integration/ (real PostgreSQL)
├── frontend/
│   ├── src/lib/api.ts         the only HTTP code: errors, CSRF header, idempotent retries
│   ├── src/features/          auth, account, admin, dashboard, work-items, teams, notifications
│   ├── e2e/smoke.mjs          multi-browser end-to-end test
│   └── nginx.conf · Dockerfile
└── docs/                      problem formulation, architecture, API, database, testing,
                               deployment, limitations, final report, diagrams, screenshots"""
    )


def section_api() -> None:
    h1("11. API quick reference")
    p("Base path `/api/v1`. Interactive documentation at `/api/docs`. Errors always look like "
      "`{\"error\": {\"code\", \"message\", \"details\"}, \"request_id\"}`.")
    table(
        [
            ["Area", "Endpoints"],
            ["Auth", "GET /auth/config · POST /auth/login · POST /auth/token · POST /auth/logout · GET /auth/me · GET /auth/oidc/login · GET /auth/oidc/callback"],
            ["Account", "GET /account/sessions · DELETE /account/sessions/{id} · POST /account/sessions/revoke-others · POST /account/password"],
            ["Work items", "GET, POST /work-items · GET, PATCH /work-items/{id} · POST …/claim · …/assign · …/transitions · GET …/activity · GET, POST …/comments · PATCH /comments/{id}"],
            ["Teams", "GET, POST /teams · GET /teams/{id} · GET /teams/{id}/members · PUT, DELETE /teams/{id}/members/{user_id}"],
            ["Other", "GET /dashboard/summary · GET /notifications · POST /notifications/{id}/read · POST /notifications/read-all · GET /users"],
            ["Admin", "GET, POST /admin/users · GET, PATCH /admin/users/{id} · POST …/reset-password · POST …/revoke-sessions · GET /admin/security-events · GET /admin/outbox"],
        ],
        [0.8, 4],
    )
    table(
        [
            ["Status", "Meaning (examples of codes)"],
            ["401", "not signed in / session expired or revoked / wrong credentials"],
            ["403", "not allowed (`forbidden`), missing CSRF header, `password_change_required`"],
            ["404", "does not exist **or** belongs to a team you are not in"],
            ["409", "`version_conflict`, `already_claimed`, `invalid_transition`, `last_manager`, `cannot_modify_self`"],
            ["422", "validation, `reason_required`, `weak_password`, `idempotency_key_reused`"],
            ["429", "`too_many_attempts` with `Retry-After`"],
        ],
        [0.5, 4],
    )


def section_troubleshooting() -> None:
    h1("12. Troubleshooting")
    table(
        [
            ["Symptom", "What to do"],
            ["Port already in use", "Stop the other service or change the port mapping in docker-compose.yml"],
            ["pg_trgm extension not available", "Install your distribution’s PostgreSQL contrib package"],
            ["Tests cannot connect", "Create the `opsflow_test` database or set `OPSFLOW_TEST_DATABASE_URL`"],
            ["Every write returns 403 csrf_failed", "Scripts must use a bearer token (`POST /auth/token`) or send `X-Requested-With: opsflow`"],
            ["“Too many failed attempts” while testing", "Wait 15 minutes or clear: `DELETE FROM auth_throttle`"],
            ["Everyone shares one rate-limit bucket", "Behind your own proxy set `OPSFLOW_TRUST_PROXY_HEADERS=true`"],
            ["No SSO button", "Set both `OPSFLOW_OIDC_ISSUER` and `OPSFLOW_OIDC_CLIENT_ID`; check `GET /api/v1/auth/config`"],
            ["SSO: “no OpsFlow account”", "Create the user first, or enable auto-provisioning for your domain"],
            ["Notifications never appear", "Start the worker (`python -m app.worker`); check Admin → System"],
            ["New user stuck on “Choose a new password”", "Expected: temporary passwords must be changed first"],
        ],
        [1.4, 2.6],
    )


def section_limits() -> None:
    h1("13. Known limitations and next steps")
    bullets(
        [
            "Docker images were not built in the development sandbox (no registry access); the compose file validates "
            "and the same entrypoint and nginx configuration were exercised natively. Not deployed to any cloud.",
            "No multi-factor authentication inside OpsFlow — enforce MFA at the identity provider. No SCIM provisioning "
            "and no mapping of identity-provider groups to teams.",
            "No email delivery (invitations, password resets, notifications are in-app only).",
            "Pages poll for changes (every 15–30 s) instead of live push; correctness does not depend on it.",
            "Fixed-window rate limiting (no CAPTCHA); a very distributed attack is slowed, not stopped.",
            "No reopening of closed items, attachments, SLA timers or per-team custom workflows.",
            "No CI pipeline or metrics dashboards yet.",
        ]
    )
    p("**Next steps:** CI, live updates via server-sent events fed by the outbox, email/Slack notifications, SLA "
      "timers and escalation, a real identity-provider integration with MFA and SCIM, and optional “similar items” "
      "duplicate detection using pgvector.")


def section_qa() -> None:
    h1("14. Questions and answers")
    h2("General")
    qa("What problem does OpsFlow solve in one sentence?",
       "It gives every operational request one owner, a trustworthy state, an approval step and an explainable "
       "history, and keeps that record correct even when many people act at once.")
    qa("Is this a ticketing system like Jira or ServiceNow?",
       "It is in the same family but deliberately smaller: it focuses on ownership, approval and auditability for "
       "internal operations, and on behaving correctly under concurrency — the parts the challenge emphasised.")
    qa("Who is it for?",
       "Operations, support, payments, engineering and compliance teams inside one company, plus their managers and administrators.")
    qa("Can I use it for my own team today?",
       "For a pilot, yes (with Docker, SSO and backups). Before production use see section 13 — mainly CI, "
       "monitoring, email delivery and MFA at your identity provider.")
    h2("Installation and running")
    qa("What is the fastest way to run it?", "`docker compose up --build`, then open http://localhost:8080 and sign in as `priya@opsflow.dev` / `opsflow-demo`.")
    qa("Do I need to install PostgreSQL?", "Not with Docker — it runs in a container. For local development you need PostgreSQL 14+.")
    qa("Why are there two databases (opsflow and opsflow_test)?",
       "The tests empty every table between tests, so they must run against a separate, disposable database.")
    qa("How do I reset the demo data?", "`python -m app.seed --reset` (local) or `docker compose run --rm backend seed --reset`.")
    qa("How do I know the installation is correct?", "Run `python scripts/verify_setup.py`; all six checks should report OK.")
    qa("The first Docker build is slow — is that normal?", "Yes; it downloads base images and installs Python and Node dependencies once. Later starts take seconds.")
    qa("How do I stop it and delete everything?", "`docker compose down -v` (the `-v` removes the database volume).")
    h2("Using the application")
    qa("What is the difference between “claim” and “assign”?",
       "Claim means “I take this unowned item”; any team member can do it and exactly one simultaneous claimer wins. "
       "Assign means a manager gives the item to someone (or reassigns it).")
    qa("Why can’t I approve my own work?",
       "Approval (`Awaiting approval → Closed`) is manager-only, so the person who did the work cannot also sign it off (unless they are a manager).")
    qa("Why am I asked for a reason?", "Blocking, cancelling and rejecting need a reason so that later readers know why; it is stored in the history.")
    qa("Why did my save fail with “changed by someone else”?",
       "Someone saved a newer version while you were editing. Your text is kept: compare both versions, then apply your changes on top or discard them.")
    qa("Why can’t I see an item a colleague sent me?", "It belongs to a team you are not in. For privacy the server answers “not found”.")
    qa("How do I sign out a lost phone or laptop?", "Account → “Where you’re signed in” → Revoke (or “Sign out other devices”). It stops working immediately.")
    qa("Why do notifications arrive a second or two later?", "They are delivered by a background worker so that your action never waits for, or fails because of, notification delivery.")
    h2("Architecture and design")
    qa("Why a monolith and not microservices?",
       "The important rules span several tables (item, history, membership, retry keys). In one database transaction "
       "they are simple and correct; across services they would need sagas and compensation, with no benefit at thousands of users.")
    qa("Why PostgreSQL?",
       "Row locks with safe re-checks, CHECK constraints, triggers, partial and GIN indexes, built-in full-text search, "
       "JSONB and `SKIP LOCKED` — every critical behaviour relies on these.")
    qa("Why is there no repository layer?", "SQLAlchemy’s session already provides the unit-of-work and identity map; a pass-through layer would add code without adding a useful seam.")
    qa("Why Vite + React instead of Next.js?", "It is an authenticated internal tool with no SEO or server-rendering need; static files behind nginx are simpler to run.")
    qa("How does the frontend avoid showing wrong data?",
       "Server data lives only in the TanStack Query cache; after every action the cache is replaced with the server’s response and dependent views refresh. Nothing is shown as done until the server confirms it.")
    h2("Concurrency and consistency")
    qa("What exactly happens when two people click Claim at the same moment?",
       "Both requests run `UPDATE … WHERE assignee IS NULL`. PostgreSQL locks the row; the second waits, then re-checks "
       "the condition against the committed row, finds it assigned, and updates nothing. The API returns 409 with the current owner.")
    qa("Why do edits use a version number and a row lock?",
       "The lock orders concurrent writers and gives exact before/after values for the history; the version detects that "
       "the person decided based on stale information — which a lock alone cannot know.")
    qa("Why not use SERIALIZABLE isolation for everything?",
       "It would be correct but surfaces as generic serialization failures that need retry loops; explicit locks give precise, explainable conflicts.")
    qa("What is an idempotency key and why store it in the same transaction?",
       "A client-generated ID for an action. If a retry arrives with the same key, the stored response is returned instead "
       "of repeating the action. Storing it with the change means the key and the effect always commit (or roll back) together.")
    qa("What if the notification worker is down?", "Actions still succeed; events wait in the outbox and are delivered when it returns. Admins see the backlog under Admin → System.")
    qa("What if an event is processed twice?", "A unique constraint on (event, recipient) makes the second delivery a no-op.")
    h2("Security, identity and SSO")
    qa("How are passwords stored?", "As bcrypt hashes (cost 12). Admin-issued passwords are temporary and must be replaced at first sign-in.")
    qa("Why server-side sessions instead of plain JWTs?",
       "A plain JWT cannot be recalled before it expires. With a session row behind every token, logout, deactivation and password resets end access on the very next request.")
    qa("How does rate limiting work with several API servers?",
       "Counters are in PostgreSQL and updated with one atomic statement, so every server sees the same counts. No extra component such as Redis is needed.")
    qa("Can an attacker find out which emails have accounts?",
       "No: unknown and known emails get the same error, the same bcrypt timing, and the same lockout behaviour.")
    qa("Which identity providers work with the SSO?",
       "Any standards-compliant OpenID Connect provider — Okta, Microsoft Entra ID, Google Workspace, Auth0, Keycloak. Configure the issuer, client ID/secret and redirect URI.")
    qa("What does the SSO implementation protect against?",
       "`state` blocks forged callbacks (login CSRF), `nonce` blocks token replay, PKCE protects the authorization code, "
       "signature/issuer/audience/expiry checks block forged or misdirected tokens, the verified-email check blocks account "
       "takeover, and redirects are limited to same-site paths.")
    qa("Does SSO create accounts automatically?", "Only if `OPSFLOW_OIDC_AUTO_PROVISION=true`, and optionally only for listed domains. New accounts have no team access until a manager adds them.")
    qa("Is the bundled identity provider safe to deploy?", "No. It signs in anyone who types an email. It exists only for demos and tests and refuses to start in production mode.")
    qa("What stops an admin from locking everyone out?", "Admins cannot deactivate or demote themselves, and the system always keeps at least one active administrator.")
    qa("What is recorded in the security log?", "Sign-ins, failed sign-ins, lockouts, sign-outs, session revocations, password changes and resets, account creation and changes, admin grants, team creation and SSO events — with actor, subject, IP and time. It cannot be edited or deleted.")
    h2("Performance and scaling")
    qa("How large can it get?", "It was measured at 50,000 items with typical requests at 10–35 ms. The design targets thousands of users and tens of thousands of active items.")
    qa("Why keyset pagination?", "Offset pagination slows down with depth and skips or repeats rows when items change between pages; keyset pagination does neither.")
    qa("What would you change at ten times the load?", "More API replicas behind a connection pooler (PgBouncer), a read replica for lists and the dashboard, cached dashboard counts, partitioned history tables, and live updates via server-sent events.")
    h2("Testing")
    qa("Why test against a real database instead of SQLite?", "The protections being tested — row locks, unique-index waits, SKIP LOCKED, constraints, triggers — are PostgreSQL behaviour; SQLite would pass for the wrong reasons.")
    qa("How do you test race conditions reliably?", "Two ways: many threads released at once (repeated several times), and deterministic interleavings where one session holds a lock and the test asserts the second is blocked.")
    qa("What is not tested?", "A real corporate identity provider, sustained concurrent load, the Docker image build in this environment, and an accessibility audit.")
    h2("AI and the job description")
    qa("Why is there no AI, RAG or vector database?",
       "The search need is lexical and permission-sensitive (words, IDs, keys). PostgreSQL full-text search answers it in ~15 ms "
       "with the same permission filter in the same query. A vector database would add eventual consistency and a second place to enforce permissions.")
    qa("Where would AI fit later?", "As an optional “similar items” panel to catch duplicates, using pgvector in the same database, populated by an outbox handler and evaluated against the current search before shipping.")


def section_glossary() -> None:
    h1("15. Glossary")
    table(
        [
            ["Term", "Meaning"],
            ["Work item", "A request that needs investigation, action or approval"],
            ["Claim", "Taking ownership of an unassigned item yourself"],
            ["Optimistic concurrency", "Detecting conflicting edits by comparing version numbers instead of locking while a person thinks"],
            ["Idempotency key", "A client-chosen ID that makes retrying a request safe"],
            ["Transactional outbox", "Writing “events to deliver” in the same transaction as the change; a worker delivers them later"],
            ["Keyset pagination", "Paging by “items after this one” instead of “skip N”"],
            ["OIDC", "OpenID Connect — the standard single sign-on protocol built on OAuth 2.0"],
            ["PKCE", "Proof Key for Code Exchange — protects the sign-in code from interception"],
            ["Session revocation", "Ending a sign-in on the server so its token stops working immediately"],
            ["Append-only", "Rows can be added but never changed or deleted"],
        ],
        [1, 3],
    )


def build() -> None:
    cover()
    contents()
    section_what()
    section_why()
    section_requirements()
    section_run()
    section_first_steps()
    section_config()
    section_approach()
    section_testing()
    section_decisions()
    section_structure()
    section_api()
    section_troubleshooting()
    section_limits()
    section_qa()
    section_glossary()
    doc = GuideDoc(str(OUT))
    doc.multiBuild(story)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
