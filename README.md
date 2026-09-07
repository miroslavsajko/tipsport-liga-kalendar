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
pip install -r scripts/requirements.txt

scripts/run_local.sh              # scrape, commit and push
scripts/run_local.sh --no-push    # scrape only, leave the changes to review
```

It publishes a partial scrape rather than holding every calendar back when one
team fails, and exits non-zero so the failure is still visible. Push
authentication uses whatever the checkout already has. Set `PYTHON` to use a
particular interpreter (a venv, say). To run the scraper alone:
`python3 scripts/update_calendar.py`.

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

## How it works

`curl_cffi` fetches each team page while replaying a real Chrome TLS/HTTP2
fingerprint — bot management matches on that (JA3) whatever the headers say,
and plain `requests` has an obvious one. From a residential connection that is
enough; Cloudflare does not challenge.

Fixtures are read structurally: a table row counts as a game if it names two
clubs, a date and a time, in whatever column order. That survives reordered or
added columns, unlike matching a fixed text layout.

Two guards, both of which have caught real breakage:

- A team parsing fewer than `MIN_GAMES` (40) games keeps its existing `.ics`
  rather than overwriting it, and the page is saved to a temp file for
  inspection.
- A fixture row naming a club that is not in `TEAMS` is reported. A club
  renamed on the site (new sponsor, different apostrophe) otherwise vanishes
  from every opponent's calendar while the count stays above the threshold.

If it stops working, the likely causes are a renamed club (update `TEAMS`), a
changed page structure (the saved page shows what arrived), or a Cloudflare
challenge (you are not on a residential connection).
