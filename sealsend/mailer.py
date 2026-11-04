"""Email: building messages, and delivering through Gmail/Outlook/SMTP, Resend or the sandbox outbox."""
import base64
import html
import json
import smtplib
import socket
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from . import config, settings

SIGNOFF = """Warm regards,
{{ORG_NAME|The Organizing Team}}"""

DEFAULT_SUBJECT = "Your certificate for {{EVENT}}"
DEFAULT_BODY = """Dear {{NAME}},

Congratulations! Your certificate for {{EVENT}} is attached to this email.

It is digitally signed, so anyone (a university, an employer) can confirm it is genuine:
{{VERIFY_URL}}
Certificate ID: {{CERT_ID}}

""" + SIGNOFF

PRESETS = {
    "standard": {"label": "Standard", "subject": DEFAULT_SUBJECT, "body": DEFAULT_BODY},
    "winner": {
        "label": "Winner congratulations",
        "subject": "Congratulations, {{NAME}}! Your award certificate from {{EVENT}}",
        "body": """Dear {{NAME}},

Huge congratulations on {{HONOR_PHRASE|your achievement}} at {{EVENT}}! The whole team is proud of you.

Your certificate is attached. It is digitally signed, so anyone can confirm it is genuine:
{{VERIFY_URL}}
Certificate ID: {{CERT_ID}}

Keep up the great work.

""" + SIGNOFF},
    "participation": {
        "label": "Thank you for participating",
        "subject": "Thank you for participating in {{EVENT}}, {{NAME}}",
        "body": """Dear {{NAME}},

Thank you for participating in {{EVENT}}. Your effort and enthusiasm made the event a success.

Your certificate of participation is attached. You can verify it at any time:
{{VERIFY_URL}}
Certificate ID: {{CERT_ID}}

We hope to see you at the next one!

""" + SIGNOFF},
    "appreciation": {
        "label": "Appreciation (volunteers, organizers)",
        "subject": "Thank you for your contribution to {{EVENT}}, {{NAME}}",
        "body": """Dear {{NAME}},

On behalf of everyone involved, thank you for your contribution to {{EVENT}}[[ as {{CATEGORY_A}}]]. The event would not have been possible without you.

Your certificate of appreciation is attached. You can verify it at any time:
{{VERIFY_URL}}
Certificate ID: {{CERT_ID}}

With sincere gratitude,
{{ORG_NAME|The Organizing Team}}"""},
    "completion": {
        "label": "Completion (courses, internships)",
        "subject": "Congratulations on completing {{EVENT}}, {{NAME}}",
        "body": """Dear {{NAME}},

Congratulations on successfully completing {{EVENT}}!

Your certificate of completion is attached. It is digitally signed and can be verified here:
{{VERIFY_URL}}
Certificate ID: {{CERT_ID}}

We wish you every success ahead.

""" + SIGNOFF},
}


class PermanentFailure(Exception):
    """This recipient address was rejected; retrying it is pointless."""


class TransientFailure(Exception):
    """Temporary problem (network, busy server); a retry may succeed."""


class AuthFailure(Exception):
    """The sending account's login was rejected. Stop the whole batch: every send would fail."""


class QuotaFailure(Exception):
    """The provider's daily sending limit was reached. Pause and resume later."""


def sender_identity(org_name: str = "", account: dict | None = None) -> tuple[str, str, str]:
    """(display name, from address, reply-to). `account` is the sending account (default: the main one);
    the second account uses the main account's sender name and sends replies to the main inbox."""
    main = settings.mail()
    m = account or main
    name = m.get("from_name") or main.get("from_name") or org_name or settings.org().get("org_name") or config.MAIL_FROM_NAME
    if config.mail_mode() in ("smtp", "resend") and m.get("address"):
        reply = m.get("reply_to", "")
        if not reply and m.get("slot") == "mail2":
            reply = main.get("reply_to") or main.get("address", "")
        return name, m["address"], reply
    return name, config.MAIL_FROM, ""


def build_message(to_addr: str, to_name: str, subject: str, body: str,
                  attachment: bytes, attachment_name: str, cert_code: str, org_name: str = "",
                  account: dict | None = None, feedback: tuple[str, str] | None = None) -> EmailMessage:
    """feedback: (question, link) adds a "rate this event" line; in the HTML version, five tappable stars."""
    from_name, from_addr, reply_to = sender_identity(org_name, account)
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, from_addr))
    msg["To"] = formataddr((to_name, to_addr))
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Message-ID"] = make_msgid(domain=from_addr.split("@")[-1] or "localhost")
    msg["X-SealSend-Certificate"] = cert_code
    msg.set_content(body.rstrip() + (f"\n\n{feedback[0]} {feedback[1]}\n" if feedback else ""))
    paragraphs = "".join(
        f"<p style=\"margin:0 0 14px\">{html.escape(p).replace(chr(10), '<br>')}</p>" for p in body.split("\n\n") if p.strip())
    if feedback:
        stars = "".join(f'<a href="{html.escape(feedback[1])}?stars={n}" title="{n} out of 5" style="font-size:32px;line-height:1;'
                        f'color:#f5a623;text-decoration:none;padding:0 3px">&#9733;</a>' for n in range(1, 6))
        paragraphs += ('<div style="margin:24px 0 4px;padding:16px 18px;border-radius:12px;background:#f6f7f9">'
                       f'<div style="font-weight:600;margin-bottom:8px">{html.escape(feedback[0])}</div>{stars}</div>')
    msg.add_alternative(
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:15px;line-height:1.55;'
        f'color:#111;max-width:560px">{paragraphs}</div>', subtype="html")
    msg.add_attachment(attachment, maintype="application", subtype="pdf", filename=attachment_name)
    return msg


def _classify_smtp(e: Exception) -> Exception:
    text = str(e).lower()
    if isinstance(e, smtplib.SMTPAuthenticationError) or getattr(e, "smtp_code", 0) in (530, 534, 535):
        return AuthFailure("The email account rejected the login. In Settings > Email, check the address and the "
                           "16-letter App Password (Gmail needs 2-Step Verification on).")
    if any(k in text for k in ("daily user sending", "quota", "limit exceeded", "sending limit", "too many messages",
                                "5.4.5", "4.5.3", "rate limit")):
        return QuotaFailure(f"The provider's sending limit was reached: {e}")
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        return PermanentFailure(f"Recipient refused: {e.recipients}")
    if isinstance(e, smtplib.SMTPResponseException):
        code = e.smtp_code
        if 500 <= code < 600 and code not in (500, 503):
            return PermanentFailure(f"{code} {e.smtp_error!r}")
        return TransientFailure(f"{code} {e.smtp_error!r}")
    return TransientFailure(f"{type(e).__name__}: {e}")


def connect_smtp(m: dict | None = None):
    """Log in to the configured SMTP server. Tries STARTTLS:587, then SSL:465 (many campus networks block 587)."""
    m = m or settings.mail()
    host = m.get("host") or config.SMTP_HOST
    user = m.get("username") or m.get("address") or config.SMTP_USER
    password = m.get("password") or config.SMTP_PASSWORD
    port = int(m.get("port") or config.SMTP_PORT)
    security = (m.get("security") or config.SMTP_SECURITY).lower()
    attempts = [(port, security)]
    if (port, security) == (587, "starttls"):
        attempts.append((465, "ssl"))
    last = None
    for p, sec in attempts:
        try:
            if sec == "ssl":
                s = smtplib.SMTP_SSL(host, p, context=ssl.create_default_context(), timeout=20)
            else:
                s = smtplib.SMTP(host, p, timeout=20)
                s.ehlo()
                if sec == "starttls":
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
            if user:
                s.login(user, password)
            return s
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPResponseException) as e:
            raise _classify_smtp(e) from e
        except (OSError, smtplib.SMTPException) as e:
            last = e
            continue
    raise TransientFailure(f"Could not reach {host} on port 587 or 465 ({type(last).__name__}: {last}). "
                           "Check the internet connection or whether the network blocks email ports.")


def check_connection(account: dict | None = None) -> dict:
    """Used by Settings > Test connection and before every batch. `account`: test that account instead."""
    if account:
        try:
            connect_smtp(account).quit()
            return {"ok": True, "mode": "smtp", "message": "Connected and logged in"}
        except (AuthFailure, QuotaFailure, TransientFailure, PermanentFailure) as e:
            return {"ok": False, "mode": "smtp", "message": str(e)}
        except socket.gaierror as e:
            return {"ok": False, "mode": "smtp", "message": f"Server not found: {e}"}
    mode = config.mail_mode()
    if mode == "sandbox":
        return {"ok": True, "mode": mode, "message": "Sandbox: emails are saved to the outbox folder, not delivered"}
    if mode == "resend":
        return {"ok": bool(settings.mail().get("password") or config.RESEND_API_KEY), "mode": mode,
                "message": "Resend API key saved"}
    try:
        s = connect_smtp()
        s.quit()
        return {"ok": True, "mode": mode, "message": "Connected and logged in"}
    except (AuthFailure, QuotaFailure, TransientFailure, PermanentFailure) as e:
        return {"ok": False, "mode": mode, "message": str(e)}
    except socket.gaierror as e:
        return {"ok": False, "mode": mode, "message": f"Server not found: {e}"}


class Sender:
    """One delivery channel per batch run; reuses a single SMTP connection across the batch.

    `faults` (Chaos Test only, sandbox only) simulates a flaky provider:
    {"transient": [addresses that fail once], "reject": [addresses that bounce permanently]}
    """

    def __init__(self, mode: str | None = None, faults: dict | None = None, account: dict | None = None):
        self.mode = mode or config.mail_mode()
        self.account = account  # which sending account this connection logs in as (None: the main one)
        self.smtp = None
        self.faults = {k: {a.lower() for a in v} for k, v in (faults or {}).items()}
        self._tripped: set[str] = set()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _simulate(self, to_addr: str) -> None:
        addr = to_addr.lower()
        if addr in self.faults.get("reject", set()):
            raise PermanentFailure(f"550 5.1.1 <{to_addr}>: mailbox does not exist (simulated)")
        if addr in self.faults.get("transient", set()) and addr not in self._tripped:
            self._tripped.add(addr)
            raise TransientFailure("421 4.7.0 Try again later: server busy (simulated)")

    def send(self, msg: EmailMessage, to_addr: str, outbox_name: str) -> str:
        if self.mode == "sandbox":
            self._simulate(to_addr)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            path = config.OUTBOX_DIR / f"{outbox_name}_{stamp}.eml"
            path.write_bytes(bytes(msg))
            return f"Sandbox: saved {path.name}"
        if self.mode == "resend":
            return self._send_resend(msg, to_addr)
        return self._send_smtp(msg)

    def _send_smtp(self, msg: EmailMessage) -> str:
        if self.smtp is None:
            self.smtp = connect_smtp(self.account)
        try:
            try:
                self.smtp.send_message(msg)
            except smtplib.SMTPServerDisconnected:
                self.close()
                self.smtp = connect_smtp(self.account)
                self.smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            err = _classify_smtp(e)
            if isinstance(err, TransientFailure):
                self.close()
            raise err from e
        return "Accepted by SMTP server"

    def _send_resend(self, msg: EmailMessage, to_addr: str) -> str:
        key = settings.mail().get("password") or config.RESEND_API_KEY
        att = next(msg.iter_attachments())
        payload = {
            "from": msg["From"], "to": [to_addr], "subject": msg["Subject"],
            "text": msg.get_body(("plain",)).get_content(), "html": msg.get_body(("html",)).get_content(),
            "attachments": [{"filename": att.get_filename(), "content": base64.b64encode(att.get_content()).decode()}],
            "headers": {"X-SealSend-Certificate": msg["X-SealSend-Certificate"]},
        }
        if msg["Reply-To"]:
            payload["reply_to"] = msg["Reply-To"]
        req = urllib.request.Request(
            "https://api.resend.com/emails", data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "SendEZ/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return f"Resend accepted: {json.loads(r.read() or b'{}').get('id', '')}"
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code in (401, 403):
                raise AuthFailure(f"Resend rejected the API key ({e.code})") from e
            if e.code == 429:
                raise QuotaFailure(f"Resend rate or daily limit reached: {detail}") from e
            if e.code >= 500:
                raise TransientFailure(f"Resend {e.code}: {detail}") from e
            raise PermanentFailure(f"Resend {e.code}: {detail}") from e
        except (urllib.error.URLError, OSError) as e:
            raise TransientFailure(f"Resend unreachable: {e}") from e

    def close(self):
        if self.smtp is not None:
            try:
                self.smtp.quit()
            except Exception:
                pass
            self.smtp = None
