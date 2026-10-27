#!/usr/bin/env sh
set -eu
cd -- "$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' 'uv が必要です。README の導入手順を確認してください。' >&2
    exit 1
fi
uv sync --locked --no-dev
exec uv run --frozen --no-sync shorthand-trainer "$@"
