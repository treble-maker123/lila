IMAGE := email-org-harness
PWD   := $(shell pwd)

.PHONY: build run run-smoke run-individual score notebook shell generate-individual deanonymize

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

# Run the individual E-mail dataset
# make run-individual
run-individual: build
	docker run --rm \
		-v $(PWD):/workspace \
		-e OLLAMA_URL=http://host.docker.internal:11434 \
		$(IMAGE) python -m src.main run \
			--setup 1 --setup 2 \
			--runs 5 --temperature 0.6 \
			--data datasets/emails_individual.json



# Re-score a finished results file against its labels; no inference. The dataset is
# read from the results filename unless ARGS overrides it with --data.
# make score RESULTS=results/emails_smoke_....json
score: build
	docker run --rm \
		-v $(PWD):/workspace \
		$(IMAGE) python -m src.main score --results $(RESULTS) $(ARGS)

# Start the marimo server at the experiment root, so both the results notebook
# (results/visualize_emails_individual.py) and the dataset notebook
# (datasets/visualize_emails_individual.py) can be opened from one browser UI.
# Runs on the host rather than in Docker: marimo serves a browser UI, and it reads
# the same results/ JSON the containerized runs write.
# make notebook
notebook:
	uv run marimo edit --watch .



shell:
	docker run --rm -it \
		-v $(PWD):/workspace \
		-e OLLAMA_URL=http://host.docker.internal:11434 \
		$(IMAGE) /bin/bash
