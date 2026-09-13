#!/usr/bin/env bash
#
# Refresh the backend container's temporary AWS credentials.
#
#   ./scripts/refresh_aws_credentials.sh
#   AWS_PROFILE_NAME=other ./scripts/refresh_aws_credentials.sh
#
# Why this exists: the backend receives credentials as environment variables, and a container's
# environment is fixed when it is created. `docker compose restart backend` reuses the old, expired
# values, so the backend must be RECREATED. Only the backend is touched: postgres and frontend keep
# running, and the named volumes (database, source assets) are never removed.
#
# The credentials come from the `aws login` session on this host via `aws configure
# export-credentials`. They are exported into this script's own shell and consumed by the
# `docker compose up` in the same process. Nothing is printed and nothing is written to any file.
#
# These credentials are SHORT-LIVED — typically about 15 minutes. Run this immediately before a
# live run. If a run fails with ExpiredTokenException, run it again.
#
# Makes no Bedrock model call and therefore costs nothing. Model, region, live-mode flags and call
# limits are read from .env and are left exactly as they are.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

PROFILE="${AWS_PROFILE_NAME:-kdaa}"
# Keep the credential provider's region aligned with the Bedrock region in .env.
REGION="${AWS_REGION_NAME:-$(sed -n 's/^BEDROCK_REGION=//p' .env 2>/dev/null | head -1)}"
REGION="${REGION:-us-east-1}"

command -v aws >/dev/null 2>&1 || { echo "error: the AWS CLI is not on PATH." >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "error: docker is not on PATH." >&2; exit 1; }
[ -f .env ] || { echo "error: no .env in $(pwd). Live mode settings live there." >&2; exit 1; }

if grep -qE '^\s*AWS_PROFILE=[^[:space:]]' .env; then
  echo "warning: .env sets AWS_PROFILE. An 'aws login' profile cannot refresh through the" >&2
  echo "         read-only ~/.aws mount, and a set AWS_PROFILE can shadow these environment" >&2
  echo "         credentials. Comment it out if the backend still fails to authenticate." >&2
fi

echo "profile: $PROFILE | region: $REGION"

if ! aws sts get-caller-identity --profile "$PROFILE" --region "$REGION" >/dev/null 2>&1; then
  cat >&2 <<EOF
error: the '$PROFILE' session is not valid (it has expired, or you are not signed in).

Sign in again, then re-run this script:

    aws login --profile $PROFILE --region $REGION
EOF
  exit 1
fi

# Export into this shell only. `eval` consumes the values; they are never echoed.
eval "$(aws configure export-credentials --profile "$PROFILE" --region "$REGION" --format env)"
: "${AWS_ACCESS_KEY_ID:?credential export produced no access key}"
: "${AWS_SECRET_ACCESS_KEY:?credential export produced no secret key}"
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
[ -n "${AWS_SESSION_TOKEN:-}" ] && export AWS_SESSION_TOKEN
# The container must not be sent back to a profile it cannot refresh.
unset AWS_PROFILE

# Recreate ONLY the backend. --no-deps leaves postgres and frontend alone; no volume is removed.
docker compose up -d --force-recreate --no-deps backend

printf 'waiting for the backend to report healthy'
for _ in $(seq 1 60); do
  status="$(docker compose ps --format '{{.Service}} {{.Status}}' 2>/dev/null | sed -n 's/^backend //p')"
  case "$status" in
    *healthy*) printf '\n' ; break ;;
    *) printf '.' ; sleep 2 ;;
  esac
done
case "${status:-}" in
  *healthy*) ;;
  *) printf '\n' ; echo "error: backend did not become healthy: ${status:-unknown}" >&2
     echo "       docker compose logs --tail=50 backend" >&2 ; exit 1 ;;
esac

# Confirm the container resolves credentials. This reads the credential chain only: it makes no
# Bedrock call, so it is free and cannot consume the run's model-call budget.
docker compose exec -T backend python -c "
import boto3
from kdaa_api.engines import drop_empty_aws_environment
drop_empty_aws_environment()
c = boto3.Session().get_credentials()
if c is None:
    raise SystemExit('error: the backend resolved no credentials')
f = c.get_frozen_credentials()
print(f'backend credentials OK (method={c.method}, session token: {bool(f.token)})')
" || { echo "error: the backend could not resolve credentials." >&2; exit 1; }

expiry="$(aws configure export-credentials --profile "$PROFILE" --region "$REGION" --format process 2>/dev/null \
  | python3 -c 'import sys,json;print(json.load(sys.stdin).get("Expiration",""))' 2>/dev/null || true)"
[ -n "$expiry" ] && echo "credentials expire at $expiry (short-lived; re-run this before each live run)"

echo "done. Live settings unchanged:"
grep -E '^(KDAA_LLM_ENABLED|BEDROCK_REGION|BEDROCK_MODEL_ID|LLM_MAX_CANDIDATES|LLM_MAX_MODEL_CALLS)=' .env \
  | sed 's/^/  /'
echo "Retry the run from the browser at http://localhost:${WEB_PORT:-5183}"
