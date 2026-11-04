"""In-app help assistant: answers "how do I…" questions and offers buttons that jump to the right place.

Works offline with built-in answers. When ANTHROPIC_API_KEY is set it uses Claude, grounded in the same
answers plus the current state of the app; any failure falls back to the offline answers.
"""
import json
import os
import re

from . import config

ACTIONS = {
    "open_settings": "Open Settings",
    "open_share": "Share online",
    "new_event": "New Event",
    "open_templates": "Templates",
    "goto_students": "Go to Students",
    "goto_certs": "Go to Certificates",
    "goto_email": "Go to Email",
    "goto_review": "Go to Review & Send",
    "goto_proof": "Go to Proof",
    "open_verify": "Open the verifier",
    "run_chaos": "Run Chaos Test",
}

# (id, trigger phrases, answer, actions). Answers use **bold** and line breaks; links are written out in full.
KB = [
    ("app_password", ["app password", "16 letter", "16-letter", "gmail password", "apppassword", "2-step", "two step"],
     "An **App Password** is a special 16-letter password Google gives you just for SendEZ. Your normal Gmail password won’t work.\n"
     "1. Turn on 2-Step Verification: https://myaccount.google.com/signinoptions/twosv\n"
     "2. Open https://myaccount.google.com/apppasswords, type “SendEZ”, press **Create**, copy the 16 letters.\n"
     "3. In SendEZ: **Settings → Gmail**, paste it into **App Password**, press **Save & Test**.\n"
     "College or work Google accounts sometimes block App Passwords; a personal Gmail always works.",
     ["open_settings"]),
    ("connect_gmail", ["connect gmail", "set up gmail", "setup gmail", "add gmail", "gmail", "connect email", "set up email", "email account", "sender"],
     "To send from Gmail: **Settings → Gmail** → type your Gmail address and your 16-letter **App Password** → **Save & Test**. "
     "When it says **✓ Connected**, type your own address next to **Send test** to check a real email arrives.",
     ["open_settings"]),
    ("outlook", ["outlook", "hotmail", "office 365", "microsoft 365", "office365", "live.com", "university email", "college email"],
     "To send from Outlook / Microsoft 365: **Settings → Outlook** → your address and password → **Save & Test**.\n"
     "Many colleges turn off this kind of sign-in for their Microsoft accounts. If the test fails with a login error, use a Gmail account instead.",
     ["open_settings"]),
    ("email_not_working", ["not working", "test failed", "didn't arrive", "didnt arrive", "did not arrive", "not received", "no email", "rejected", "could not reach", "error", "failed to send", "not sending", "can't send", "cant send"],
     "Let’s find the cause. The message next to **Send test** in Settings tells you which one it is:\n"
     "• **Sandbox / test mode**: email isn’t connected yet. Choose **Gmail** in Settings and **Save & Test**.\n"
     "• **“rejected the login”**: use the 16-letter **App Password**, not your normal password.\n"
     "• **“Could not reach smtp.gmail.com”**: the Wi-Fi blocks email. Try your phone’s hotspot.\n"
     "• **“Test email sent” but nothing arrived**: check **Spam**, and the address you typed.",
     ["open_settings"]),
    ("test_mode", ["test mode", "sandbox", "outbox", "saved on this computer", "not emailed"],
     "**Test mode** means no email account is connected, so certificates and emails are saved on this computer instead of being sent. "
     "Connect Gmail in **Settings** to send for real.",
     ["open_settings"]),
    ("new_event", ["upload", "excel", "spreadsheet", "csv", "new event", "start", "begin", "xlsx", "student list", "import students"],
     "Press **＋ New Event**, pick the club (or **Other**), type the event name, then drop your Excel or CSV file into the box. "
     "SendEZ finds the names, emails and categories by itself. Any column names work.",
     ["new_event"]),
    ("columns", ["wrong column", "column", "mapping", "detected", "name not found", "email not found", "wrong name", "first name", "last name"],
     "In step **1 · Students**, the list **“What we found in your sheet”** shows which column is used for each thing. "
     "If one is wrong, tap it and pick the right column. Everything updates instantly.",
     ["goto_students"]),
    ("invalid", ["invalid", "need fixing", "problem rows", "skipped", "duplicate", "missing name", "bad rows", "errors in sheet"],
     "Rows marked **! Invalid** have a problem: a missing or junk name (like #REF!), a broken email, or a duplicate student. "
     "They are skipped, and everyone else still gets their certificate. Tap **Need fixing** in step 1 to see each reason with its row number. "
     "Fix them in Excel and upload again, or leave them out.",
     ["goto_students"]),
    ("categories", ["category", "categories", "winner", "participant", "volunteer", "different certificate", "runner up", "group"],
     "In step **2 · Certificates**, each category (Winner, Participant, Volunteer…) gets its own **design**, **title** and **email**. "
     "SendEZ guesses them from the category name, and you can change any of them. Use **Category column** to choose which column groups the students.",
     ["goto_certs"]),
    ("design", ["template", "design", "change certificate", "look", "style", "theme", "gold", "change the certificate"],
     "Go to step **2 · Certificates** and tap the picture next to a category to choose another design. "
     "There are 14 designs for different kinds of events, and you can also use your own from Canva.",
     ["goto_certs", "open_templates"]),
    ("canva", ["canva", "canva design", "my canva", "my own design", "own template", "import design", "custom design", "upload design"],
     "To use your own design: **Templates → Import from Canva**.\n"
     "1. In Canva, pick or make a certificate and delete the sample name.\n"
     "2. **Share → Download → PDF Standard** (or PNG).\n"
     "3. Drop it into SendEZ, then drag the name and other fields where they belong, and press **Save**.",
     ["open_templates"]),
    ("clubs", ["club", "clubs", "mahindra", "kalakriti", "roboverse", "synolo", "outreach", "erudite", "coding ninjas", "art felt", "palaestro", "celeste", "aeiforia"],
     "When you start a **New Event**, tap the club it’s for. Every certificate then shows **Mahindra University** and the club, with both logos. "
     "Upload or replace the university and club logos in **Settings → Mahindra University logos**.",
     ["new_event", "open_settings"]),
    ("branding", ["logo", "signature", "signatory", "signatories", "college name", "organization name", "organisation", "sign"],
     "Logos, signatories and signature images are set in step **2 · Certificates → Certificate details**. "
     "Use **Save as my defaults** to reuse them for future events. Mahindra University and club logos are uploaded once in **Settings**.",
     ["goto_certs", "open_settings"]),
    ("email_text", ["email message", "change email", "subject", "email text", "message", "wording", "congratulation"],
     "Step **3 · Email** shows the message each category gets. Edit the default message there. Tap a grey chip like {{NAME}} to insert the student’s details.",
     ["goto_email"]),
    ("send", ["send", "how do i send", "deliver", "email certificates", "send certificates", "issue"],
     "Go to step **4 · Review & Send**: read the checklist, use **Previous / Next** to preview a few students, then press **Send**. "
     "Each attachment is checked against its student right before sending. You can keep using the app while it works.",
     ["goto_review"]),
    ("limit", ["limit", "500", "paused", "quota", "daily", "too many", "pause", "per day", "a day", "how many emails",
               "more than 500", "over 500", "1000 students", "thousand students"],
     "Gmail lets you send about **500 emails a day**. SendEZ stops safely at your limit and the rest stay queued. "
     "It **continues automatically** once the limit resets (keep SendEZ open), or press **Send** again yourself. Nobody gets two emails.\n"
     "To send about twice as many a day, add a **Second sending account** in **Settings**.",
     ["goto_review", "open_settings"]),
    ("sheets", ["google sheet", "google sheets", "google form", "google forms", "form responses", "sheet link", "spreadsheet link",
                "import link", "paste a link", "from google"],
     "On **New Event**, paste the link of your Google Sheet into **Or paste a Google Sheets link** and press **Import**. "
     "It works great with Google Form responses.\nThe sheet must be shared: in Google Sheets press **Share → General access → "
     "Anyone with the link (Viewer)**.",
     ["new_event"]),
    ("reuse", ["reuse", "same settings", "copy settings", "last event", "previous event", "same design again", "last time",
               "same as before", "repeat event"],
     "On **New Event**, choose the earlier event under **Copy settings from** before you upload the list. "
     "Its designs, certificate titles, emails, signatories and logos are copied; only the students change.",
     ["new_event"]),
    ("schedule", ["schedule", "send later", "later", "tomorrow", "send at", "specific time", "set a time", "timer", "morning"],
     "In **Review & Send**, press **Schedule…**, pick the date and time, then **Schedule**. SendEZ starts sending by itself at that time.\n"
     "Keep SendEZ open and the computer awake (lid open). To change or cancel it, use the banner at the top of the event.",
     ["goto_review"]),
    ("auto_continue", ["continue automatically", "auto continue", "automatically continue", "resume", "continue tomorrow",
                       "limit resets", "after the limit", "keeps going"],
     "When the daily limit is reached, SendEZ waits and **continues automatically** when the limit resets, about 24 hours later. "
     "The time shows in the banner on the event, with **Send now** and **Don’t continue**. You can turn this off in **Settings → Continue after the daily limit**.",
     ["open_settings"]),
    ("second_account", ["second account", "two accounts", "another gmail", "second gmail", "two gmail", "extra account",
                        "another account", "more emails", "double the limit", "more per day",
                        "send more emails", "more emails a day", "more emails per day"],
     "In **Settings → Second sending account**, choose Gmail or Outlook, enter the address and its App Password, then **Save & Test**. "
     "When the first account reaches its daily limit, SendEZ continues from the second one. Replies still go to your first address.",
     ["open_settings"]),
    ("reports", ["report for", "club report", "semester report", "statistics", "stats", "how many students", "analytics",
                 "summary for faculty", "pdf report", "annual report", "how many certificates"],
     "Open **Reports** in the menu, pick the **club** and the **period** (this semester, last semester, this year or all time). "
     "You see events, certificates issued, students reached, students who came to 2+ events, categories, departments and ratings. "
     "Press **Download PDF** for a report you can give to faculty, or **CSV** for Excel.",
     []),
    ("feedback", ["feedback", "rating", "ratings", "rate the event", "stars", "survey", "what students thought", "reviews"],
     "In step **3 · Email**, turn on **Ask for feedback (1–5 stars)** (on by default). The email then ends with five stars; "
     "students tap one to rate the event and can add a comment. Results show in **Review & Send → Feedback**, without names, "
     "with a CSV download.",
     ["goto_email"]),
    ("linkedin", ["linkedin", "add to profile", "share certificate", "share on", "instagram", "whatsapp status", "post my certificate"],
     "When a student opens their certificate's verify page (the QR code or the link in the email), they can press "
     "**Add to LinkedIn profile**: name, issuer, date and certificate ID are filled in for them. **Share** sends the link with a "
     "preview card. You can also put the LinkedIn link in the email with **{{LINKEDIN_URL}}**.",
     ["goto_email"]),
    ("alerts", ["notification", "notifications", "notify", "alert", "sound", "chime", "when it finishes", "when done", "vibrate",
                "vibration", "haptic"],
     "When sending finishes or pauses, SendEZ shows a message and plays a short sound. If you’re in another window, you also get a "
     "notification (allow it when your browser asks). On phones it gives a small vibration.",
     []),
    ("failed", ["failed", "bounce", "bounced", "retry", "didn't get", "didnt get", "not delivered"],
     "In **Review & Send**, press **Retry Failed** to try again. If an address bounced, SendEZ already tried the backup email column. "
     "For anyone still failing, tap their name and **Copy** their claim link to send it another way.",
     ["goto_review"]),
    ("claim", ["claim link", "claim", "whatsapp", "without email", "no email address", "missing email"],
     "Students with no usable email get a private **claim link** instead of being left out. Tap the student in step 1, press **Copy** next to Claim link, "
     "and send it by WhatsApp or message. They enter their student ID to download their certificate.",
     ["goto_students"]),
    ("resend", ["resend", "send again", "send one", "one student"],
     "Tap the student’s name in step 1, then press **Send Again**. SendEZ normally never emails anyone twice, so it asks you to confirm.",
     ["goto_students"]),
    ("verify", ["verify", "qr", "fake", "real", "genuine", "authentic", "check certificate", "forged"],
     "Every certificate has a **QR code**. Scanning it opens a page that says **“Verified, issued to …”**. "
     "Anyone can also upload a PDF on the verify page: an edited certificate shows **“This file has been altered”**.",
     ["open_verify", "goto_proof"]),
    ("share", ["share", "link", "operator", "phone", "iphone", "ipad", "another device", "online", "other laptop", "dell", "access", "public"],
     "Press **Share online** to get a free public link. Send it to your operator or open it on your phone or iPad; they sign in with your SendEZ password. "
     "Keep this computer on and SendEZ open while they use it. You get a new link each time you start sharing.",
     ["open_share"]),
    ("report", ["report", "download", "zip", "records", "export", "spreadsheet of results", "who received"],
     "In **Review & Send**, press **Delivery report (.csv)** for a spreadsheet of who got what and when, and **All certificates (.zip)** for every PDF, sorted into folders by category.",
     ["goto_review"]),
    ("password", ["password", "forgot", "change password", "sign in", "login", "log in"],
     "Change your SendEZ password in **Settings → Security**. If you forgot it: quit SendEZ, delete the file **settings.json** in SendEZ’s data folder, and open SendEZ again "
     "to create a new one. You’ll need to connect Gmail again after that.",
     ["open_settings"]),
    ("chaos", ["chaos", "demo", "judges", "test the system", "prove", "safety"],
     "**Run Chaos Test** creates a practice event with 15 real problems (broken names, email typos, swapped files, a failing mail server) and shows SendEZ catching every one. "
     "It never sends real emails.",
     ["run_chaos"]),
    ("caps", ["capital", "all caps", "uppercase", "lowercase", "name format", "capitalization", "capitalisation"],
     "Turn on **Fix name capitalization** in step 1 (Automatic clean-up) to change ROHAN MEHTA into Rohan Mehta. It only changes names that are ALL CAPS or all lowercase.",
     ["goto_students"]),
    ("typos", ["typo", "typos", "wrong email", "misspelled", "spelling", "fix email"],
     "**Fix email typos** (step 1) corrects things like gmial.com → gmail.com and shows what it changed. Other wrong addresses must be fixed in your Excel file. "
     "Then upload it again; students who already got their certificate won’t be emailed twice.",
     ["goto_students"]),
    ("preview", ["preview", "see the certificate", "what will they get", "check before", "look at"],
     "Step **4 · Review & Send** shows exactly what each student will receive: the certificate on the left and the email on the right. Use the category menu and **Previous / Next**.",
     ["goto_review"]),
    ("install", ["install", "mac", "windows", "download app", "laptop", "computer", "open the app", "start the app"],
     "On a Mac, open the **SendEZ** app (the first time: if macOS warns you, go to **System Settings → Privacy & Security → Open Anyway**). "
     "On Windows, double-click **SendEZ.exe** (if a blue box appears: **More info → Run anyway**). Nothing else to install.",
     []),
    ("about", ["what is", "what can", "help", "features", "how does", "what does", "hi", "hello", "hey"],
     "I’m the SendEZ helper. SendEZ emails every student their own certificate:\n"
     "1. **Students**: upload your Excel file\n2. **Certificates**: a design per category\n3. **Email**: the message\n4. **Review & Send**\n"
     "Ask me things like “How do I connect Gmail?”, “Why didn’t my test email arrive?” or “How do I share the app with my operator?”",
     ["new_event"]),
]
SUGGESTIONS = ["How do I connect Gmail?", "What is an App Password?", "How do I share the app?", "Why didn't my test email arrive?",
               "How do I schedule sending?", "Can I import a Google Sheet?", "How do I send more emails a day?",
               "How do I give winners a different certificate?", "How do I use my own Canva design?"]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9@.' -]", " ", text.lower())).strip()


_VOCAB = sorted({w for e in KB for ph in e[1] for w in _norm(ph).split() if len(w) >= 4})


def _fix_typos(text: str) -> str:
    """'passweord' -> 'password', 'gmial' stays (it is a phrase itself). Only words of 5+ letters are corrected."""
    import difflib
    out = []
    for w in text.split():
        if len(w) >= 5 and w not in _VOCAB:
            close = difflib.get_close_matches(w, _VOCAB, n=1, cutoff=0.8)
            w = close[0] if close else w
        out.append(w)
    return " ".join(out)


def match(message: str):
    t = f" {_fix_typos(_norm(message))} "
    best, best_score = None, 0.0
    for entry in KB:
        score = 0.0
        for phrase in entry[1]:
            p = _norm(phrase)
            if p and re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", t):
                score += 1 + 0.6 * (len(p.split()) - 1)  # longer phrases are stronger evidence
        if score > best_score:
            best, best_score = entry, score
    return best if best_score > 0 else None


def _tailor(entry_id: str, state: dict) -> str:
    """Short state-aware first line, e.g. 'You're in test mode right now.'"""
    mode = state.get("mail_mode")
    if entry_id in ("email_not_working", "send", "connect_gmail") and mode == "sandbox":
        return "Right now **no email account is connected** (test mode), so nothing is actually emailed yet.\n\n"
    if entry_id == "limit" and state.get("quota_left") is not None:
        return f"You have **{state['quota_left']}** emails left in today’s limit.\n\n"
    if entry_id == "limit" and mode == "sandbox":
        return "You’re in **test mode** right now, so there is no limit yet (nothing is actually emailed).\n\n"
    return ""


def answer_offline(message: str, state: dict) -> dict:
    entry = match(message)
    if not entry:
        return {"answer": "I’m not sure about that one. Try asking it another way, or pick a question below. "
                          "I know about connecting Gmail, uploading students, certificates, sending, sharing and verifying.",
                "actions": [], "suggestions": SUGGESTIONS[:4], "engine": "offline"}
    eid, _, text, actions = entry
    return {"answer": _tailor(eid, state) + text, "actions": [{"id": a, "label": ACTIONS[a]} for a in actions],
            "suggestions": [s for s in SUGGESTIONS if not match(s) or match(s)[0] != eid][:3], "engine": "offline"}


SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["answer", "actions"],
    "properties": {"answer": {"type": "string"},
                   "actions": {"type": "array", "items": {"type": "string", "enum": list(ACTIONS)}}},
}


def answer_claude(message: str, history: list[dict], state: dict) -> dict:
    import anthropic
    guide = "\n\n".join(f"[{e[0]}] {e[2]}" for e in KB)
    system = (
        "You are the friendly in-app helper of SendEZ, an app that emails students their own verified certificates. "
        "Answer the user's question in plain, simple English (they may be new to computers), in at most 6 short lines. "
        "Use **bold** for button and screen names exactly as they appear in the app. Only describe features that exist in the "
        "reference below; if something isn't supported, say so briefly. Pick up to 2 actions that take the user to the right place.\n\n"
        f"Current app state: {json.dumps(state)}\n\nReference (the app's real screens and features):\n{guide}")
    msgs = []
    for h in history[-6:]:
        role = h.get("role")
        content = str(h.get("content", ""))[:2000]
        if role in ("user", "assistant") and content and (msgs or role == "user"):
            if msgs and msgs[-1]["role"] == role:
                msgs[-1]["content"] += "\n" + content
            else:
                msgs.append({"role": role, "content": content})
    if msgs and msgs[-1]["role"] == "user":
        msgs[-1]["content"] += "\n" + message[:2000]
    else:
        msgs.append({"role": "user", "content": message[:2000]})
    client = anthropic.Anthropic(timeout=40.0, max_retries=1)
    resp = client.beta.messages.create(
        model=config.COPILOT_MODEL, max_tokens=1500,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=system, messages=msgs)
    if resp.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(resp.stop_reason)
    data = json.loads(next(b.text for b in resp.content if b.type == "text"))
    acts = [a for a in data.get("actions", []) if a in ACTIONS][:2]
    return {"answer": str(data.get("answer", ""))[:3000], "actions": [{"id": a, "label": ACTIONS[a]} for a in acts],
            "suggestions": [], "engine": "claude"}


def ask(message: str, history: list[dict] | None = None, state: dict | None = None) -> dict:
    message = (message or "").strip()[:2000]
    state = state or {}
    if not message:
        return {"answer": "Ask me anything about using SendEZ.", "actions": [], "suggestions": SUGGESTIONS, "engine": "offline"}
    use_claude = config.COPILOT_ENGINE == "claude" or (config.COPILOT_ENGINE == "auto" and os.environ.get("ANTHROPIC_API_KEY"))
    if use_claude:
        try:
            return answer_claude(message, history or [], state)
        except Exception:
            pass  # offline answers below
    return answer_offline(message, state)
