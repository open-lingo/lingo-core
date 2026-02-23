#!/usr/bin/env bash
# Package lingo-core for deployment. Output: lingo-core.zip with app/ and package/ (deps).
# If Lambda ARN/name is provided (prompted or via -f), pushes the zip to Lambda.
#
# Lambda optimizations: no uvicorn (Mangum handles ASGI), excludes cruft to shrink zip.

set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
OUT=lingo-core.zip
LAMBDA_ARN=""

while getopts "f:" opt; do
  case $opt in
    f) LAMBDA_ARN="$OPTARG" ;;
    *) echo "Usage: $0 [-f LAMBDA_ARN]" >&2; exit 1 ;;
  esac
done

if [ -z "$LAMBDA_ARN" ]; then
  echo -n "Lambda ARN or function name (empty to skip push): "
  read -r LAMBDA_ARN
fi

echo "Building $OUT ..."
rm -rf build package
mkdir -p build package

# Lambda doesn't need uvicorn — Mangum adapts ASGI directly. Saves ~15MB.
pip install -t package/ -q \
  fastapi \
  pydantic-settings \
  "python-jose[cryptography]" \
  httpx \
  aiosqlite \
  aioboto3 \
  mangum

cp -r app build/
cp -r package build/

# Exclude cruft to shrink zip and speed cold starts
cd build
zip -rq "$ROOT/$OUT" . \
  -x "*.pyc" -x "*__pycache__*" \
  -x "*.dist-info/*" -x "*.egg-info/*" \
  -x "*/tests/*" -x "*/test/*"
cd ..
rm -rf build package

echo "Done: $OUT"

if [ -n "$LAMBDA_ARN" ]; then
  echo "Pushing to Lambda: $LAMBDA_ARN"
  aws lambda update-function-code \
    --function-name "$LAMBDA_ARN" \
    --zip-file "fileb://$ROOT/$OUT"
  echo "Lambda updated."
fi
