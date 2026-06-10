# External cron fallback (if GitHub schedule is silent)

GitHub's built-in `schedule` trigger is best-effort and can fail to register on
new repos. If you see **no** runs with the `schedule` event in the Actions tab,
use a free external cron to call `repository_dispatch` every 5 minutes instead.

## 1. Create a fine-grained PAT

1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens**
2. **Generate new token**
3. Resource owner: your account
4. Repository access: **Only select repositories → Lorcana**
5. Permissions → **Actions: Read and write**
6. Generate and copy the token

## 2. Add it to cron-job.org

1. Sign up at [cron-job.org](https://cron-job.org) (free)
2. **Create cronjob**
3. URL: `https://api.github.com/repos/cheongj2009/Lorcana/dispatches`
4. Schedule: every 5 minutes
5. Request method: **POST**
6. Headers:
   - `Accept: application/vnd.github+json`
   - `Authorization: Bearer YOUR_PAT_HERE`
   - `Content-Type: application/json`
7. Request body:

```json
{"event_type":"stock-watcher-poll","client_payload":{}}
```

8. Save and enable the job

Each POST triggers the **Stock Watcher** workflow the same way a manual run does,
without needing your Mac.

## Verify

Actions tab → runs should appear every ~5 minutes with event **repository_dispatch**.
