.PHONY: setup seed dev test clean

setup:
	pip install -r requirements.txt
	cd frontend && npm install

seed:
	python -m app.seed

test:
	pytest tests/ -v

dev:
	@echo "Starting MasteryFlow Backend (Port 8000) and Frontend (Port 5173)..."
	@echo "Open http://localhost:5173 in your browser"
	@python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 & cd frontend && npm run dev
