.PHONY: up down logs restart test clean

up:
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example, add your real API keys in it")
	docker compose up -d --build
	@echo "Backend:  http://localhost:8000/health"
	@echo "Frontend: http://localhost:3000"

down:
	docker compose down

logs:
	docker compose logs -f

restart: down up

test:
	cd backend && python -m pytest -q

clean:
	docker compose down -v
