#!/usr/bin/env bash
set -Eeuo pipefail

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

LEGACY_SERVICE="actions.runner.walshg3-Homelab.walshit-production-containers.service"
ONCE_SERVICE="walshit-production-runner-once.service"
RUNNER_USER="walshit-deploy"
RUNNER_EXEC="/home/walshit-deploy/actions-runner/run.sh"
LOCK_DIR="/run/walshit"
LOCK_FILE="${LOCK_DIR}/runner-window.lock"
START_TIMEOUT_SECONDS=600
JOB_TIMEOUT_SECONDS=2700
STOP_TIMEOUT_SECONDS=310
SYSTEMCTL_TIMEOUT_SECONDS=15
POLL_SECONDS=1
job_seen=0

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

systemctl_value() {
  /usr/bin/timeout --kill-after=5s "${SYSTEMCTL_TIMEOUT_SECONDS}s" \
    /usr/bin/systemctl show "$1" "--property=$2" --value
}

unit_state() {
  /usr/bin/timeout --kill-after=5s "${SYSTEMCTL_TIMEOUT_SECONDS}s" \
    /usr/bin/systemctl is-active "$1" 2>/dev/null || true
}

unit_enabled() {
  /usr/bin/timeout --kill-after=5s "${SYSTEMCTL_TIMEOUT_SECONDS}s" \
    /usr/bin/systemctl is-enabled "$1" 2>/dev/null || true
}

runner_process_exists() {
  /usr/bin/pgrep --uid "$RUNNER_USER" --exact 'Runner.Listener' >/dev/null 2>&1 ||
    /usr/bin/pgrep --uid "$RUNNER_USER" --exact 'Runner.Worker' >/dev/null 2>&1
}

worker_exists() {
  /usr/bin/pgrep --uid "$RUNNER_USER" --exact 'Runner.Worker' >/dev/null 2>&1
}

stop_once() {
  if ! /usr/bin/timeout --kill-after=10s "${STOP_TIMEOUT_SECONDS}s" \
    /usr/bin/systemctl stop "$ONCE_SERVICE"; then
    printf 'CRITICAL: failed to restore %s to inactive state.\n' "$ONCE_SERVICE" >&2
    return 1
  fi
}

on_exit() {
  local status=$?
  trap - EXIT
  trap '' INT TERM
  if ! stop_once; then
    status=1
  fi
  exit "$status"
}

on_signal() {
  printf 'Runner window interrupted; restoring offline state.\n' >&2
  exit 130
}

[[ "$(/usr/bin/id --user)" -eq 0 ]] || fail "run this supervisor as root via sudo"
/usr/bin/install -d --owner=root --group=root --mode=0700 "$LOCK_DIR"
exec {LOCK_FD}>"$LOCK_FILE"
/usr/bin/flock --nonblock "$LOCK_FD" || fail "another operator already owns the runner window"

[[ "$(unit_enabled "$LEGACY_SERVICE")" == "disabled" ]] || fail "generated runner service must remain disabled"
[[ "$(unit_state "$LEGACY_SERVICE")" == "inactive" ]] || fail "generated runner service is already active"
[[ "$(systemctl_value "$LEGACY_SERVICE" User)" == "$RUNNER_USER" ]] || fail "generated runner service has an unexpected user"
[[ "$(unit_enabled "$ONCE_SERVICE")" == "disabled" ]] || fail "one-job runner unit must remain disabled"
[[ "$(unit_state "$ONCE_SERVICE")" == "inactive" ]] || fail "one-job runner unit is already active"
[[ "$(systemctl_value "$ONCE_SERVICE" User)" == "$RUNNER_USER" ]] || fail "one-job unit has an unexpected user"
[[ "$(systemctl_value "$ONCE_SERVICE" KillMode)" == "control-group" ]] || fail "one-job unit must use KillMode=control-group"
[[ "$(systemctl_value "$ONCE_SERVICE" KillSignal)" == "15" ]] || fail "one-job unit must use SIGTERM"
[[ "$(systemctl_value "$ONCE_SERVICE" TimeoutStopUSec)" == "5min" ]] || fail "one-job unit must allow five minutes for rollback"
[[ "$(systemctl_value "$ONCE_SERVICE" Restart)" == "no" ]] || fail "one-job unit must not restart"
exec_start="$(systemctl_value "$ONCE_SERVICE" ExecStart)"
[[ "$exec_start" == *"$RUNNER_EXEC --once"* ]] || fail "one-job unit does not use the runner's native --once mode"
runner_process_exists && fail "a runner process already exists"

trap on_exit EXIT
trap on_signal INT TERM
printf 'Starting the offline-gated runner in native one-job mode...\n'
/usr/bin/timeout --kill-after=10s "${SYSTEMCTL_TIMEOUT_SECONDS}s" \
  /usr/bin/systemctl start "$ONCE_SERVICE"
start_deadline=$((SECONDS + START_TIMEOUT_SECONDS))

while (( SECONDS < start_deadline )); do
  [[ "$(unit_state "$ONCE_SERVICE")" == "active" ]] || fail "one-job runner exited before a worker was observed"
  if worker_exists; then
    job_seen=1
    printf 'Runner accepted its single job. Waiting for native --once shutdown...\n'
    break
  fi
  /usr/bin/sleep "$POLL_SECONDS"
done

[[ "$job_seen" -eq 1 ]] || fail "no job arrived within ${START_TIMEOUT_SECONDS}s"
job_deadline=$((SECONDS + JOB_TIMEOUT_SECONDS))
while [[ "$(unit_state "$ONCE_SERVICE")" == "active" ]]; do
  (( SECONDS < job_deadline )) || fail "runner job exceeded ${JOB_TIMEOUT_SECONDS}s"
  /usr/bin/sleep "$POLL_SECONDS"
done

[[ "$(systemctl_value "$ONCE_SERVICE" Result)" == "success" ]] || fail "one-job runner unit exited unsuccessfully"
stop_once
trap - EXIT INT TERM

[[ "$(unit_enabled "$LEGACY_SERVICE")" == "disabled" ]] || fail "generated runner service became enabled"
[[ "$(unit_state "$LEGACY_SERVICE")" == "inactive" ]] || fail "generated runner service became active"
[[ "$(unit_enabled "$ONCE_SERVICE")" == "disabled" ]] || fail "one-job runner unit became enabled"
[[ "$(unit_state "$ONCE_SERVICE")" == "inactive" ]] || fail "one-job runner unit did not stop"
runner_process_exists && fail "a runner process remains after the one-job window"
printf 'One-job runner window completed; service is disabled and inactive.\n'
