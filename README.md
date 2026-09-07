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
