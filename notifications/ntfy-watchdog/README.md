# Public ntfy watchdog

A Python-standard-library-only watchdog that runs outside the ntfy Docker stack and checks the canonical public health endpoint:

```text
https://notify.walshit.com/v1/health
```

It is deliberately independent from ntfy and the ntfy-to-Discord subscriber bridge. After three consecutive failed or unhealthy probes, it posts one outage notification directly to a dedicated Discord `alerts` channel webhook. After an outage, two consecutive healthy probes produce one recovery notification. Healthy steady state is silent.

## Runtime boundary

The deployed script is copied from reviewed repository source to the active Hermes homelab profile and runs every five minutes as a script-only cron job. `cron-job.json` is the reviewed schedule specification and must match the live Hermes job exactly; its `script` value is intentionally relative to the active profile’s `scripts/` directory, as required by Hermes cron. Defaults derive from `$HERMES_HOME`:

- secret: `secrets/ntfy-watchdog.env`, mode `0600`;
- state: `runtime/ntfy-watchdog/state.json`, mode `0600` in a mode-`0700` directory;
- process lock: `runtime/ntfy-watchdog/state.json.lock`, mode `0600`;
- public endpoint: `https://notify.walshit.com/v1/health`.

A nonblocking process lock covers the full state/probe/post/save transaction, so an overlapping cron or manual run exits silently without probing or posting. Discord incoming webhooks do not support idempotency nonces. Delivery therefore remains deliberately at-least-once: a process crash after Discord accepts an outage/recovery but before atomic state persistence can duplicate that transition. This small crash window is preferred over persisting first and potentially losing an actionable outage entirely.

The secret file contains exactly:

```text
DISCORD_WEBHOOK_URL=replace-locally
```

Use a dedicated Discord webhook scoped to the Homelab `alerts` channel. Never commit or print the webhook URL. The script refuses group/world-readable secret files and symlinks, disables all Discord mentions, bounds probe response reads, and sanitizes webhook-delivery exceptions so the webhook URL cannot appear in scheduler output.

Optional runtime overrides are `NTFY_WATCHDOG_URL`, `NTFY_WATCHDOG_SECRET_FILE`, `NTFY_WATCHDOG_STATE_FILE`, `NTFY_WATCHDOG_FAILURE_THRESHOLD`, `NTFY_WATCHDOG_RECOVERY_THRESHOLD`, and `NTFY_WATCHDOG_TIMEOUT_SECONDS`.

## Validation

From the repository root:

```bash
python3 -m unittest notifications/ntfy-watchdog/test_ntfy_watchdog.py
python3 -m py_compile \
  notifications/ntfy-watchdog/ntfy_watchdog.py \
  notifications/ntfy-watchdog/test_ntfy_watchdog.py
git diff --check
```

The test suite uses local mock HTTP endpoints. It verifies consecutive-failure and recovery thresholds, a single outage/recovery post, disabled mentions, mode-`0600` state, secret-file permission enforcement, silent healthy execution, and sanitized Discord failures without interrupting production ntfy.

For production acceptance, first run once against the real healthy endpoint and require empty stdout plus `status=healthy` in restricted local state. Then send one explicit configuration message through the dedicated webhook and verify Discord reports the Homelab `alerts` channel ID. Do not stop ntfy to test the outage path.

## Rollback

Rollback is a pause: pause the watchdog cron job and preserve its script, state, and webhook secret for diagnosis or resumption. Removing the cron job, state, secret, or Discord webhook requires separate scoped approval because those are resource-removal operations.
