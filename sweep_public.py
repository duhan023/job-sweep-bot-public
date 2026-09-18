#!/usr/bin/env python3
"""
sweep_public.py - Job Sweep Bot engine.

Reads config.json, scrapes employer ATS boards (Greenhouse, Lever, Ashby) and the
Google Careers API, scores every posting against the user's resume keywords, drops
anything that fails a hard filter or has already been sent, emails the survivors as
an HTML digest, and appends them to areas/job-search-log.md.

No user-specific logic lives in this file. Everything is driven by config.json,
which configure.py writes.

Usage:
    python sweep_public.py                  # full run (scrape + email + log)
    python sweep_public.py --dry-run        # scrape + score, print table, no email, no log write
    python sweep_public.py --no-email       # scrape + score + log, skip SMTP
    python sweep_public.py --limit 5 -v     # cap results, verbose filter reasons

Environment:
    GMAIL_USER            Gmail address used as the sender
    GMAIL_APP_PASSWORD    Google App Password (16 chars, 2FA required)
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import random
import re
import smtplib
import ssl
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Iterable
from urllib.parse import quote_plus, urlparse

try:
    import requests
except ImportError:  # pragma: no cover
    print("Missing dependency: requests. Run `pip install -r requirements.txt`.", file=sys.stderr)
    raise

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.json"
DEFAULT_LOG = ROOT / "areas" / "job-search-log.md"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36",
]

# Titles above the user's level. Any term here that also appears in one of the
# user's own target titles is automatically un-banned at runtime.
SENIORITY_TERMS = [
    "senior", "sr.", "sr ", "staff ", "principal", "director", "vp ",
    "vice president", "head of", "chief", "c-level", "distinguished",
    "fellow", "executive", "iii", " iv", "architect",
]

VERBOSE = False


def log(msg: str) -> None:
    print(msg, flush=True)


def vlog(msg: str) -> None:
    if VERBOSE:
        print(f"  · {msg}", flush=True)


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class Job:
    source: str
    company: str
    title: str
    location: str
    url: str
    description: str = ""
    salary_text: str = "Unlisted"
    salary_value: int = 0
    posted: str = ""
    fit: int = 0
    matched_keywords: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        raw = f"{norm(self.company)}|{norm(self.title)}|{norm(self.location)[:24]}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]

    @property
    def haystack(self) -> str:
        return f"{self.title}\n{self.company}\n{self.location}\n{self.description}".lower()


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).strip()


def squash(text: str, limit: int = 6000) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def load_config(path: Path) -> dict:
    if not path.exists():
        log(f"ERROR: {path.name} not found. Run `python configure.py` first.")
        sys.exit(2)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log(f"ERROR: {path.name} is not valid JSON ({exc}).")
        sys.exit(2)
    if not cfg.get("resume_keywords"):
        log("ERROR: config.json has no resume_keywords. Re-run configure.py.")
        sys.exit(2)
    if not cfg.get("targeting", {}).get("titles"):
        log("ERROR: config.json has no target titles. Re-run configure.py.")
        sys.exit(2)
    cfg.setdefault("sources", {})["ats_boards"] = normalize_ats_boards(
        cfg.get("sources", {}).get("ats_boards", [])
    )
    return cfg


VALID_ATS = ("greenhouse", "lever", "ashby")


def normalize_ats_boards(raw) -> list[dict]:
    """
    Accept either form in config.json, because both are natural to hand-write:

        "ats_boards": [{"type": "greenhouse", "token": "stripe"}]
        "ats_boards": ["greenhouse:stripe", "lever:netflix"]

    Returns a list of validated dicts. Bad entries are reported and skipped
    rather than crashing the run.
    """
    boards: list[dict] = []
    if isinstance(raw, str):  # a single "greenhouse:stripe" instead of a list
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        log(f"WARNING: sources.ats_boards should be a list, got {type(raw).__name__}. Ignoring.")
        return boards

    for entry in raw:
        kind = token = ""
        if isinstance(entry, dict):
            kind = str(entry.get("type", "")).strip().lower()
            token = str(entry.get("token", "")).strip()
        elif isinstance(entry, str):
            if ":" not in entry:
                log(f"WARNING: skipping ATS board '{entry}' - expected 'type:token'.")
                continue
            kind, token = entry.split(":", 1)
            kind, token = kind.strip().lower(), token.strip()
        else:
            log(f"WARNING: skipping ATS board entry of type {type(entry).__name__}.")
            continue

        if kind not in VALID_ATS:
            log(f"WARNING: skipping ATS board '{kind}:{token}' - type must be one of {', '.join(VALID_ATS)}.")
            continue
        if not token:
            log(f"WARNING: skipping ATS board '{kind}' - no company token.")
            continue
        boards.append({"type": kind, "token": token})
    return boards


def make_session(cfg: dict) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/json,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        }
    )
    return session


def polite_sleep(cfg: dict) -> None:
    base = float(cfg.get("sources", {}).get("polite_delay_seconds", 2.5))
    time.sleep(base + random.uniform(0, 1.0))


def get(session: requests.Session, url: str, cfg: dict, **kwargs):
    timeout = int(cfg.get("sources", {}).get("request_timeout_seconds", 20))
    tries = 3
    for attempt in range(1, tries + 1):
        try:
            resp = session.get(url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            vlog(f"request error ({attempt}/{tries}) {url}: {exc}")
            time.sleep(2 * attempt)
            continue
        if resp.status_code == 429:
            vlog(f"rate limited by {urlparse(url).netloc}; backing off")
            time.sleep(10 * attempt)
            continue
        if resp.status_code >= 500:
            vlog(f"server error {resp.status_code} from {urlparse(url).netloc}")
            time.sleep(3 * attempt)
            continue
        return resp
    return None


# --------------------------------------------------------------------------
# Source: Google Careers
# --------------------------------------------------------------------------

GOOGLE_ENDPOINTS = [
    "https://www.google.com/about/careers/applications/api/v3/search/",
    "https://careers.google.com/api/v3/search/",
]


def scrape_google(cfg: dict, session: requests.Session) -> list[Job]:
    jobs: list[Job] = []
    targeting = cfg["targeting"]
    pages = int(cfg["sources"].get("max_pages_per_query", 2))
    for title in targeting["titles"]:
        for location in targeting["locations"]:
            for page in range(1, pages + 1):
                params = {"q": title, "page": page, "sort_by": "date"}
                if location and location.lower() not in ("remote", "anywhere"):
                    params["location"] = location
                payload = None
                for endpoint in GOOGLE_ENDPOINTS:
                    resp = get(session, endpoint, cfg, params=params)
                    if resp is None or resp.status_code != 200:
                        continue
                    try:
                        payload = resp.json()
                    except ValueError:
                        continue
                    if isinstance(payload, dict) and "jobs" in payload:
                        break
                    payload = None
                if not payload:
                    vlog(f"google: no payload for '{title}' / '{location}' p{page}")
                    break
                batch = payload.get("jobs") or []
                if not batch:
                    break
                for item in batch:
                    jobs.append(_google_job(item))
                polite_sleep(cfg)
    log(f"  Google Careers: {len(jobs)} raw postings")
    return jobs


def _google_job(item: dict) -> Job:
    locations = item.get("locations") or []
    loc_names = []
    for loc in locations:
        if isinstance(loc, dict):
            loc_names.append(loc.get("display") or loc.get("city") or "")
        elif isinstance(loc, str):
            loc_names.append(loc)
    location = ", ".join([l for l in loc_names if l][:2]) or "See posting"

    description = " ".join(
        squash(str(item.get(k, "")))
        for k in ("summary", "description", "responsibilities", "qualifications",
                  "minimum_qualifications", "preferred_qualifications")
    )
    posted = str(item.get("publish_date") or item.get("created") or "")[:10]
    job_id = str(item.get("id") or item.get("job_id") or "")
    url = item.get("apply_url") or item.get("url") or ""
    if not url and job_id:
        slug = re.sub(r"[^a-z0-9]+", "-", (item.get("title") or "role").lower()).strip("-")
        url = f"https://www.google.com/about/careers/applications/jobs/results/{job_id}-{slug}"
    return Job(
        source="Google Careers",
        company=item.get("company_name") or "Google",
        title=squash(item.get("title") or "", 200),
        location=squash(location, 120),
        url=url,
        description=description,
        posted=posted,
    )


# --------------------------------------------------------------------------
# Source: ATS boards (Greenhouse / Lever / Ashby)
# --------------------------------------------------------------------------

def scrape_ats(cfg: dict, session: requests.Session) -> list[Job]:
    jobs: list[Job] = []
    for board in cfg["sources"].get("ats_boards", []):
        kind = board.get("type", "").lower()
        token = board.get("token", "").strip()
        if not token:
            continue
        try:
            if kind == "greenhouse":
                jobs.extend(_greenhouse(session, cfg, token))
            elif kind == "lever":
                jobs.extend(_lever(session, cfg, token))
            elif kind == "ashby":
                jobs.extend(_ashby(session, cfg, token))
        except Exception as exc:  # noqa: BLE001
            log(f"  ATS {kind}:{token} failed: {exc}")
        polite_sleep(cfg)
    if cfg["sources"].get("ats_boards"):
        log(f"  ATS boards: {len(jobs)} raw postings")
    return jobs


def _greenhouse(session, cfg, token: str) -> list[Job]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    resp = get(session, url, cfg)
    if resp is None or resp.status_code != 200:
        return []
    data = resp.json()
    out = []
    for item in data.get("jobs", []):
        out.append(
            Job(
                source=f"Greenhouse/{token}",
                company=token.replace("-", " ").title(),
                title=squash(item.get("title", ""), 200),
                location=squash((item.get("location") or {}).get("name", "See posting"), 120),
                url=item.get("absolute_url", ""),
                description=squash(item.get("content", "")),
                posted=str(item.get("updated_at", ""))[:10],
            )
        )
    return out


def _lever(session, cfg, token: str) -> list[Job]:
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    resp = get(session, url, cfg)
    if resp is None or resp.status_code != 200:
        return []
    out = []
    for item in resp.json():
        cats = item.get("categories") or {}
        created = item.get("createdAt")
        posted = (
            datetime.fromtimestamp(created / 1000, tz=timezone.utc).date().isoformat()
            if isinstance(created, (int, float))
            else ""
        )
        out.append(
            Job(
                source=f"Lever/{token}",
                company=token.replace("-", " ").title(),
                title=squash(item.get("text", ""), 200),
                location=squash(cats.get("location", "See posting"), 120),
                url=item.get("hostedUrl", ""),
                description=squash(item.get("descriptionPlain") or item.get("description", "")),
                salary_text=squash(cats.get("commitment", "") or "", 60) or "Unlisted",
                posted=posted,
            )
        )
    return out


def _ashby(session, cfg, token: str) -> list[Job]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"
    resp = get(session, url, cfg)
    if resp is None or resp.status_code != 200:
        return []
    out = []
    for item in resp.json().get("jobs", []):
        comp = item.get("compensation") or {}
        summary = comp.get("compensationTierSummary") if isinstance(comp, dict) else ""
        out.append(
            Job(
                source=f"Ashby/{token}",
                company=item.get("organizationName") or token.title(),
                title=squash(item.get("title", ""), 200),
                location=squash(item.get("location", "See posting"), 120),
                url=item.get("jobUrl", ""),
                description=squash(item.get("descriptionPlain") or item.get("descriptionHtml", "")),
                salary_text=squash(summary or "", 60) or "Unlisted",
                posted=str(item.get("publishedAt", ""))[:10],
            )
        )
    return out


# --------------------------------------------------------------------------
# Parsing helpers: salary and years of experience
# --------------------------------------------------------------------------

SALARY_RE = re.compile(
    r"\$\s?(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?\s?[kK]\b|\d{5,7})",
)

YOE_RE = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs?)"
    r"(?:[^.]{0,40}?(?:experience|exp\b))",
    re.IGNORECASE,
)


def parse_salary(text: str) -> tuple[str, int]:
    """Return (display_text, max_annual_value). 0 when nothing parseable."""
    if not text:
        return "Unlisted", 0
    found: list[int] = []
    for raw in SALARY_RE.findall(text):
        token = raw.replace(",", "").strip().lower()
        if token.endswith("k"):
            value = int(float(token[:-1].strip()) * 1000)
        else:
            value = int(float(token))
        if 15_000 <= value <= 1_500_000:
            found.append(value)
    if not found:
        return "Unlisted", 0
    lo, hi = min(found), max(found)
    display = f"${lo:,}" if lo == hi else f"${lo:,}–${hi:,}"
    return display, hi


def parse_min_years(text: str) -> int | None:
    """Smallest 'N years of experience' requirement found, or None."""
    mins = []
    for m in YOE_RE.finditer(text or ""):
        try:
            mins.append(int(m.group(1)))
        except (TypeError, ValueError):
            continue
    mins = [v for v in mins if 0 <= v <= 30]
    return min(mins) if mins else None


def parse_posted_date(raw: str) -> date | None:
    raw = (raw or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# Filtering
# --------------------------------------------------------------------------

def build_forbidden_terms(cfg: dict) -> list[str]:
    titles_blob = " ".join(cfg["targeting"]["titles"]).lower()
    return [t for t in SENIORITY_TERMS if t.strip() not in titles_blob]


def location_ok(job: Job, cfg: dict) -> bool:
    """
    Punctuation-insensitive location match. A user who types "Bentonville AR"
    must match a posting that says "Bentonville, AR, United States", so both
    sides are normalised and every token of the target must be present.
    """
    targets = [norm(l) for l in cfg["targeting"]["locations"]]
    remote_ok = bool(cfg["targeting"].get("remote_ok", True))
    job_loc = norm(job.location)
    blob = f"{job_loc} {norm(job.title)}"

    broad = {"anywhere", "worldwide", "global", "united states", "usa", "us", "nationwide"}
    if any(t in broad for t in targets):
        return True
    if remote_ok and re.search(r"\bremote\b|work from home|wfh|distributed|virtual", blob):
        return True
    if not job_loc or "see posting" in job_loc:
        return True

    def present(token: str) -> bool:
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", job_loc))

    for raw_target in cfg["targeting"]["locations"]:
        if norm(raw_target) in ("remote", "anywhere"):
            continue
        # Match on the city portion: everything before the first comma or slash.
        city = norm(re.split(r"[,/]", raw_target)[0])
        tokens = [t for t in city.split() if len(t) >= 2]
        if not tokens:
            continue
        if all(present(t) for t in tokens):
            return True
        # "Bentonville AR" typed without a comma should still match a posting
        # that only says "Bentonville". Only for distinctive city names.
        if len(tokens) > 1 and len(tokens[0]) >= 5 and present(tokens[0]):
            return True
    return False


def passes_filters(job: Job, cfg: dict, forbidden: list[str]) -> tuple[bool, str]:
    targeting = cfg["targeting"]
    title_l = job.title.lower()

    if not job.url:
        return False, "no apply link"

    for term in forbidden:
        if term in title_l:
            return False, f"seniority term '{term.strip()}'"

    blob = job.haystack
    for term in targeting.get("exclusions", []):
        if term and term in blob:
            return False, f"dealbreaker '{term}'"

    cap = int(targeting.get("max_years_experience", 99))
    min_years = parse_min_years(job.description)
    if min_years is not None and min_years > cap:
        return False, f"requires {min_years}y > cap {cap}y"

    floor = int(targeting.get("salary_floor", 0))
    if floor and job.salary_value and job.salary_value < floor:
        return False, f"salary {job.salary_value:,} < floor {floor:,}"

    if not location_ok(job, cfg):
        return False, f"location '{job.location}'"

    window = int(cfg["sources"].get("posted_within_days", 0))
    posted = parse_posted_date(job.posted)
    if window and posted and posted < date.today() - timedelta(days=window + 1):
        return False, f"posted {posted} outside {window}d window"

    return True, ""


# --------------------------------------------------------------------------
# Fit scoring
# --------------------------------------------------------------------------

def score_job(job: Job, cfg: dict) -> int:
    weights = cfg["scoring"].get("weights", {"keywords": 55, "title": 30, "experience": 15})
    keywords = [k.lower() for k in cfg["resume_keywords"]]
    blob = job.haystack

    # --- keyword overlap -------------------------------------------------
    matched = []
    for kw in keywords:
        if " " in kw:
            if kw in blob:
                matched.append(kw)
        elif re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", blob):
            matched.append(kw)
    job.matched_keywords = matched[:12]
    # A strong posting echoes roughly half the resume vocabulary, capped at 30
    # terms (past that, longer keyword lists should not make scoring harder) and
    # floored at 8 so a three-word config cannot trivially hit 100.
    expected = max(8, min(len(keywords), 30) * 0.55)
    kw_score = min(1.0, len(matched) / expected)

    # --- title precision --------------------------------------------------
    title_l = norm(job.title)
    best_title = 0.0
    for target in cfg["targeting"]["titles"]:
        t = norm(target)
        if not t:
            continue
        if t in title_l:
            best_title = max(best_title, 1.0)
            continue
        t_tokens = [w for w in t.split() if len(w) > 2]
        if not t_tokens:
            continue
        hits = sum(1 for w in t_tokens if w in title_l)
        best_title = max(best_title, hits / len(t_tokens) * 0.85)

    # --- experience compatibility ----------------------------------------
    cap = int(cfg["targeting"].get("max_years_experience", 99))
    min_years = parse_min_years(job.description)
    if min_years is None:
        exp_score = 0.6  # unknown: neutral-positive, not free points
    elif min_years <= cap:
        # tighter band = better fit; a 2y ask against a 5y cap is a fine match
        exp_score = 1.0 if min_years >= max(0, cap - 3) else 0.85
    else:
        exp_score = 0.0

    total = (
        weights["keywords"] * kw_score
        + weights["title"] * best_title
        + weights["experience"] * exp_score
    )
    scale = sum(weights.values()) / 100.0 or 1.0
    return max(1, min(100, round(total / scale)))


# --------------------------------------------------------------------------
# Deduplication ledger
# --------------------------------------------------------------------------

LOG_HEADER = """# Job Search Log

Append-only ledger written by `sweep_public.py`. Every role that has been emailed
to you is recorded here so it is never emailed twice. Do not delete rows unless
you want those roles to resurface.

| Date Sent | Company | Role | Location | Fit | Link | Key |
| --------- | ------- | ---- | -------- | --- | ---- | --- |
"""

KEY_RE = re.compile(r"`([0-9a-f]{10})`")


def load_seen_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(KEY_RE.findall(path.read_text(encoding="utf-8", errors="ignore")))


def append_to_log(path: Path, jobs: list[Job]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        path.write_text(LOG_HEADER, encoding="utf-8")
    today = date.today().isoformat()
    rows = []
    for job in jobs:
        rows.append(
            "| {d} | {c} | {t} | {l} | {f} | [apply]({u}) | `{k}` |".format(
                d=today,
                c=md_cell(job.company),
                t=md_cell(job.title),
                l=md_cell(job.location),
                f=job.fit,
                u=job.url,
                k=job.key,
            )
        )
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")


def md_cell(text: str) -> str:
    return (text or "").replace("|", "/").replace("\n", " ").strip()[:80]


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

def score_color(score: int) -> str:
    if score >= 80:
        return "#128a3d"
    if score >= 65:
        return "#1f6feb"
    if score >= 50:
        return "#9a6700"
    return "#6e7781"


def build_email_html(jobs: list[Job], cfg: dict, stats: dict) -> str:
    rows = []
    for i, job in enumerate(jobs, start=1):
        shade = "#ffffff" if i % 2 else "#f7f8fa"
        kw = ", ".join(job.matched_keywords[:6])
        rows.append(
            f"""
      <tr style="background:{shade};">
        <td style="padding:10px 8px;border-bottom:1px solid #e6e8eb;color:#6e7781;">{i}</td>
        <td style="padding:10px 8px;border-bottom:1px solid #e6e8eb;font-weight:600;">{html.escape(job.company)}</td>
        <td style="padding:10px 8px;border-bottom:1px solid #e6e8eb;">
          {html.escape(job.title)}
          <div style="font-size:11px;color:#6e7781;margin-top:3px;">{html.escape(job.source)}{(' · ' + html.escape(kw)) if kw else ''}</div>
        </td>
        <td style="padding:10px 8px;border-bottom:1px solid #e6e8eb;">{html.escape(job.location)}</td>
        <td style="padding:10px 8px;border-bottom:1px solid #e6e8eb;white-space:nowrap;">{html.escape(job.salary_text)}</td>
        <td style="padding:10px 8px;border-bottom:1px solid #e6e8eb;text-align:center;">
          <span style="display:inline-block;min-width:34px;padding:3px 8px;border-radius:12px;
                       background:{score_color(job.fit)};color:#ffffff;font-weight:700;font-size:12px;">{job.fit}</span>
        </td>
        <td style="padding:10px 8px;border-bottom:1px solid #e6e8eb;white-space:nowrap;color:#6e7781;">{html.escape(job.posted or '—')}</td>
        <td style="padding:10px 8px;border-bottom:1px solid #e6e8eb;">
          <a href="{html.escape(job.url)}" style="color:#1f6feb;font-weight:600;text-decoration:none;">Apply →</a>
        </td>
      </tr>"""
        )

    titles = ", ".join(cfg["targeting"]["titles"])
    locations = ", ".join(cfg["targeting"]["locations"])
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#eef0f3;">
<div style="max-width:940px;margin:0 auto;padding:20px 12px;
            font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
            color:#1f2328;">
  <div style="background:#0d1117;color:#ffffff;padding:18px 20px;border-radius:10px 10px 0 0;">
    <div style="font-size:19px;font-weight:700;">Job Sweep — {date.today().strftime('%b %d, %Y')}</div>
    <div style="font-size:13px;color:#b8c0cc;margin-top:4px;">
      {len(jobs)} new matches · {stats['scraped']} scraped · {stats['filtered']} filtered out ·
      {stats['duplicates']} already sent
    </div>
  </div>
  <div style="background:#ffffff;padding:14px 20px;font-size:12px;color:#57606a;border-bottom:1px solid #e6e8eb;">
    <strong>Targets:</strong> {html.escape(titles)} &nbsp;|&nbsp;
    <strong>Locations:</strong> {html.escape(locations)} &nbsp;|&nbsp;
    <strong>Min fit:</strong> {cfg['scoring']['min_fit_score']}/100
  </div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
         style="background:#ffffff;border-collapse:collapse;font-size:13px;">
    <thead>
      <tr style="background:#f0f2f5;text-align:left;">
        <th style="padding:9px 8px;color:#57606a;font-size:11px;letter-spacing:.4px;">#</th>
        <th style="padding:9px 8px;color:#57606a;font-size:11px;letter-spacing:.4px;">COMPANY</th>
        <th style="padding:9px 8px;color:#57606a;font-size:11px;letter-spacing:.4px;">ROLE</th>
        <th style="padding:9px 8px;color:#57606a;font-size:11px;letter-spacing:.4px;">LOCATION</th>
        <th style="padding:9px 8px;color:#57606a;font-size:11px;letter-spacing:.4px;">SALARY</th>
        <th style="padding:9px 8px;color:#57606a;font-size:11px;letter-spacing:.4px;text-align:center;">FIT</th>
        <th style="padding:9px 8px;color:#57606a;font-size:11px;letter-spacing:.4px;">POSTED</th>
        <th style="padding:9px 8px;color:#57606a;font-size:11px;letter-spacing:.4px;">LINK</th>
      </tr>
    </thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <div style="background:#ffffff;padding:16px 20px;border-radius:0 0 10px 10px;
              font-size:11px;color:#8b949e;line-height:1.6;">
    Fit Score = {cfg['scoring']['weights']['keywords']}% resume-keyword overlap +
    {cfg['scoring']['weights']['title']}% title precision +
    {cfg['scoring']['weights']['experience']}% experience compatibility.
    It ranks the queue; it does not decide for you.<br>
    Generated by Job Sweep Bot. Edit <code>config.json</code> to change targets, or re-run
    <code>configure.py</code>.
  </div>
</div>
</body></html>"""


def build_email_text(jobs: list[Job]) -> str:
    lines = [f"Job Sweep — {date.today().isoformat()}", ""]
    for i, job in enumerate(jobs, start=1):
        lines.append(
            f"{i}. [{job.fit}/100] {job.title} — {job.company} ({job.location})\n"
            f"   {job.salary_text} · posted {job.posted or 'n/a'}\n   {job.url}"
        )
    return "\n".join(lines)


def send_email(jobs: list[Job], cfg: dict, stats: dict) -> bool:
    user = os.environ.get("GMAIL_USER", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not user or not password:
        log("ERROR: GMAIL_USER / GMAIL_APP_PASSWORD are not set. Skipping email.")
        return False

    to_addr = cfg["delivery"]["to_email"]
    prefix = cfg["delivery"].get("subject_prefix", "Job Sweep")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{prefix}: {len(jobs)} new matches — {date.today().strftime('%b %d')}"
    msg["From"] = user
    msg["To"] = to_addr
    msg.attach(MIMEText(build_email_text(jobs), "plain", "utf-8"))
    msg.attach(MIMEText(build_email_html(jobs, cfg, stats), "html", "utf-8"))

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as server:
            server.login(user, password)
            server.sendmail(user, [to_addr], msg.as_string())
    except smtplib.SMTPAuthenticationError:
        log(
            "ERROR: Gmail rejected the login. Use a 16-character App Password "
            "(myaccount.google.com/apppasswords), not your account password, and "
            "make sure 2-Step Verification is on."
        )
        return False
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR: could not send email: {exc}")
        return False
    log(f"  Email sent to {to_addr}")
    return True


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def dedupe_by_key(jobs: Iterable[Job]) -> list[Job]:
    seen: set[str] = set()
    out: list[Job] = []
    for job in jobs:
        if job.key in seen:
            continue
        seen.add(job.key)
        out.append(job)
    return out


def write_step_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError:
        pass


def main() -> int:
    global VERBOSE
    parser = argparse.ArgumentParser(description="Job Sweep Bot engine")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    parser.add_argument("--dry-run", action="store_true", help="no email, no log write")
    parser.add_argument("--no-email", action="store_true", help="log results but skip SMTP")
    parser.add_argument("--limit", type=int, default=0, help="cap the number of results")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    VERBOSE = args.verbose

    cfg = load_config(Path(args.config))
    log_path = Path(args.log)
    started = time.time()

    log(f"Job Sweep — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    log(f"Targets: {', '.join(cfg['targeting']['titles'])}")
    log(f"Locations: {', '.join(cfg['targeting']['locations'])}")
    log("Scraping sources…")

    if not cfg["sources"].get("ats_boards"):
        log(
            "WARNING: no ATS boards configured. Google Careers only returns Google's own\n"
            "         roles, so coverage will be thin. Add companies to sources.ats_boards\n"
            "         in config.json, or re-run configure.py."
        )

    session = make_session(cfg)
    raw: list[Job] = []
    if cfg["sources"].get("google_careers", True):
        raw.extend(scrape_google(cfg, session))
    raw.extend(scrape_ats(cfg, session))

    raw = dedupe_by_key(raw)
    log(f"Scraped {len(raw)} unique postings")

    forbidden = build_forbidden_terms(cfg)
    seen_keys = load_seen_keys(log_path)
    min_fit = int(cfg["scoring"]["min_fit_score"])

    kept: list[Job] = []
    filtered = duplicates = below_fit = 0
    for job in raw:
        display, value = parse_salary(f"{job.salary_text} {job.description[:2500]}")
        if job.salary_text in ("", "Unlisted"):
            job.salary_text = display
        job.salary_value = value

        ok, reason = passes_filters(job, cfg, forbidden)
        if not ok:
            filtered += 1
            vlog(f"drop [{reason}] {job.title} @ {job.company}")
            continue
        if job.key in seen_keys:
            duplicates += 1
            vlog(f"dup  {job.title} @ {job.company}")
            continue
        job.fit = score_job(job, cfg)
        if job.fit < min_fit:
            below_fit += 1
            vlog(f"low  [{job.fit}] {job.title} @ {job.company}")
            continue
        kept.append(job)

    kept.sort(key=lambda j: (j.fit, j.posted), reverse=True)
    cap = args.limit or int(cfg["scoring"].get("max_results", 25))
    kept = kept[:cap]

    stats = {
        "scraped": len(raw),
        "filtered": filtered + below_fit,
        "duplicates": duplicates,
        "kept": len(kept),
    }
    log(
        f"Result: {len(kept)} to send · {filtered} filtered · {below_fit} below fit {min_fit} · "
        f"{duplicates} already sent · {time.time() - started:.1f}s"
    )

    if kept:
        for job in kept:
            log(f"  [{job.fit:>3}] {job.title[:52]:<52} {job.company[:22]:<22} {job.location[:22]}")
    summary = [
        "## Job Sweep",
        f"- Scraped: **{stats['scraped']}**",
        f"- New matches: **{stats['kept']}**",
        f"- Filtered out: **{stats['filtered']}**",
        f"- Already sent: **{stats['duplicates']}**",
    ]
    write_step_summary("\n".join(summary))

    if args.dry_run:
        log("Dry run: no email sent, log not written.")
        return 0

    if not kept:
        if cfg["delivery"].get("send_empty_digest") and not args.no_email:
            send_email([], cfg, stats)
        else:
            log("Nothing new today. No email sent.")
        return 0

    emailed = True
    if not args.no_email:
        emailed = send_email(kept, cfg, stats)

    if emailed:
        append_to_log(log_path, kept)
        log(f"  Logged {len(kept)} roles to {log_path}")
    else:
        log("Email failed — roles were NOT logged, so they will resurface next run.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
