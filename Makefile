.PHONY: setup update upgrade format format-check typecheck dev ci test test-unit test-integ

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

# Same checks as `format`, but reports instead of rewriting. Used by `ci`.
format-check:
	uv run black --check .
	uv run ruff check --select I .

typecheck:
	uv run --no-sync basedpyright

dev: update
	uv run --no-sync lila

ci: format-check typecheck test

# Delegate to each package's Makefile so the targets mean the same thing everywhere.
# Containerised runs stay package-local: see `make -C src/core docker-test`.
test test-unit test-integ:
	@for pkg in $(PACKAGES); do $(MAKE) -C $$pkg $@ || exit 1; done
