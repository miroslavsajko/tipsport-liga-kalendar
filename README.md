# Tipsport liga calendar sync

Daily auto-updated `.ics` calendars — one per Tipsport liga team.

- `scripts/update_calendar.py` — scrapes hockeyslovakia.sk and rebuilds one `.ics` per team
- `scripts/service.py` — the Railway service: scrapes on a schedule, serves the
  `.ics` files over HTTP, pushes them back to this repo
- `scripts/publish_github.py` — commits the scraped files to `docs/` on `main`
- `Dockerfile` / `railway.json` — the Railway deployment
- `.github/workflows/update-calendar.yml` — manual trigger only; the daily cron
  is disabled because Cloudflare challenges GitHub-hosted runners
- `docs/<team-slug>.ics` — the files GitHub Pages serves publicly

## Where this can run

Cloudflare serves a managed JS challenge to **datacenter IPs**, and that is the
whole story — it is not about headers, TLS fingerprints or the browser:

| Environment | Result |
|---|---|
| A home/residential connection | works — all 12 teams scrape |
| GitHub-hosted Actions runners | blocked (`cf-mitigated: challenge`) |
| Railway | blocked, same challenge |

So the scraper has to run from a residential IP. Three ways, in order of how
little there is to go wrong:

1. **`scripts/run_local.sh` on a schedule** on a machine you already own — see
   *Running locally on a schedule*. Simplest, and it is the configuration
   already proven to work.
2. **A self-hosted GitHub Actions runner** on that machine. Keeps the Actions
   UI, schedule, logs and secrets; set the repository variable `RUNNER_LABEL`
   to `self-hosted` and re-enable the cron in the workflow.
3. **Railway plus a residential proxy** (`SCRAPER_PROXY`). Only worth it if you
   have no always-on machine, since it means paying for both.

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

## Running locally on a schedule

`scripts/run_local.sh` scrapes, commits and pushes. It publishes a partial
scrape rather than holding everything back when one team fails, and exits
non-zero so a scheduler still records the failure.

```bash
python3 -m venv .venv && .venv/bin/pip install -r scripts/requirements.txt
.venv/bin/playwright install chromium
scripts/run_local.sh --no-push   # try it once without pushing
```

The script uses `.venv/bin/python` when that exists, so a scheduler needs no
global install. Push authentication uses whatever the checkout already has
(SSH key or credential helper).

**cron** (Linux/macOS) — every day at 06:30:

```cron
30 6 * * * /path/to/tipsport-liga-kalendar/scripts/run_local.sh >> /tmp/tipsport-kalendar.log 2>&1
```

**launchd** (macOS, survives sleep better than cron) — write a
`~/Library/LaunchAgents/sk.tipsport.kalendar.plist` with `StartCalendarInterval`
and `ProgramArguments` pointing at the script, then `launchctl load` it.

**systemd** (Linux) — a `tipsport-kalendar.service` of `Type=oneshot` running
the script, plus a `.timer` with `OnCalendar=*-*-* 06:30:00` and
`Persistent=true` so a missed run happens at next boot.

**Windows** — Task Scheduler, running `bash scripts/run_local.sh` under WSL or
Git Bash.

If the machine is often asleep, prefer the self-hosted Actions runner: GitHub
queues the scheduled run and it executes when the runner next comes online.

## Deploying on Railway

**This is blocked as-is** — Railway's IPs get the same Cloudflare challenge as
GitHub Actions. The deployment below only works with `SCRAPER_PROXY` pointing
at a residential/ISP proxy. It is kept because everything except the egress IP
is proven working: the service builds, scrapes, serves and pushes.


The service is one always-on container: a background thread scrapes every
`SCRAPE_INTERVAL_HOURS` and pushes to GitHub, while an HTTP server on `$PORT`
serves the same files. `Dockerfile` builds it (Nixpacks will not install
Chromium's system libraries, so the build is explicit) and `railway.json`
points the healthcheck at `/healthz`.

### 1. Create the service
New Project → **Deploy from GitHub repo** → this repo. Railway detects the
`Dockerfile` and builds it. The first build takes a few minutes: it installs
Chromium and its system dependencies.

### 2. Add a volume
Service → **Variables/Settings → Volumes** → New Volume, **mount path `/data`**.
This is required, not optional. It holds three things:

| Path | Why it matters |
|---|---|
| `/data/ics` | The generated calendars, so a restart still serves them |
| `/data/browser-profile` | Cloudflare's clearance cookie, so the challenge is solved once rather than on every scrape |
| `/data/repo` | Scratch clone used when pushing to GitHub |

### 3. Set variables
Service → **Variables**. Only the two GitHub ones are required, and only
because you asked for the push-back-to-GitHub half:

| Variable | Value | Notes |
|---|---|---|
| `GITHUB_TOKEN` | a fine-grained PAT | **Required for the push.** Scope it to this repo only, with `Contents: Read and write`. Nothing else. |
| `GITHUB_REPOSITORY` | `miroslavsajko/tipsport-liga-kalendar` | Where to push |
| `GIT_BRANCH` | `main` | Optional, defaults to `main` |
| `SCRAPE_INTERVAL_HOURS` | `24` | Optional, defaults to 24 |
| `SCRAPE_RETRY_MINUTES` | `60` | Optional; retry gap after a failed run |

`SCRAPER_OUTPUT_DIR`, `SCRAPER_BROWSER_PROFILE`, `GIT_WORK_DIR` and `PORT` are
already set in the `Dockerfile` and only need overriding if you move the mount.
Leave `GITHUB_TOKEN` unset and the service still scrapes and serves — it just
skips the push and says so in `/healthz`.

### 4. Generate a domain
Settings → **Networking → Generate Domain**. Railway injects `$PORT`; the
service binds it. Then:

- `https://<app>.up.railway.app/` — index of every team with last-updated times
- `https://<app>.up.railway.app/hc-kosice.ics` — subscribe directly
- `https://<app>.up.railway.app/healthz` — JSON status: last run, teams
  written, failures, push result

The first scrape starts in the background right after boot, so the port binds
immediately and the healthcheck passes before any scraping finishes. Until the
first run completes the index shows "not generated yet".

### Cost note
This is an always-on container sleeping most of the day for a once-daily job.
On Railway's usage-based pricing that is small but not zero. Railway cron
services would be cheaper, but they cannot also serve the files, and a fresh
container each run would discard the Cloudflare clearance cookie.

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
| `SCRAPER_PROXY` | none | Outbound proxy, `http://user:pass@host:port`, used by both the HTTP clients and Chromium |

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
