# dastock — convenience targets
# Most commands are added in later sessions as features land.

.PHONY: help install lint test

help:
	@echo "Available targets:"
	@echo "  install   - Install dependencies via uv"
	@echo "  lint      - Run ruff + mypy"
	@echo "  test      - Run pytest"

install:
	uv sync

lint:
	uv run ruff check src tests
	uv run mypy src

test:
	uv run pytest
