set shell := ["bash", "-c"]

default:
    @just --list

install:
    uv sync --no-sources
    uv run pre-commit install

[private]
prepare:
    @uv sync --group dev --frozen --quiet

alias c := check
alias l := lint
alias tc := typecheck
alias fmt := format
alias f := format
alias t := test

check: lint typecheck

lint: prepare
    uv run ruff check
    uv run ruff format --check

typecheck: prepare
    uv run ty check

format: prepare
    uv run ruff check --fix
    uv run ruff format

test *args: prepare
    uv run pytest {{args}}
