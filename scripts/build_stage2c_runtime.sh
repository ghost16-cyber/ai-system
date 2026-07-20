#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
IMAGE_REFERENCE=astra-project-runtime:stage2c-v1
ENVIRONMENT_FILE="$REPOSITORY_ROOT/.astra-stage2c-runtime.env"

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is unavailable or is not on PATH." >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "Docker is installed but its engine is unavailable." >&2
    exit 1
fi
if git -C "$REPOSITORY_ROOT" ls-files --error-unmatch .astra-stage2c-runtime.env >/dev/null 2>&1; then
    echo "Refusing to overwrite a tracked environment file." >&2
    exit 1
fi

docker build \
    --file "$REPOSITORY_ROOT/docker/stage2c-runtime/Dockerfile" \
    --tag "$IMAGE_REFERENCE" \
    "$REPOSITORY_ROOT/docker/stage2c-runtime"

IMAGE_DIGEST=$(docker image inspect "$IMAGE_REFERENCE" --format '{{.Id}}')
case "$IMAGE_DIGEST" in
    sha256:????????????????????????????????????????????????????????????????) ;;
    *)
        echo "Docker returned an invalid image ID: $IMAGE_DIGEST" >&2
        exit 1
        ;;
esac
if ! printf '%s\n' "$IMAGE_DIGEST" | grep -Eq '^sha256:[0-9a-f]{64}$'; then
    echo "Docker returned a malformed image ID: $IMAGE_DIGEST" >&2
    exit 1
fi

umask 077
TEMPORARY_FILE="${ENVIRONMENT_FILE}.tmp.$$"
trap 'rm -f "$TEMPORARY_FILE"' EXIT HUP INT TERM
{
    printf '%s\n' 'export ASTRA_PROJECT_EXECUTION_BACKEND=docker'
    printf '%s\n' 'export ASTRA_PROJECT_RUNTIME_IMAGE=astra-project-runtime:stage2c-v1'
    printf 'export ASTRA_PROJECT_RUNTIME_IMAGE_DIGEST=%s\n' "$IMAGE_DIGEST"
} >"$TEMPORARY_FILE"
mv "$TEMPORARY_FILE" "$ENVIRONMENT_FILE"
trap - EXIT HUP INT TERM

printf '%s\n' "Built $IMAGE_REFERENCE"
printf '%s\n' "export ASTRA_PROJECT_EXECUTION_BACKEND=docker"
printf '%s\n' "export ASTRA_PROJECT_RUNTIME_IMAGE=$IMAGE_REFERENCE"
printf '%s\n' "export ASTRA_PROJECT_RUNTIME_IMAGE_DIGEST=$IMAGE_DIGEST"
printf '%s\n' "Load the ignored local environment with: source ./scripts/load_stage2c_runtime.sh"
