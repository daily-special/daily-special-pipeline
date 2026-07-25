.PHONY: check fmt test

# 커밋 전에 이것 하나만 친다. 하나라도 실패하면 거기서 멈춘다.
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy --strict src tests
	uv run lint-imports
	uv run pytest

fmt:
	uv run ruff check --fix .
	uv run ruff format .

test:
	uv run pytest
