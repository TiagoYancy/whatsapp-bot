.PHONY: help install venv run dev test lint docker-build docker-up docker-down clean simulate tunnel

VENV := venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

help:
	@echo "Comandos disponíveis:"
	@echo "  make install       - cria venv e instala dependências"
	@echo "  make run           - inicia servidor (produção, gunicorn)"
	@echo "  make dev           - inicia servidor em modo debug"
	@echo "  make test          - roda a suíte de testes"
	@echo "  make simulate MSG='...' - simula mensagem recebida"
	@echo "  make tunnel        - expõe porta 5000 via ngrok"
	@echo "  make docker-build  - build da imagem"
	@echo "  make docker-up     - sobe com docker compose"
	@echo "  make docker-down   - derruba o compose"
	@echo "  make clean         - remove artefatos e banco de teste"

install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

run:
	$(VENV)/bin/gunicorn --bind 0.0.0.0:5000 --workers 4 --timeout 60 app:app

dev:
	$(PY) app.py

test:
	$(PY) -m pytest tests/ -v

simulate:
	@curl -s -X POST http://localhost:5000/api/simulate \
		-H "Content-Type: application/json" \
		-d '{"message": "$(MSG)"}' | $(PY) -m json.tool

tunnel:
	ngrok http 5000

docker-build:
	docker build -t whatsapp-faq-bot:latest .

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

clean:
	rm -rf .pytest_cache __pycache__ */__pycache__ tests/_test_bot.db* data/bot.db*
	find . -name "*.pyc" -delete
