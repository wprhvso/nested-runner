#!/usr/bin/env bash

die() {
    echo "$*" >&2
    exit 1
}

config_path() {
    [[ -n "${NR_CONFIG:-}" ]] || die "NR_CONFIG не задан"
    echo "$NR_CONFIG"
}

state_path() {
    [[ -n "${NR_STATE:-}" ]] || die "NR_STATE не задан"
    echo "$NR_STATE"
}

inflight_file() {
    echo "$(state_path)/$(echo "$1" | tr '/' '_').inflight"
}

parse_config() {
    local file="$1"

    [[ -f "$file" ]] || die "конфига нет: $file"

    awk '
        function fail(message) {
            print "конфиг заполнен неверно: " message > "/dev/stderr"
            exit 1
        }
        function flush_repo() {
            if (slug == "" && warm == "" && max == "") return
            if (slug == "") fail("в блоке [[repos]] нет slug")
            if (slug !~ /^[^\/]+\/[^\/]+$/) fail("ожидается owner/name, получено " slug)
            if (warm == "") fail(slug ": нет warm")
            if (max == "") fail(slug ": нет max")
            if (warm + 0 < 1) fail(slug ": warm должен быть больше нуля")
            if (max + 0 < warm + 0) fail(slug ": max меньше warm")
            if (seen[slug]++) fail("один и тот же репозиторий указан дважды: " slug)
            print "repo", slug, warm, max
        }
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*$/ { next }
        /^[[:space:]]*poll_seconds[[:space:]]*=/ {
            gsub(/[^0-9]/, "", $0); poll = $0; next
        }
        /^[[:space:]]*inflight_ttl[[:space:]]*=/ {
            gsub(/[^0-9]/, "", $0); ttl = $0; next
        }
        /^[[:space:]]*\[\[repos\]\][[:space:]]*$/ {
            flush_repo(); slug = ""; warm = ""; max = ""; started = 1; next
        }
        /^[[:space:]]*slug[[:space:]]*=/ {
            match($0, /"[^"]*"/)
            if (RSTART == 0) fail("slug без кавычек")
            slug = substr($0, RSTART + 1, RLENGTH - 2); next
        }
        /^[[:space:]]*warm[[:space:]]*=/ {
            gsub(/[^0-9]/, "", $0); warm = $0; next
        }
        /^[[:space:]]*max[[:space:]]*=/ {
            gsub(/[^0-9]/, "", $0); max = $0; next
        }
        END {
            flush_repo()
            if (poll == "") fail("нет poll_seconds")
            if (ttl == "") fail("нет inflight_ttl")
            if (poll + 0 < 1) fail("poll_seconds должен быть больше нуля")
            if (ttl + 0 < 1) fail("inflight_ttl должен быть больше нуля")
            if (!started) fail("нет ни одного блока [[repos]]")
            print "poll", poll
            print "ttl", ttl
        }
    ' "$file"
}

live_inflight() {
    local file ttl
    file="$(inflight_file "$1")"
    ttl="$2"
    [[ -n "$ttl" ]] || die "live_inflight без ttl"
    if [[ -f "$file" ]]; then
        awk -v now="$(date +%s)" -v ttl="$ttl" '$1 + ttl > now' "$file"
    fi
}

prune_inflight() {
    local file
    file="$(inflight_file "$1")"
    mkdir -p "$(state_path)"
    touch "$file"
    live_inflight "$1" "$2" > "$file.tmp"
    mv "$file.tmp" "$file"
}

count_runners() {
    gh api "repos/$1/actions/runners?per_page=100" --jq '.runners[] | "\(.status) \(.busy)"'
}
