"""Printable / emailable PDF copy of an AbelOrder purchase request, including the signature block."""

import re

from fpdf import FPDF

from .abelorder import money, qty_text, unit_money, upload_root
from .config import settings
from .models import PurchaseRequest, StepState, utcnow

_REPLACE = {"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
            "…": "...", "•": "*", " ": " ", "→": "->", "€": "EUR"}


def _t(text) -> str:
    """Core PDF fonts are Latin-1 only; map common symbols and replace the rest."""
    s = "" if text is None else str(text)
    for a, b in _REPLACE.items():
        s = s.replace(a, b)
    return s.encode("latin-1", "replace").decode("latin-1")


class _Doc(FPDF):
    def __init__(self, pr: PurchaseRequest):
        super().__init__(format="Letter")
        self.pr = pr
        self.set_auto_page_break(True, margin=18)
        self.set_margins(15, 15, 15)

    def header(self):
        self.set_font("Helvetica", "B", 16)
        self.cell(0, 8, "AbelOrder - Purchase Request", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 10)
        status = self.pr.status.label + (f"   |   PO {self.pr.po_number}" if self.pr.po_number else "")
        self.cell(0, 6, _t(f"{self.pr.number}   |   {status}"), new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(31, 78, 121)
        self.set_line_width(0.6)
        self.line(15, self.get_y() + 1, self.w - 15, self.get_y() + 1)
        self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(110)
        self.cell(0, 5, _t(f"Generated {utcnow():%Y-%m-%d %H:%M} UTC by {settings.app_name}   -   page "
                           f"{self.page_no()}/{{nb}}"), align="C")
        self.set_text_color(0)

    def section(self, title: str):
        self.ln(2)
        self.set_font("Helvetica", "B", 11)
        self.set_fill_color(235, 242, 250)
        self.cell(0, 7, _t(title), fill=True, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def field(self, label: str, value, width: float = 0):
        self.set_font("Helvetica", "B", 9)
        self.cell(42, 6, _t(label))
        self.set_font("Helvetica", "", 9)
        self.multi_cell(width or 0, 6, _t(value or "-"), align="L", new_x="LMARGIN", new_y="NEXT")


def render(pr: PurchaseRequest) -> bytes:
    pdf = _Doc(pr)
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.section("Request")
    pdf.field("Requester", pr.requester_name + (f"  ({pr.requester.email})" if pr.requester else ""))
    pdf.field("Department", pr.department.name if pr.department else "-")
    pdf.field("Date of request", pr.request_date.isoformat())
    pdf.field("Urgency", pr.urgency.label + (f" - needed by {pr.needed_by.isoformat()}" if pr.needed_by else ""))
    pdf.field("Project / Work order", pr.project_number)
    pdf.field("PO number", pr.po_number or "(issued by Procurement after approval)")

    pdf.section("Vendor")
    pdf.field("Vendor", pr.vendor_name + ("  (NEW VENDOR)" if pr.vendor_is_new else "  (existing vendor)"))
    pdf.field("Contact name", pr.vendor_contact_name)
    pdf.field("Contact email", pr.vendor_contact_email)

    pdf.section("Items")
    cols = [("#", 8, "C"), ("Qty", 16, "R"), ("UOM", 14, "C"), ("Description", 70, "L"),
            ("RMS / Catalog #", 30, "L"), ("Cost/UOM", 22, "R"), ("Total", 26, "R")]
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(245, 245, 245)
    for title, w, align in cols:
        pdf.cell(w, 6, title, border=1, align=align, fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", "", 8)
    for item in pr.items:
        values = [str(item.line_no), qty_text(item.quantity), item.uom, item.description,
                  item.catalog_number or "", unit_money(item.unit_cost), money(item.line_total)]
        # Row height follows the longest wrapped cell.
        lines = max(len(pdf.multi_cell(w, 5, _t(v), dry_run=True, output="LINES")) for (_, w, _), v in zip(cols, values))
        h = 5 * max(1, lines)
        if pdf.get_y() + h > pdf.page_break_trigger:
            pdf.add_page()
        x, y = pdf.get_x(), pdf.get_y()
        for (_, w, align), v in zip(cols, values):
            pdf.rect(x, y, w, h)
            pdf.set_xy(x, y)
            pdf.multi_cell(w, 5, _t(v), align=align)
            x += w
        pdf.set_xy(pdf.l_margin, y + h)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(sum(w for _, w, _ in cols) - 26, 8, "TOTAL BUDGET COST", border=1, align="R")
    pdf.cell(26, 8, money(pr.total), border=1, align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.section("Reason / justification")
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 5, _t(pr.justification), new_x="LMARGIN", new_y="NEXT")
    if pr.notes:
        pdf.section("Additional instructions / notes")
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 5, _t(pr.notes), new_x="LMARGIN", new_y="NEXT")

    if pr.attachments:
        pdf.section("Attachments (supplier quotes)")
        pdf.set_font("Helvetica", "", 9)
        for a in pr.attachments:
            pdf.cell(0, 5, _t(f"- {a.filename} ({a.size // 1024 + 1} KB)"), new_x="LMARGIN", new_y="NEXT")

    pdf.section("Approvals / signatures")
    if not pr.approvals:
        pdf.set_font("Helvetica", "I", 9)
        pdf.cell(0, 6, "Not yet submitted for approval.", new_x="LMARGIN", new_y="NEXT")
    for a in pr.approvals:
        signed = a.state == StepState.APPROVED
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(42, 6, _t(a.role.label))
        pdf.set_font("Helvetica", "", 9)
        when = f" on {a.decided_at:%Y-%m-%d %H:%M} UTC" if a.decided_at else ""
        text = f"{a.approver.full_name} <{a.approver.email}> - {a.state.label.upper()}{when}"
        if signed:
            text += "  [electronically signed]"
        pdf.multi_cell(0, 6, _t(text), align="L", new_x="LMARGIN", new_y="NEXT")
        extra = "; ".join(x for x in (a.note, a.comment) if x)
        if extra:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_x(pdf.l_margin + 42)
            pdf.multi_cell(0, 5, _t(extra), align="L", new_x="LMARGIN", new_y="NEXT")
    if pr.po_number:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 10)
        who = f" by {pr.po_issued_by.full_name}" if pr.po_issued_by else ""
        when = f" on {pr.po_issued_at:%Y-%m-%d}" if pr.po_issued_at else ""
        pdf.cell(0, 7, _t(f"PO {pr.po_number} issued{who}{when}"), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def write_pdf(pr: PurchaseRequest, stage: str) -> dict:
    """Save a PDF snapshot for emailing; returns an email attachment descriptor."""
    folder = upload_root() / "pdf"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{pr.number}-{stage}-{utcnow():%Y%m%d%H%M%S%f}.pdf"
    path.write_bytes(render(pr))
    po = re.sub(r"[^A-Za-z0-9._-]", "_", pr.po_number) if pr.po_number else ""
    suffix = f"-PO-{po}" if po else ""
    return {"path": str(path), "filename": f"{pr.number}{suffix}.pdf",
            "content_type": "application/pdf"}
