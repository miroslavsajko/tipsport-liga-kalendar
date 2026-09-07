# Tipsport liga calendar sync

Daily auto-updated `.ics` calendars — one per Tipsport liga team.

- `scripts/update_calendar.py` — scrapes hockeyslovakia.sk and rebuilds one `.ics` per team into `docs/`
- `scripts/run_local.sh` — scrape, commit and push in one step
- `docs/<team-slug>.ics` — the files GitHub Pages serves publicly

Updates are run by hand from a machine on a home connection, then committed.
There is no scheduled workflow: Cloudflare blocks hosted runners (see
*Why this is not automated*), so an automated one could only fail.

## Team files
| Team | File |
|---|---|
| HC '05 Banská Bystrica | `hc-05-banska-bystrica.ics` |
| HC Košice | `hc-kosice.ics` |
| HC Prešov | `hc-presov.ics` |
| HC Slovan Bratislava | `hc-slovan-bratislava.ics` |
| HK 32 Liptovský Mikuláš | `hk-32-liptovsky-mikulas.ics` |
| HK Dukla Michalovce | `hk-dukla-michalovce.ics` |
| HK Dukla Trenčín | `hk-dukla-trencin.ics` |
| HK Nitra | `hk-nitra.ics` |
| HK Poprad | `hk-poprad.ics` |
| HK Spišská Nová Ves | `hk-spisska-nova-ves.ics` |
| HKM Zvolen | `hkm-zvolen.ics` |
| Vlci Žilina | `vlci-zilina.ics` |

## Subscribe in Google Calendar
Google Calendar → "Other calendars" → "+" → "From URL", paste the file's
published Pages URL (see repo Settings → Pages), e.g.:

    https://<username>.github.io/<repo-name>/hc-kosice.ics

Repeat per team you want. Google re-fetches subscribed URL calendars roughly
every 12-24 hours on its own schedule (not instantly), which is fine for a
daily-updated source.

Google re-fetches on its own schedule, so a freshly committed change shows up
within a day rather than immediately.

## Updating the calendars

Run this from a machine on a home/residential connection:

```bash
python3 -m venv .venv && .venv/bin/pip install -r scripts/requirements.txt
.venv/bin/playwright install chromium

scripts/run_local.sh              # scrape, commit and push
scripts/run_local.sh --no-push    # scrape only, leave the changes to review
```

`run_local.sh` uses `.venv/bin/python` when it exists. It publishes a partial
scrape rather than holding every calendar back when one team fails, and exits
non-zero so the failure is still visible. Push authentication uses whatever the
checkout already has. To run the scraper alone: `python3 scripts/update_calendar.py`.

Committing to `main` is what publishes — GitHub Pages serves `docs/`.

### Why this is not automated

Cloudflare serves a managed JS challenge based on the **source IP**, not on how
the client looks:

| Environment | Result |
|---|---|
| A home/residential connection | works — all 12 teams scrape |
| GitHub-hosted Actions runners | blocked (`cf-mitigated: challenge`) |
| Railway | blocked, same challenge |

The telling detail: a plain HTTP client with no browser at all gets `200` from a
residential IP, while a real Chromium executing the challenge gets blocked from
a datacenter one. A scheduled workflow on hosted runners therefore cannot work,
whatever the client or language. To schedule it anyway, use a self-hosted runner
or a local cron/systemd timer calling `run_local.sh` — both on a machine with a
residential connection.

## Troubleshooting: 403 from hockeyslovakia.sk
The site sits behind a WAF that rejects requests that don't look like a real
browser. The scraper therefore sends a full browser header set, reuses one
`requests.Session` (warmed up on the site root so edge cookies are carried),
retries `403`/`429`/`5xx` with exponential backoff, and pauses briefly between
teams.

If it starts returning 403 again, tune it with environment variables rather
than code changes:

| Variable | Default | Purpose |
|---|---|---|
| `SCRAPER_USER_AGENT` | current Chrome UA string | Refresh the browser identity (plain-`requests` backend only) |
| `SCRAPER_DELAY` | `1.5` | Seconds between team pages |
| `SCRAPER_MAX_ATTEMPTS` | `4` | Retries per page |
| `SCRAPER_IMPERSONATE` | `chrome` | curl_cffi browser profile, e.g. `chrome131`, `safari17_0` |
| `SCRAPER_FORCE_REQUESTS` | `0` | Set to `1` to bypass curl_cffi and use plain `requests` |
| `SCRAPER_MIN_GAMES` | `40` | Below this a team is treated as a parse failure |
| `SCRAPER_DEBUG_DIR` | temp dir | Where an unparseable page is saved for inspection |
| `SCRAPER_BACKEND` | `auto` | `auto`, `browser`, `curl_cffi` or `requests` |
| `SCRAPER_CHALLENGE_TIMEOUT` | `45` | Seconds to let the interstitial run |
| `SCRAPER_BROWSER_HEADLESS` | `1` | `0` runs headed (pair with `xvfb-run`) |
| `SCRAPER_BROWSER_PATH` | Playwright's | Override the Chromium binary path |

The site is behind **Cloudflare**, which serves a JS interstitial
(`cf-mitigated: challenge`, "Just a moment..."). No HTTP client can clear
that — the challenge has to be executed, not re-requested — so the scraper
escalates through three backends:

1. **curl_cffi** — replays a real Chrome TLS/HTTP2 fingerprint. Bot
   management matches on that fingerprint (JA3) whatever the headers say, and
   plain `requests` has an obvious one.
2. **requests** — fallback when curl_cffi is unavailable.
3. **Playwright Chromium** — a real browser, used automatically the moment a
   challenge is detected. One browser context is reused across all teams, so
   the clearance cookie from the first solve makes the other eleven pages
   ordinary requests.

The log line `HTTP backend: ...` says which is in use, and a challenge prints
`escalating to a real browser`. Set `SCRAPER_BACKEND=browser` to skip straight
to Chromium.

**Caveat:** a headless browser on a datacenter IP is exactly what a managed
challenge targets, which is why this only works from a residential connection.
From home the challenge usually does not appear at all and curl_cffi alone is
enough.

When a request is refused, the script dumps the first blocked response —
status, telltale headers (`server`, `cf-ray`, `x-iinfo`, …) and a body
snippet. That identifies the blocker, which decides the fix:

- **A JS/CAPTCHA challenge page** — no HTTP client gets through; it needs a
  real browser (Playwright).
- **A bare deny, no challenge** — the source IP is blocked, and nothing in this
  script can change that; it needs a different network.

A failed run never overwrites a good `.ics`: teams that error out, or that
parse fewer than `SCRAPER_MIN_GAMES` games, are skipped and their existing
file is left alone.

## Parsing
Fixtures are read structurally: a table row counts as a game if it names two
clubs, a date and a time, in whatever column order. That survives reordered
or added columns, which a single flattened-text regex does not — the original
regex went to zero games on a markup change. That regex is kept as a fallback
for a page whose fixtures are not in a table.

The round number is optional. When the markup does not carry one, the event
UID falls back to date-plus-opponent so it stays stable across runs and
subscribers do not see duplicated events.

If a page still parses short, the script saves it (`SCRAPER_DEBUG_DIR`) and
prints its size, table/row counts and a text snippet — enough to tell a
markup change from fixtures that are rendered client-side and simply are not
in the HTML.
