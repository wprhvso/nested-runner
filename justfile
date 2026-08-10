image := "nested-runner"

default:
    @just --list

build:
    docker build -t {{ image }} .

run *repos: build
    #!/usr/bin/env bash
    set -euo pipefail
    docker run --rm -it \
        -e GH_TOKEN="$(gh auth token)" \
        -e GH_REPO -e NESTED_SCALE_SET -e NESTED_MAX -e NESTED_WORKFLOW -e NESTED_DEBUG \
        -v "$PWD/keys:/app/keys:ro" \
        {{ image }} {{ repos }}

version *args:
    #!/usr/bin/env bash
    set -euo pipefail
    given=({{ args }})
    if [ "${#given[@]}" -eq 0 ]; then
        printf 'v%s\n' "$(cat VERSION)"
        exit 0
    fi
    if [ "${#given[@]}" -gt 1 ]; then
        echo "usage: just version [vX.Y.Z]" >&2
        exit 2
    fi
    new="${given[0]#v}"
    [[ "$new" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$ ]] || {
        echo "это не версия: ${given[0]}" >&2
        exit 1
    }
    old="$(cat VERSION)"
    if [ "$new" != "$old" ]; then
        git ls-files -z | xargs -0 perl -pi -e "s/\Q$old\E/$new/g"
    fi
    printf 'v%s\n' "$new"

tag:
    #!/usr/bin/env bash
    set -euo pipefail
    [ -z "$(git status --porcelain)" ] || { echo "дерево грязное — сначала закоммить" >&2; exit 1; }
    v="v$(cat VERSION)"
    git tag -a "$v" -m "$v"
    git push origin "$v"
    printf '%s\n' "$v"

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
    repo="${GH_REPO:-wprhvso/nested-runner}"
    gh workflow run test.yml --repo "$repo"
    sleep 3
    gh run watch --repo "$repo" --exit-status

qa: yamllint actionlint python

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

# Настройки QA живут в wprhvso/qa-python — скрипт подкладывает те же конфиги,
# что экшен использует в CI, и запускает те же команды.
python:
    bash <(curl -fsSL https://raw.githubusercontent.com/wprhvso/qa-python/v1/scripts/local.sh)

fix:
    bash <(curl -fsSL https://raw.githubusercontent.com/wprhvso/qa-python/v1/scripts/local.sh) --fix
