# Lorcana Stock Watcher

Self-contained watcher that polls Ravensburger / Disney Lorcana product pages
every 3 minutes and pushes you a notification whenever a product's stock status
**changes** (in stock ↔ out of stock).

- **Zero third-party dependencies** — pure Python standard library.
- **Reliable detection** — reads the page's `schema.org` structured data
  (`availability: InStock` vs `OutOfStock`), with a visible-text fallback.
- **No spam** — only notifies when stock status actually changes; new products are baselined silently on first check.
- **Runs on GitHub Actions** every 3 minutes (via cron-job.org) — no Mac required, works 24/7.
- **Portable state** — persisted in git with timestamped history snapshots.

## Files

| File | Purpose |
|------|---------|
| `stock_watcher.py` | The watcher. Edit `PRODUCTS` to change which URLs are tracked. |
| `state/current.json` | Live stock state (committed after each run). |
| `state/history/` | Immutable snapshots written when stock status changes. |
| `.env` | Local config/secrets (ntfy topic, optional SMTP). Gitignored. |
| `.env.example` | Template for the above. |
| `.github/workflows/stock-watcher.yml` | Scheduled GitHub Actions runner. |
| `install.sh` / `run.sh` | Optional local launchd setup (macOS only). |

## GitHub Actions setup

1. Create the repo on GitHub (or push this folder to `cheongj2009/Lorcana`).
2. Add a repository secret:
   - **`NTFY_TOPIC`** — the topic you subscribe to in the ntfy app (must be
     hard to guess; anyone who knows it can read/post).
   - **`NTFY_TOKEN`** — optional, only if using a protected/self-hosted server.
3. In the ntfy app, subscribe to that exact topic.
4. Trigger a test run: **Actions → Stock Watcher → Run workflow**, enable
   **Send test ntfy notification**.
5. Once verified, disable the local launchd job if you were using it:
   `./uninstall.sh`

cron-job.org triggers the workflow every 3 minutes. Each run loads
`state/current.json` from git, compares every product against that saved state,
then atomically commits the updated file. When stock status changes, an
additional timestamped snapshot is saved under `state/history/`.

> **If scheduled runs never appear** in the Actions tab (only manual runs show
> up), GitHub's native cron may not have registered for this repo. Use the
> external cron fallback in [`docs/external-cron-setup.md`](docs/external-cron-setup.md)
> to trigger polling every 5 minutes via `repository_dispatch` instead.

### Viewing state history

```bash
# Recent state commits
git log --oneline -- state/current.json

# Diff a previous snapshot
git show HEAD~1:state/current.json

# Browse on-disk history snapshots (stock changes only)
ls state/history/
```

## Local setup (optional)

You can still run locally on macOS via launchd:

```bash
./install.sh
python3 stock_watcher.py --test
```

Polling only happens while the Mac is awake. GitHub Actions is the recommended
24/7 path.

## Notifications

Push alerts go through **ntfy.sh** (free, no credentials beyond the topic name):

1. Install the **ntfy** app (iOS / Android).
2. Subscribe to the topic in your `NTFY_TOPIC` secret / `.env`.
3. Test locally: `python3 stock_watcher.py --test`

A macOS desktop notification also fires when running locally. Email (SMTP) is
optional and documented in `.env.example`.

## Adding / changing products

Edit the `PRODUCTS` list near the top of `stock_watcher.py`. Each entry is a
`name` (used in alerts) and a product `url`. The detector is generic and works
on any Ravensburger product page.

## How detection works

Each product page embeds JSON-LD like
`"availability": "https://schema.org/OutOfStock"`. The watcher treats
`OutOfStock` / `SoldOut` / `Discontinued` as unavailable and anything else
(`InStock`, `LimitedAvailability`, `PreOrder`, …) as available, falling back to
the visible "Currently out of stock" text if the structured data is missing. If
neither can be determined, it logs a warning and does nothing (no false alarms).

## Notes

- Secrets are read only from environment / GitHub Secrets and are never logged.
- Be a good citizen: 3-minute polling is reasonably gentle for a small product list.
- Scheduled GitHub Actions runs are best-effort and may be delayed slightly
  during high platform load.
