# ReserveCMU

    reservecmu/
      backend/    FastAPI + SQLite + AI recommender (port 8000)
      frontend/   React + Vite site (port 5173)

## Run it (two terminals, from the reservecmu folder)

Terminal 1, backend:

    cd backend
    python -m venv .venv
    source .venv/Scripts/activate          # Git Bash on Windows (macOS/Linux: source .venv/bin/activate)
    pip install -r requirements.txt
    cp .env.example .env                   # then paste your OPENROUTER_API_KEY into .env
    python -m app.ingest data/raw_pages.txt data/availability.txt
    python -m uvicorn app.main:app --reload

Terminal 2, frontend:

    cd frontend
    npm install
    npm run dev                            # open http://localhost:5173

Backend API docs: http://localhost:8000/docs   Tests: `cd backend && pytest`
Keep data fresh: `cd backend && python -m app.sync` (see backend/README.md).
