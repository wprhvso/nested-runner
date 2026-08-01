#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/lib.sh

config="$(config_path)"

[[ -f "$config" ]] || die "конфига нет: $config"

inflight_ttl=""
slugs=()
warms=()
maxes=()

while read -r kind a b c; do
    case "$kind" in
        repo) slugs+=("$a"); warms+=("$b"); maxes+=("$c") ;;
        ttl) inflight_ttl="$a" ;;
    esac
done < <(parse_config "$config")

[[ -n "$inflight_ttl" ]] || die "конфиг не дал inflight_ttl"

printf '%-30s %6s %6s %8s %6s %6s\n' repo online idle inflight max need

for index in "${!slugs[@]}"; do
    slug="${slugs[$index]}"
    warm="${warms[$index]}"
    max="${maxes[$index]}"

    inflight="$(live_inflight "$slug" "$inflight_ttl" | wc -l | tr -d ' ')"
    runners="$(count_runners "$slug" 2>/dev/null || true)"
    online="$(echo "$runners" | grep -c '^online ' || true)"
    idle="$(echo "$runners" | grep -c '^online false$' || true)"

    need=$(( warm - idle - inflight ))
    [[ $need -lt 0 ]] && need=0

    room=$(( max - online - inflight ))
    [[ $room -lt 0 ]] && room=0
    [[ $need -gt $room ]] && need=$room

    printf '%-30s %6s %6s %8s %6s %6s\n' "$slug" "$online" "$idle" "$inflight" "$max" "$need"
done
