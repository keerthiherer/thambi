from __future__ import annotations

import email
import imaplib
import json
import mailbox
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from .. import safety
from ..config import DRAFT_DIR, ROOT, ensure_dirs, load, resolve
from ..memory import default as default_memory
from ..retrieval import extract_text, parse_eml
from .registry import register


@register("import_email", "Import .eml or .mbox email files into local searchable memory. Never deletes or sends.",
          {"path": "file or folder path"}, ["path"], category="ceo", read_only=False)
def import_email(path: str) -> str:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = ROOT / p
    p = p.resolve()
    if not p.exists():
        raise FileNotFoundError(str(p))
    mem = default_memory()
    files: list[Path] = []
    if p.is_dir():
        files = [f for f in p.rglob("*") if f.suffix.lower() in (".eml", ".mbox")]
    else:
        files = [p]
    imported = 0
    errors = []
    for f in files:
        try:
            if f.suffix.lower() == ".mbox":
                box = mailbox.mbox(str(f), create=False)
                for key in box.keys():
                    msg = box.get_message(key)
                    subject = str(msg.get("Subject", ""))
                    from_addr = str(msg.get("From", ""))
                    to_addr = str(msg.get("To", ""))
                    date = str(msg.get("Date", ""))
                    uid = str(msg.get("Message-ID", "")) or f"mbox:{f}:{key}"
                    if msg.is_multipart():
                        bodies = [str(part.get_content()) for part in msg.walk()
                                  if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", ""))]
                    else:
                        bodies = [str(msg.get_content())]
                    mem.add_email(uid, "import", from_addr, to_addr, subject, date, "\n".join(bodies), str(f),
                                  str(msg.get("Thread-Index", "") or subject))
                    imported += 1
            else:
                d = parse_eml(f)
                mem.add_email(d["uid"], "import", d["from"], d["to"], d["subject"], d["date"], d["body"], str(f), d["thread_id"])
                imported += 1
        except Exception as e:
            errors.append(f"{f}: {e}")
    msg = f"Imported {imported} email(s) into local memory. No email was sent, deleted, or modified."
    if errors:
        msg += "\nErrors:\n" + "\n".join(errors[:10])
    return msg


@register("search_email", "Search locally imported email by sender, subject or body.",
          {"query": "search terms", "limit": "max results"}, ["query"], category="ceo")
def search_email(query: str, limit: int | str = 20) -> str:
    rows = default_memory().search_emails(query, int(limit))
    if not rows:
        return "No matching local emails. Import .eml/.mbox first or enable read-only IMAP sync."
    out = []
    for r in rows:
        body = re.sub(r"\s+", " ", r["body"] or "")[:500]
        out.append(f"#{r['id']} | {r['date']} | {r['from_addr']} -> {r['to_addr']}\nSubject: {r['subject']}\n{body}")
    return "\n\n---\n\n".join(out)


@register("email_context", "Return full local email records for summarising or drafting a reply.",
          {"query": "search terms", "limit": "max emails"}, ["query"], category="ceo")
def email_context(query: str, limit: int | str = 5) -> str:
    rows = default_memory().search_emails(query, int(limit))
    if not rows:
        return "No matching local email."
    parts = []
    for r in rows:
        parts.append(f"[Email #{r['id']}] From: {r['from_addr']}\nTo: {r['to_addr']}\nDate: {r['date']}\nSubject: {r['subject']}\n\n{r['body'][:12000]}")
    return "\n\n===\n\n".join(parts)


@register("draft_email", "Create a local .eml draft. THAMBI never sends email; you send it yourself.",
          {"to": "recipient", "subject": "subject", "body": "body", "cc": "cc (optional)"},
          ["to", "subject", "body"], category="ceo", read_only=False)
def draft_email(to: str, subject: str, body: str, cc: str = "") -> str:
    ensure_dirs()
    msg = EmailMessage()
    msg["From"] = "THAMBI local draft"
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="thambi.local")
    msg.set_content(body)
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", subject)[:60] or "draft"
    path = DRAFT_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{name}.eml"
    path.write_bytes(msg.as_bytes())
    did = default_memory().add_draft("email", subject, to, body, str(path))
    return (f"Draft #{did} saved to {path}. It has NOT been sent. "
            f"Open it in your email client to review and send manually.")


@register("summary_of_email", "Prepare local email text for summarisation (no network).",
          {"query": "thread or sender", "limit": "number"}, ["query"], category="ceo")
def summary_of_email(query: str, limit: int | str = 8) -> str:
    return email_context(query, limit)


@register("import_calendar", "Import an .ics calendar into local memory (read-only).",
          {"path": ".ics file"}, ["path"], category="ceo", read_only=False)
def import_calendar(path: str) -> str:
    try:
        from icalendar import Calendar
    except ImportError as e:
        raise RuntimeError("icalendar is not installed; run scripts\\install.ps1") from e
    p = safety.validate_read(path)
    if p.suffix.lower() != ".ics":
        raise ValueError("Expected .ics")
    cal = Calendar.from_ical(p.read_bytes())
    mem = default_memory()
    n = 0
    for ev in cal.walk("VEVENT"):
        uid = str(ev.get("UID") or f"{p}:{n}")
        summary = str(ev.get("SUMMARY", "(untitled)"))
        dtstart = ev.get("DTSTART")
        dtend = ev.get("DTEND")
        mem.add_event(uid, summary, str(dtstart.dt) if dtstart else "", str(dtend.dt) if dtend else "",
                      str(ev.get("DESCRIPTION", "")), str(ev.get("LOCATION", "")), str(p))
        n += 1
    return f"Imported {n} calendar event(s) from {p}"


@register("list_calendar", "List locally stored calendar events.", {"limit": "max events"}, category="ceo")
def list_calendar(limit: int | str = 50) -> str:
    rows = default_memory().list_events(int(limit))
    if not rows:
        return "No local calendar events. Import an .ics or create one."
    return "\n".join(f"#{r['id']} {r['dtstart']} - {r['summary']} @ {r['location']}" for r in rows)


@register("create_event", "Create a local .ics event after confirmation.",
          {"summary": "event title", "start": "ISO start e.g. 2026-11-02T10:00:00",
           "end": "ISO end", "description": "details", "location": "place"},
          ["summary", "start"], category="ceo", dangerous=True, read_only=False)
def create_event(summary: str, start: str, end: str = "", description: str = "", location: str = "") -> str:
    try:
        from icalendar import Calendar, Event
    except ImportError as e:
        raise RuntimeError("icalendar is not installed") from e
    if not safety.require_confirmation(
        "Create calendar event?",
        f"{summary}\n{start} - {end or '(no end)'}\n{location}",
        action="create_event",
    ):
        raise PermissionError("Event creation denied by user")
    cal_dir = default_memory().path.parent / "calendar"
    cal_dir.mkdir(parents=True, exist_ok=True)
    cal = Calendar()
    cal.add("prodid", "-//THAMBI//Local Calendar//EN")
    cal.add("version", "2.0")
    ev = Event()
    ev.add("summary", summary)
    ev.add("dtstart", datetime.fromisoformat(start))
    if end:
        ev.add("dtend", datetime.fromisoformat(end))
    ev.add("description", description)
    ev.add("location", location)
    uid = f"{uuid.uuid4()}@thambi.local"
    ev.add("uid", uid)
    cal.add_component(ev)
    p = cal_dir / f"{uid}.ics"
    p.write_bytes(cal.to_ical())
    default_memory().add_event(uid, summary, start, end, description, location, str(p))
    return f"Created local event {uid} at {p} (not synced externally)"


@register("add_task", "Add a company/project task to local tracking.",
          {"title": "task", "priority": "low|normal|high", "due": "date", "notes": "details"},
          ["title"], category="ceo", read_only=False)
def add_task(title: str, priority: str = "normal", due: str = "", notes: str = "") -> str:
    tid = default_memory().add_task(title, priority, due, notes)
    return f"Task #{tid} added: {title}"


@register("list_tasks", "List tracked tasks.", {"status": "open|done (optional)"}, category="ceo")
def list_tasks(status: str = "") -> str:
    rows = default_memory().list_tasks(status or None)
    if not rows:
        return "No tasks."
    return "\n".join(f"#{r['id']} [{r['status']}] {r['priority']} {r['due'] or '-'} {r['title']}" for r in rows)


@register("update_task", "Update a task's status/notes after confirmation.",
          {"task_id": "id", "status": "open|done|blocked", "notes": "new notes"},
          ["task_id"], category="ceo", dangerous=True, read_only=False)
def update_task(task_id: int | str, status: str | None = None, notes: str | None = None) -> str:
    tid = int(task_id)
    if not safety.require_confirmation("Update task?", f"Task #{tid}\nstatus={status}\nnotes={notes}", action="update_task"):
        raise PermissionError("Task update denied")
    ok = default_memory().update_task(tid, status=status, notes=notes)
    return f"Task #{tid} updated" if ok else f"Task #{tid} not found"


@register("read_resume", "Extract a resume/CV for analysis (PDF or DOCX).",
          {"path": "resume path"}, ["path"], category="ceo")
def read_resume(path: str) -> str:
    p = safety.validate_read(path)
    text = extract_text(p)
    return f"{p}\n\n{text[:30000]}"


def _money(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _quote_doc(kind: str, path: str, customer: str, number: str, items, tax_percent: float,
               notes: str, due_date: str) -> str:
    try:
        import docx
    except ImportError as e:
        raise RuntimeError("python-docx is not installed") from e
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except json.JSONDecodeError as e:
            raise ValueError("items must be JSON [{description, qty, unit_price}]") from e
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a non-empty list")
    p = safety.validate_write(path)
    if p.suffix.lower() != ".docx":
        p = p.with_suffix(".docx")
    if not safety.require_confirmation(
        f"Create {kind}?",
        f"{p}\nCustomer: {customer}\nItems: {len(items)}\nTotal will be calculated locally.",
        action=f"create_{kind}",
    ):
        raise PermissionError("Document creation denied by user")
    subtotal = Decimal("0")
    rows = []
    for it in items:
        qty = Decimal(str(it.get("qty", 1)))
        price = Decimal(str(it.get("unit_price", 0)))
        line = _money(qty * price)
        subtotal += line
        rows.append((str(it.get("description", "")), qty, price, line))
    tax = _money(subtotal * Decimal(str(tax_percent)) / Decimal("100"))
    total = subtotal + tax
    d = docx.Document()
    d.add_heading("Melouz Technologies", level=0)
    d.add_heading(f"{kind.title()} {number}", level=1)
    d.add_paragraph(f"Customer: {customer}")
    d.add_paragraph(f"Date: {datetime.now():%Y-%m-%d}")
    if due_date:
        d.add_paragraph(f"Due: {due_date}")
    table = d.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    for i, h in enumerate(("Description", "Qty", "Unit price", "Line total")):
        table.rows[0].cells[i].text = h
    for desc, qty, price, line in rows:
        cells = table.add_row().cells
        cells[0].text = desc
        cells[1].text = str(qty)
        cells[2].text = f"{price}"
        cells[3].text = f"{line}"
    d.add_paragraph(f"Subtotal: {subtotal}")
    d.add_paragraph(f"Tax ({tax_percent}%): {tax}")
    d.add_paragraph(f"Total: {total}")
    if notes:
        d.add_paragraph("")
        d.add_paragraph(notes)
    p.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(p))
    return f"Created {kind} at {p}\nSubtotal={subtotal} tax={tax} total={total}"


@register("create_quotation", "Create a quotation .docx with validated arithmetic.",
          {"path": "output .docx", "customer": "customer", "items": "JSON [{description,qty,unit_price}]",
           "tax_percent": "tax %", "number": "quote number", "notes": "terms", "valid_until": "date"},
          ["path", "customer", "items"], category="ceo", dangerous=True, read_only=False)
def create_quotation(path: str, customer: str, items, tax_percent: float | str = 0,
                     number: str = "", notes: str = "", valid_until: str = "") -> str:
    return _quote_doc("quotation", path, customer, number or f"Q-{datetime.now():%Y%m%d-%H%M}", items,
                      float(tax_percent or 0), notes, valid_until)


@register("create_invoice", "Create an invoice .docx with validated arithmetic.",
          {"path": "output .docx", "customer": "customer", "items": "JSON [{description,qty,unit_price}]",
           "tax_percent": "tax %", "number": "invoice number", "notes": "payment terms", "due_date": "date"},
          ["path", "customer", "items"], category="ceo", dangerous=True, read_only=False)
def create_invoice(path: str, customer: str, items, tax_percent: float | str = 0,
                   number: str = "", notes: str = "", due_date: str = "") -> str:
    return _quote_doc("invoice", path, customer, number or f"INV-{datetime.now():%Y%m%d-%H%M}", items,
                      float(tax_percent or 0), notes, due_date)


@register("imap_sync_readonly", "Read-only IMAP sync. Never sends or deletes; requires explicit config and confirmation.",
          {"query": "IMAP search, e.g. ALL or UNSEEN", "limit": "max messages"},
          ["query"], category="ceo", dangerous=True, read_only=False)
def imap_sync_readonly(query: str = "ALL", limit: int | str = 50) -> str:
    cfg = load()
    em = cfg.get("email", {})
    if not safety.require_external(
        "allow_imap_read", "Read email over IMAP?",
        f"Host: {em.get('imap_host')}\nFolder: {em.get('imap_folder', 'INBOX')}\n"
        "THAMBI opens the folder read-only (EXAMINE). It cannot send, delete, or flag messages."
    ):
        raise PermissionError("IMAP read denied")
    host = em.get("imap_host")
    user_env = em.get("imap_user_env", "AETHER_IMAP_USER")
    pass_env = em.get("imap_password_env", "AETHER_IMAP_PASSWORD")
    user, password = os.environ.get(user_env), os.environ.get(pass_env)
    if not host or not user or not password:
        raise RuntimeError(f"Set email.imap_host in config and environment variables {user_env}/{pass_env}.")
    limit = min(500, max(1, int(limit)))
    conn = imaplib.IMAP4_SSL(host, int(em.get("imap_port", 993)))
    try:
        conn.login(user, password)
        typ, _ = conn.select(em.get("imap_folder", "INBOX"), readonly=True)
        if typ != "OK":
            raise RuntimeError("Could not open mailbox read-only")
        typ, data = conn.search(None, query)
        if typ != "OK":
            raise RuntimeError("IMAP search failed")
        ids = data[0].split()[-limit:]
        imported = 0
        for mid in ids:
            typ, msgdata = conn.fetch(mid, "(RFC822)")
            if typ != "OK" or not msgdata or not isinstance(msgdata[0], tuple):
                continue
            msg = email.message_from_bytes(msgdata[0][1], policy=policy.default)
            bodies = []
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                        try:
                            bodies.append(str(part.get_content()))
                        except Exception:
                            pass
            else:
                bodies.append(str(msg.get_content()))
            uid = str(msg.get("Message-ID", "")) or f"imap:{host}:{mid.decode()}"
            default_memory().add_email(uid, "imap-readonly", str(msg.get("From", "")), str(msg.get("To", "")),
                                       str(msg.get("Subject", "")), str(msg.get("Date", "")), "\n".join(bodies),
                                       "", str(msg.get("Thread-Index", "") or msg.get("Subject", "")))
            imported += 1
        return f"Imported {imported} message(s) read-only. No message was sent, deleted, or marked."
    finally:
        try:
            conn.logout()
        except Exception:
            pass
