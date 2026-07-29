.PHONY: check fmt test live

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

# 돈이 드는 유일한 타깃. .env를 읽는 곳도 여기뿐이라 check와 CI는 키 없이 돈다.
# -s는 생성물을 눈으로 보기 위한 것이다 — 질은 사람이 판단한다.
live:
	uv run --env-file .env pytest -m live -s
