.PHONY: install lint format test demo

install:
	pip install -r requirements-dev.txt

lint:
	ruff check . && ruff format --check .

format:
	ruff format . && ruff check --fix .

test:
	pytest

demo:
	python -m mal_lifecycle demo
