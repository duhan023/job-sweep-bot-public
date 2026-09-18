# Job Sweep Bot

An automated job search that runs itself in the cloud, for free.

Every morning it polls employer job boards for roles matching your resume, scores each one
from 1 to 100 against your actual skills, throws out anything it already sent you, and
emails you a ranked digest with direct apply links.

**You never edit a line of Python.** One interactive script asks you questions and writes
every configuration file for you.

Works for any field — engineering, nursing, finance, marketing, operations, logistics.
Nothing in the code assumes an industry.

---

## Setup in 3 steps (about 15 minutes, once)

### Step 1 — Fork this repo and add two secrets

1. Click **Fork** at the top right. You now have your own copy.
2. Recommended: **Settings → General → Change visibility → Private**. Your resume keywords
   and salary target will live in this repo.
3. Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

   | Name | Value |
   | ---- | ----- |
   | `GMAIL_USER` | The Gmail address that will send you the digest |
   | `GMAIL_APP_PASSWORD` | A 16-character Google App Password (see below) |

   **Getting the App Password:** Gmail refuses normal passwords for scripts. Turn on
   2-Step Verification at [myaccount.google.com/security](https://myaccount.google.com/security),
   then create one at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
   Name it `job-sweep-bot`, copy the 16 characters, remove the spaces. Google shows it once.
   You can revoke it any time without changing your real password.

### Step 2 — Run the setup script

```bash
git clone https://github.com/<your-username>/job-sweep-bot-public.git
cd job-sweep-bot-public
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python configure.py
```

It asks for:

- your resume file (`.pdf`, `.docx`, `.txt`, or pasted text) — it extracts your skill
  vocabulary and shows you the list so you can edit it
- the job titles you want, exactly as employers post them
- your locations, and whether remote counts
- the maximum years of experience a posting may demand
- a salary floor, or "Unlisted"
- your deal-breakers ("Exclude C2C", "No Security Clearance")
- **the companies to watch** — see [Sources](#sources) below
- where to email the digest, and what time to run it in your own timezone

It writes `config.json`, `areas/job-search-log.md`, and `.github/workflows/daily_sweep.yml`
with your local time already converted to a UTC cron expression.

### Step 3 — Test it, push it, turn it on

```bash
python sweep_public.py --dry-run --verbose     # scrapes and scores, sends nothing
git add -A && git commit -m "configure job sweep" && git push
```

Open the **Actions** tab → **Daily Job Sweep** → **Run workflow**. A green check and an
email in your inbox means you are done. It now runs on your schedule forever, with no
further input.

---

## Sources

The pipeline polls two things:

**1. Employer ATS boards — the main source.** Greenhouse, Lever and Ashby host the actual
job boards for a large share of companies. Their APIs are public, stable, and carry the
posting before it reaches any aggregator. Add companies you actually want to work at:

```json
"ats_boards": [
  { "type": "greenhouse", "token": "stripe"  },
  { "type": "lever",      "token": "netflix" },
  { "type": "ashby",      "token": "ramp"    }
]
```

The token is the company slug in their careers URL:

| Their careers page | Config entry |
| ------------------ | ------------ |
| `boards.greenhouse.io/stripe` | `{ "type": "greenhouse", "token": "stripe" }` |
| `jobs.lever.co/netflix` | `{ "type": "lever", "token": "netflix" }` |
| `jobs.ashbyhq.com/ramp` | `{ "type": "ashby", "token": "ramp" }` |

Ten to twenty companies is a good starting list. This is the single highest-leverage
setting in the whole config.

A shorthand string list is accepted too, if you find it easier to hand-edit:

```json
"ats_boards": ["greenhouse:stripe", "lever:netflix", "ashby:ramp"]
```

Entries with an unsupported type or a missing token are reported and skipped; they
do not stop the run.

**2. Google Careers API — off by default.** The code is still there, but Google's public
careers endpoints stopped returning a usable payload, so the source yields nothing as of
this writing. Set `sources.google_careers` to `true` if you want to try it. ATS boards are
the source that actually works.

**No LinkedIn or Indeed scraping.** Both prohibit it in their terms of service, both block
shared cloud IP ranges (including every GitHub Actions runner), and the data is worse than
going to the employer's own board. This repo does not ship it and will not accept a PR
adding it.

### Adding your own source

Every scraper is a function returning a `list[Job]`. To add one, write a fetcher in
`sweep_public.py` and register it in `scrape_ats()`:

```python
def _smartrecruiters(session, cfg, token: str) -> list[Job]:
    url = f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100"
    resp = get(session, url, cfg)
    if resp is None or resp.status_code != 200:
        return []
    out = []
    for item in resp.json().get("content", []):
        loc = item.get("location", {})
        out.append(
            Job(
                source=f"SmartRecruiters/{token}",
                company=item.get("company", {}).get("name", token),
                title=squash(item.get("name", ""), 200),
                location=squash(f"{loc.get('city','')}, {loc.get('region','')}", 120),
                url=item.get("ref", ""),
                description=squash(json.dumps(item.get("jobAd", {}))),
                posted=str(item.get("releasedDate", ""))[:10],
            )
        )
    return out
```

Then add `elif kind == "smartrecruiters": jobs.extend(_smartrecruiters(session, cfg, token))`
to `scrape_ats()` and the corresponding type to the validation list in `configure.py`.
The same pattern covers Workday (`{tenant}.wdN.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs`,
POST with a JSON body), Recruitee, Workable, and Personio. Filtering, scoring, dedup, email
and logging all work unchanged — they only care about the `Job` fields.

---

## What you get

An HTML table in your inbox, sorted by fit, showing `#`, Company, Role, Location, Salary,
**Fit Score /100**, Posted date, and a direct apply link.

```
[ 87 ]  Senior FP&A Analyst        Stripe          Remote (US)     $140k–$175k
[ 74 ]  Financial Analyst II       Chime           Austin, TX      Unlisted
[ 61 ]  Revenue Analyst            Ramp            New York, NY    $110k–$130k
```

### How the Fit Score works — no black box

| Component | Weight | What it measures |
| --------- | -----: | ---------------- |
| Resume keyword overlap | 55% | How much of your vocabulary the posting echoes back |
| Title precision | 30% | How closely the posting title matches your targets |
| Experience compatibility | 15% | Whether the required years fit your profile |

Keyword overlap is normalised against an expectation of roughly half your resume
vocabulary, capped at 30 terms and floored at 8 — so a long keyword list is not penalised
and a three-word list cannot trivially score 100. Terms found in an explicit skills or
certifications section of your resume are weighted 3× during extraction, because that is
the vocabulary a job description actually echoes.

Before anything is scored, hard filters drop postings that exceed your experience cap,
carry seniority terms above your level, fall below your salary floor, sit outside your
locations, or match a deal-breaker string.

### Architecture

```
ATS boards (Greenhouse / Lever / Ashby) ─┐
                                         ├─→ Hard filters ─→ Fit Score ─→ Dedup ledger ─→ Gmail SMTP
Google Careers API ──────────────────────┘    YOE cap          55/30/15     SHA-1 key        HTML digest
                                              seniority                     job-search-log
                                              salary floor                                  ↓
                                              dealbreakers                          auto-commit ledger
```

The ledger is the reason no database is needed: each dispatched role gets a 10-character
SHA-1 key derived from company + title + location, written to `areas/job-search-log.md`,
and the GitHub Actions runner commits that file back to your repo after every run.

---

## Files

| File | What it does |
| ---- | ------------ |
| `configure.py` | Interactive setup. Writes everything else. Re-run any time to change targets. |
| `sweep_public.py` | The engine. Scrapes, filters, scores, dedupes, emails, logs. Driven entirely by `config.json`. |
| `config.json` | Your settings. Generated by `configure.py`, git-ignored, never committed. |
| `config.example.json` | A filled-in example showing every available option. |
| `areas/job-search-log.md` | The dedup ledger. Every role ever sent. Delete a row to make it resurface. |
| `.github/workflows/daily_sweep.yml` | The cloud schedule. Generated for your timezone. |
| `generate_guide_pdf.py` | Builds a printable PDF manual: `python generate_guide_pdf.py` |

---

## Tuning

Everything is in `config.json`. Three knobs matter:

- **`targeting.titles`** — the biggest lever. Use employer phrasing, not your internal
  title. "Program Manager" and "Technical Project Manager" pull different pools. Add 2–5.
- **`sources.ats_boards`** — more companies, more coverage. Linear return.
- **`scoring.min_fit_score`** — too many emails? Raise from 55 to 70. Too few? Drop to 45.

```bash
python sweep_public.py --dry-run -v     # full run, nothing sent, shows why each job was dropped
python sweep_public.py --no-email       # score and log, skip SMTP
python sweep_public.py --limit 5        # cap results
python configure.py                     # re-run setup; dedup history is preserved
```

---

## Known limits — read these

- **GitHub cron is UTC and DST-blind.** Your local run time shifts by an hour across a DST
  change until you re-run `configure.py`. GitHub also queues scheduled jobs under load, so
  expect 0–20 minutes of drift.
- **Scheduled workflows pause after 60 days of repository inactivity.** The auto-commit of
  the log counts as activity, so this only bites during genuinely dead periods.
- **Coverage is a function of your board list,** not of the tool. This is a deliberate
  trade: fewer sources, all of them reliable and permitted, rather than broad scraping that
  breaks weekly and violates terms of service.
- **`posted_within_days` defaults to 30, not 7.** ATS boards only list roles that are
  currently open, so the date is when the posting went up, not whether it is still live.
  A 7-day window throws away open jobs. Lower it if you want only the freshest roles.
- **Seniority filtering is title-based and blunt.** "Account Executive" is dropped because
  "executive" is on the seniority list. If a term appears in one of your own target titles
  it is automatically un-banned, so add the title rather than editing the code.
- **Salary filtering only works when a number is published.** Most postings do not publish
  one; those pass the filter rather than being silently dropped.
- **This is a discovery tool, not an auto-applier.** It improves your timing and coverage.
  It does not improve your candidacy. If applications go out and interviews do not come
  back, the constraint is the resume or the target level, and automation will not fix that.

---

## Troubleshooting

| Symptom | Cause and fix |
| ------- | ------------- |
| `SMTPAuthenticationError` | You used your account password. Generate an App Password and paste it without spaces. |
| Zero results every run | Filters too tight, or no ATS boards. Run with `--verbose` to see the rejection reason for every dropped posting. |
| Everything dropped as `dealbreaker 'x'` | A deal-breaker string is matching common words. Leave the field blank rather than typing "nothing" or "none". |
| Most roles dropped as `requires Ny > cap` | Your experience cap is below what the market posts. A cap of 3 removes the bulk of mid-level postings; 5–6 is usually more realistic. |
| Workflow never fires | Unverified GitHub email, or 60 days of repo inactivity. Push any commit to re-enable. |
| Duplicate emails | The auto-commit step failed. Confirm the workflow has `permissions: contents: write`. |
| `externally-managed-environment` on install | Use a virtual environment: `python3 -m venv venv && source venv/bin/activate` |

---

## License

MIT — see [LICENSE](LICENSE). Fork it, change it, ship it.
