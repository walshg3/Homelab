#!/usr/bin/env bash
set -Eeuo pipefail

readonly IMAGE_REPOSITORY="ghcr.io/walshg3/walshit-landing"
readonly COMPOSE_FILE="/etc/walshit/compose.production.yaml"
readonly SERVICE_NAME="landing-hugo"
readonly NETWORK_NAME="walshit-landing-hugo-prod-9238dbfa_default"
readonly LOCK_DIR="/run/walshit"
readonly LOCK_FILE="${LOCK_DIR}/production.lock"
readonly DEPLOY_HEALTH_TIMEOUT="${DEPLOY_HEALTH_TIMEOUT:-180}"

fail() {
  printf 'walshit deployment failed: %s\n' "$*" >&2
  exit 1
}

compose() {
  local project="$1"
  local port_binding="$2"
  shift 2
  PORT_BINDING="$port_binding" docker compose \
    --project-name "$project" --file "$COMPOSE_FILE" "$@"
}

container_for_project() {
  compose "$1" "$2" ps --quiet "$SERVICE_NAME"
}

any_container_for_project() {
  compose "$1" "$2" ps --all --quiet "$SERVICE_NAME"
}

wait_healthy() {
  local container="$1"
  local deadline="$((SECONDS + DEPLOY_HEALTH_TIMEOUT))"
  local status
  while (( SECONDS < deadline )); do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$container")"
    case "$status" in
      healthy) return 0 ;;
      unhealthy|missing) return 1 ;;
    esac
    sleep 2
  done
  return 1
}

verify_same_image() {
  local first_image="$1"
  local second_image="$2"
  local first_id
  local second_id
  first_id="$(docker image inspect --format '{{.Id}}' "$first_image")" || return 1
  second_id="$(docker image inspect --format '{{.Id}}' "$second_image")" || return 1
  [[ "$first_id" == "$second_id" ]]
}

verify_container() {
  local container="$1"
  local expected_image="$2"
  local container_image_id
  local expected_image_id
  container_image_id="$(docker inspect --format '{{.Image}}' "$container")" || return 1
  expected_image_id="$(docker image inspect --format '{{.Id}}' "$expected_image")" || return 1
  [[ "$container_image_id" == "$expected_image_id" ]] || return 1
  [[ "$(docker exec "$container" wget -qO- http://127.0.0.1:8080/healthz.txt)" == "walshit-landing-hugo-ok" ]]
}

stop_if_running() {
  local container="$1"
  if [[ -n "$container" && "$(docker inspect --format '{{.State.Running}}' "$container")" == "true" ]]; then
    docker stop "$container" >/dev/null
  fi
}

rollback() {
  local rejected_container="$1"
  local reason="$2"
  printf 'walshit deployment failed after cutover: %s; starting retained predecessor\n' "$reason" >&2
  stop_if_running "$rejected_container" || true
  if ! docker start "$PREVIOUS_CONTAINER" >/dev/null; then
    fail "rollback failed: retained predecessor would not start"
  fi
  if ! wait_healthy "$PREVIOUS_CONTAINER"; then
    fail "rollback failed: retained predecessor did not become healthy"
  fi
  if [[ "$(docker exec "$PREVIOUS_CONTAINER" wget -qO- http://127.0.0.1:8080/healthz.txt)" != "walshit-landing-hugo-ok" ]]; then
    fail "rollback failed: retained predecessor health body is invalid"
  fi
  ROLLBACK_HANDLED=true
  printf 'rollback restored retained predecessor %.12s\n' "$PREVIOUS_CONTAINER" >&2
  exit 1
}

on_exit() {
  local status="$1"
  local rejected_container=""
  trap - EXIT
  trap '' INT TERM
  if [[ "$status" -ne 0 && "${CUTOVER_STARTED:-false}" == "true" && \
        "${ROLLBACK_HANDLED:-false}" != "true" ]]; then
    printf 'deployment interrupted after cutover began; attempting emergency rollback\n' >&2
    rejected_container="${LIVE_CONTAINER:-$(any_container_for_project "$LIVE_PROJECT" "$LIVE_BIND" 2>/dev/null || true)}"
    stop_if_running "$rejected_container" || true
    if ! docker start "$PREVIOUS_CONTAINER" >/dev/null 2>&1 || \
       ! wait_healthy "$PREVIOUS_CONTAINER" || \
       [[ "$(docker exec "$PREVIOUS_CONTAINER" wget -qO- http://127.0.0.1:8080/healthz.txt 2>/dev/null || true)" != \
          "walshit-landing-hugo-ok" ]]; then
      printf 'emergency rollback failed; production requires immediate operator recovery\n' >&2
    else
      printf 'emergency rollback restored retained predecessor %.12s\n' "$PREVIOUS_CONTAINER" >&2
    fi
  fi
  exit "$status"
}

main() {
[[ "${EUID}" -eq 0 ]] || fail "must run as root through the audited sudo rule"
[[ "$#" -eq 2 ]] || fail "usage: deploy-walshit <full-commit-sha> <sha256-digest>"
readonly IMAGE_TAG="$1"
readonly IMAGE_DIGEST="$2"
[[ "$IMAGE_TAG" =~ ^[0-9a-f]{40}$ ]] || fail "revision must be a full lowercase 40-character commit SHA"
[[ "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "digest must be a lowercase sha256 digest"
[[ "$DEPLOY_HEALTH_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || fail "DEPLOY_HEALTH_TIMEOUT must be a positive integer"

SCRIPT_PATH="$(realpath -- "$0")" || fail "cannot resolve deployment wrapper path"
readonly SCRIPT_PATH
[[ "$(stat --format '%u:%g:%a:%F' -- "$SCRIPT_PATH")" == "0:0:755:regular file" ]] || fail "deployment wrapper must be root-owned mode 0755"
[[ ! -L "$COMPOSE_FILE" ]] || fail "trusted Compose file must not be a symlink"
[[ "$(stat --format '%u:%g:%a:%F' -- "$COMPOSE_FILE" 2>/dev/null || true)" == "0:0:644:regular file" ]] || fail "trusted Compose file must be root-owned mode 0644"

install -d --owner=root --group=root --mode=0700 "$LOCK_DIR" || fail "cannot prepare root-only deployment lock directory"
[[ "$(stat --format '%u:%g:%a:%F' -- "$LOCK_DIR")" == "0:0:700:directory" ]] || fail "deployment lock directory must be root-owned mode 0700"
exec 9>"$LOCK_FILE"
flock --exclusive --nonblock 9 || fail "another Walshit deployment is in progress"
docker network inspect "$NETWORK_NAME" >/dev/null 2>&1 || fail "required external network is absent"

readonly TAGGED_IMAGE="${IMAGE_REPOSITORY}:${IMAGE_TAG}"
readonly DIGEST_IMAGE="${IMAGE_REPOSITORY}@${IMAGE_DIGEST}"
printf 'Pulling commit tag %s and verifying immutable digest %s\n' "$IMAGE_TAG" "$IMAGE_DIGEST"
docker pull "$TAGGED_IMAGE" >/dev/null || fail "exact commit tag pull failed"
docker image inspect "$DIGEST_IMAGE" >/dev/null 2>&1 || fail "commit tag did not resolve to the published digest"
verify_same_image "$TAGGED_IMAGE" "$DIGEST_IMAGE" || fail "commit tag does not resolve to the published digest"
[[ "$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$DIGEST_IMAGE")" == "$IMAGE_TAG" ]] || fail "image revision label does not match requested commit"

export IMAGE_TAG IMAGE_DIGEST
readonly SHORT_SHA="${IMAGE_TAG:0:12}"
readonly CANDIDATE_PROJECT="walshit-landing-candidate-${SHORT_SHA}"
readonly LIVE_PROJECT="walshit-landing-live-${SHORT_SHA}"
readonly CANDIDATE_BIND="127.0.0.1::8080"
readonly LIVE_BIND="192.168.5.252:3003:8080"

compose "$CANDIDATE_PROJECT" "$CANDIDATE_BIND" config --quiet || fail "candidate Compose model is invalid"
[[ -z "$(any_container_for_project "$CANDIDATE_PROJECT" "$CANDIDATE_BIND")" ]] || \
  fail "candidate project already exists; retained resources require operator review"
compose "$CANDIDATE_PROJECT" "$CANDIDATE_BIND" pull landing-hugo >/dev/null || fail "candidate image pull failed"
if ! compose "$CANDIDATE_PROJECT" "$CANDIDATE_BIND" up -d --no-deps --no-build --pull never landing-hugo >/dev/null; then
  fail "candidate application service failed to start"
fi
CANDIDATE_CONTAINER="$(container_for_project "$CANDIDATE_PROJECT" "$CANDIDATE_BIND")" || \
  fail "candidate Compose lookup failed"
readonly CANDIDATE_CONTAINER
[[ -n "$CANDIDATE_CONTAINER" ]] || fail "candidate Compose project returned no container"
if ! wait_healthy "$CANDIDATE_CONTAINER" || ! verify_container "$CANDIDATE_CONTAINER" "$DIGEST_IMAGE"; then
  stop_if_running "$CANDIDATE_CONTAINER" || true
  fail "candidate failed health or immutable-image acceptance; stopped and retained"
fi
docker stop "$CANDIDATE_CONTAINER" >/dev/null || fail "accepted candidate could not be stopped for retention"

REMOTE_MAIN="$(git ls-remote https://github.com/walshg3/Homelab.git refs/heads/main | cut -f1)" || \
  fail "could not verify current main revision"
readonly REMOTE_MAIN
[[ "$REMOTE_MAIN" == "$IMAGE_TAG" ]] || fail "revision was superseded before cutover; candidate remains stopped and retained"

mapfile -t origin_owners < <(docker ps --filter publish=3003 --format '{{.ID}}')
[[ "${#origin_owners[@]}" -eq 1 ]] || fail "expected exactly one running owner of production port 3003"
readonly PREVIOUS_CONTAINER="${origin_owners[0]}"
PREVIOUS_BINDING="$(docker inspect --format '{{range (index .NetworkSettings.Ports "8080/tcp")}}{{.HostIp}}:{{.HostPort}}{{end}}' "$PREVIOUS_CONTAINER")" || \
  fail "could not inspect current production listener"
readonly PREVIOUS_BINDING
[[ "$PREVIOUS_BINDING" == "192.168.5.252:3003" ]] || fail "production listener does not match the reviewed fixed origin"

compose "$LIVE_PROJECT" "$LIVE_BIND" config --quiet || fail "live Compose model is invalid"
[[ -z "$(any_container_for_project "$LIVE_PROJECT" "$LIVE_BIND")" ]] || \
  fail "live project already exists; retained resources require operator review"
CUTOVER_STARTED=true
ROLLBACK_HANDLED=false
trap 'on_exit $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
docker stop "$PREVIOUS_CONTAINER" >/dev/null || fail "retained predecessor could not be stopped"

if ! compose "$LIVE_PROJECT" "$LIVE_BIND" up -d --no-deps --no-build --pull never landing-hugo >/dev/null; then
  FAILED_LIVE_CONTAINER="$(any_container_for_project "$LIVE_PROJECT" "$LIVE_BIND" 2>/dev/null || true)"
  rollback "$FAILED_LIVE_CONTAINER" "new live application service failed to start"
fi
LIVE_CONTAINER="$(container_for_project "$LIVE_PROJECT" "$LIVE_BIND")" || \
  rollback "" "live Compose lookup failed"
readonly LIVE_CONTAINER
[[ -n "$LIVE_CONTAINER" ]] || rollback "" "live Compose project returned no container"
if ! wait_healthy "$LIVE_CONTAINER"; then
  rollback "$LIVE_CONTAINER" "new live container did not become healthy within ${DEPLOY_HEALTH_TIMEOUT}s"
fi
if ! verify_container "$LIVE_CONTAINER" "$DIGEST_IMAGE"; then
  rollback "$LIVE_CONTAINER" "new live container failed immutable-image or health-body acceptance"
fi

ROLLBACK_HANDLED=true
printf 'Walshit revision %s (%s) is healthy in container %.12s; predecessor %.12s is stopped and retained\n' \
  "$IMAGE_TAG" "$IMAGE_DIGEST" "$LIVE_CONTAINER" "$PREVIOUS_CONTAINER"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
