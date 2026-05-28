---
title: Keyframe Transcribe
emoji: 🎙️
colorFrom: indigo
colorTo: pink
sdk: docker
app_port: 7860
pinned: false
---

# Video Transcription Pipeline

Multi-stage transcription pipeline for short-form video/audio content, producing structured output with speaker diarization, multi-language detection, English translation, and on-screen text extraction.

## Pipeline Architecture

```
Video/Audio Input
       │
       ▼
┌──────────────────────────────────┐
│ 1. Whisper tiny (local, ~10ms)   │  Language detection
└──────────────┬───────────────────┘
               │
               ▼
┌──────────────────────────────────┐
│ 2. Chirp 3                       │  Speaker diarization
│                                  │
└──────────────┬───────────────────┘
               │
               ▼
┌──────────────────────────────────┐
│ 3. Gemini 2.5 Flash              │  Structured transcription
│    (Pro fallback if sparse)       │  with diarization context
└──────────────────────────────────┘
```

### Fallback chain
- If diarization returns < 3 segments → Gemini 2.5 Pro (no diarization context)

## Output Schema

The pipeline produces a `TranscriptionResult` with:
- **text** — full English transcript
- **diarizedTranscript** — speaker-labeled segments with per-segment language and translation
- **audioMode** — spoken-narration / music-only / music-with-lyrics / silent / mixed
- **detectedLanguage** / **detectedLanguageName** — primary spoken language
- **languagesUsed** / **languagesUsedNames** — all detected languages
- **isTranslated** — whether any segment needed translation

Each `DiarizedSegment` contains:
- **speaker** — creator / ai / narrator / on-screen-ocr / person1..personN / other
- **text** — English translation
- **originalText** — original language text
- **language** — ISO 639-1 code
- **languageName** — human-readable language name

## Setup

### Prerequisites
- Python 3.11+
- ffmpeg installed (`apt install ffmpeg` or `brew install ffmpeg`)
- GCP service account with Speech-to-Text API enabled (for Chirp 3 diarization)

### Install

```bash
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# Fill in your API keys
```

**Required keys (just 2):**
| Key | Purpose |
|-----|---------|
| `GCP_PROJECT_ID` | GCP project ID — used for both Gemini (Vertex AI) and Chirp 3 |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to GCP service account JSON (needs `roles/aiplatform.user` + `roles/speech.client`) |


## Usage

### CLI

```bash
# Transcribe a local file
python transcribe.py video.mp4

# Transcribe from URL
python transcribe.py https://example.com/video.mp4

# JSON output
python transcribe.py video.mp4 --json

# Force a specific Gemini model (skips diarization)
python transcribe.py video.mp4 --model gemini-2.5-pro
```

### Python

```python
from transcribe import transcribe

# From a local file
result = transcribe(input_path="video.mp4")

# From a URL
result = transcribe(url="https://example.com/video.mp4")

# Access structured output
print(result["text"])                    # Full English transcript
print(result["detectedLanguage"])        # e.g. "ko"
for seg in result["diarizedTranscript"]:
    print(f"[{seg['speaker']}] ({seg['language']}): {seg['text']}")
```

## Cost per request

| Stage | Service | Approx. Cost |
|-------|---------|-------------|
| Language detection | Whisper tiny (local) | $0.00 |
| Diarization | Chirp 3 | ~$0.008 (30s video) |
| Transcription | Gemini 2.5 Flash | ~$0.01-0.02 |
| Transcription (fallback) | Gemini 2.5 Pro | ~$0.05-0.08 |

**Total: ~$0.02-0.08 per video** depending on model fallback.

## File Structure

```
├── app/
│   ├── auth.py          # API authentication & billing checks
│   ├── database.py      # SQLite database setup & ORM schemas
│   ├── main.py          # FastAPI application & API endpoints
│   ├── mcp_server.py    # Model Context Protocol (MCP) server
│   └── rate_limiter.py  # Token-bucket client rate limiting
├── transcribe.py        # Main pipeline + CLI
├── schemas.py           # Pydantic models + Gemini prompt
├── config.py            # Client singletons, usage tracker, retry logic
├── rate_limiter.py      # Thread-safe token-bucket rate limiter for models
├── .env                 # Environment config & credentials
├── requirements.txt     # Python dependencies
├── Dockerfile           # Deployment container definition
├── llms.txt             # LLM-friendly API documentation
├── DECISIONS.md         # Architecture, pricing & engineering decisions
└── test_api.py          # Automated verification script
```

## Running the API Server

1. **Start the server**:
   ```bash
   .venv\Scripts\uvicorn app.main:app --reload
   ```
2. **Access Interactive Docs**:
   Go to `http://127.0.0.1:8000/docs` to view the interactive Swagger OpenAPI docs.

## Running the MCP Server

To run the MCP server locally over stdio (e.g. for integration with Claude Desktop or Cursor):
```bash
.venv\Scripts\python app/mcp_server.py
```

## Running Tests

Run the automated integration test suite:
1. Ensure the API server is running at `http://127.0.0.1:8000`.
2. Run the test script:
   ```bash
   .venv\Scripts\python test_api.py
   ```

## Authentication & API Key Management

The API requires authentication for rate-limiting, usage tracking, and budget quota enforcement.

### 1. Generating an API Key
You can generate keys directly from the developer playground:
1. Open the portal in your browser (e.g., `http://127.0.0.1:8000/` or your deployed Space URL).
2. Look at the left sidebar, and click the **`+` (Plus)** button next to the **TranscribeAgent** logo.
3. In the popup dialog:
   - Provide an **Agent Name / Owner** (e.g., `Agent-X`).
   - Customize the **Rate Limit (RPM)** (Default: `60`).
   - Customize the **Usage Budget Limit (USD)** (Default: `10.0`).
4. Click **Generate Key**. The portal will automatically copy the new key to your clipboard and activate it.

### 2. Using Your Key in API Requests
Add the key to your HTTP headers as `X-API-Key`:
```bash
curl -X POST "http://127.0.0.1:8000/v1/transcribe" \
     -H "X-API-Key: sk_your_api_key_here" \
     -H "Content-Type: application/json" \
     -d '{"url": "https://www.w3schools.com/html/mov_bbb.mp4"}'
```

### 3. Programmatic Usage & Billing Checks
Callers can query their current quota usage, remaining budget, and request logs programmatically:
```bash
curl -X GET "http://127.0.0.1:8000/v1/usage" \
     -H "X-API-Key: sk_your_api_key_here"
```

