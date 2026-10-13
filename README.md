# DataPulse deploy rehearsal (backend)

A dummy FastAPI backend shaped like the real DataPulse backend. We deploy it to
Render to rehearse and verify the deploy plan before the real deploy.

```
datapulse-deploy-rehearsal/
├── backend/            FastAPI app (main.py, requirements.txt, .env.example)
├── knowledge-base/     Dummy .md files (stands in for the real KB)
├── scripts/            smoke_test.py - verifies any deployed URL
├── .python-version     Pins Python 3.12 on Render
├── render.yaml         Optional: Render settings as code
└── Dockerfile          Exit plan: same app as a container
```

## Run locally (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt

$env:API_KEY = "change-me-local-only"
$env:DUMMY_SECRET = "anything"
$env:ALLOWED_ORIGINS = "http://localhost:5173,http://localhost:3000"
$env:INVESTIGATION_SECONDS = "5"

uvicorn main:app --app-dir backend --port 8000 --reload
```

Open http://localhost:8000/docs to try every endpoint in the browser.

In a second terminal:

```powershell
python scripts\smoke_test.py http://localhost:8000 change-me-local-only
```

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | none | Render health check; shows commit and KB file count |
| GET | `/config-check` | API key | Which env vars are set (never their values) |
| GET | `/kb` | API key | Lists knowledge-base files the server can see |
| POST/GET/PUT/DELETE | `/items`, `/items/{id}` | API key | Basic CRUD (in memory) |
| POST | `/investigations` | API key | Starts a fake long-running job, returns `job_id` |
| GET | `/investigations/{job_id}` | API key | Poll job status until `completed` |

Protected routes need the header `X-API-Key: <API_KEY>`.

## Deploy

Follow the team deploy plan (DataPulse Backend Deploy Plan). Render settings:

- Root Directory: **leave empty** (the backend reads `knowledge-base/` from the repo root)
- Build: `pip install -r backend/requirements.txt`
- Start: `uvicorn main:app --app-dir backend --host 0.0.0.0 --port $PORT --workers 1`
- Health check path: `/health`
