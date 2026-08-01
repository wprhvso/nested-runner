default:
    @just --list

run repo warm max poll:
    #!/usr/bin/env bash
    set -euo pipefail

    repo="{{ repo }}"
    warm="{{ warm }}"
    max="{{ max }}"
    poll="{{ poll }}"

    die() { echo "$*" >&2; exit 1; }

    [[ "$repo" =~ ^[^/]+/[^/]+$ ]] || die "ожидается owner/name, получено: $repo"
    [[ "$warm" =~ ^[0-9]+$ ]] || die "warm должен быть числом, получено: $warm"
    [[ "$max" =~ ^[0-9]+$ ]] || die "max должен быть числом, получено: $max"
    [[ "$poll" =~ ^[0-9]+$ ]] || die "poll должен быть числом, получено: $poll"
    [[ "$warm" -ge 1 ]] || die "warm должен быть больше нуля"
    [[ "$max" -ge "$warm" ]] || die "max меньше warm"
    [[ "$poll" -ge 1 ]] || die "poll должен быть больше нуля"

    stopping=0
    on_interrupt() {
        [[ $stopping -eq 0 ]] || return
        stopping=1
        trap - INT TERM
        echo
        echo "останавливаюсь"
        just stop "$repo" || echo "stop отработал с ошибкой" >&2
        exit 130
    }
    trap on_interrupt INT TERM

    command -v gh > /dev/null || die "gh не найден — ставь: https://cli.github.com"
    gh auth status > /dev/null 2>&1 || die "gh не залогинен — запусти: gh auth login"

    gh api "repos/$repo" > /dev/null 2>&1 \
        || die "не видно репозитория $repo — проверь slug и права токена"
    gh api "repos/$repo/actions/workflows/runner.yml" > /dev/null 2>&1 \
        || die "в $repo нет runner.yml — залей workflow и включи Actions"
    gh api "repos/$repo/actions/runners?per_page=1" > /dev/null 2>&1 \
        || die "не читается список раннеров $repo — токену нужен Administration: Read and write"

    if ! gh secret list --repo "$repo" --json name --jq '.[].name' 2>/dev/null | grep -qx RUNNER_PAT; then
        if gh auth token 2>/dev/null | gh secret set RUNNER_PAT --repo "$repo" > /dev/null 2>&1; then
            echo "$repo: положил RUNNER_PAT из gh auth token"
        else
            {
                echo "$repo: нет секрета RUNNER_PAT и не смог его поставить"
                echo "сделай fine-grained PAT (Actions rw, Administration rw, Contents r) и:"
                echo "  gh secret set RUNNER_PAT --repo $repo"
            } >&2
            exit 1
        fi
    fi

    echo "поехали: $repo warm=$warm max=$max тик ${poll} сек"

    while true; do
        if ! runners="$(gh api "repos/$repo/actions/runners?per_page=100" --jq '.runners[] | "\(.status) \(.busy)"' 2>&1)"; then
            echo "$repo: $runners" >&2
        else
            online="$(echo "$runners" | grep -c '^online ' || true)"
            idle="$(echo "$runners" | grep -c '^online false$' || true)"

            need=$(( warm - idle ))
            [[ $need -lt 0 ]] && need=0

            room=$(( max - online ))
            [[ $room -lt 0 ]] && room=0
            [[ $need -gt $room ]] && need=$room

            branch="$(gh api "repos/$repo" --jq '.default_branch')"
            sent=0

            for _ in $(seq 1 "$need"); do
                if gh workflow run runner.yml --repo "$repo" --ref "$branch" > /dev/null 2>&1; then
                    sent=$(( sent + 1 ))
                else
                    echo "$repo: не удалось запустить runner.yml" >&2
                    break
                fi
            done

            echo "$repo online=$online idle=$idle max=$max need=$need dispatched=$sent"
        fi

        sleep "$poll"
    done

stop repo:
    #!/usr/bin/env bash
    set -euo pipefail

    repo="{{ repo }}"

    die() { echo "$*" >&2; exit 1; }

    [[ "$repo" =~ ^[^/]+/[^/]+$ ]] || die "ожидается owner/name, получено: $repo"

    command -v gh > /dev/null || die "gh не найден — ставь: https://cli.github.com"
    gh auth status > /dev/null 2>&1 || die "gh не залогинен — запусти: gh auth login"

    gh api "repos/$repo" > /dev/null 2>&1 \
        || die "не видно репозитория $repo — проверь slug и права токена"
    gh api "repos/$repo/actions/runners?per_page=1" > /dev/null 2>&1 \
        || die "не читается список раннеров $repo — токену нужен Administration: Read and write"

    cancelled=0
    for state in in_progress queued; do
        while read -r id; do
            [[ -n "$id" ]] || continue
            if gh run cancel "$id" --repo "$repo" > /dev/null 2>&1; then
                cancelled=$(( cancelled + 1 ))
            else
                echo "$repo: не удалось отменить run $id" >&2
            fi
        done < <(
            gh run list --repo "$repo" --workflow runner.yml --status "$state" \
                --json databaseId --jq '.[].databaseId' 2>/dev/null || true
        )
    done

    removed=0
    while read -r id; do
        [[ -n "$id" ]] || continue
        if gh api -X DELETE "repos/$repo/actions/runners/$id" > /dev/null 2>&1; then
            removed=$(( removed + 1 ))
        else
            echo "$repo: не удалось снести раннера $id" >&2
        fi
    done < <(gh api "repos/$repo/actions/runners" --jq '.runners[].id' 2>/dev/null || true)

    echo "$repo cancelled=$cancelled removed=$removed"

test repo:
    gh workflow run test.yml --repo {{ repo }}
    sleep 3
    gh run watch --repo {{ repo }} --exit-status

qa: qa-yaml qa-actions

qa-yaml:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! command -v yamllint > /dev/null; then
        echo "yamllint не найден — yaml не проверен"
        exit 0
    fi
    yamllint .github/workflows

qa-actions:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! command -v actionlint > /dev/null; then
        echo "actionlint не найден — workflow не проверены"
        echo "поставь его, чтобы qa был полным: https://github.com/rhysd/actionlint"
        exit 0
    fi
    actionlint
