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

config:
    ${EDITOR:-vi} "{{ config }}"

qa:
    uv run ruff format --check .
    uv run ruff check .
    uv run basedpyright

fix:
    uv run ruff format .
    uv run ruff check --fix .

lock:
    uv lock --upgrade

clean:
    rm -rf .venv .ruff_cache **/__pycache__
