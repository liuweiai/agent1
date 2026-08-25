.PHONY: install dev test eval verify frontend-install frontend-dev docker-up docker-stop

install:
	python -m pip install -e "./agent-service[dev]"

dev:
	python -m uvicorn factorypilot.main:app --app-dir agent-service --reload --port 8000

test:
	python -m pytest agent-service/tests -q

eval:
	python evals/evaluate.py

verify: test eval
	cd frontend && npm run build
	cd mes-gateway && mvn test

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

docker-up:
	docker compose up -d --build

docker-stop:
	docker compose stop
