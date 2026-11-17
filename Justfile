set shell := ["bash", "-c"]

default:
    @just --list

install:
    uv sync --no-sources
    uv run --no-sync pre-commit install

[private]
prepare:
    @uv sync --group dev --frozen --quiet
    @if [ -f .use-local-pycomap ]; then uv pip install --quiet -e ../pycomap; fi

# Develop against the sibling ../pycomap checkout (local only, nothing committed).
local-lib:
    touch .use-local-pycomap
    uv pip install -e ../pycomap

# Go back to the pycomap version pinned in uv.lock.
remote-lib:
    rm -f .use-local-pycomap
    uv sync --group dev --frozen

alias c := check
alias l := lint
alias tc := typecheck
alias fmt := format
alias f := format
alias t := test

check: lint typecheck

lint: prepare
    uv run --no-sync ruff check
    uv run --no-sync ruff format --check

typecheck: prepare
    uv run --no-sync ty check

format: prepare
    uv run --no-sync ruff check --fix
    uv run --no-sync ruff format

test *args: prepare
    uv run --no-sync pytest {{args}}
