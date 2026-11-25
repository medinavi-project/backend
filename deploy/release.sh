#!/usr/bin/env bash
set -Eeuo pipefail
cd -- "$(dirname -- "$0")"
umask 077
exec 9>.deploy.lock
flock -w 300 9
export DOCKER_CONFIG
DOCKER_CONFIG=$(mktemp -d)
cleanup() { rm -rf -- "$DOCKER_CONFIG"; rm -f .docker-token .docker-user .next-image; }
trap cleanup EXIT
image=$(cat .next-image)
[[ "$image" =~ ^ghcr\.io/[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}$ ]] || { echo 'Invalid image digest'; exit 1; }
previous=''
if [[ -f .current-image ]]; then previous=$(cat .current-image); fi
previous_env=''
if [[ -f .env ]]; then
  cp .env .env.rollback
  previous_env=1
fi
mv .next-env .env
chmod 600 .env
if ! docker login ghcr.io --username "$(cat .docker-user)" --password-stdin < .docker-token; then
  if [[ -n "$previous_env" ]]; then mv .env.rollback .env; fi
  exit 1
fi
export API_IMAGE="$image"
if docker compose -f compose.yml pull api && docker compose -f compose.yml up -d --wait --wait-timeout 150; then
  printf '%s\n' "$image" > .current-image
  rm -f .env.rollback
  # Caddyfile은 파일만 바뀌면 compose가 컨테이너를 다시 만들지 않으므로 매번 새로 띄워 반영한다.
  docker compose -f compose.yml up -d --force-recreate --no-deps caddy
  docker compose -f compose.yml ps
else
  echo 'Deployment failed; restoring previous API image and environment'
  if [[ -n "$previous_env" ]]; then mv .env.rollback .env; fi
  if [[ -n "$previous" ]]; then
    export API_IMAGE="$previous"
    docker compose -f compose.yml up -d --wait --wait-timeout 150 api || true
  fi
  exit 1
fi
