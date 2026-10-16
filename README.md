# AI ATS Resume Scorer

Score your resume against a job description the way an Applicant Tracking System does — 5-component scoring, skill validation, NLP-powered feedback, and PDF reports.

Built with **FastAPI** (backend), **Streamlit** (frontend), **spaCy + sentence-transformers** (NLP), **Groq** (LLM parsing), and **Supabase** (auth + history).

---

## Features

| Area | What you get |
|---|---|
| **5-component score** | Formatting (20) · Keywords & Skills (25) · Content Quality (25) · Skill Validation (15) · ATS Compatibility (15) |
| **JD comparison** | Match %, semantic similarity, matched/missing keywords, skills gap |
| **Skill validation** | Semantic check that every claimed skill is backed by a project or experience bullet |
| **Detailed feedback** | Issue-by-issue breakdown with severity, explanation, and copy-pasteable fix examples |
| **Action items** | Prioritised to-do list sorted by severity |
| **PDF reports** | 4-section HTML report (summary, skill report, JD report, recommendations) exported to a single PDF |
| **History** | Past analyses saved per user (Supabase), re-viewable and re-downloadable |
| **Auth** | Email/password + Google OAuth via Supabase |

**Upload formats:** PDF, DOC, DOCX (max 5 MB)

---

## Architecture

```
Browser
   │
   ▼
Streamlit frontend (port 8501)  ──HTTP──►  FastAPI backend (port 8000)
   │                                          │
   │                                          ├─► spaCy (en_core_web_md)
   │                                          ├─► SentenceTransformer (embeddings)
   │                                          ├─► Groq API (resume/JD → structured JSON)
   │                                          └─► Supabase REST (history + auth)
   └─► Supabase GoTrue (login / Google OAuth)
```

---

## Project structure

```
.
├── .env                       # secrets (NOT committed)
├── requirements.txt
├── backend/
│   ├── main.py                # app factory, model loading (lifespan)
│   ├── core/config.py         # env loading + app config
│   ├── api/
│   │   ├── routes.py          # all API endpoints
│   │   └── auth.py            # JWT verification (RS256/JWKS + HS256)
│   ├── models/schemas.py      # Pydantic response models
│   ├── services/
│   │   ├── resume_parser.py   # PDF/DOCX text extraction + validation
│   │   ├── groq_parser.py     # LLM → structured resume / JD JSON
│   │   ├── ats_scorer.py      # 5 component scores + skill validation
│   │   ├── jd_matcher.py      # semantic similarity + keyword matching
│   │   ├── feedback_engine.py # issue detection → IssueDetail list
│   │   ├── resume_analyzer.py # full pipeline orchestration
│   │   ├── report_generator.py# Jinja HTML reports
│   │   └── pdf_export.py      # WeasyPrint → PDF
│   ├── database/supabase_db.py# history CRUD via Supabase REST
│   ├── templates/*.html       # 4 report templates
│   └── utils/                 # file utils, skill matching helpers
└── frontend/
    ├── streamlit_app.py       # entry point + sidebar nav + auth UI
    ├── views/                 # landing, scorer, history, resources
    ├── components/            # dashboard, score display, feedback blocks
    └── services/              # api_client (backend), supabase_client (auth)
```

---

## Prerequisites

- Python **3.10+**
- A **Groq** API key (free tier works) — https://console.groq.com
- A **Supabase** project (auth + `analyses` table) — https://supabase.com

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/avartan007/AI-ATS-Resume-Scorer.git
cd AI-ATS-Resume-Scorer

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. spaCy model

```bash
python -m spacy download en_core_web_md
```

### 3. Configure `.env`

Create `.env` in the **repo root** (both backend and frontend read it from there):

```env
SUPABASE_URL=https://<your-project-ref>.supabase.co
SUPABASE_KEY=<service_role key>          # history writes (bypasses RLS)
SUPABASE_ANON_KEY=<anon/public key>      # frontend auth
SUPABASE_JWT_SECRET=<JWT secret>         # optional — used only for HS256 tokens
GROQ_API_KEY=<your groq key>
GROQ_MODEL=openai/gpt-oss-120b           # optional override
SENTENCE_TRANSFORMER_MODEL=all-MiniLM-L6-v2
AUTH_REDIRECT_URL=http://localhost:8501  # Google OAuth callback
```

> ⚠️ `SUPABASE_URL` must be the **`https://<ref>.supabase.co`** REST URL, **not** the `postgresql://` connection string.

#### Supabase table

Run this in the Supabase SQL editor:

```sql
create table public.analyses (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  filename text,
  ats_score numeric,
  keyword_match numeric,
  missing_keywords jsonb,
  created_at timestamptz default now(),
  analysis_result jsonb
);

alter table public.analyses enable row level security;

-- the backend authenticates with the service_role key, which bypasses RLS
create policy "service role full access"
  on public.analyses for all
  using (true) with check (true);
```

#### Google OAuth

In Supabase → **Authentication → Providers → Google**, enable it and add to **Redirect URLs**:

```
http://localhost:8501
https://<your-deployed-frontend-url>
```

---

### 4. Run

**Backend** (terminal 1):

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload --port 8000
```

**Frontend** (terminal 2, must run from `frontend/` so its `.streamlit/config.toml` applies):

```bash
source .venv/bin/activate
cd frontend
streamlit run streamlit_app.py
```

Then open **http://localhost:8501** and sign in — analysis requires an account.

First startup takes ~15s while models load. Verify with:

```bash
curl http://localhost:8000/api/v1/health
# {"status":"healthy","nlp_loaded":true,"embedder_loaded":true}
```

---

## Using your own / locally trained model

The backend loads the embedder as `SentenceTransformer(SENTENCE_TRANSFORMER_MODEL)`.

1. Put your checkpoint in `models/` (git-ignored), e.g. `models/bert-ats/`
2. Set it in `.env`:
   ```env
   SENTENCE_TRANSFORMER_MODEL=/absolute/path/to/models/bert-ats
   ```
3. Restart the backend.

The folder must be a **sentence-transformers** layout (`modules.json`, `config_sentence_transformers.json`, `1_Pooling/`) — a plain HuggingFace `Trainer.save_model()` BERT export will not load. To use a raw transformer, either convert it with `SentenceTransformer(model_name_or_path)`, or adapt `backend/main.py` to build embeddings manually.

---

## API endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/analyze-resume` | Bearer | Upload resume (+ optional JD) → full analysis |
| `GET` | `/api/v1/health` | – | Model readiness check |
| `GET` | `/api/v1/history` | Bearer | User's past analyses |
| `DELETE` | `/api/v1/history/{id}` | Bearer | Delete one entry |
| `GET` | `/api/v1/history/{id}/pdf` | Bearer | Regenerate PDF for a stored analysis |
| `POST` | `/api/v1/generate-pdf` | Bearer | Render any analysis payload to PDF |
| `GET` | `/docs` | – | Swagger UI |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `404 model_not_found` from Groq | Your key lacks the hardcoded model — set `GROQ_MODEL` in `.env` to one your key lists at `GET https://api.groq.com/openai/v1/models` |
| `Invalid URL` from supabase-py | `SUPABASE_URL` is a `postgresql://` string — use `https://<ref>.supabase.co` |
| `HS256 token received but SUPABASE_JWT_SECRET is not configured` | Fill `SUPABASE_JWT_SECRET` (Supabase → Settings → API) |
| Analysis returns 500 `GROQ_API_KEY environment variable not set` | Add `GROQ_API_KEY` to the **root** `.env`, restart backend |
| `/generate-pdf` 500 | `pip install weasyprint` + system libs (see below) |
| Frontend can't reach backend | Set `backend.url` in `frontend/.streamlit/secrets.toml` if not on port 8000 |
| `python-magic` import fails on macOS | `brew install libmagic` |

**WeasyPrint system libs (only needed for PDF export):**

```bash
# macOS
brew install pango gdk-pixbuf libffi libmagic

# Debian/Ubuntu
apt-get install libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev
```

Without it, PDF export returns 500 but scoring/history work fine.

---

## Development

```bash
pip install ruff
ruff check backend frontend          # lint (should be clean)
python -m compileall backend frontend # syntax check
```

---

## License

MIT
