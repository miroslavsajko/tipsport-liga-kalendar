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
| `SCRAPER_USER_AGENT` | current Chrome UA string | Refresh the browser identity |
| `SCRAPER_DELAY` | `1.5` | Seconds between team pages |
| `SCRAPER_MAX_ATTEMPTS` | `4` | Retries per page |

If *every* team fails with 403 even after that, the block is on the source IP,
not the headers — GitHub-hosted runner ranges are widely blocklisted. The
remaining options are a self-hosted runner or an outbound proxy on an
acceptable network. The script prints this hint when all teams fail.

A failed run never overwrites a good `.ics`: teams that error out, or that
parse fewer than 40 games, are skipped and their existing file is left alone.
