# Deployment

## Purpose
Run SkillPulse once a day at 06:30 IST on a small Linux server. Missed runs catch up, the DB is backed up nightly, and the
whole thing is easy to move to GitHub Actions later.

## Key files
- `deploy/setup.sh`: one-shot, idempotent server setup (user, uv, clone, `uv sync`, `.env`, timezone, systemd units).
- `deploy/update.sh`: pull + re-sync + re-install units.
- `deploy/systemd/skillpulse.{service,timer}`: daily run, `OnCalendar=*-*-* 06:30:00 Asia/Kolkata`, `Persistent=true`.
- `deploy/systemd/skillpulse-backup.{service,timer}`: `skillpulse backup --keep 14` at 02:15 IST.
- `deploy/crontab.example`: the cron alternative.

## Target
Ubuntu 24.04 LTS. 1 vCPU / 1–2 GB RAM is plenty, because the job is I/O-bound and finishes in minutes.
An India region (e.g. AWS ap-south-1 Mumbai / ap-south-2 Hyderabad, GCP asia-south1, Azure Central India) keeps
Adzuna/ATS geolocation and latency sensible. Disk: < 1 GB for years of data.

## Setup
```bash
# on the server, as a sudo user
git clone <repo-url> /tmp/skillpulse && sudo bash /tmp/skillpulse/deploy/setup.sh <repo-url> main
sudo -u skillpulse nano /opt/skillpulse/.env          # fill in keys (file is chmod 600)
sudo -u skillpulse -H bash -c 'cd /opt/skillpulse && ~/.local/bin/uv run skillpulse send-test'
sudo -u skillpulse -H bash -c 'cd /opt/skillpulse && ~/.local/bin/uv run skillpulse check-boards'
sudo systemctl start skillpulse.service               # optional: first real run now
```
What `setup.sh` does: sets the server timezone to `Asia/Kolkata`; installs git/curl/sqlite3; creates the system user
`skillpulse`; installs `uv` for that user; clones to `/opt/skillpulse`; runs `uv sync --no-dev --frozen`
(Python 3.12+ is fetched by uv if the system lacks it); creates `.env` from `.env.example` with mode 600;
templates and enables the systemd timers.

## Operating it
| Task | Command |
|---|---|
| Next scheduled runs | `systemctl list-timers 'skillpulse*'` |
| Run now | `sudo systemctl start skillpulse.service` |
| Logs | `journalctl -u skillpulse.service -n 200` · `/opt/skillpulse/logs/skillpulse.log` (JSON lines) |
| Cache / spend | `sudo -u skillpulse -H bash -c 'cd /opt/skillpulse && ~/.local/bin/uv run skillpulse cache stats'` |
| Re-send a day | `… uv run skillpulse report --date 2026-11-02 --email` |
| Update code | `sudo bash /opt/skillpulse/deploy/update.sh main` |
| Restore a backup | stop the timer, `cp data/backups/skillpulse-<stamp>.db data/skillpulse.db`, start the timer |

**Why a systemd timer over cron:** `Persistent=true` runs a missed 06:30 job at boot, the journal keeps stdout, and
`systemctl --failed` shows failures. Cron alternative (no catch-up):
`30 6 * * * cd /opt/skillpulse && uv run skillpulse run >> logs/cron.log 2>&1` (see `deploy/crontab.example`).

**Overlap:** the app takes a lock on `data/skillpulse.lock`. A second concurrent run exits with code 2. systemd also
never starts a oneshot unit twice at once.

**Backups:** `skillpulse backup` uses SQLite's online backup API, so it's safe while the app runs, and writes
`data/backups/skillpulse-YYYY-MM-DD_HHMMSS.db`, keeping the newest 14. For off-box copies, add an `rclone`/`aws s3 cp`
of `data/backups/` to the backup service.

## Migrating to GitHub Actions
**State that must persist between runs:**
| Path | Needed? | Notes |
|---|---|---|
| `data/skillpulse.db` | **yes** | cache, history and baselines. Without it every run starts cold and re-pays for extraction |
| `reports/` | nice to have | archive. Can be uploaded as a workflow artifact, or committed to a `reports` branch |
| `memory/MEMORY.md` | optional | regenerated each run. Commit it if you want it in the repo |
| `logs/` | no | the Actions log captures stdout |

**Persisting the DB** (pick one):
1. **Object storage (recommended)**: S3 / Cloudflare R2 / GCS. Download at job start, upload at the end (even on failure).
   No code change is needed. If you later want it in code, wrap `SqliteStore` in a `Store` implementation that syncs on
   open/close. **Only `skillpulse/store/` changes.**
2. **Release asset**: `gh release download db --pattern skillpulse.db` / `gh release upload db skillpulse.db --clobber`.
   Free and simple, but it's public on public repos, so use a private repo.
3. Not workflow artifacts alone: they expire (max 90 days) and are awkward to fetch across runs.

**Secrets to set** (repo → Settings → Secrets and variables → Actions): `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY`,
`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `ZOHO_SMTP_USER`, `ZOHO_SMTP_APP_PASSWORD`, `REPORT_TO`, optional `SERPAPI_API_KEY`,
plus storage credentials (e.g. `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, or use OIDC).
SkillPulse reads them from the environment. `python-dotenv` doesn't override existing env vars, and no `.env` file is needed.

**Schedule:** Actions cron is UTC and often runs 5–30 min late. 06:30 IST = 01:00 UTC, so schedule `0 0 * * *`
(05:30 IST) for a report before the workday. Use `concurrency: skillpulse` in place of the lock file across runners.

Example workflow (not installed; copy to `.github/workflows/skillpulse.yml` when migrating):
```yaml
name: skillpulse
on:
  schedule: [{cron: "0 0 * * *"}]
  workflow_dispatch: {}
concurrency: skillpulse
jobs:
  run:
    runs-on: ubuntu-24.04
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --no-dev --frozen
      - name: Restore DB
        run: aws s3 cp s3://$BUCKET/skillpulse.db data/skillpulse.db || echo "no DB yet (first run)"
        env: {BUCKET: "${{ vars.SKILLPULSE_BUCKET }}", AWS_ACCESS_KEY_ID: "${{ secrets.AWS_ACCESS_KEY_ID }}", AWS_SECRET_ACCESS_KEY: "${{ secrets.AWS_SECRET_ACCESS_KEY }}", AWS_DEFAULT_REGION: ap-south-1}
      - name: Run
        run: uv run skillpulse run
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          ADZUNA_APP_ID: ${{ secrets.ADZUNA_APP_ID }}
          ADZUNA_APP_KEY: ${{ secrets.ADZUNA_APP_KEY }}
          ZOHO_SMTP_USER: ${{ secrets.ZOHO_SMTP_USER }}
          ZOHO_SMTP_APP_PASSWORD: ${{ secrets.ZOHO_SMTP_APP_PASSWORD }}
          REPORT_TO: ${{ secrets.REPORT_TO }}
          TZ: Asia/Kolkata
      - name: Save DB
        if: always()
        run: aws s3 cp data/skillpulse.db s3://$BUCKET/skillpulse.db
        env: {BUCKET: "${{ vars.SKILLPULSE_BUCKET }}", AWS_ACCESS_KEY_ID: "${{ secrets.AWS_ACCESS_KEY_ID }}", AWS_SECRET_ACCESS_KEY: "${{ secrets.AWS_SECRET_ACCESS_KEY }}", AWS_DEFAULT_REGION: ap-south-1}
      - uses: actions/upload-artifact@v4
        if: always()
        with: {name: "report-${{ github.run_id }}", path: reports/, retention-days: 90}
```
Note: the run date comes from `schedule.timezone` (Asia/Kolkata), so a 00:00 UTC run is still labelled with the IST date.
SMTP to `smtp.zoho.in:465` works from GitHub-hosted runners.

## Decisions and why
- **Non-root system user, `/opt/skillpulse`, `.env` mode 600**: least privilege. The unit adds `NoNewPrivileges`,
  `PrivateTmp`, `ProtectSystem=full`.
- **Backups at 02:15**: far from the 06:30 run. The online backup is safe either way.
- **`uv sync --frozen`** uses the committed `uv.lock`, so the server runs exactly the tested dependency set.

## Known limitations
- The scripts were checked with `bash -n`, `systemd-analyze calendar` and `systemd-analyze verify` in the build sandbox,
  not on a real Ubuntu 24.04 VM. Run `setup.sh` on a fresh VM once and check `systemctl list-timers`.
- If SMTP is down, neither the report nor the failure email arrives. Watch `systemctl --failed`, or add
  `OnFailure=` to a unit that alerts by another channel.

## TODOs
- Off-box backup upload (rclone/S3) in `skillpulse-backup.service`.
