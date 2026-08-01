#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/lib.sh

repo="${NR_REPO:?}"

if [[ -z "$repo" ]]; then
    echo "нет remote origin — нечего останавливать" >&2
    exit 1
fi

for status in in_progress queued; do
    gh run list --repo "$repo" --workflow runner.yml --status "$status" --json databaseId --jq '.[].databaseId' \
        | xargs -r -n1 gh run cancel --repo "$repo"
done

gh api "repos/$repo/actions/runners" --jq '.runners[].id' \
    | xargs -r -n1 -I{} gh api -X DELETE "repos/$repo/actions/runners/{}"

rm -f "$(inflight_file "$repo")"
