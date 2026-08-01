#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! command -v yamllint > /dev/null; then
    echo "yamllint не найден — yaml не проверен"
    exit 0
fi

yamllint .github/workflows
