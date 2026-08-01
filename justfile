default:
    @just --list

run repo:
    python3 -m nested_runner {{ repo }}

test repo:
    gh workflow run test.yml --repo {{ repo }}
    sleep 3
    gh run watch --repo {{ repo }} --exit-status

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
