# External cron fallback (if GitHub schedule is silent)

GitHub's built-in `schedule` trigger is best-effort and can fail to register on
new repos. If you see **no** runs with the `schedule` event in the Actions tab,
use a free external cron to call `repository_dispatch` every 5 minutes instead.

## 1. Create a fine-grained PAT

1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens**
2. **Generate new token**
3. Resource owner: your account
4. Repository access: **Only select repositories → Lorcana**
5. Permissions → **Actions: Read and write** (Metadata read-only is added automatically)
6. Generate and copy the token
7. Save locally as `lorcana-cron-job-token.txt` (gitignored — never commit)

### Test the token (Part 1)

```bash
export GITHUB_PAT="$(tr -d '[:space:]' < lorcana-cron-job-token.txt)"

curl -s -o /dev/null -w "HTTP %{http_code}\n" \
  -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $GITHUB_PAT" \
  -H "Content-Type: application/json" \
  -d '{"ref":"main"}' \
  https://api.github.com/repos/cheongj2009/Lorcana/actions/workflows/stock-watcher.yml/dispatches
```

Expect **`HTTP 204`**. Then check Actions for a new **Stock Watcher** run.

> **Note:** The `repository_dispatch` API requires **Contents** permission on
> fine-grained tokens. With **Actions: Read and write** only, use the
> `workflow_dispatch` endpoint above instead (same result).

## 2. Add it to cron-job.org

1. Sign up at [cron-job.org](https://cron-job.org) (free)
2. **Create cronjob**
3. URL: `https://api.github.com/repos/cheongj2009/Lorcana/actions/workflows/stock-watcher.yml/dispatches`
4. Schedule: every 5 minutes
5. Request method: **POST**
6. Headers:
   - `Accept: application/vnd.github+json`
   - `Authorization: Bearer YOUR_PAT_HERE`
   - `Content-Type: application/json`
7. Request body:

```json
{"ref":"main"}
```

8. Save and enable the job

Each POST triggers the **Stock Watcher** workflow on `main` without needing your Mac.

## Verify

Actions tab → runs should appear every ~5 minutes with event **workflow_dispatch**.
