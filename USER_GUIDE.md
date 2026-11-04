# SendEZ: Features and How to Use It

## Feature list

### Reading your student list
- **Any Excel (.xlsx) or CSV file.** Column names don't need to follow any format.
- **Finds the right sheet** when the file has several, and **skips title rows** above the header and "Total" rows at the bottom.
- **Detects columns automatically:** student name (even when split into First Name and Last Name), email, backup email, student ID, event, date and category.
- **You can change any detected column** with one click.

### Checking and fixing the data
- **Flags problem rows** with their row number: missing or junk names (`#REF!`, `N/A`, digits), missing or invalid emails, duplicate students.
- **Fixes email typos:** `gmial.com → gmail.com`, `.con → .com`, extra spaces, `name(at)college`.
- **Uses the backup email** when the main one is broken.
- **Claim links:** a student with no email gets a private download link instead of being left out.
- **Optional name tidy-up:** `ROHAN MEHTA → Rohan Mehta`.

### Certificates
- **14 professional designs** for different kinds of events: University, Competitions & Awards, Tech/Hackathon, Corporate Training, Schools, Sports, Healthcare, NGO/Volunteer, Workshops, Arts & Culture, Government, Internships, Participation and Mono.
- **A different design per category:** for example, winners get Gold, volunteers get Appreciation, participants get Participation. This is set up automatically and you can change it.
- **Proper wording:** "for being the Winner at…", "for participating in…", "in appreciation of valuable contribution to…".
- **Your branding:** organization name, logo, two signatories with names, titles and signature images.
- **Use your own Canva design:** import a PDF or PNG and drag the name and other fields into place.
- **Live preview** of exactly what each student gets.

### Email
- **Sends from your own Gmail** (Outlook and other email services also supported).
- **A ready-written personal email per category:** winner congratulations, thank-you for participating, appreciation, completion. All of them can be edited.
- **Each student receives only their own certificate**, checked right before sending.
- **Daily limit protection:** stops safely at Gmail's limit and continues by itself when the limit resets (keep SendEZ open).
- **Second sending account:** add another Gmail or Outlook account in Settings to send about twice as many a day.
- **Schedule sending:** pick a date and time and SendEZ sends by itself.
- **Alerts:** a message, a short sound and a notification when sending finishes or pauses.
- **Never emails anyone twice**, even if you press Send again or upload the same sheet again.
- **Automatic retries** if the internet hiccups, and **switches to the backup email** if an address bounces.
- **Clear messages** if the Gmail password is wrong, without marking any student as failed.

### Proof and trust
- **Every certificate is digitally signed** and has a **QR code**. Scanning it shows "Verified, issued to [name]".
- **Edited or fake certificates fail** the check at the Verify page.
- **Batch seal:** one check confirms that no certificate in the batch was changed.
- **Activity log** of everything that happened to every student.

### Reports and records
- **Delivery report (CSV)** that opens in Excel: who got what, when, and any problems.
- **Download all certificates** as one ZIP, organized in folders by category.
- **Full history of every student:** tap a name to see it.

### Works everywhere
- **A real app for Mac and Windows** (download, open, done), plus Linux from source.
- **iPhone and iPad** through the Share online link, with layouts made for each. Use *Share → Add to Home Screen* to install it like an app.
- **Free public link** with the online start file, or a permanent website on a paid host.
- **Light and dark mode**, with a clean iOS-style design.
- **Password protected.** Gmail passwords are stored encrypted.

### Saving time
- **Google Sheets:** paste the link of a Google Sheet or Google Form responses instead of uploading a file.
- **Reuse a past event:** start a new event with the same designs, emails, signatories and logos as an earlier one.

### Help
- **Ask for help:** tap **Help** (bottom right, or the Help tab on iPhone) and ask in your own words, like "how do I connect Gmail?". It answers, and gives you a button that takes you straight to the right screen.

### Mahindra University clubs
- **Pick the club** when you start a new event (Kalakriti, Roboverse, Synolo, Outreach Club, The Erudite, Coding Ninjas, Art Felt, Palaestro, Celeste, AeiForia). Every certificate then shows "Mahindra University · Club" with the university and club logos side by side.
- **Logos** are uploaded once in **Settings → Mahindra University logos**.

### Demo
- **Chaos Test:** one button injects 15 real problems (broken names, typos, swapped files, a server that fails) and shows the system catching every one.

---

## How to use it

### First time only

1. **Get SendEZ.** Download it:
   - Mac: https://github.com/abdulazyz2008/SendEZ/releases/latest/download/SendEZ-Mac.zip → unzip → drag **SendEZ** into **Applications** → open it.
     If the Mac says it can't verify the app: **System Settings → Privacy & Security → Open Anyway** (first time only).
   - Windows: https://github.com/abdulazyz2008/SendEZ/releases/latest/download/SendEZ-Windows.zip → unzip → open the **SendEZ** folder → double-click **SendEZ.exe**.
     If you see "Windows protected your PC": **More info → Run anyway** (first time only).
2. **Create a password** when SendEZ asks. It protects your student data and your email account.
3. **Connect Gmail.** Click **Settings** in the left menu:
   - Choose **Gmail**.
   - Type your Gmail address.
   - Get an **App Password**: open myaccount.google.com/apppasswords (turn on 2-Step Verification first if asked), type "SendEZ", click Create, and copy the 16 letters.
   - Paste them into **App Password** and click **Save & Test**. You should see **✓ Connected**.
   - Type your own email next to **Send test** and click it. Check your inbox.
4. *(Optional)* In Settings, fill in **Organization defaults** (your college name and signatories) and click **Save**.

### Every event (4 steps)

**Start:** click **＋ New Event** in the left menu, type the **event name**, and drop your **Excel file** into the box.

**Step 1 · Students**
- Check **"What we found in your sheet"**: name, email, category and so on. If something is wrong, pick the correct column from its menu.
- Look at the numbers: **Ready** and **Need fixing**. Click **Need fixing** to see the problem rows. You can fix them in Excel and upload again, or leave them out.
- Click **Next: Certificates ›**.

**Step 2 · Certificates**
- Each category (Winner, Participant, Volunteer…) has a **design**, a **title** and an **email**.
- To change a design, click its picture and choose another one.
- Fill in **Certificate details**: organization name, logo, signatory names and titles, and signature images.
- Click **Next: Email ›**.

**Step 3 · Email**
- Check that **Sending account** shows your Gmail with ✓.
- Edit the message if you want (optional).
- Click **Next: Review & Send ›**.

**Step 4 · Review & Send**
- Read the **checklist**. Every line should have a ✓; a **!** means "worth a look".
- Use **Previous / Next** to see what different students will receive (certificate and email).
- Click **Send N Certificates**, then **OK**.
- Watch **Live activity**. When it's done:
  - Click **⬇ Delivery report (.csv)** to keep a record.
  - Click **⬇ All certificates (.zip)** to keep copies.

### Useful extras

| To… | Do this |
|---|---|
| See everything about one student | Step 1 → click their name |
| Give a student without email their certificate | Click their name → **Copy** the claim link → send it by WhatsApp or message |
| Resend to one student | Click their name → **Send Again** |
| Retry failed emails | Step 4 → **Retry Failed** |
| Use your own Canva design | Left menu → **Templates** → **Import from Canva** → follow the 4 steps → drag the fields → **Save** |
| Check a certificate is real | Scan its QR code, or left menu → **Verify a certificate** → upload the PDF |
| Open SendEZ on your phone or the operator's laptop | Left menu → **Share online** → **Start sharing** → send the link or scan the QR code |
| Get help with anything | **Help** button → type your question |
| Use a Google Sheet or Google Form responses | **New Event** → paste the link → **Import** (share the sheet as "Anyone with the link can view") |
| Start with last event's designs and emails | **New Event** → **Copy settings from** → pick the event |
| Send at a set time | Step 4 → **Schedule…** → pick date and time (keep SendEZ open, computer awake) |
| Send more than ~500 a day | **Settings** → **Second sending account** → Gmail address + App Password → **Save & Test** |
| Show the judges the safety checks | Left menu → **Run Chaos Test** |

### Good to know
- **Keep SendEZ open** while you (or your operator) use it. Closing it stops the app and the shared link.
- **Gmail sends about 500 emails a day.** If you have more students, SendEZ pauses and continues by itself when the limit resets, as long as SendEZ stays open. A second sending account doubles this.
- **Without Gmail connected**, SendEZ runs in **test mode**: certificates are saved on your computer, not emailed.
- **The Share online link** changes every time you start sharing.
