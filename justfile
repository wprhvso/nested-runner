default:
    @just --list

run repo:
    #!/usr/bin/env python3
    import sys
    sys.path.insert(0, "{{ justfile_directory() }}")
    import nested
    sys.exit(nested.main(["run", "{{ repo }}"]))

stop repo:
    #!/usr/bin/env python3
    import sys
    sys.path.insert(0, "{{ justfile_directory() }}")
    import nested
    sys.exit(nested.main(["stop", "{{ repo }}"]))

status repo:
    #!/usr/bin/env python3
    import sys
    sys.path.insert(0, "{{ justfile_directory() }}")
    import nested
    sys.exit(nested.main(["status", "{{ repo }}"]))

test repo:
    gh workflow run test.yml --repo {{ repo }}
    sleep 3
    gh run watch --repo {{ repo }} --exit-status

qa: qa-yaml qa-actions qa-python

qa-yaml:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v yamllint > /dev/null || { echo "yamllint не найден — yaml не проверен"; exit 0; }
    yamllint .github/workflows

qa-actions:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v actionlint > /dev/null || { echo "actionlint не найден — workflow не проверены"; exit 0; }
    actionlint

qa-python:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v ruff > /dev/null || { echo "ruff не найден — python не проверен"; exit 0; }
    ruff check nested.py
    ruff format --check nested.py
