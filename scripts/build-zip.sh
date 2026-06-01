#!/usr/bin/env bash
# Package lingo-core for AWS Lambda deployment. Output: lingo-core.zip.
#
# Layout matches lingo-ops: deps installed at the zip ROOT alongside app/,
# so everything lands directly on /var/task (the Python path). Do NOT put
# deps in a subdirectory — Lambda won't find them without PYTHONPATH hacks.
#
# Targets x86_64 / py3.13 to match the Terraform-managed lingo-core function
# (lingo-infra/lingo_core_function.tf). If you change the Lambda arch there,
# update the --platform tag below to match.
#
# Optional: -f <LAMBDA_NAME> pushes the zip after building.

set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
OUT="$ROOT/lingo-core.zip"
BUILD_DIR="$ROOT/build"
LAMBDA_ARN=""

while getopts "f:" opt; do
  case $opt in
    f) LAMBDA_ARN="$OPTARG" ;;
    *) echo "Usage: $0 [-f LAMBDA_ARN]" >&2; exit 1 ;;
  esac
done

if [ -z "$LAMBDA_ARN" ] && [ -t 0 ]; then
  # Only prompt when running interactively. CI (no tty on stdin) skips
  # the push and just produces the zip — the deploy workflow runs
  # aws lambda update-function-code separately.
  echo -n "Lambda ARN or function name (empty to skip push): "
  read -r LAMBDA_ARN
fi

echo "Building $OUT ..."
rm -rf "$BUILD_DIR" "$OUT"
mkdir -p "$BUILD_DIR"

# Install runtime deps targeted at the Lambda arch/runtime. Lambda doesn't
# need uvicorn — Mangum adapts ASGI directly. --only-binary forces manylinux
# wheels so native deps (cryptography) are Lambda-compatible.
pip install \
  --target "$BUILD_DIR" \
  --platform manylinux2014_x86_64 \
  --python-version 3.13 \
  --only-binary=:all: \
  --quiet \
  fastapi \
  pydantic-settings \
  "python-jose[cryptography]" \
  httpx \
  aiosqlite \
  aioboto3 \
  mangum \
  "kombu>=5"

# Application code alongside the deps (both at zip root -> /var/task).
cp -r app "$BUILD_DIR/"

# Zip from inside build/ so paths have no leading "build/" prefix.
( cd "$BUILD_DIR" && zip -rq "$OUT" . \
    -x "*.pyc" -x "*__pycache__*" \
    -x "*.dist-info/*" -x "*.egg-info/*" \
    -x "*/tests/*" -x "*/test/*" )

rm -rf "$BUILD_DIR"

SIZE_BYTES=$(stat -c%s "$OUT" 2>/dev/null || stat -f%z "$OUT")
SIZE_MB=$(awk -v b="$SIZE_BYTES" 'BEGIN { printf "%.1f", b/1024/1024 }')
echo "Done: $OUT (${SIZE_MB} MB)"

if [ -n "$LAMBDA_ARN" ]; then
  echo "Pushing to Lambda: $LAMBDA_ARN"
  aws lambda update-function-code \
    --function-name "$LAMBDA_ARN" \
    --zip-file "fileb://$OUT"
  echo "Lambda updated."
fi
