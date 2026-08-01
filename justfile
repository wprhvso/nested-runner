repo := `git remote get-url origin 2>/dev/null | sed -E 's#.*[:/]([^/]+/[^/]+)$#\1#; s#\.git$##' || echo ""`
token := "${XDG_CONFIG_HOME:-$HOME/.config}/nested-runner/token"
config := "${XDG_CONFIG_HOME:-$HOME/.config}/nested-runner/config.toml"

default:
    @just --list

vars:
    @echo "repo:   {{ repo }}"
    @echo "token:  {{ token }}"
    @echo "config: {{ config }}"

login:
    uv run nested-runner login
    gh secret set RUNNER_PAT --repo {{ repo }} < "{{ token }}"

status:
    uv run nested-runner status

run *args:
    uv run nested-runner run {{ args }}

test:
    gh workflow run test.yml --repo {{ repo }}
    sleep 3
    gh run watch --repo {{ repo }} --exit-status

runners:
    gh api repos/{{ repo }}/actions/runners --jq '.runners[] | "\(.name) \(.status) busy=\(.busy)"'

qa: qa-python qa-yaml qa-actions

qa-python:
    uv run ruff format --check .
    uv run ruff check .
    uv run basedpyright

qa-yaml:
    uv run yamllint .github/workflows

qa-actions:
    #!/usr/bin/env bash
    if ! command -v actionlint > /dev/null; then
        echo "actionlint не найден — workflow не проверены"
        echo "поставь его, чтобы qa был полным: https://github.com/rhysd/actionlint"
        exit 0
    fi
    echo -e "\033[1mactionlint\033[0m"
    actionlint

fix:
    uv run ruff format .
    uv run ruff check --fix .

lock:
    uv lock --upgrade

clean:
    rm -rf .venv .ruff_cache **/__pycache__
