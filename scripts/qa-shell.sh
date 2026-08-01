#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! command -v shellcheck > /dev/null; then
    echo "shellcheck не найден — скрипты не проверены"
    exit 0
fi

shellcheck -x -e SC1091 scripts/*.sh
