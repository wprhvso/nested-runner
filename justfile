default:
    @just --list

run repo:
    python3 -m nested_runner {{ repo }}

keys force="":
    #!/usr/bin/env bash
    set -euo pipefail
    command -v age-keygen > /dev/null || { echo "age не найден: https://github.com/FiloSottile/age" >&2; exit 1; }
    if [ -f keys/nested.pub ] && [ "{{ force }}" != "force" ]; then
        echo "keys/nested.pub уже есть — 'just keys force' чтобы перезаписать" >&2
        exit 1
    fi
    mkdir -p keys
    pair="$(age-keygen 2> /dev/null)"
    printf '%s' "$pair" | grep '^AGE-SECRET-KEY-' | gh secret set NESTED_KEY
    printf '%s' "$pair" | grep -o 'age1[0-9a-z]*' | head -1 > keys/nested.pub
    unset pair
    echo "приватный — в секрет NESTED_KEY, публичный — в keys/nested.pub"

test:
    #!/usr/bin/env bash
    set -euo pipefail
    repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
    gh workflow run test.yml --repo "$repo"
    sleep 3
    gh run watch --repo "$repo" --exit-status

qa: yamllint actionlint ruff basedpyright

yamllint:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v yamllint > /dev/null || { echo "yamllint не найден — yaml не проверен"; exit 0; }
    yamllint .github/workflows

actionlint:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v actionlint > /dev/null || { echo "actionlint не найден — workflow не проверены"; exit 0; }
    actionlint

ruff:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v ruff > /dev/null || { echo "ruff не найден — python не проверен"; exit 0; }
    ruff check nested_runner
    ruff format --check nested_runner

basedpyright:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v basedpyright > /dev/null || { echo "basedpyright не найден — типы не проверены"; exit 0; }
    basedpyright
