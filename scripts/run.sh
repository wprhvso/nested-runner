#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/lib.sh

config="$(config_path)"
once=0

for arg in "$@"; do
    case "$arg" in
        --once) once=1 ;;
        *) die "неизвестный флаг: $arg" ;;
    esac
done

command -v gh > /dev/null || die "gh не найден — ставь: https://cli.github.com"
gh auth status > /dev/null 2>&1 || die "gh не залогинен — запусти: gh auth login"

if [[ ! -f "$config" ]]; then
    {
        echo "конфига нет: $config"
        echo
        echo "создай его руками, дефолтов больше нет:"
        echo
        echo 'poll_seconds = 10'
        echo 'inflight_ttl = 90'
        echo
        echo '[[repos]]'
        echo 'slug = "owner/name"'
        echo 'warm = 2'
        echo 'max = 10'
    } >&2
    exit 1
fi

poll_seconds=""
inflight_ttl=""
slugs=()
warms=()
maxes=()

while read -r kind a b c; do
    case "$kind" in
        repo) slugs+=("$a"); warms+=("$b"); maxes+=("$c") ;;
        poll) poll_seconds="$a" ;;
        ttl) inflight_ttl="$a" ;;
    esac
done < <(parse_config "$config")

[[ ${#slugs[@]} -gt 0 ]] || die "конфиг не дал ни одного репозитория"
[[ -n "$poll_seconds" ]] || die "конфиг не дал poll_seconds"
[[ -n "$inflight_ttl" ]] || die "конфиг не дал inflight_ttl"

if [[ "$inflight_ttl" -lt "$poll_seconds" ]]; then
    echo "внимание: inflight_ttl ($inflight_ttl) меньше poll_seconds ($poll_seconds), учёт заказанных раннеров работать не будет" >&2
fi

mkdir -p "$(state_path)"

for slug in "${slugs[@]}"; do
    gh api "repos/$slug" > /dev/null 2>&1 \
        || die "не видно репозитория $slug — проверь slug и права токена"
    gh api "repos/$slug/actions/workflows/runner.yml" > /dev/null 2>&1 \
        || die "в $slug нет runner.yml — залей workflow и включи Actions"
    gh api "repos/$slug/actions/runners?per_page=1" > /dev/null 2>&1 \
        || die "не читается список раннеров $slug — токену нужен Administration: Read and write"

    if ! gh secret list --repo "$slug" --json name --jq '.[].name' 2>/dev/null | grep -qx RUNNER_PAT; then
        if gh auth token 2>/dev/null | gh secret set RUNNER_PAT --repo "$slug" > /dev/null 2>&1; then
            echo "$slug: положил RUNNER_PAT из gh auth token"
        else
            {
                echo "$slug: нет секрета RUNNER_PAT и не смог его поставить"
                echo "сделай fine-grained PAT (Actions rw, Administration rw, Contents r) и:"
                echo "  gh secret set RUNNER_PAT --repo $slug"
            } >&2
            exit 1
        fi
    fi
done

echo "поехали: ${#slugs[@]} репо, тик ${poll_seconds} сек, ttl ${inflight_ttl} сек"

while true; do
    for index in "${!slugs[@]}"; do
        slug="${slugs[$index]}"
        warm="${warms[$index]}"
        max="${maxes[$index]}"

        prune_inflight "$slug" "$inflight_ttl"

        if ! runners="$(count_runners "$slug" 2>&1)"; then
            echo "$slug: $runners" >&2
            continue
        fi

        online="$(echo "$runners" | grep -c '^online ' || true)"
        idle="$(echo "$runners" | grep -c '^online false$' || true)"
        inflight="$(live_inflight "$slug" "$inflight_ttl" | wc -l | tr -d ' ')"

        need=$(( warm - idle - inflight ))
        [[ $need -lt 0 ]] && need=0

        room=$(( max - online - inflight ))
        [[ $room -lt 0 ]] && room=0
        [[ $need -gt $room ]] && need=$room

        branch="$(gh api "repos/$slug" --jq '.default_branch')"
        sent=0

        for _ in $(seq 1 "$need"); do
            if gh workflow run runner.yml --repo "$slug" --ref "$branch" > /dev/null 2>&1; then
                date +%s >> "$(inflight_file "$slug")"
                sent=$(( sent + 1 ))
            else
                echo "$slug: не удалось запустить runner.yml" >&2
                break
            fi
        done

        echo "$slug online=$online idle=$idle inflight=$inflight max=$max need=$need dispatched=$sent"
    done

    [[ $once -eq 1 ]] && break
    sleep "$poll_seconds"
done
