default:
    @just --list

qa:
    uv run ruff format --check .
    uv run ruff check .
    uv run basedpyright

fix:
    uv run ruff format .
    uv run ruff check --fix .

install:
    uv sync
    uv tool install --force .

run *args:
    uv run nested-runner run {{ args }}

status:
    uv run nested-runner status

login:
    uv run nested-runner login

lock:
    uv lock --upgrade

clean:
    rm -rf .venv .ruff_cache **/__pycache__
