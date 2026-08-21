.PHONY: test test-unit test-integ docker-build docker-test docker-test-unit docker-test-integ

IMAGE := lila-core-test
REPO_ROOT := $(shell git rev-parse --show-toplevel)
# From inside a container the ollama daemon lives on the host.
DOCKER_OLLAMA_HOST ?= http://host.docker.internal:11434
OLLAMA_MODEL ?= qwen3:4b

# Unit tests only. No daemon, no network.
test-unit:
	uv run --no-sync pytest tst -q

# Requires a running ollama; individual tests skip themselves when it is absent.
test-integ:
	uv run --no-sync pytest integ -q

test: test-unit test-integ

docker-build:
	docker build -f $(REPO_ROOT)/src/core/Dockerfile -t $(IMAGE) $(REPO_ROOT)

docker-test-unit: docker-build
	docker run --rm $(IMAGE)

# host.docker.internal resolves to the host on Docker Desktop; on Linux the
# --add-host flag makes it resolve too.
docker-test-integ: docker-build
	docker run --rm \
		--add-host=host.docker.internal:host-gateway \
		-e LILA_OLLAMA_HOST=$(DOCKER_OLLAMA_HOST) \
		-e LILA_OLLAMA_MODEL=$(OLLAMA_MODEL) \
		$(IMAGE) uv run --no-sync pytest integ -q

docker-test: docker-test-unit docker-test-integ
