#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! command -v actionlint > /dev/null; then
    echo "actionlint не найден — workflow не проверены"
    echo "поставь его, чтобы qa был полным: https://github.com/rhysd/actionlint"
    exit 0
fi

actionlint
