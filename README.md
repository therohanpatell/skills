# pdfocr

A local PDF → text service. Drop a PDF in the browser or POST it to the API, and it comes
back as text — transcribed by a vision LLM you run yourself (Qwen2.5-VL, DeepSeek-OCR, …),
with live per-page progress while it works.

Nothing leaves your network. There is no cloud API key anywhere in this project.

```
┌─ browser UI ─┐
│ drag a PDF   │──┐
└──────────────┘  │   ┌─────────────┐  page images  ┌────────────────────┐
                  ├──▶│ pdfocr API  │──────────────▶│ Ollama / vLLM /    │
┌─ CLI / curl ─┐  │   │  FastAPI    │◀──────────────│ LM Studio (vision) │
│ pdfocr ocr   │──┘   └─────────────┘   markdown    └────────────────────┘
└──────────────┘             │
                             └── SSE progress ──▶ progress bar, ETA, per-page state
```

## Why a vision model

Scanned pages are images. Classic OCR reads glyphs but loses layout; a vision LLM reads the
page the way a person does, so tables stay tables, headings stay headings and multi-column
layouts come out in reading order. The trade is speed — a few seconds per page — which is
exactly why this service streams progress instead of blocking.

Three engines ship in the box, and `auto` picks between them per document:

| engine | what it does | when `auto` uses it |
| --- | --- | --- |
| `native` | reads the PDF's embedded text layer | the PDF is digital, not scanned — instant and lossless |
| `vlm` | sends each page image to a vision LLM | scanned/image PDFs, when the model server answers |
| `tesseract` | classic OCR, fully offline | scanned PDFs with no model server reachable |

## Install

```bash
git clone <this repo> && cd skills
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

Then start a vision model. Any OpenAI-compatible server works — pick one:

```bash
# Ollama (easiest; ~6 GB download)
ollama pull qwen2.5vl:7b
ollama serve
# -> PDFOCR_VLM_BASE_URL=http://localhost:11434/v1  PDFOCR_VLM_MODEL=qwen2.5vl:7b

# vLLM with DeepSeek-OCR (needs a CUDA GPU, best quality on dense scans)
vllm serve deepseek-ai/DeepSeek-OCR --port 8001 --trust-remote-code
# -> PDFOCR_VLM_BASE_URL=http://localhost:8001/v1  PDFOCR_VLM_MODEL=deepseek-ai/DeepSeek-OCR

# LM Studio: load any vision model, enable the local server
# -> PDFOCR_VLM_BASE_URL=http://localhost:1234/v1
```

Check what the service can see:

```bash
pdfocr engines
```

## Run it on your network

```bash
pdfocr serve                 # binds 0.0.0.0:8000
```

Open `http://<your-lan-ip>:8000` from any machine or phone on the network: drag a PDF in,
watch the progress bar and the per-page grid fill up, copy or download the text.
Interactive API docs are at `/docs`.

With Docker instead:

```bash
docker compose up --build    # http://<your-lan-ip>:8000, tesseract included
```

## CLI

```bash
pdfocr ocr scan.pdf -o scan.txt        # progress bar on stderr, text to the file
pdfocr ocr scan.pdf                    # text to stdout, pipe it anywhere
pdfocr ocr scan.pdf -e vlm --dpi 300   # force the vision model, denser rendering
pdfocr engines                         # which backends are reachable right now
```

## HTTP API

| method | path | purpose |
| --- | --- | --- |
| `GET` | `/api/health` | service version and per-engine availability |
| `POST` | `/api/jobs` | upload a PDF (`file`, optional `engine`, `dpi`) → `202` + job id |
| `GET` | `/api/jobs` | recent jobs |
| `GET` | `/api/jobs/{id}` | full job state, including per-page status and text |
| `GET` | `/api/jobs/{id}/events` | **SSE** stream of progress updates until the job settles |
| `GET` | `/api/jobs/{id}/result?format=txt\|md\|json&download=true` | the transcription |
| `POST` | `/api/jobs/{id}/cancel` | stop a running job |
| `DELETE` | `/api/jobs/{id}` | forget the job and delete its PDF |

Submit and follow along with nothing but curl:

```bash
ID=$(curl -s -F file=@scan.pdf http://192.168.1.10:8000/api/jobs | jq -r .id)
curl -N http://192.168.1.10:8000/api/jobs/$ID/events     # live progress, one JSON per line
curl http://192.168.1.10:8000/api/jobs/$ID/result        # the text
```

Each progress event carries everything a UI needs:

```json
{"event":"progress","id":"a22755c5fabf42af","status":"running","engine":"vlm",
 "progress":{"pages_total":42,"pages_done":11,"pages_failed":0,"percent":26.2,
             "stage":"ocr","message":"page 11/42 with vlm","eta_seconds":93.4},
 "pages":[{"number":1,"status":"done","chars":1840,"error":null}, ...]}
```

Python client:

```python
import httpx, json

base = "http://192.168.1.10:8000"
job = httpx.post(f"{base}/api/jobs", files={"file": open("scan.pdf", "rb")}).json()

with httpx.stream("GET", f"{base}{job['events_url']}", timeout=None) as stream:
    for line in stream.iter_lines():
        if line.startswith("data: "):
            state = json.loads(line[6:])
            print(f"{state['progress']['percent']:.0f}% {state['progress']['message']}")
            if state["status"] != "running":
                break

print(httpx.get(f"{base}{job['result_url']}").text)
```

## Configuration

Every setting is an environment variable (or a line in `.env` — copy `.env.example`).

| variable | default | notes |
| --- | --- | --- |
| `PDFOCR_HOST` / `PDFOCR_PORT` | `0.0.0.0` / `8000` | where the server binds |
| `PDFOCR_API_KEY` | *(unset)* | when set, clients must send `X-API-Key` |
| `PDFOCR_ENGINE` | `auto` | `auto`, `vlm`, `native`, `tesseract` |
| `PDFOCR_DPI` | `200` | page render resolution; 300 helps on small print |
| `PDFOCR_PAGE_CONCURRENCY` | `2` | pages in flight per job — raise it if your GPU has headroom |
| `PDFOCR_JOB_CONCURRENCY` | `2` | documents processed at once |
| `PDFOCR_MAX_PAGES` | `0` | cap pages per document (`0` = no cap) |
| `PDFOCR_MAX_UPLOAD_MB` | `200` | upload size limit |
| `PDFOCR_VLM_BASE_URL` | `http://localhost:11434/v1` | any OpenAI-compatible endpoint |
| `PDFOCR_VLM_MODEL` | `qwen2.5vl:7b` | model name as your server exposes it |
| `PDFOCR_VLM_PROMPT` | transcription prompt | override to change the output style |
| `PDFOCR_TESSERACT_LANG` | `eng` | e.g. `eng+deu` |
| `PDFOCR_STORAGE_DIR` | `storage` | uploads and results live here |
| `PDFOCR_JOB_TTL_SECONDS` | `86400` | jobs and their PDFs are purged after this |

## Notes on accuracy and speed

- **Digital PDFs are free.** `auto` reads their text layer directly; no model runs at all.
- **Page concurrency is the main speed dial.** A 7B vision model on a mid-range GPU handles
  roughly one page every 3–8 s; two or three pages in flight usually saturates it.
- **A failed page does not fail the document.** The page is marked `failed` with its error,
  the rest still finish, and the result contains everything that was transcribed.
- **Vision models can hallucinate.** They are excellent at layout and messy handwriting, but
  for legal or financial documents where a wrong digit matters, spot-check the output.

## Development

```bash
pip install -r requirements-dev.txt
pytest          # 28 tests, no model server or network needed
ruff check .
```

The layout: `pdfocr/pdf.py` renders pages, `pdfocr/engines/` holds the interchangeable OCR
backends, `pdfocr/jobs.py` runs jobs and broadcasts progress, `pdfocr/api.py` is the HTTP
surface and `pdfocr/static/` is the UI. To add an engine, subclass `Engine`, implement
`run()` and `check()`, and register it in `pdfocr/engines/__init__.py`.
