IMAGE       ?= image-converter
TAG         ?= latest
NAME        ?= image-converter
PORT        ?= 8000
SECRET      ?= change-me

.PHONY: help build run stop restart rebuild logs shell clean venv dev test

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ { printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

build: ## Build the Docker image
	docker build -t $(IMAGE):$(TAG) .

run: ## Run the container in the background
	docker run -d --rm \
	  -p $(PORT):8000 \
	  -e API_SECRET_KEY=$(SECRET) \
	  --name $(NAME) \
	  $(IMAGE):$(TAG)

stop: ## Stop the running container
	-docker stop $(NAME)

restart: stop run ## Stop and run

rebuild: stop build run ## Stop, rebuild, and run

logs: ## Tail container logs
	docker logs -f $(NAME)

shell: ## Open a shell inside the running container
	docker exec -it $(NAME) sh

clean: ## Remove the image
	-docker rmi $(IMAGE):$(TAG)

venv: ## Create local venv and install requirements
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt

dev: ## Run the server locally (requires venv)
	API_SECRET_KEY=$(SECRET) .venv/bin/python server.py

test: ## Quick smoke test against a running container on $(PORT)
	@curl -fsS http://localhost:$(PORT)/health && echo " ✓ healthy"
