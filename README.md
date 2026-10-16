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
AUTH_REDIRECT_URL=http://localhost:8501  # local dev only — omit when deployed
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

In Supabase → **Authentication → Providers → Google**, enable it and add your
Google client ID/secret.

Then open **Authentication → URL Configuration**. Both fields matter, and
getting them wrong is the usual reason a deployed app sends users back to
`localhost`:

| Field | Value |
| --- | --- |
| **Site URL** | your frontend URL — `https://<your-app>.streamlit.app` (or `http://localhost:8501` for local dev) |
| **Redirect URLs** | every URL you sign in from, one per line |

```
http://localhost:8501
https://<your-app>.streamlit.app
```

Two independent things have to line up, and it's easy to fix only one:

1. **The app must ask for the right `redirect_to`.** It resolves that itself:
   `AUTH_REDIRECT_URL` → `[google_oauth] redirect_uri` → *the URL the user is
   actually on*. So a deployed app needs no configuration here, and a stale
   `http://localhost:8501` from your local `.env` is ignored in the cloud.
2. **Supabase must allow that URL.** If the requested `redirect_to` isn't in
   **Redirect URLs**, Supabase silently substitutes the **Site URL** — so if
   the Site URL is still `http://localhost:8501`, that's where the user lands
   no matter what the app asked for.

Add the deployed URL to **both** the Google Cloud console's authorized
redirect URI and Supabase's Redirect URLs.

---

### 4. Run

**Backend** (terminal 1):

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload --port 8000
```

**Frontend** (terminal 2):

```bash
source .venv/bin/activate
streamlit run frontend/streamlit_app.py
```

Then open **http://localhost:8501** and sign in — analysis requires an account.

> `streamlit_app.py` boots the backend itself if nothing is listening on port
> 8000, so running a separate `uvicorn` is optional. Starting it yourself
> (terminal 1) gives you the auto-reload and the logs, which is nicer for
> development.

First startup takes ~15s while models load. Verify with:

```bash
curl http://localhost:8000/api/v1/health
# {"status":"healthy","nlp_loaded":true,"embedder_loaded":true}
```

---

## Deploying to Streamlit Community Cloud

The app is a **single deployable unit**: `streamlit_app.py` spawns the FastAPI
backend as a child process, so Streamlit Cloud only needs this one repo.

### 1. Are the ML models committed?

No — and that's intentional. Weights are not stored in git. Instead:

- The default embedder `all-MiniLM-L6-v2` is a **public HuggingFace model**.
  It is downloaded automatically into the host's HF cache on first startup
  (~90 MB). Nothing to commit, nothing to configure.
- spaCy's `en_core_web_md` (~40 MB) is pinned as a wheel URL in
  `requirements.txt`, so `pip install -r requirements.txt` fetches it too.
- If you later swap in **your own trained model**, do **not** commit it either.
  Upload it to HuggingFace Hub (public or private) and point the app at it:
  ```env
  SENTENCE_TRANSFORMER_MODEL=<your-username>/<your-model>
  HF_TOKEN=hf_xxx          # only needed for a private repo
  ```
  Weights in git would bloat the clone, slow deploys, and hit GitHub's
  100 MB file limit.

### 2. Streamlit Cloud settings

1. Push the repo to GitHub, then go to <https://share.streamlit.io>
2. **New app** → select the repo / branch / main file path:
   ```
   frontend/streamlit_app.py
   ```
3. Under **Advanced settings** → *Secrets*, paste the following. This becomes
   `.streamlit/secrets.toml`, which is git-ignored.

   Note the two sections — Streamlit Cloud runs **both** halves of the app in
   one container, so one file has to satisfy two different readers:

   - **Top-level keys** are exported as *environment variables*, which is how
     the spawned backend gets its config. They must come first (TOML puts
     everything after a `[table]` header inside that table).
   - **Tables** are read by the frontend through `st.secrets`.

   ```toml
   # ── backend ── read via os.getenv() in the spawned uvicorn ─────────────
   GROQ_API_KEY = "gsk_..."
   GROQ_MODEL = "openai/gpt-oss-120b"
   SUPABASE_URL = "https://<your-project-ref>.supabase.co"
   SUPABASE_KEY = "<service_role key>"

   # ── frontend ── read via st.secrets ────────────────────────────────────
   [supabase]
   SUPABASE_URL = "https://<your-project-ref>.supabase.co"
   SUPABASE_ANON_KEY = "<anon key>"

   [google_oauth]
   redirect_uri = "https://<your-app>.streamlit.app"
   ```

   > The `[google_oauth]` section is optional: with it omitted the app uses the
   > URL the user is actually on. Set it only if you serve the app from behind
   > a proxy whose public URL differs from what the browser reports.

   > Use the **`service_role`** key (not the anon key) for `SUPABASE_KEY` —
   > the backend needs it to write history past RLS. The anon key stays
   > frontend-only.

4. Deploy. `packages.txt` installs `libmagic1` automatically for
   PDF/DOCX type detection.

Add the deployed URL to Supabase → **Authentication → URL Configuration** —
both the **Site URL** and the **Redirect URLs** list:

```
https://<your-app>.streamlit.app
```

If you only set one of them, Google sign-in on the deployed app drops the user
back to `localhost` — see [Google OAuth](#google-oauth) above.

> The first load takes a couple of minutes: the embedding model is fetched
> from Hugging Face and the NLP pipeline is loaded, with a spinner shown the
> whole time. Later visits are instant while the container stays warm.
>
> If it fails, the app prints the backend's own log right on the page, so you
> don't have to dig for it.

### 3. Splitting the backend out (Render / Railway / Fly.io)

If you outgrow the single-process setup — cold starts, or the ~1 GB container
running out of memory while loading the models — host the API separately and
point the frontend at it.

**Deploy the backend on Render** as a Web Service from the same repo:

| Setting  | Value |
| -------- | ----- |
| Runtime  | Python |
| Build    | `pip install -r requirements.txt` |
| Start    | `uvicorn backend.main:app --host 0.0.0.1 --port $PORT` |

Render assigns the port, so the start command must read `$PORT` — binding to a
fixed `8000` will make the service fail its health check. Add the same
`GROQ_*` / `SUPABASE_*` values as environment variables in the service's
**Environment** tab; the backend reads them straight from `os.getenv`.

Then tell the frontend to stop spawning its own backend:

```toml
# .streamlit/secrets.toml on Streamlit Cloud
[backend]
url = "https://your-api.onrender.com"
```

`backend.url` disables the in-process backend entirely — the backend URL never
needs to be in Supabase's Redirect URLs, since OAuth returns to the frontend.

> Render's free tier also sleeps when idle, so the first request after a quiet
> spell takes ~30 s while the instance restarts and reloads its models.

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
