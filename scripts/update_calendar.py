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
import tempfile
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

# Plain `requests` has a distinctive TLS/HTTP2 fingerprint (JA3) that bot
# management products match on regardless of how browser-like the headers
# are. curl_cffi replays a real Chrome fingerprint, which is the only way
# to get past that from Python without driving an actual browser.
try:
    from curl_cffi import requests as curl_requests
except ImportError:  # optional — the script still runs on plain requests
    curl_requests = None

# Status codes worth retrying: WAF/rate-limit pushback and transient 5xx.
RETRY_STATUSES = {403, 408, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = int(_env("SCRAPER_MAX_ATTEMPTS", "4"))
REQUEST_TIMEOUT = int(_env("SCRAPER_TIMEOUT", "30"))
# Politeness delay between team pages, so a dozen hits in a row do not
# look like a burst to the rate limiter.
PER_TEAM_DELAY = float(_env("SCRAPER_DELAY", "1.5"))
# Browser profile curl_cffi impersonates; "chrome" tracks its newest build.
# A full season is ~54 games; far fewer means parsing broke.
MIN_GAMES = int(_env("SCRAPER_MIN_GAMES", "40"))
# Where to save a page that would not parse, for offline inspection.
DEBUG_DIR = _env("SCRAPER_DEBUG_DIR", "")
IMPERSONATE = _env("SCRAPER_IMPERSONATE", "chrome")
# Set to 1 to skip curl_cffi and use plain requests (for comparing the two).
FORCE_REQUESTS = _env("SCRAPER_FORCE_REQUESTS", "0") not in ("0", "false", "no")

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

DATE_RE = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})")
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")
TEAM_FIND_RE = re.compile(TEAM_ALT)

# Fallback only — see parse_games. Matches a schedule row as it appears in
# the flattened page text, which breaks on any column or markup change:
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


def use_curl_cffi() -> bool:
    return curl_requests is not None and not FORCE_REQUESTS


def build_session():
    """A browser-like session, warmed up on the site root.

    The warm-up matters: the edge sets cookies on the first document
    request, and following requests that carry them are treated as an
    ongoing browsing session rather than a bare hit on a deep URL.
    """
    if use_curl_cffi():
        # Impersonation supplies its own coherent header set; overriding it
        # piecemeal is what makes a fingerprint look stitched together.
        session = curl_requests.Session(impersonate=IMPERSONATE)
        session.headers.update({"Accept-Language": BROWSER_HEADERS["Accept-Language"]})
        print(f"HTTP backend: curl_cffi (impersonate={IMPERSONATE})", file=sys.stderr)
    else:
        session = requests.Session()
        session.headers.update(BROWSER_HEADERS)
        reason = "forced" if FORCE_REQUESTS else "curl_cffi not installed"
        print(f"HTTP backend: requests ({reason})", file=sys.stderr)

    try:
        session.get(f"{BASE_URL}/sk/", timeout=REQUEST_TIMEOUT)
    except Exception as e:
        # Not fatal — the team pages may still work.
        print(f"WARNING: warm-up request failed: {e}", file=sys.stderr)

    return session


_block_reported = False


def report_block(resp) -> None:
    """Dump the first refused response so the blocker can be identified.

    Which product is saying no, and why, decides the fix: a JS/CAPTCHA
    challenge needs a real browser, a plain IP deny needs a different
    network. Guessing between them from a bare status code is what makes
    this class of bug drag on.
    """
    global _block_reported
    if _block_reported or resp is None:
        return
    _block_reported = True

    interesting = {
        "server", "cf-ray", "cf-mitigated", "cf-cache-status", "x-iinfo",
        "x-cdn", "x-sucuri-id", "x-amz-cf-id", "via", "retry-after",
        "content-type", "set-cookie", "x-request-id", "x-powered-by",
}

    print("\n--- block diagnostics (first refused response) ---", file=sys.stderr)
    print(f"status: {getattr(resp, 'status_code', '?')}", file=sys.stderr)
    try:
        for k, v in resp.headers.items():
            if k.lower() in interesting:
                print(f"header: {k}: {v}", file=sys.stderr)
    except Exception as e:
        print(f"(could not read response headers: {e})", file=sys.stderr)
    try:
        body = " ".join((resp.text or "").split())[:800]
        print(f"body[:800]: {body}", file=sys.stderr)
    except Exception as e:
        print(f"(could not read response body: {e})", file=sys.stderr)
    print("--- end diagnostics ---\n", file=sys.stderr)


def fetch_page_html(session, url: str) -> str:
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = session.get(
                url,
                headers={"Referer": f"{BASE_URL}/sk/"},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code in RETRY_STATUSES:
                report_block(resp)
                reason = getattr(resp, "reason", "") or ""
                last_error = requests.HTTPError(
                    f"{resp.status_code} {reason} for url: {url}".replace("  ", " "),
                    response=resp,
                )
            elif resp.status_code >= 400:
                raise requests.HTTPError(
                    f"{resp.status_code} for url: {url}", response=resp
                )
            else:
                return resp.text
        except Exception as e:
            # curl_cffi raises its own exception types, so this stays broad;
            # main() reports whatever comes out per team.
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


def teams_in(text: str):
    """Team names in order of appearance, with consecutive repeats collapsed.

    A row names each club several times over (crest alt text, short name,
    full name), so only the transitions between clubs carry information.
    """
    found = []
    for m in TEAM_FIND_RE.finditer(text):
        name = m.group(0)
        if not found or found[-1] != name:
            found.append(name)
    return found


def round_from(cells):
    """Round number: the first standalone small integer cell, if any."""
    for cell in cells:
        if cell.isdigit() and len(cell) <= 3:
            return int(cell)
    return None


def venue_from(cells, pair):
    """Best-effort venue: the last cell that is not a team, date, time or number."""
    for cell in reversed(cells):
        # Skip separator and label cells ("VS", "-", a lone weekday).
        if len(cell) < 4 or cell.isdigit():
            continue
        if DATE_RE.search(cell) or TIME_RE.search(cell):
            continue
        # A venue legitimately contains a club name ("Zimný štadión HC
        # Košice"), so only reject a cell that is *nothing but* club names.
        remainder = cell
        for team in pair:
            remainder = remainder.replace(team, " ")
        if len(remainder.strip()) < 4:
            continue
        return cell
    return ""


def parse_games_from_rows(soup, target_team):
    """Read the schedule off the table rows.

    Structural rather than positional: a row counts if it names two clubs,
    a date and a time, whatever order its columns are in. Reordered or
    added columns then no longer take the whole parse to zero, which is
    what a single flattened-text regex does.
    """
    games = []
    seen = set()

    for tr in soup.find_all("tr"):
        cells = [" ".join(c.get_text(" ").split()) for c in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        row_text = " ".join(cells)

        pair = teams_in(row_text)
        # Home first, away second — the order the fixture is written in.
        if len(pair) < 2 or target_team not in pair[:2]:
            continue

        date_m = DATE_RE.search(row_text)
        time_m = TIME_RE.search(row_text)
        if not date_m or not time_m:
            continue

        d, mo, y = (int(x) for x in date_m.groups())
        date_str = f"{d:02d}.{mo:02d}.{y}"
        time_str = f"{int(time_m.group(1)):02d}:{time_m.group(2)}"

        home, away = pair[0], pair[1]
        key = (date_str, home, away)
        if key in seen:
            continue
        seen.add(key)

        games.append(
            {
                "round": round_from(cells),
                "is_home": home == target_team,
                "opponent": away if home == target_team else home,
                "date": date_str,
                "time": time_str,
                "venue": venue_from(cells, (home, away)),
            }
        )

    return games


def parse_games_from_text(page_text: str, target_team: str):
    """Legacy fallback for a page whose fixtures are not in a table."""
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


def parse_games(html: str, target_team: str):
    soup = BeautifulSoup(html, "html.parser")
    games = parse_games_from_rows(soup, target_team)

    if len(games) < MIN_GAMES:
        fallback = parse_games_from_text(soup.get_text(separator=" "), target_team)
        if len(fallback) > len(games):
            return fallback

    return games


_page_dumped = False


def dump_page(slug: str, html: str) -> None:
    """Save and summarise a page that would not parse.

    Whether the markup changed shape or the fixtures are not in the HTML
    at all (rendered client-side) decides the fix, and the game count
    alone does not distinguish them.
    """
    global _page_dumped
    if _page_dumped:
        return
    _page_dumped = True

    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.get_text(separator=" ").split())

    print(
        "\n--- page diagnostics (first team that parsed too few games) ---",
        file=sys.stderr,
    )
    print(f"html length: {len(html)}", file=sys.stderr)
    print(
        f"tables: {len(soup.find_all('table'))}, "
        f"rows: {len(soup.find_all('tr'))}, "
        f"team-name mentions: {len(TEAM_FIND_RE.findall(text))}",
        file=sys.stderr,
    )
    print(f"text[:1200]: {text[:1200]}", file=sys.stderr)

    target = Path(DEBUG_DIR) if DEBUG_DIR else Path(tempfile.gettempdir())
    try:
        target.mkdir(parents=True, exist_ok=True)
        out = target / f"{slug}.debug.html"
        out.write_text(html, encoding="utf-8")
        print(f"saved raw HTML to {out}", file=sys.stderr)
    except OSError as e:
        print(f"(could not save raw HTML: {e})", file=sys.stderr)
    print("--- end page diagnostics ---\n", file=sys.stderr)


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
        # Round is not always present in the markup; the date plus the
        # opponent still identifies the fixture uniquely, and the UID has
        # to stay stable so subscribers do not see duplicate events.
        if g["round"] is None:
            opp_key = re.sub(r"[^a-z0-9]+", "", g["opponent"].lower())
            uid = f"{slug}-{y}{mo}{d}-{opp_key}@tipsportliga"
        else:
            uid = f"{slug}-{g['round']}-{y}{mo}{d}@tipsportliga"

        round_part = f"kolo {g['round']}, " if g["round"] is not None else ""
        if g["is_home"]:
            summary = f"{team_name} - {g['opponent']}"
            desc = (
                f"Domáci zápas {team_name} ({round_part}Tipsport liga). "
                f"Súper: {g['opponent']}."
            )
        else:
            summary = f"{g['opponent']} - {team_name}"
            desc = (
                f"Zápas {team_name} na ihrisku súpera ({round_part}"
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
            html = fetch_page_html(session, url)
            games = parse_games(html, team_name)

            if len(games) < MIN_GAMES:
                # Skip this team rather than overwrite a good file with a
                # broken one, and dump the page so the shape can be seen.
                print(
                    f"WARNING: {team_name}: only parsed {len(games)} games, "
                    "expected ~54. Skipping this team's file.",
                    file=sys.stderr,
                )
                dump_page(slug, html)
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
            backend = "curl_cffi" if use_curl_cffi() else "requests"
            print(
                f"\nEvery request was rejected by the site's edge, using the "
                f"{backend} backend. See the block diagnostics above: response "
                "headers and body identify which product is refusing and why.\n"
                "  - A JS/CAPTCHA challenge page means no HTTP client will get "
                "through; it needs a real browser.\n"
                "  - A bare deny with no challenge means the source IP is "
                "blocked. GitHub-hosted runner ranges are widely blocklisted, "
                "and nothing inside this script can change that — it needs a "
                "self-hosted runner or an outbound proxy on an accepted "
                "network.\n"
                "Run the same script from a local machine to tell the two "
                "apart: if it works there and not here, the block is the IP.",
                file=sys.stderr,
            )
        sys.exit(1)


if __name__ == "__main__":
    main()
