# Tipsport liga calendar sync

Daily auto-updated `.ics` calendars — one per Tipsport liga team.

- `scripts/update_calendar.py` — scrapes hockeyslovakia.sk and rebuilds one `.ics` per team in `docs/`
- `.github/workflows/update-calendar.yml` — runs the script daily at 06:00 UTC (and on manual trigger)
- `docs/<team-slug>.ics` — the files GitHub Pages serves publicly

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

Note: only `hc-kosice.ics` is included in this initial commit (pre-built from
the current schedule). The other 11 team files will appear in `docs/` after
the workflow's first run (manual trigger or the next 06:00 UTC).

## Troubleshooting: 403 from hockeyslovakia.sk
The site sits behind a WAF that rejects requests that don't look like a real
browser. The scraper therefore sends a full browser header set, reuses one
`requests.Session` (warmed up on the site root so edge cookies are carried),
retries `403`/`429`/`5xx` with exponential backoff, and pauses briefly between
teams.

If it starts returning 403 again, tune it without touching the code via
repository variables (Settings → Secrets and variables → Actions → Variables):

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

**Caveat:** a headless browser on a datacenter IP is exactly what a Cloudflare
managed challenge targets, so this can still fail on GitHub-hosted runners. If
it does, the script says so and lists the options: headed under `xvfb-run`
(`SCRAPER_BROWSER_HEADLESS=0`), a self-hosted runner or local cron on a
residential IP, or asking the site to allowlist the scraper.

When a request is refused, the script dumps the first blocked response —
status, telltale headers (`server`, `cf-ray`, `x-iinfo`, …) and a body
snippet. That identifies the blocker, which decides the fix:

- **A JS/CAPTCHA challenge page** — no HTTP client gets through; it needs a
  real browser (Playwright).
- **A bare deny, no challenge** — the source IP is blocked. GitHub-hosted
  runner ranges are widely blocklisted and nothing in this script can change
  that; it needs a self-hosted runner or an outbound proxy on an accepted
  network.

Running the script from a local machine tells the two apart: if it works
there and not in Actions, the block is on the IP.

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
