# Architecture

## Services Overview

```mermaid
flowchart TB
    Browser["🌐 Browser\nHTMX · Tailwind CSS"]

    subgraph infra["Infrastructure  (docker compose up -d)"]
        PG[("🗄 Postgres 16 + pgvector\nlocalhost:5432")]
        RD[("⚡ Redis 7\nlocalhost:6379")]
    end

    subgraph agent["oly-agent/"]
        subgraph web["Web Server  (uvicorn)"]
            App["FastAPI\nauth · rate-limit · session\n11 routers · Jinja2 templates"]
            APool["asyncpg pool"]
            AQueue["arq client"]
        end

        subgraph worker["ARQ Worker  (separate process)"]
            WFn["run_generation()"]
            subgraph pipe["6-step pipeline"]
                direction LR
                S1["1·ASSESS"] --> S2["2·PLAN"] --> S3["3·RETRIEVE"]
                S3 --> S4["4·GENERATE"] --> S5["5·VALIDATE"] --> S6["6·EXPLAIN"]
            end
        end
    end

    subgraph ingestion["oly-ingestion/"]
        Ing["Ingestion pipeline\npipeline.py · ingest_web.py"]
    end

    subgraph ext["External APIs"]
        Anthropic["☁ Anthropic\nclaude-sonnet-4-6"]
        OpenAI["☁ OpenAI\ntext-embedding-3-small"]
    end

    Browser <-->|HTTP| App
    App --- APool & AQueue
    APool <-->|asyncpg| PG
    AQueue -->|enqueue job| RD
    RD -->|poll| WFn
    WFn --> pipe
    pipe <-->|psycopg2| PG
    S4 & S6 <-->|LLM calls| Anthropic
    S3 <-->|pgvector search| PG

    Ing <-->|psycopg2| PG
    Ing -->|embed| OpenAI
    Ing -->|classify + principles| Anthropic
```

---

## Program Generation Flow

```mermaid
sequenceDiagram
    actor User
    participant UI as Web UI
    participant Redis
    participant Worker as ARQ Worker
    participant DB as Postgres
    participant LLM as Claude API

    User->>UI: POST /generate/run
    UI->>Redis: enqueue_job("run_generation", athlete_id)
    Redis-->>UI: job_id
    UI-->>User: render polling fragment (HTMX)

    loop every 3 s
        User->>UI: GET /generate/status/{job_id}
        UI->>Redis: check job status
        Redis-->>UI: running / done / failed
        UI-->>User: update fragment
    end

    Worker->>Redis: poll for jobs
    Redis-->>Worker: run_generation(athlete_id)

    Worker->>DB: 1·ASSESS — load athlete, maxes, goals, history
    Worker->>DB: 2·PLAN — phase selection, Prilepin targets
    Worker->>DB: 3·RETRIEVE — fault exercises, templates, pgvector search
    loop per session (N = weeks × sessions/week)
        Worker->>LLM: 4·GENERATE — session prompt
        LLM-->>Worker: structured JSON
        Worker->>Worker: 5·VALIDATE — Prilepin / intensity checks
    end
    Worker->>LLM: 6·EXPLAIN — program rationale
    LLM-->>Worker: rationale text
    Worker->>DB: save generated_programs + sessions + exercises
    Worker->>Redis: store result (program_id, duration)

    User->>UI: GET /generate/status/{job_id}
    UI->>Redis: fetch result
    Redis-->>UI: done, program_id=N
    UI-->>User: link to /program/N
```

---

## Ingestion Pipeline Flow

```mermaid
flowchart LR
    subgraph src["Source Material"]
        PDF[PDF]
        EPUB[EPUB]
        WEB[Web / HTML]
    end

    subgraph extract["Extract"]
        PE["pdf_extractor\nPyMuPDF → pdfplumber\n→ Claude vision OCR"]
        EE["epub_extractor\nebooklib"]
        HE["html_extractor\nBeautifulSoup"]
    end

    CLASS["Classifier\nheuristic + LLM fallback\n(confidence < 0.6)"]

    subgraph route["Route by content type"]
        CHUNK["Chunker\nprofile-aware sizing\n500–1100 tokens"]
        SL["Structured Loader\nupsert tables"]
        PEX["Principle Extractor\nClaude LLM"]
    end

    subgraph store["Postgres"]
        KC[("knowledge_chunks\n+ pgvector embeddings")]
        PP[("programming_principles")]
        EX[("exercises · templates\nprilepin_chart")]
    end

    OAI["OpenAI\ntext-embedding-3-small"]

    PDF --> PE --> CLASS
    EPUB --> EE --> CLASS
    WEB --> HE --> CLASS

    CLASS -->|prose| CHUNK --> OAI --> KC
    CLASS -->|tables / programs| SL --> EX
    CLASS -->|if-then rules| PEX --> PP
    CLASS -->|mixed| CHUNK & PEX
```

---

## Database Schema (20 tables)

```mermaid
erDiagram
    sources ||--o{ knowledge_chunks : contains
    sources ||--o{ programming_principles : yields
    sources ||--o{ ingestion_runs : tracks

    knowledge_chunks }o--o{ ingestion_chunk_log : logged_in
    exercises ||--o{ exercise_substitutions : has
    exercises ||--o{ exercise_complexes : part_of

    athletes ||--o{ athlete_maxes : has
    athletes ||--o{ athlete_goals : sets
    athletes ||--o{ generated_programs : owns

    generated_programs ||--o{ program_sessions : contains
    generated_programs ||--o{ generation_log : tracked_in

    program_sessions ||--o{ session_exercises : has
    program_sessions ||--o{ training_logs : logged_as

    training_logs ||--o{ training_log_exercises : has
    training_log_exercises }o--o| session_exercises : references
```

---

## Local Development

Three processes must be running simultaneously:

| Process | Command | Purpose |
|---------|---------|---------|
| Infrastructure | `cd oly-ingestion && docker compose up -d` | Postgres + Redis |
| Web server | `cd oly-agent && PYTHONUTF8=1 uv run uvicorn web.app:app --reload --port 8080` | Serves the UI |
| ARQ worker | `cd oly-agent && PYTHONUTF8=1 uv run arq web.worker.WorkerSettings` | Runs generation jobs |

The web server and ARQ worker are **separate OS processes** — both connect to the same Redis and Postgres. The worker can be restarted independently without affecting the web server.

---

## Production Deployment

```
                     ┌─────────────────────────────────────────┐
Internet ──► Reverse │  nginx / Caddy / ALB  (HTTPS termination)│
             Proxy   └──────────────┬──────────────────────────┘
                                    │ HTTP
                     ┌──────────────▼──────────────┐
                     │  uvicorn  (web.app:app)       │  ← 1+ instances
                     └──────────────┬──────────────┘
                                    │ asyncpg / arq
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
   ┌──────────▼──────┐   ┌──────────▼──────┐   ┌─────────▼───────┐
   │   Postgres 16   │   │    Redis 7       │   │   ARQ Worker    │
   │   + pgvector    │   │                 │   │  (1 process,    │
   └─────────────────┘   └─────────────────┘   │   max_jobs=1)   │
                                                └─────────────────┘
```

**Required environment variables for production:**

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Full Postgres connection string (required — no localhost fallback) |
| `SECRET_KEY` | Session signing key — must be stable across restarts |
| `REDIS_URL` | Redis connection string (default: `redis://localhost:6379`) |
| `HTTPS_ONLY` | Set to `true` to enable `Secure` cookie flag |
| `ANTHROPIC_API_KEY` | Claude API — required for generation |
| `OPENAI_API_KEY` | OpenAI embeddings — required for vector search |

See [`SECURITY.md`](SECURITY.md) for the full security audit and deployment checklist.
