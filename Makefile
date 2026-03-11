.PHONY: install chat feishu menubar menubar-app env-up env-restart env-down env-status dev-up dev-restart dev-down dev-status test test-cov lint format check precommit-install

install:
	@bash install.sh

chat:
	@hermit chat

feishu:
	@hermit serve --adapter feishu

menubar:
	@hermit-menubar --adapter feishu

menubar-app:
	@hermit-menubar-install-app --adapter feishu --open

env-up:
	@bash scripts/hermit-envctl.sh $(if $(ENV),$(ENV),dev) up

env-restart:
	@bash scripts/hermit-envctl.sh $(if $(ENV),$(ENV),dev) restart

env-down:
	@bash scripts/hermit-envctl.sh $(if $(ENV),$(ENV),dev) down

env-status:
	@bash scripts/hermit-envctl.sh $(if $(ENV),$(ENV),dev) status

dev-up:
	@bash scripts/hermit-envctl.sh dev up

dev-restart:
	@bash scripts/hermit-envctl.sh dev restart

dev-down:
	@bash scripts/hermit-envctl.sh dev down

dev-status:
	@bash scripts/hermit-envctl.sh dev status

test:
	@uv run pytest -q

test-cov:
	@uv run pytest --cov=hermit --cov-report=term-missing

lint:
	@uv run ruff check .

format:
	@uv run ruff format .

check:
	@$(MAKE) lint
	@$(MAKE) test

precommit-install:
	@uv run pre-commit install
