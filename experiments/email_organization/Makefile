IMAGE := email-org-harness
PWD   := $(shell pwd)

.PHONY: build run run-smoke shell generate-individual deanonymize

build:
	docker build -t $(IMAGE) .

# Regenerate datasets/emails_individual.json scaffolding (labels only; empty
# headers/body/actions/draft/difficulty). See datasets/EMAIL_GUIDELINE.md.
# make generate-individual
generate-individual: build
	docker run --rm \
		-v $(PWD):/workspace \
		$(IMAGE) python datasets/scripts/generate_individual.py

# Flatten a raw email seed under datasets/seed/ into a single-line, de-anonymized
# <stem>.body.txt next to it. See datasets/scripts/deanonymize.py.
# make deanonymize SEED=fyi-gas_bill.md
deanonymize: build
	docker run --rm \
		-v $(PWD):/workspace \
		$(IMAGE) python datasets/scripts/deanonymize.py $(SEED)

# make run ARGS="--setup 1 --runs 2 --temperature 0.0"
run: build
	docker run --rm \
		-v $(PWD):/workspace \
		-e OLLAMA_URL=http://host.docker.internal:11434 \
		$(IMAGE) python -m src.main run $(ARGS)

# Quick local smoke test: setups 1 & 2 on the smoke dataset, 2 runs, temp 0.0.
# make run-smoke
run-smoke: build
	docker run --rm \
		-v $(PWD):/workspace \
		-e OLLAMA_URL=http://host.docker.internal:11434 \
		$(IMAGE) python -m src.main run \
			--setup 1 --setup 2 \
			--runs 2 --temperature 0.0 \
			--data datasets/emails_smoke.json

shell:
	docker run --rm -it \
		-v $(PWD):/workspace \
		-e OLLAMA_URL=http://host.docker.internal:11434 \
		$(IMAGE) /bin/bash
