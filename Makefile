.PHONY: setup update upgrade format dev

# Installs the dependencies without updating them
setup:
	uv sync --all-packages --group dev --frozen

# When adding a new dependency, run this to update the lockfile
update:
	uv sync --all-packages --group dev

# Bump any versions of existing dependencies
upgrade:
	uv sync --all-packages --group dev --upgrade

format:
	uv run black .
	uv run ruff check --select I --fix .

dev: update
	uv run --no-sync lila