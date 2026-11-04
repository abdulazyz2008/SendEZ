# SendEZ

> Upload your student list once. Every student gets their own congratulatory email with the right certificate attached, and every certificate can be verified as genuine.

SendEZ reads any reasonable Excel or CSV file, works out who each student is and which category they belong to, picks the right certificate design for each category, and emails each student their own digitally signed PDF from your Gmail account. Before each email goes out, it checks that the attachment really belongs to that student.

---

## Download the app (Mac or Windows, nothing else to install)

| Computer | Download | Then |
|---|---|---|
| **Mac** (2020 or newer, macOS 11+) | [**SendEZ-Mac.zip**](https://github.com/abdulazyz2008/SendEZ/releases/latest/download/SendEZ-Mac.zip) | Unzip, drag **SendEZ** into **Applications**, open it |
| **Windows 10 / 11** | [**SendEZ-Windows.zip**](https://github.com/abdulazyz2008/SendEZ/releases/latest/download/SendEZ-Windows.zip) | Unzip, open the **SendEZ** folder, double-click **SendEZ.exe** |

These links never change; they always give the newest version. SendEZ opens in its own window. No Python, no Terminal.

**The first time only**, your computer may warn you because the app isn't from the App Store:
- **Mac:** *“Apple could not verify SendEZ…”* → press **Done**, open **System Settings → Privacy & Security**, scroll down, press **Open Anyway**.
- **Windows:** *“Windows protected your PC”* → press **More info** → **Run anyway**.

To let your operator or your phone use it: in the app press **Share online** → **Start sharing**, and send them the link.

Making a new version: Actions → **Desktop apps** → **Run workflow** → type a version such as `1.0.1` (or push a tag `v1.0.1`). GitHub builds both apps, checks that each one really works
(it sends a sample batch in test mode and verifies the seal), and publishes them at the links above.

## Features added beyond the original brief
The original task: read an Excel/CSV list → make a certificate for each person → email each person
their own certificate → track status and retry failures. SendEZ does all of that.
**Everything below was added on top**, with what it's useful for.

---

### 1. Trust and proof (certificates that can't be faked)

| Feature | What it does | Why it's useful |
|---|---|---|
| **Digitally signed certificates** | Every PDF is signed with a secret key only your SendEZ has. | Nobody can make a fake certificate that passes as yours. |
| **QR code on every certificate** | Scanning it opens a page saying "Verified, issued to [name]". | Employers or colleges can check a certificate in 2 seconds. |
| **Verify page for files** | Upload any PDF at `/verify`; edited files show "This file has been altered". | Catches students who change their name, rank or event on a PDF. |
| **Mapping Lock** | Right before sending, each attachment is checked against the student it's going to. | The wrong student can never receive someone else's certificate. |
| **Batch seal** | One signed "fingerprint" covers the whole batch; *Verify Batch Now* re-checks every file. | Proves nothing in the batch was changed after issuing. |
| **Chaos Test (demo)** | One button injects 15 real problems and shows each being caught. | Shows judges and faculty the safety checks working live. |

### 2. Handling messy spreadsheets

| Feature | What it does | Why it's useful |
|---|---|---|
| **Smart column detection** | Finds name, email, ID, category, date… with any column names, even First + Last name split. | No need to reformat the Excel file. |
| **Finds the right sheet and header** | Skips title rows, picks the sheet with emails, ignores "Total" rows. | Works with real club/college sheets as they are. |
| **Email typo fixing** | gmial.com → gmail.com, .con → .com, spaces, "name(at)college". | Fewer bounced emails. |
| **Backup email column** | If the main email is broken or bounces, uses the personal email. | More students actually receive their certificate. |
| **Claim links** | Students with no email get a private download link (they confirm their ID). | Nobody is left out. |
| **Problem rows flagged** | Junk names (#REF!, N/A), missing emails, duplicates are shown with their row number. | Easy to fix before sending. |
| **Name capitalisation** | ROHAN MEHTA → Rohan Mehta. | Certificates look professional. |
| **Google Sheets link** | Paste a Google Sheet / Google Form responses link instead of a file. | No downloading and uploading Excel files. |

### 3. Certificates and design

| Feature | What it does | Why it's useful |
|---|---|---|
| **14 original designs** | Academic, awards, tech, sports, arts, NGO, workshops, internships and more. | A fitting design for every kind of event, free to use. |
| **Different design per category** | Winners get Gold, volunteers get Appreciation, participants get Participation, automatically. | One upload handles every type of certificate. |
| **Proper wording** | "for being the Winner at…", "for participating in…", "in appreciation of…". | Each certificate reads correctly for its category. |
| **Your branding** | Organisation name, logo, two signatories with signature images. | Certificates look official. |
| **Canva import** | Bring your own Canva design and drag the name/QR into place. | Use any design you like. |
| **Live preview** | See exactly what each student will receive before sending. | No surprises after sending. |
| **Reuse a past event** | Start a new event with the same designs, emails, signatories and logos. | Repeat events take seconds to set up. |

### 4. Mahindra University clubs

| Feature | What it does | Why it's useful |
|---|---|---|
| **Club picker** | Kalakriti, Roboverse, Synolo, Outreach, The Erudite, Coding Ninjas, Art Felt, Palaestro, Celeste, AeiForia. | One tap sets up an event for a club. |
| **University + club branding** | Certificates show "Mahindra University · Club" with both logos side by side. | Consistent, official look across all clubs. |
| **Logo manager** | Upload the university and club logos once in Settings. | Every future certificate uses them automatically. |
| **Club report** | Per club and semester: events, certificates, students reached, repeat attendees, departments, ratings. Download as PDF or CSV. | Ready-made report for faculty and club heads. |

### 5. Email and sending

| Feature | What it does | Why it's useful |
|---|---|---|
| **Ready-written emails per category** | Winner congratulations, participation thanks, appreciation, completion. | Personal emails without writing them yourself. |
| **Gmail, Outlook or any email** | Sends from your own account. | Emails come from you, not an unknown service. |
| **Daily limit protection** | Stops safely at Gmail's limit (~500/day). | Your Gmail account doesn't get blocked. |
| **Automatic continue** | After the daily limit, continues by itself when the limit resets. | Big events finish without you pressing Send every day. |
| **Second sending account** | When the first account is full, continues from a second Gmail/Outlook. | About twice as many emails a day. |
| **Scheduled sending** | Pick a date and time; SendEZ sends by itself (keeps the computer awake). | Send at 9 AM the morning after the event. |
| **Never emails anyone twice** | Pressing Send again or re-uploading skips students who already got it. | No embarrassing duplicates. |
| **Automatic retries and port fallback** | Retries internet hiccups; switches port when a campus network blocks email. | Fewer failures on college Wi-Fi. |
| **Clear Gmail password errors** | Explains exactly what's wrong (e.g. App Password needed) without failing students. | Easy to fix setup problems. |

### 6. For students

| Feature | What it does | Why it's useful |
|---|---|---|
| **Add to LinkedIn** | One button on the verify page adds the certificate to the student's LinkedIn profile, details pre-filled. | Students show off their achievement instantly. |
| **Share with preview card** | Sharing the link on WhatsApp/LinkedIn shows a card with their name and certificate. | Looks great when shared. |
| **Feedback stars** | The email ends with 5 stars; students tap one to rate the event (results are anonymous). | Clubs learn what students thought, with no forms. |

### 7. Ease of use and access

| Feature | What it does | Why it's useful |
|---|---|---|
| **Desktop app for Mac and Windows** | Download, open, done. No Python, no Terminal. | Anyone can install it. |
| **Permanent download links** | The same links always give the newest version. | Send the link once. |
| **Share online** | One button gives a public link so your operator or your phone can use SendEZ. | Work from any device, anywhere. |
| **iPhone and iPad layouts** | Bottom tab bar, big buttons, full-screen panels. | Comfortable on phones and tablets. |
| **Help chat** | Ask in your own words ("what is an App Password?"); it answers and opens the right screen. | No manual needed. |
| **Done/paused alerts** | Message, sound, notification and (on phones) vibration when sending finishes or pauses. | You can do other work while it sends. |
| **Professional, clean design** | Light and dark mode, Gmail/Outlook icons, step-by-step screens. | Looks trustworthy; easy to follow. |
| **Password protection** | App password, encrypted email passwords, first-time setup only on the main computer. | Student data and your email stay safe. |

### 8. Records

| Feature | What it does | Why it's useful |
|---|---|---|
| **Delivery report (CSV)** | Who got what, when, and any problems. Opens in Excel. | Proof of delivery for your records. |
| **All certificates as ZIP** | Every PDF, sorted into folders by category. | Easy archive and printing. |
| **Full history per student** | Tap a name to see every step that happened to them. | Answer "did I get it?" questions in seconds. |
| **Live activity log** | Everything that happens, as it happens. | Full transparency while sending. |

## Free public link without the app (no account, no card)

Double-click **`start-online-windows.bat`** / **`start-online-mac.command`** / **`start-online-linux.sh`**.
SendEZ starts on your computer and prints a public link like `https://some-words.trycloudflare.com`, using Cloudflare's free Quick Tunnel.
Anyone can open that link on any device, anywhere. The first time, it downloads Cloudflare's tool (once) and asks you to set a password.

- Free and instant, but **your computer must stay on with the window open**.
- **The link changes each time you start.** QR codes on certificates made in one session stop verifying after you restart.
  For permanent links, use the paid option below.

## Permanent website (one link, paid)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/abdulazyz2008/SendEZ)

Press the button, sign in to Render, type a password for **ADMIN_PASSWORD**, and press **Deploy Blueprint**. A few minutes later you get a permanent link like `https://sendez-xxxx.onrender.com` that works on any phone, tablet or computer. It uses a paid Render instance with a 1 GB disk, because free instances block email ports and delete data on restart. Check the exact price on Render before confirming.

## Or run it from the source code (advanced)

**You need Python 3.10 or newer** (free from [python.org](https://www.python.org/downloads/); on Windows tick *“Add Python to PATH”* during install).

| Computer | Do this |
|---|---|
| **Windows** | Double-click `start-windows.bat` |
| **Mac** | Double-click `start-mac.command` (first time: right-click → Open) |
| **Linux** | Run `./start-linux.sh` |

The first start takes 1–3 minutes to set itself up (needs internet once). Then your browser opens SendEZ. Keep the black window open while you use it.

**iPhone, iPad, Android:** connect to the same Wi-Fi as the computer, then in SendEZ tap **Open on phone/iPad** and scan the QR code. Use *Share → Add to Home Screen* to install it like an app.

## First-time setup (2 minutes)

1. **Create a password** (only possible on the computer running SendEZ).
2. **Connect Gmail:** Settings → Gmail → your address + a **Gmail App Password**:
   - turn on [2-Step Verification](https://myaccount.google.com/signinoptions/twosv), then open [App passwords](https://myaccount.google.com/apppasswords), type “SendEZ”, and copy the 16 letters.
   - Your normal Gmail password will **not** work; Google requires an App Password.
3. Press **Save & Test**, then **Send test** to your own address. If the test arrives with its sample certificate, you’re ready.

Until Gmail is connected, SendEZ runs in **test mode**: emails and certificates are saved in `data/outbox/` instead of being sent.

## Issue certificates: 4 steps

| Step | What happens |
|---|---|
| **1 · Students** | Upload the Excel/CSV. SendEZ finds the right sheet, skips title rows, and detects the name (even split across *First Name* / *Last Name*), email, backup email, ID, date and **category** columns. You can correct any of them. Problem rows are flagged with their row number. |
| **2 · Certificates** | Each category value (e.g. *Winner, Runner Up, Volunteer, Participant*) gets a design, a title and an email, guessed from its name: winners get **Gold Excellence** and a congratulations email, volunteers get *Certificate of Appreciation*, and so on. Click any design to choose another, add your organization name, logo and signatories. |
| **3 · Email** | Check the sending account and edit the default message if you like. Each category already has a ready-written email (winner, participation, appreciation, completion). |
| **4 · Review & Send** | A checklist, then a preview of exactly what any student will receive (certificate + email). Press **Send**. Progress, a live activity log, a CSV delivery report and a ZIP of all certificates are on the same page. |

## Certificate designs

14 original designs, made for this app, so there are no licensing issues:

| Niche | Design |
|---|---|
| Academic | University Classic |
| Competitions & Awards | Gold Excellence |
| Technology | Tech Hackathon |
| Corporate & Training | Corporate Modern |
| Schools | School Stars |
| Sports | Sports Champion |
| Healthcare | Medical & CME |
| NGO & Community | Volunteer Appreciation |
| Workshops & Seminars | Workshop Minimal |
| Arts & Culture | Arts & Culture |
| Government & Institutions | Official Formal |
| Internships | Internship |
| General | Participation, Mono Minimal |

**Your own design from Canva:** Templates → *Import from Canva*. Design or pick a certificate in your Canva account, delete the sample name, then download it as **PDF Standard** or PNG and drop it in. Drag the name, description and QR code to where they belong, then press Save. Canva’s own template library can’t be bundled into another app (their license forbids it), but anything you export from your account works.

## Time-savers

| Feature | Where |
|---|---|
| **Google Sheets / Google Forms** | New Event → paste the sheet's link → Import (share the sheet as "Anyone with the link can view") |
| **Reuse a past event** | New Event → *Copy settings from* → pick the earlier event: designs, emails, signatories and logos come along |
| **Schedule sending** | Review & Send → **Schedule…** → pick a date and time (keep SendEZ open; it keeps the computer awake) |
| **Automatic continue** | After the daily limit, sending continues by itself when the limit resets (Settings switch) |
| **Second sending account** | Settings → Second sending account: used when the first reaches its daily limit; replies still go to the first |
| **Done / paused alerts** | A message, a short sound, a notification if you're in another window, and a small vibration on phones |
| **Help chat** | The **Help** button answers how-to questions and takes you to the right screen |

## Handling the messy cases

| Problem in the sheet | What SendEZ does |
|---|---|
| Title rows above the header, several sheets, a “Total” row | Finds the real header and the sheet that has emails; skips the footer |
| `name@gmial.com`, `.con`, spaces, `name(at)college` | Fixes it and shows what it changed |
| Broken main email, valid personal email | Uses the backup column |
| No email at all | Creates a private **claim link** (the student confirms their ID to download) |
| `#REF!`, `N/A`, digits in a name, blank name | Blocks that row and shows why |
| Same student twice | Blocks the duplicate (by ID, or by name + email) |
| ALL CAPS names | Optional switch: *ROHAN MEHTA → Rohan Mehta* |
| Gmail daily limit (~500/day) | Stops safely at your limit and **continues by itself** when the limit resets (keep SendEZ open). Add a **second sending account** to send about twice as many a day. Nobody gets two emails. |
| Wrong App Password | Refuses to start and tells you exactly what to fix, without marking anyone as failed |
| Network blocks port 587 | Falls back to port 465 automatically |
| A mailbox bounces | Retries with the student’s backup email |
| Send pressed twice / sheet uploaded again | Already-delivered students are skipped |

## Why it can be trusted

- **Mapping Lock:** each PDF carries a hidden keyed hash of *student ID + email + event*. Right before sending, it is recomputed from the recipient, together with the file’s hash and signature. A wrong attachment is blocked, rebuilt from the source data, and checked again.
- **Signed certificates + QR:** every certificate is signed (Ed25519). Scanning the QR opens *“Verified, issued to X for Y”*. Uploading an edited PDF at `/verify` shows *“This file has been altered”*.
- **Batch seal:** a signed Merkle root covers the whole batch. *Proof → Verify Batch Now* re-checks every file.
- **Chaos Test** (sidebar, demo): injects 15 real faults into a sandbox batch and shows each one being contained. Results come from opening every delivered email, not from what the app says about itself.

**About the QR link:** QR codes open the address in *Settings → Verification link*. By default that is your computer on the local Wi-Fi, which is fine for a demo. For certificates students will show to employers later, host SendEZ online (any server that runs Python) and enter its public address **before** creating certificates.

## Tests

```bash
pip install -r requirements.txt
python -m pytest -q
```

38 tests cover the messy Excel reader, the email and name rules, all 14 designs, Canva import and the field editor, branding, the full send flow (with an audit of every delivered email), claim links, swapped and tampered files, database tampering, Gmail login failure, the port 465 fallback, Share online (with a stand-in tunnel tool), Google Sheets import, reusing a past event, scheduled sending, automatic continue after the daily limit, a second sending account, the LinkedIn button and share card, event feedback stars, the daily limit with pause and resume, provider quota errors, bounces with a switch to the backup email, retries, Resend, the copilot and the in-app Help assistant (offline and Claude), Mahindra University club branding, the full Chaos Test, and a 120-student parallel batch.

`sample/tech_fest_2026.xlsx` is a deliberately messy workbook for trying everything.

## Files

```
desktop.py                         the desktop app (own window; packaged with packaging/sealsend.spec)
run.py, start-*.{bat,command,sh}   one-click start from source on Windows / macOS / Linux
sealsend/
  data.py           spreadsheet reading, column + category detection, validation, email self-healing
  templates_lib.py  the 14 designs, fonts, text layout, imported (Canva) templates
  certificate.py    rendering, previews, imports, proof metadata
  pipeline.py       generate → sign → seal → verify-or-heal → deliver, daily limit, pause/resume
  mailer.py         Gmail / Outlook / SMTP / Resend / test mode, email wording per category
  settings.py       in-app settings (App Password encrypted at rest), admin password
  crypto.py         Ed25519, Mapping Lock, Merkle seal
  copilot.py        plain-English rules (Claude or offline)
  assistant.py      in-app Help chat (offline answers, or Claude when a key is set)
  clubs.py          Mahindra University clubs and combined logos
  tunnel.py         Share online (Cloudflare Quick Tunnel)
  gsheets.py        import a student list from a Google Sheets link
  keepawake.py      keeps the computer awake while sending or a send is scheduled
  chaos.py          Chaos Test
  main.py           web server, public /verify and /claim pages
  static/           the app (iOS-style, light and dark, iPhone and iPad layouts)
  fonts/            bundled open-licensed fonts (SIL OFL), so certificates look identical everywhere
```
