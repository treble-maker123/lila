.PHONY: setup format

setup:
	uv sync --all-packages --group dev

format:
	uv run black .
	uv run ruff check --select I --fix .
