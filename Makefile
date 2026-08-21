.PHONY: setup update upgrade format typecheck dev test test-unit test-integ

# Every package under src/ that carries its own Makefile.
PACKAGES := $(patsubst %/,%,$(dir $(wildcard src/*/Makefile)))

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

typecheck:
	uv run --no-sync basedpyright

dev: update
	uv run --no-sync lila

# Delegate to each package's Makefile so the targets mean the same thing everywhere.
# Containerised runs stay package-local: see `make -C src/core docker-test`.
test test-unit test-integ:
	@for pkg in $(PACKAGES); do $(MAKE) -C $$pkg $@ || exit 1; done
