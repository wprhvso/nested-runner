repo := `git remote get-url origin 2>/dev/null | sed -E 's#.*[:/]([^/]+/[^/]+)$#\1#; s#\.git$##' || echo ""`
config := `echo "${XDG_CONFIG_HOME:-$HOME/.config}/nested-runner/config.toml"`
state := `echo "${XDG_STATE_HOME:-$HOME/.local/state}/nested-runner"`

export NR_REPO := repo
export NR_CONFIG := config
export NR_STATE := state

default:
    @just --list

vars:
    @echo "repo:   {{ repo }}"
    @echo "config: {{ config }}"
    @echo "state:  {{ state }}"

run *args:
    @scripts/run.sh {{ args }}

status:
    @scripts/status.sh

stop:
    @scripts/stop.sh

test:
    gh workflow run test.yml --repo {{ repo }}
    sleep 3
    gh run watch --repo {{ repo }} --exit-status

runners:
    gh api repos/{{ repo }}/actions/runners --jq '.runners[] | "\(.name) \(.status) busy=\(.busy)"'

qa: qa-yaml qa-actions qa-shell

qa-yaml:
    @scripts/qa-yaml.sh

qa-actions:
    @scripts/qa-actions.sh

qa-shell:
    @scripts/qa-shell.sh

clean:
    rm -rf "{{ state }}"
