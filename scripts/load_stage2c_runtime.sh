#!/usr/bin/env sh

if [ "${0##*/}" = "load_stage2c_runtime.sh" ]; then
    echo "Source this helper so its exports affect the current shell:" >&2
    echo "  source ./scripts/load_stage2c_runtime.sh" >&2
    exit 1
fi

ASTRA_STAGE2C_SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE:-$0}")" && pwd)
ASTRA_STAGE2C_REPOSITORY_ROOT=$(CDPATH= cd -- "$ASTRA_STAGE2C_SCRIPT_DIR/.." && pwd)
ASTRA_STAGE2C_ENVIRONMENT_FILE="$ASTRA_STAGE2C_REPOSITORY_ROOT/.astra-stage2c-runtime.env"

if [ ! -f "$ASTRA_STAGE2C_ENVIRONMENT_FILE" ]; then
    echo "Missing generated runtime configuration. Run ./scripts/build_stage2c_runtime.sh first." >&2
    return 1
fi

# This file is generated locally by build_stage2c_runtime.sh and contains only
# the fixed backend/image values plus the exact Docker image ID.
. "$ASTRA_STAGE2C_ENVIRONMENT_FILE"
unset ASTRA_STAGE2C_SCRIPT_DIR ASTRA_STAGE2C_REPOSITORY_ROOT ASTRA_STAGE2C_ENVIRONMENT_FILE
