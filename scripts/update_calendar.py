#!/usr/bin/env python3
"""
Scrapes every Tipsport liga team's schedule from hockeyslovakia.sk and
regenerates one .ics file per team (home/away noted, correct local
kickoff time converted to UTC).

Run with: python3 scripts/update_calendar.py
Writes to: docs/<team-slug>.ics  (one file per team)
"""

import os
import random
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
TZ = ZoneInfo("Europe/Bratislava")
GAME_DURATION = timedelta(hours=2, minutes=30)

BASE_URL = "https://www.hockeyslovakia.sk"

# The site sits behind a WAF that rejects requests which do not look like a
# real browser (a bot-ish User-Agent alone is enough to get a blanket 403).
# Send a full, ordinary browser header set and reuse cookies across requests.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _env(name: str, default: str) -> str:
    """Env override that treats an unset *or empty* value as "not set".

    CI passes ``${{ vars.X }}`` as an empty string when the variable does
    not exist, which would otherwise clobber the default.
    """
    return os.environ.get(name, "").strip() or default


USER_AGENT = _env("SCRAPER_USER_AGENT", DEFAULT_USER_AGENT)

BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "sk-SK,sk;q=0.9,cs;q=0.8,en-US;q=0.7,en;q=0.6",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
}

# Status codes worth retrying: WAF/rate-limit pushback and transient 5xx.
RETRY_STATUSES = {403, 408, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = int(_env("SCRAPER_MAX_ATTEMPTS", "4"))
REQUEST_TIMEOUT = int(_env("SCRAPER_TIMEOUT", "30"))
# Politeness delay between team pages, so a dozen hits in a row do not
# look like a burst to the rate limiter.
PER_TEAM_DELAY = float(_env("SCRAPER_DELAY", "1.5"))

# name -> (team_id, slug)
TEAMS = {
    "HC '05 Banská Bystrica": (670399, "hc-05-banska-bystrica"),
    "HC Košice": (670395, "hc-kosice"),
    "HC Prešov": (670405, "hc-presov"),
    "HC Slovan Bratislava": (670394, "hc-slovan-bratislava"),
    "HK 32 Liptovský Mikuláš": (670398, "hk-32-liptovsky-mikulas"),
    "HK Dukla Michalovce": (670403, "hk-dukla-michalovce"),
    "HK Dukla Trenčín": (670396, "hk-dukla-trencin"),
    "HK Nitra": (670400, "hk-nitra"),
    "HK Poprad": (670402, "hk-poprad"),
    "HK Spišská Nová Ves": (670404, "hk-spisska-nova-ves"),
    "HKM Zvolen": (670401, "hkm-zvolen"),
    "Vlci Žilina": (670397, "vlci-zilina"),
}

TEAM_NAMES = sorted(TEAMS.keys(), key=len, reverse=True)
TEAM_ALT = "|".join(re.escape(t) for t in TEAM_NAMES)

# Matches a single schedule row as it appears in the flattened page text:
#   - <round> <home x3> VS <time> <date> <away x3> <weekday> <date> <date> <time> <venue...>
ROW_RE = re.compile(
    r"-\s*(\d+)\s+"
    rf"(?:{TEAM_ALT})\s+(?:{TEAM_ALT})\s+({TEAM_ALT})\s+"
    r"VS\s+(\d{1,2}:\d{2})\s+(\d{2}\.\d{2}\.\d{4})\s+"
    rf"(?:{TEAM_ALT})\s+(?:{TEAM_ALT})\s+({TEAM_ALT})\s+"
    r"\S+\s+\d{2}\.\d{2}\.\d{4}\s+\d{2}\.\d{2}\.\d{4}\s+\d{1,2}:\d{2}\s+"
    r"(.+?)(?=(?:-\s*\d+\s+(?:" + TEAM_ALT + r"))|\Z)"
)


def team_page_url(team_id: int, slug: str) -> str:
    return (
        f"https://www.hockeyslovakia.sk/sk/stats/teams/1197/tipsport-liga/"
        f"team/{team_id}/{slug}/Program"
    )


def build_session() -> requests.Session:
    """A session with browser-like headers, warmed up on the site root.

    The warm-up matters: the edge sets cookies on the first document
    request, and following requests that carry them are treated as an
    ongoing browsing session rather than a bare hit on a deep URL.
    """
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)

    try:
        session.get(
            f"{BASE_URL}/sk/",
            headers={"Sec-Fetch-Site": "none"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        # Not fatal — the team pages may still work.
        print(f"WARNING: warm-up request failed: {e}", file=sys.stderr)

    return session


def fetch_page_text(session: requests.Session, url: str) -> str:
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = session.get(
                url,
                headers={"Referer": f"{BASE_URL}/sk/"},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code in RETRY_STATUSES:
                last_error = requests.HTTPError(
                    f"{resp.status_code} {resp.reason} for url: {url}",
                    response=resp,
                )
            else:
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")
                return soup.get_text(separator=" ")
        except requests.RequestException as e:
            last_error = e

        if attempt < MAX_ATTEMPTS:
            # Exponential backoff with jitter: 2s, 4s, 8s (+/- a bit).
            backoff = 2**attempt + random.uniform(0, 1)
            print(
                f"  attempt {attempt}/{MAX_ATTEMPTS} failed ({last_error}); "
                f"retrying in {backoff:.1f}s",
                file=sys.stderr,
            )
            time.sleep(backoff)

    raise last_error


def parse_games(page_text: str, target_team: str):
    games = []
    for m in ROW_RE.finditer(page_text):
        round_no, home_team, time_str, date_str, away_team, venue = m.groups()
        is_home = home_team == target_team
        opponent = away_team if is_home else home_team
        venue = venue.strip().split("  ")[0].strip()
        games.append(
            {
                "round": int(round_no),
                "is_home": is_home,
                "opponent": opponent,
                "date": date_str,
                "time": time_str,
                "venue": venue,
            }
        )
    return games


def to_utc(date_str: str, time_str: str) -> datetime:
    d, mo, y = date_str.split(".")
    hh, mm = time_str.split(":")
    local_dt = datetime(int(y), int(mo), int(d), int(hh), int(mm), tzinfo=TZ)
    return local_dt.astimezone(ZoneInfo("UTC"))


def fmt(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def build_ics(team_name: str, slug: str, games) -> str:
    now_stamp = fmt(datetime.now(ZoneInfo("UTC")))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{team_name} Schedule//Tipsport liga//SK",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{team_name} - Tipsport liga",
    ]

    for g in games:
        start_utc = to_utc(g["date"], g["time"])
        end_utc = start_utc + GAME_DURATION
        d, mo, y = g["date"].split(".")
        uid = f"{slug}-{g['round']}-{y}{mo}{d}@tipsportliga"

        if g["is_home"]:
            summary = f"{team_name} - {g['opponent']}"
            desc = (
                f"Domáci zápas {team_name} (kolo {g['round']}, Tipsport liga). "
                f"Súper: {g['opponent']}."
            )
        else:
            summary = f"{g['opponent']} - {team_name}"
            desc = (
                f"Zápas {team_name} na ihrisku súpera (kolo {g['round']}, "
                f"Tipsport liga). Súper: {g['opponent']}."
            )

        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_stamp}",
            f"DTSTART:{fmt(start_utc)}",
            f"DTEND:{fmt(end_utc)}",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{desc}",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main():
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    failures = []
    forbidden = 0
    session = build_session()

    for i, (team_name, (team_id, slug)) in enumerate(TEAMS.items()):
        if i:
            time.sleep(PER_TEAM_DELAY)

        url = team_page_url(team_id, slug)
        try:
            page_text = fetch_page_text(session, url)
            games = parse_games(page_text, team_name)

            if len(games) < 40:
                # A full season is ~54 games. Far fewer means parsing
                # broke (site redesign etc.) — skip this team rather
                # than overwrite a good file with a broken one.
                print(
                    f"WARNING: {team_name}: only parsed {len(games)} games, "
                    "expected ~54. Skipping this team's file.",
                    file=sys.stderr,
                )
                failures.append(team_name)
                continue

            ics_content = build_ics(team_name, slug, games)
            out_path = DOCS_DIR / f"{slug}.ics"
            out_path.write_text(ics_content, encoding="utf-8")
            print(f"{team_name}: wrote {len(games)} games to {out_path}")

        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (403, 429):
                forbidden += 1
            print(f"ERROR: {team_name}: {e}", file=sys.stderr)
            failures.append(team_name)

    if failures:
        print(f"\nCompleted with issues for: {', '.join(failures)}", file=sys.stderr)
        if forbidden == len(TEAMS):
            print(
                "\nEvery request was rejected by the site's edge (403/429). The "
                "request headers are not the problem on their own — the source "
                "IP is likely blocked too. Options: override the User-Agent via "
                "the SCRAPER_USER_AGENT env var, slow the run down via "
                "SCRAPER_DELAY, or run the scraper from a network the site "
                "accepts (self-hosted runner / outbound proxy) instead of a "
                "GitHub-hosted runner.",
                file=sys.stderr,
            )
        sys.exit(1)


if __name__ == "__main__":
    main()
