# AutoCon — Parsing Bot

AI-powered document parser for documentary video production. Upload a Word-exported `.htm` article (plus an optional PDF of screenshots) and get back a structured JSON analysis and an annotated Word document with matched visual evidence.

## Stack

- **Backend**: Python 3.11, FastAPI, Pydantic, OpenAI, PyMuPDF, BeautifulSoup, python-docx
- **Frontend**: React 18, Vite
- **Infrastructure**: Docker Compose

## Pipeline

1. **HTML/DOCX extraction** — parses narration, quotes, inline images, and citation paragraphs
2. **Citation grouping** — groups images under the citation block that follows them
3. **Semantic matching** — OpenAI text embeddings + cosine similarity to link narration segments to citations
4. **Quote linking** — LLM extracts description ↔ quote pairs; nearest image assigned by position
5. **DOCX generation** — annotated Word document with matched screenshots inserted after each quote block
6. **PDF screenshot matching** — optional OCR + embedding-based matching of uploaded PDF pages to citations

## Quick start

### Prerequisites
- Python 3.11+
- Node.js 18+
- An OpenAI API key

### 1. Clone and configure
```bash
git clone <repo-url>
cd parsing-bot
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY
```

### 2. Run backend
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. Run frontend
```bash
cd frontend
npm install
npm run dev
```

Open the app at `http://localhost:5173`.

## Run with Docker

```bash
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY

docker compose up --build
```

- Frontend: `http://localhost:5173`
- Backend health: `http://localhost:8000/api/health`

Stop with `Ctrl + C`, then:
```bash
docker compose down
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/parse` | Upload HTML/DOCX + optional PDF → returns JSON result |
| GET | `/api/output/json` | Download JSON companion |
| GET | `/api/output/docx` | Download annotated Word document |
| DELETE | `/api/history/{id}` | Delete a stored run |
| GET | `/api/health` | Health check |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | OpenAI API key |
| `ELEVENLABS_API_KEY` | No | ElevenLabs TTS key |
| `ELEVENLABS_VOICE_ID` | No | Default: `21m00Tcm4TlvDq8ikWAM` |
| `YOUTUBE_API_KEY` | No | YouTube Data API key |
| `AUTOCON_SAVE_DOCX` | No | Persist DOCX artifacts to `backend/storage/` |

## License

MIT
