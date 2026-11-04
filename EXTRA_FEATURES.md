# SendEZ: features added beyond the original instructions

The original task: read an Excel/CSV list → make a certificate for each person → email each person
their own certificate → track status and retry failures. SendEZ does all of that.
**Everything below was added on top**, with what it's useful for.

---

## 1. Trust and proof (certificates that can't be faked)

| Feature | What it does | Why it's useful |
|---|---|---|
| **Digitally signed certificates** | Every PDF is signed with a secret key only your SendEZ has. | Nobody can make a fake certificate that passes as yours. |
| **QR code on every certificate** | Scanning it opens a page saying "Verified, issued to [name]". | Employers or colleges can check a certificate in 2 seconds. |
| **Verify page for files** | Upload any PDF at `/verify`; edited files show "This file has been altered". | Catches students who change their name, rank or event on a PDF. |
| **Mapping Lock** | Right before sending, each attachment is checked against the student it's going to. | The wrong student can never receive someone else's certificate. |
| **Batch seal** | One signed "fingerprint" covers the whole batch; *Verify Batch Now* re-checks every file. | Proves nothing in the batch was changed after issuing. |
| **Chaos Test (demo)** | One button injects 15 real problems and shows each being caught. | Shows judges and faculty the safety checks working live. |

## 2. Handling messy spreadsheets

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

## 3. Certificates and design

| Feature | What it does | Why it's useful |
|---|---|---|
| **14 original designs** | Academic, awards, tech, sports, arts, NGO, workshops, internships and more. | A fitting design for every kind of event, free to use. |
| **Different design per category** | Winners get Gold, volunteers get Appreciation, participants get Participation, automatically. | One upload handles every type of certificate. |
| **Proper wording** | "for being the Winner at…", "for participating in…", "in appreciation of…". | Each certificate reads correctly for its category. |
| **Your branding** | Organisation name, logo, two signatories with signature images. | Certificates look official. |
| **Canva import** | Bring your own Canva design and drag the name/QR into place. | Use any design you like. |
| **Live preview** | See exactly what each student will receive before sending. | No surprises after sending. |
| **Reuse a past event** | Start a new event with the same designs, emails, signatories and logos. | Repeat events take seconds to set up. |

## 4. Mahindra University clubs

| Feature | What it does | Why it's useful |
|---|---|---|
| **Club picker** | Kalakriti, Roboverse, Synolo, Outreach, The Erudite, Coding Ninjas, Art Felt, Palaestro, Celeste, AeiForia. | One tap sets up an event for a club. |
| **University + club branding** | Certificates show "Mahindra University · Club" with both logos side by side. | Consistent, official look across all clubs. |
| **Logo manager** | Upload the university and club logos once in Settings. | Every future certificate uses them automatically. |
| **Club report** | Per club and semester: events, certificates, students reached, repeat attendees, departments, ratings. Download as PDF or CSV. | Ready-made report for faculty and club heads. |

## 5. Email and sending

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

## 6. For students

| Feature | What it does | Why it's useful |
|---|---|---|
| **Add to LinkedIn** | One button on the verify page adds the certificate to the student's LinkedIn profile, details pre-filled. | Students show off their achievement instantly. |
| **Share with preview card** | Sharing the link on WhatsApp/LinkedIn shows a card with their name and certificate. | Looks great when shared. |
| **Feedback stars** | The email ends with 5 stars; students tap one to rate the event (results are anonymous). | Clubs learn what students thought, with no forms. |

## 7. Ease of use and access

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

## 8. Records

| Feature | What it does | Why it's useful |
|---|---|---|
| **Delivery report (CSV)** | Who got what, when, and any problems. Opens in Excel. | Proof of delivery for your records. |
| **All certificates as ZIP** | Every PDF, sorted into folders by category. | Easy archive and printing. |
| **Full history per student** | Tap a name to see every step that happened to them. | Answer "did I get it?" questions in seconds. |
| **Live activity log** | Everything that happens, as it happens. | Full transparency while sending. |
