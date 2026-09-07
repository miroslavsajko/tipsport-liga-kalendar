#!/usr/bin/env python3
"""
Scrapes every Tipsport liga team's schedule from hockeyslovakia.sk and
regenerates one .ics file per team (home/away noted, correct local
kickoff time converted to UTC).

Run with: python3 scripts/update_calendar.py
Writes to: docs/<team-slug>.ics  (one file per team)

Must be run from a residential connection: the site is behind Cloudflare,
which challenges datacenter IPs. See the README.
"""

import re
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from curl_cffi import requests

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
BASE_URL = "https://www.hockeyslovakia.sk"
TZ = ZoneInfo("Europe/Bratislava")
UTC = ZoneInfo("UTC")

GAME_DURATION = timedelta(hours=2, minutes=30)
REQUEST_TIMEOUT = 30
PER_TEAM_DELAY = 1.5
# A full season is ~54 games. Far fewer means parsing broke, so the
# existing file is kept rather than overwritten with a broken one.
MIN_GAMES = 40

# name -> (team_id, slug)
TEAMS = {
    "HC ‘05 TAM Banská Bystrica": (670399, "hc-05-banska-bystrica"),
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

# Longest first, so "HK Dukla Trenčín" wins over any shorter substring.
CLUB_RE = re.compile("|".join(re.escape(t) for t in sorted(TEAMS, key=len, reverse=True)))
DATE_RE = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})")
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")


def team_page_url(team_id: int, slug: str) -> str:
    return (
        f"{BASE_URL}/sk/stats/teams/1197/tipsport-liga/"
        f"team/{team_id}/{slug}/Program"
    )


def fetch(session, url: str) -> str:
    """Fetch a team page, or explain a Cloudflare challenge."""
    resp = session.get(url, timeout=REQUEST_TIMEOUT)
    if "Just a moment" in resp.text[:4000] or resp.status_code == 403:
        raise RuntimeError(
            "Cloudflare challenge — run this from a residential connection, "
            "not a hosted runner"
        )
    resp.raise_for_status()
    return resp.text


def clubs_in(text: str):
    """Clubs named in a row, in order, with consecutive repeats collapsed.

    A row names each club several times over (crest alt text, short name,
    full name), so only the transitions between clubs carry information.
    """
    found = []
    for m in CLUB_RE.finditer(text):
        if not found or found[-1] != m.group(0):
            found.append(m.group(0))
    return found


def parse_games(html: str, target_team: str):
    """Read the schedule off the table rows.

    Structural rather than positional: a row counts if it names two clubs,
    a date and a time, whatever order its columns are in. Reordered or
    added columns then do not take the whole parse to zero.
    """
    soup = BeautifulSoup(html, "html.parser")
    games = []
    seen = set()
    unmatched = []

    for tr in soup.find_all("tr"):
        cells = [" ".join(c.get_text(" ").split()) for c in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        row = " ".join(cells)

        date_m = DATE_RE.search(row)
        time_m = TIME_RE.search(row)
        if not (date_m and time_m):
            continue

        clubs = clubs_in(row)
        if len(clubs) < 2:
            # A club renamed on the site matches nothing here, and its
            # games would vanish from every opponent's calendar while the
            # count stayed above MIN_GAMES. Losing games quietly is worse
            # than a noisy warning.
            unmatched.append(row[:120])
            continue
        if target_team not in clubs[:2]:
            continue

        d, mo, y = (int(x) for x in date_m.groups())
        date_str = f"{d:02d}.{mo:02d}.{y}"
        home, away = clubs[0], clubs[1]  # home first, as the fixture is written
        if (date_str, home, away) in seen:
            continue
        seen.add((date_str, home, away))

        # Round number: the first standalone small integer cell, if any.
        rounds = [int(c) for c in cells if c.isdigit() and len(c) <= 3]

        games.append(
            {
                "round": rounds[0] if rounds else None,
                "is_home": home == target_team,
                "opponent": away if home == target_team else home,
                "date": date_str,
                "time": f"{int(time_m.group(1)):02d}:{time_m.group(2)}",
            }
        )

    if unmatched:
        print(
            f"WARNING: {target_team}: {len(unmatched)} fixture row(s) name a club "
            f"missing from TEAMS, so those games are dropped. A club was probably "
            f"renamed on the site — update TEAMS. First row: {unmatched[0]}",
            file=sys.stderr,
        )

    return games


def build_ics(team_name: str, slug: str, games) -> str:
    now_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{team_name} Schedule//Tipsport liga//SK",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{team_name} - Tipsport liga",
    ]

    for g in games:
        d, mo, y = g["date"].split(".")
        hh, mm = g["time"].split(":")
        start = datetime(int(y), int(mo), int(d), int(hh), int(mm), tzinfo=TZ).astimezone(UTC)
        end = start + GAME_DURATION

        # The round is not always in the markup; date plus opponent still
        # identifies the fixture, and the UID has to stay stable so
        # subscribers do not see duplicate events.
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
            f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{desc}",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main():
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    failures = []

    # Impersonate a real Chrome TLS/HTTP2 fingerprint: bot management
    # matches on that (JA3) whatever the headers say.
    session = requests.Session(impersonate="chrome")
    session.headers.update({"Accept-Language": "sk-SK,sk;q=0.9,en;q=0.8"})

    for i, (team_name, (team_id, slug)) in enumerate(TEAMS.items()):
        if i:
            time.sleep(PER_TEAM_DELAY)

        try:
            html = fetch(session, team_page_url(team_id, slug))
            games = parse_games(html, team_name)

            if len(games) < MIN_GAMES:
                debug = Path(tempfile.gettempdir()) / f"{slug}.debug.html"
                debug.write_text(html, encoding="utf-8")
                print(
                    f"WARNING: {team_name}: only parsed {len(games)} games, "
                    f"expected ~54. Keeping the existing file; page saved to {debug}",
                    file=sys.stderr,
                )
                failures.append(team_name)
                continue

            out_path = DOCS_DIR / f"{slug}.ics"
            out_path.write_text(build_ics(team_name, slug, games), encoding="utf-8")
            print(f"{team_name}: wrote {len(games)} games to {out_path}")

        except Exception as e:
            print(f"ERROR: {team_name}: {e}", file=sys.stderr)
            failures.append(team_name)

    if failures:
        print(f"\nCompleted with issues for: {', '.join(failures)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
