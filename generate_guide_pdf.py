#!/usr/bin/env python3
"""
generate_guide_pdf.py - builds the user manual PDF.

    pip install reportlab
    python generate_guide_pdf.py                 # -> Job_Sweep_Bot_Guide.pdf
    python generate_guide_pdf.py --out docs/manual.pdf

No other project files are required; this script is self-contained so it can be
run before or after configuration.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        HRFlowable,
        KeepTogether,
        ListFlowable,
        ListItem,
        PageBreak,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )
except ImportError:
    print("Missing dependency: reportlab. Run `pip install reportlab`.", file=sys.stderr)
    raise

TITLE = "Job Sweep Bot: Turn-Key Automated Job Search Architecture"

INK = colors.HexColor("#1f2328")
MUTED = colors.HexColor("#57606a")
ACCENT = colors.HexColor("#1f6feb")
DARK = colors.HexColor("#0d1117")
RULE = colors.HexColor("#d8dee4")
PANEL = colors.HexColor("#f2f4f7")


# --------------------------------------------------------------------------
# Styles
# --------------------------------------------------------------------------

def build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    s: dict[str, ParagraphStyle] = {}

    s["cover_title"] = ParagraphStyle(
        "cover_title", parent=base["Title"], fontName="Helvetica-Bold",
        fontSize=26, leading=32, textColor=INK, alignment=TA_LEFT, spaceAfter=10,
    )
    s["cover_sub"] = ParagraphStyle(
        "cover_sub", parent=base["Normal"], fontName="Helvetica",
        fontSize=12.5, leading=18, textColor=MUTED, alignment=TA_LEFT,
    )
    s["h1"] = ParagraphStyle(
        "h1", parent=base["Heading1"], fontName="Helvetica-Bold",
        fontSize=16, leading=20, textColor=INK, spaceBefore=18, spaceAfter=8,
    )
    s["h2"] = ParagraphStyle(
        "h2", parent=base["Heading2"], fontName="Helvetica-Bold",
        fontSize=12, leading=16, textColor=ACCENT, spaceBefore=12, spaceAfter=5,
    )
    s["body"] = ParagraphStyle(
        "body", parent=base["BodyText"], fontName="Helvetica",
        fontSize=10, leading=14.5, textColor=INK, spaceAfter=7,
    )
    s["bullet"] = ParagraphStyle(
        "bullet", parent=s["body"], fontSize=10, leading=14, spaceAfter=3,
    )
    s["mono"] = ParagraphStyle(
        "mono", parent=base["BodyText"], fontName="Courier",
        fontSize=9, leading=13, textColor=INK, backColor=PANEL,
        borderPadding=6, spaceBefore=4, spaceAfter=9,
    )
    s["note"] = ParagraphStyle(
        "note", parent=s["body"], fontSize=9, leading=13, textColor=MUTED,
    )
    s["flow_box"] = ParagraphStyle(
        "flow_box", parent=base["Normal"], fontName="Helvetica-Bold",
        fontSize=9, leading=12, textColor=colors.white, alignment=TA_CENTER,
    )
    s["flow_caption"] = ParagraphStyle(
        "flow_caption", parent=base["Normal"], fontName="Helvetica",
        fontSize=8, leading=10.5, textColor=MUTED, alignment=TA_CENTER,
    )
    s["th"] = ParagraphStyle(
        "th", parent=base["Normal"], fontName="Helvetica-Bold",
        fontSize=9, leading=12, textColor=colors.white,
    )
    s["td"] = ParagraphStyle(
        "td", parent=base["Normal"], fontName="Helvetica",
        fontSize=9, leading=12.5, textColor=INK,
    )
    return s


S = build_styles()


def bullets(items: list[str]) -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(t, S["bullet"]), leftIndent=14) for t in items],
        bulletType="bullet", bulletChar="•", leftIndent=12, bulletFontSize=8,
        spaceBefore=2, spaceAfter=8,
    )


def numbered(items: list[str]) -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(t, S["bullet"]), leftIndent=16) for t in items],
        bulletType="1", leftIndent=14, spaceBefore=2, spaceAfter=8,
    )


def code(text: str) -> Paragraph:
    return Paragraph(text.replace("<", "&lt;").replace("\n", "<br/>"), S["mono"])


def table(headers: list[str], rows: list[list[str]], widths: list[float]) -> Table:
    data = [[Paragraph(h, S["th"]) for h in headers]]
    data += [[Paragraph(cell, S["td"]) for cell in row] for row in rows]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), DARK),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PANEL]),
                ("GRID", (0, 0), (-1, -1), 0.4, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return t


def architecture_flow(width: float) -> Table:
    """Five-stage pipeline diagram drawn as a single-row table of boxes."""
    stages = [
        ("1. SOURCES", "Greenhouse / Lever<br/>Ashby boards<br/>Google Careers API"),
        ("2. DYNAMIC FILTERS", "YOE cap<br/>Seniority terms<br/>Salary floor<br/>Deal-breakers"),
        ("3. FIT SCORE", "Resume keywords 55%<br/>Title precision 30%<br/>Experience 15%"),
        ("4. DEDUP LEDGER", "SHA-1 key per role<br/>areas/job-search-log.md<br/>Never sent twice"),
        ("5. DISPATCH", "Ranked HTML digest<br/>Gmail SSL SMTP<br/>Auto-commit ledger"),
    ]
    arrow = Paragraph("&rarr;", ParagraphStyle("ar", fontName="Helvetica-Bold",
                                               fontSize=14, textColor=ACCENT, alignment=TA_CENTER))
    cells = []
    for i, (label, detail) in enumerate(stages):
        block = Table(
            [[Paragraph(label, S["flow_box"])], [Paragraph(detail, S["flow_caption"])]],
            colWidths=[width * 0.168],
        )
        block.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, 0), DARK),
                    ("BACKGROUND", (0, 1), (0, 1), PANEL),
                    ("BOX", (0, 0), (-1, -1), 0.5, RULE),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        cells.append(block)
        if i < len(stages) - 1:
            cells.append(arrow)

    widths = []
    for i in range(len(cells)):
        widths.append(width * 0.168 if i % 2 == 0 else width * 0.04)
    outer = Table([cells], colWidths=widths)
    outer.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return outer


# --------------------------------------------------------------------------
# Page furniture
# --------------------------------------------------------------------------

def draw_chrome(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(0.9 * inch, 0.78 * inch, LETTER[0] - 0.9 * inch, 0.78 * inch)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.9 * inch, 0.6 * inch, "Job Sweep Bot")
    canvas.drawRightString(LETTER[0] - 0.9 * inch, 0.6 * inch, f"Page {doc.page}")
    canvas.restoreState()


def build_document(out_path: str) -> None:
    doc = BaseDocTemplate(
        out_path,
        pagesize=LETTER,
        leftMargin=0.9 * inch,
        rightMargin=0.9 * inch,
        topMargin=0.85 * inch,
        bottomMargin=0.95 * inch,
        title=TITLE,
        author="Job Sweep Bot",
        subject="Automated job search pipeline setup manual",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="std", frames=[frame], onPage=draw_chrome)])
    doc.build(story(doc.width))


# --------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------

def story(width: float) -> list:
    st: list = []

    # ---------------- Cover ----------------
    st.append(Spacer(1, 1.4 * inch))
    st.append(Paragraph(TITLE, S["cover_title"]))
    st.append(HRFlowable(width="100%", thickness=2, color=ACCENT, spaceBefore=4, spaceAfter=14))
    st.append(
        Paragraph(
            "A fork-and-run pipeline that scrapes employer career APIs every morning, "
            "scores each posting against your own resume, and emails you a ranked digest "
            "of only the roles you have not already seen. Runs free on GitHub Actions. "
            "No servers, no subscriptions, and no Python editing.",
            S["cover_sub"],
        )
    )
    st.append(Spacer(1, 0.4 * inch))
    st.append(
        table(
            ["What you need", "Detail"],
            [
                ["Time to set up", "About 15 minutes, once"],
                ["Cost", "$0 — GitHub Actions free tier and Gmail SMTP"],
                ["Coding required", "None. One interactive script writes every config file."],
                ["Works for", "Any field: engineering, nursing, finance, marketing, operations"],
            ],
            [width * 0.28, width * 0.72],
        )
    )
    st.append(Spacer(1, 0.3 * inch))
    st.append(Paragraph(f"Generated {date.today().strftime('%B %d, %Y')}", S["note"]))
    st.append(PageBreak())

    # ---------------- 1. System overview ----------------
    st.append(Paragraph("1. System Overview", S["h1"]))
    st.append(
        Paragraph(
            "The bot is a five-stage pipeline. Each stage is deliberately dumb and "
            "inspectable — there is no black box and no model deciding your career for you. "
            "The entire run finishes in under two minutes on a free GitHub runner.",
            S["body"],
        )
    )
    st.append(Spacer(1, 6))
    st.append(architecture_flow(width))
    st.append(Spacer(1, 14))

    st.append(Paragraph("Stage detail", S["h2"]))
    st.append(
        bullets(
            [
                "<b>Sources.</b> Employer ATS boards (Greenhouse, Lever, Ashby) plus the Google "
                "Careers API. Postings appear on a company's own board before they propagate "
                "to aggregators. No LinkedIn or Indeed scraping: both prohibit it, both block "
                "cloud IP ranges, and the data is worse than the source.",
                "<b>Dynamic filters.</b> Hard gates run before scoring: years-of-experience cap, "
                "seniority terms above your level, salary floor, location match, and your own "
                "deal-breaker strings. A posting that fails any gate is never scored or shown.",
                "<b>Fit Score (1–100).</b> 55% keyword overlap against the vocabulary extracted "
                "from your resume, 30% title-match precision, 15% experience compatibility. "
                "The score ranks your queue; it does not decide anything for you.",
                "<b>Dedup ledger.</b> Every dispatched role is written to "
                "<font face='Courier'>areas/job-search-log.md</font> with a 10-character SHA-1 key "
                "derived from company + title + location. The same role never reaches your inbox twice.",
                "<b>Dispatch.</b> A responsive HTML table goes out over Gmail SSL SMTP, sorted by "
                "Fit Score descending. The runner then commits the updated ledger back to your repo, "
                "so the dedup state persists across runs with no database.",
            ]
        )
    )

    st.append(Paragraph("What it deliberately does not do", S["h2"]))
    st.append(
        bullets(
            [
                "It does not auto-apply. Mass auto-application destroys your conversion rate and "
                "gets accounts flagged.",
                "It does not rewrite your resume per posting. It surfaces the keyword overlap so "
                "you can decide.",
                "It does not guarantee completeness. Coverage is a function of the company "
                "boards you list, not of the tool. Treat the digest as a high-signal sample, "
                "not a census of the market.",
            ]
        )
    )
    st.append(PageBreak())

    # ---------------- 2. Prerequisites ----------------
    st.append(Paragraph("2. Prerequisites Checklist", S["h1"]))
    st.append(
        Paragraph(
            "Two accounts and one password. Both are free and take under ten minutes combined.",
            S["body"],
        )
    )

    st.append(Paragraph("A. Free GitHub account", S["h2"]))
    st.append(
        numbered(
            [
                "Go to <b>github.com/signup</b> and create an account. The free tier includes "
                "2,000 Actions minutes per month; this pipeline uses roughly 40.",
                "Verify your email address — scheduled workflows will not run until you do.",
                "Nothing else is needed. You will not install anything locally except Python, "
                "and only if you want to run the setup script on your own machine.",
            ]
        )
    )

    st.append(Paragraph("B. Google App Password (requires 2-Step Verification)", S["h2"]))
    st.append(
        Paragraph(
            "Google blocks plain-password SMTP logins. You need a 16-character App Password, "
            "which is scoped to this one script and revocable at any time without changing "
            "your account password.",
            S["body"],
        )
    )
    st.append(
        numbered(
            [
                "Open <b>myaccount.google.com/security</b> and turn on <b>2-Step Verification</b>. "
                "App Passwords are not offered until 2FA is active.",
                "Go to <b>myaccount.google.com/apppasswords</b>.",
                "Name it <font face='Courier'>job-sweep-bot</font> and click Create.",
                "Copy the 16-character string shown. Google displays it exactly once. "
                "Remove the spaces when you paste it into GitHub.",
                "If the App Passwords page is unavailable, you are on a Workspace account whose "
                "admin has disabled it — use a personal Gmail address for the sender instead.",
            ]
        )
    )
    st.append(
        Paragraph(
            "Security note: the App Password grants mail-send access to that Gmail account. "
            "It lives only in GitHub encrypted secrets, never in config.json, and never in a "
            "commit. Revoke it from the same page if your repository ever becomes public by "
            "accident.",
            S["note"],
        )
    )
    st.append(PageBreak())

    # ---------------- 3. Setup ----------------
    st.append(Paragraph("3. Step-by-Step Setup", S["h1"]))

    st.append(Paragraph("Step 1 — Fork the repository", S["h2"]))
    st.append(
        numbered(
            [
                "Open the <font face='Courier'>job-sweep-bot-public</font> repository and click "
                "<b>Fork</b> (top right). You now own a private copy under your account.",
                "Recommended: Settings &rarr; General &rarr; Danger Zone &rarr; <b>Change "
                "visibility</b> &rarr; Private. Your resume keywords and target salary live in "
                "this repo.",
                "Clone it locally, or use GitHub's built-in Codespaces terminal if you would "
                "rather not install Python:",
            ]
        )
    )
    st.append(code("git clone https://github.com/&lt;your-username&gt;/job-sweep-bot-public.git\ncd job-sweep-bot-public\npip install -r requirements.txt"))

    st.append(Paragraph("Step 2 — Add your two GitHub Secrets", S["h2"]))
    st.append(
        Paragraph(
            "In your fork: <b>Settings &rarr; Secrets and variables &rarr; Actions &rarr; "
            "New repository secret</b>. Add both, exactly as named:",
            S["body"],
        )
    )
    st.append(
        table(
            ["Secret name", "Value"],
            [
                ["GMAIL_USER", "The Gmail address that sends the digest, e.g. you@gmail.com"],
                ["GMAIL_APP_PASSWORD", "The 16-character App Password from section 2B, spaces removed"],
            ],
            [width * 0.32, width * 0.68],
        )
    )
    st.append(
        Paragraph(
            "Secrets are encrypted at rest and masked in workflow logs. They are not visible to "
            "anyone who forks your repository.",
            S["note"],
        )
    )

    st.append(Paragraph("Step 3 — Run the setup script", S["h2"]))
    st.append(code("python configure.py"))
    st.append(
        Paragraph(
            "The script asks for your resume file, target titles, locations, experience cap, "
            "salary floor, deal-breakers, destination email, and preferred run time. It then "
            "writes three files: <font face='Courier'>config.json</font>, "
            "<font face='Courier'>areas/job-search-log.md</font>, and "
            "<font face='Courier'>.github/workflows/daily_sweep.yml</font> with your local time "
            "converted to a UTC cron expression.",
            S["body"],
        )
    )
    st.append(
        bullets(
            [
                "<b>Titles matter most.</b> Use the phrasing employers post, not your internal "
                "title. 'Program Manager' and 'Technical Project Manager' pull different pools.",
                "<b>Keyword review.</b> The script shows what it extracted from your resume and "
                "lets you delete noise or add terms before writing the config.",
                "<b>Tune later.</b> Everything is plain JSON. Raising "
                "<font face='Courier'>scoring.min_fit_score</font> from 55 to 70 is the fastest "
                "way to cut digest volume.",
            ]
        )
    )

    st.append(Paragraph("Step 4 — Test the runner", S["h2"]))
    st.append(code("python sweep_public.py --dry-run --verbose"))
    st.append(
        Paragraph(
            "A dry run scrapes and scores but sends nothing and writes nothing. Verbose mode "
            "prints the rejection reason for every dropped posting, which is how you tell an "
            "over-tight filter from a genuinely empty market. When the output looks right:",
            S["body"],
        )
    )
    st.append(code('git add -A\ngit commit -m "configure job sweep"\ngit push'))
    st.append(
        Paragraph(
            "Then open the <b>Actions</b> tab, select <b>Daily Job Sweep</b>, and click "
            "<b>Run workflow</b>. A green check plus an email in your inbox means the pipeline "
            "is live. From that point it runs on your schedule with no further input.",
            S["body"],
        )
    )

    st.append(Paragraph("Troubleshooting", S["h2"]))
    st.append(
        KeepTogether(
            table(
                ["Symptom", "Cause and fix"],
                [
                    [
                        "SMTPAuthenticationError",
                        "You used your account password. Generate an App Password (section 2B) "
                        "and re-paste it without spaces.",
                    ],
                    [
                        "Zero results every run",
                        "Filters are too tight. Lower min_fit_score, raise the YOE cap, or drop "
                        "the salary floor — most postings do not publish salary, and a floor only "
                        "excludes postings where a number was actually found.",
                    ],
                    [
                        "Thin or empty digest",
                        "Almost always an empty sources.ats_boards list. Google Careers alone "
                        "returns only Google's own roles. Add 10–20 target companies.",
                    ],
                    [
                        "Workflow never fires",
                        "Scheduled workflows are disabled after 60 days of repository inactivity, "
                        "and pause on unverified emails. Push any commit to re-enable.",
                    ],
                    [
                        "Duplicates in inbox",
                        "The auto-commit step failed. Confirm the workflow has "
                        "permissions: contents: write.",
                    ],
                ],
                [width * 0.26, width * 0.74],
            )
        )
    )
    st.append(PageBreak())

    # ---------------- 4. Playbook ----------------
    st.append(Paragraph("4. The Modern Job Search Playbook", S["h1"]))
    st.append(
        Paragraph(
            "Automating discovery is not the point. Reclaiming the hours discovery consumes "
            "is the point. The job search has two funnels, and only one of them rewards human "
            "effort.",
            S["body"],
        )
    )

    st.append(Paragraph("Why the first 48 hours decide the outcome", S["h2"]))
    st.append(
        bullets(
            [
                "Recruiters review applications in submission order and often close the review "
                "queue once a shortlist forms — frequently within the first few business days of "
                "a posting going live.",
                "Aggregator lag is the enemy. A role scraped from the employer's own ATS the "
                "morning it posts reaches you days ahead of the same role surfacing on a job board.",
                "Volume on a stale posting converts near zero. Ten applications inside 48 hours "
                "beat one hundred sent in week three.",
            ]
        )
    )

    st.append(Paragraph("The bandwidth reallocation", S["h2"]))
    st.append(
        Paragraph(
            "Most self-directed searches spend the majority of weekly effort on top-of-funnel "
            "work: refreshing boards, re-running the same filters, re-reading postings already "
            "seen, and maintaining a spreadsheet of what was already applied to. That work is "
            "mechanical, and this pipeline absorbs it entirely — the ledger alone removes the "
            "re-reading problem. What remains is the part that actually moves outcomes:",
            S["body"],
        )
    )
    st.append(
        table(
            ["Where the hours go", "Manual search", "With the bot"],
            [
                ["Finding and re-checking postings", "Most of the week", "Zero — arrives at 8:30 a.m."],
                ["Deduping what you already saw", "Spreadsheet upkeep", "Zero — the ledger handles it"],
                ["Tailoring applications", "Whatever is left", "Expanded"],
                ["Referral and recruiter outreach", "Usually skipped first", "The primary activity"],
            ],
            [width * 0.40, width * 0.30, width * 0.30],
        )
    )

    st.append(Paragraph("What to do with the reclaimed time", S["h2"]))
    st.append(
        numbered(
            [
                "<b>Referral first, application second.</b> Before submitting, spend ten minutes "
                "finding one person at the company — alumni, a former colleague, anyone in the "
                "function. Referred candidates convert to interviews at a far higher rate than "
                "cold applicants at the same company.",
                "<b>Message the hiring manager, not just the recruiter.</b> Short, specific, and "
                "tied to one line in the posting. Three sentences beats three paragraphs.",
                "<b>Tailor the top third of the resume only.</b> The Fit Score email lists the "
                "keywords each posting matched — mirror the missing ones you legitimately have.",
                "<b>Work the digest same-day.</b> A role that sat in your inbox for four days has "
                "lost most of the timing advantage the pipeline bought you.",
                "<b>Re-tune monthly.</b> If the digest is consistently off-target, the fix is "
                "almost always the title list, not the scoring weights.",
            ]
        )
    )
    st.append(
        Paragraph(
            "One honest limitation: this tool improves your <i>timing</i> and your <i>coverage</i>. "
            "It does not improve your candidacy. If the digest produces applications but no "
            "interviews for several weeks, the constraint is the resume or the target level, and "
            "no amount of automation will route around that.",
            S["note"],
        )
    )
    return st


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Job Sweep Bot PDF manual")
    parser.add_argument("--out", default="Job_Sweep_Bot_Guide.pdf", help="output PDF path")
    args = parser.parse_args()
    build_document(args.out)
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
