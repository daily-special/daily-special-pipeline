.PHONY: check fmt test live generate

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

# 실제 콘텐츠를 만들어 out/packages/에 쓴다. 돈이 든다.
# 대사가 자리 80개라 호출의 대부분이므로, 나눠 돌리려면 --kind를 준다.
generate:
	uv run --env-file .env daily-special $(ARGS)
