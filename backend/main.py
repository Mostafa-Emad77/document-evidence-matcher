"""
Parsing Bot — FastAPI application entry point.

Endpoints
---------
POST /api/parse          Upload HTML or DOCX + optional screenshot PDF; run Milestone 1 pipeline
GET  /api/output/json    Download the last JSON result
GET  /api/output/docx    Download the annotated DOCX result
DELETE /api/history/{id} Delete a stored run (json/docx/meta)
GET  /api/health         Health check
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from openai import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)

from backend.schemas import Milestone1Output
from backend.services.assembler import run_pipeline
from backend.services.docx_writer import generate_docx

app = FastAPI(title="Parsing Bot", version="0.1.0")

logger = logging.getLogger("autocon.parse")

# Allow all origins in development; tighten for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory store for the latest result (stateless — single user)
_STORAGE_DIR = Path(__file__).parent / "storage"
_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

_state: dict[str, Path] = {}  # keys: "json", "docx"

def _resolve(key: str) -> Path | None:
    """Return path for key ('json' or 'docx'), using _state first then scanning disk."""
    if key in _state and _state[key].is_file():
        return _state[key]
    if key in _state and not _state[key].is_file():
        _state.pop(key, None)
    ext = key  # "json" or "docx"
    files = sorted(_STORAGE_DIR.glob(f"result_*.{ext}"), key=lambda p: p.stat().st_mtime, reverse=True)
    if files:
        _state[key] = files[0]   # cache for next call
        return files[0]
    return None


def _run_id_from_path(path: Path) -> str | None:
    stem = path.stem
    if not stem.startswith("result_"):
        return None
    return stem.split("result_", 1)[1]


def _history_from_storage(limit: int = 50) -> list[dict]:
    json_files = sorted(
        _STORAGE_DIR.glob("result_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    rows: list[dict] = []
    for json_path in json_files[:limit]:
        run_id = _run_id_from_path(json_path)
        if not run_id:
            continue

        docx_path = _STORAGE_DIR / f"result_{run_id}.docx"
        meta_path = _STORAGE_DIR / f"result_{run_id}.meta.json"

        row = {
            "run_id": run_id,
            "created_at": datetime.fromtimestamp(json_path.stat().st_mtime, tz=timezone.utc).isoformat(),
            "source_file": "Parsed Document",
            "segment_count": None,
            "quote_count": None,
            "parse_duration_seconds": None,
            "json_available": True,
            "docx_available": docx_path.is_file(),
        }

        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                row["created_at"] = meta.get("created_at", row["created_at"])
                row["source_file"] = meta.get("source_file", row["source_file"])
                row["segment_count"] = meta.get("segment_count")
                row["quote_count"] = meta.get("quote_count")
                row["parse_duration_seconds"] = meta.get("parse_duration_seconds")
            except Exception:
                pass

        rows.append(row)

    return rows


def _delete_run_files(run_id: str) -> bool:
    targets = [
        _STORAGE_DIR / f"result_{run_id}.json",
        _STORAGE_DIR / f"result_{run_id}.docx",
        _STORAGE_DIR / f"result_{run_id}.meta.json",
    ]

    deleted_any = False
    for path in targets:
        if path.is_file():
            path.unlink()
            deleted_any = True

    # Clear stale cached pointers if they pointed to the deleted run
    for key, path in list(_state.items()):
        if _run_id_from_path(path) == run_id:
            _state.pop(key, None)

    return deleted_any


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/parse", response_model=Milestone1Output)
async def parse_article(
    html_file: UploadFile | None = File(None),
    source_docx: UploadFile | None = File(None),
    screenshots_pdf: UploadFile | None = File(None),
    file: UploadFile | None = File(None),  # backward compatibility with old frontend
    span_coloring_level: str = Form("medium"),  # "minimal", "medium", "maximum"
) -> JSONResponse:
    """
    Accept an HTML or DOCX file plus an optional screenshot PDF, run the full parsing pipeline,
    store the DOCX + JSON to /storage, and return the JSON result.
    """
    # Fallback for older client that submits only "file".
    if html_file is None and source_docx is None and file is not None:
        file_suffix = Path(file.filename or "article").suffix.lower()
        if file_suffix == ".docx":
            source_docx = file
        else:
            html_file = file

    if html_file is None and source_docx is None:
        raise HTTPException(status_code=400, detail="Missing html_file or source_docx")

    html_suffix = ""
    if html_file is not None:
        html_suffix = Path(html_file.filename or "article").suffix.lower()
        if html_suffix not in (".htm", ".html"):
            raise HTTPException(status_code=400, detail="html_file must be .htm or .html")

    docx_suffix = ""
    if source_docx is not None:
        docx_suffix = Path(source_docx.filename or "article").suffix.lower()
        if docx_suffix != ".docx":
            raise HTTPException(status_code=400, detail="source_docx must be .docx")

    pdf_suffix = ""
    if screenshots_pdf is not None:
        pdf_suffix = Path(screenshots_pdf.filename or "screenshots").suffix.lower()
        if pdf_suffix != ".pdf":
            raise HTTPException(status_code=400, detail="screenshots_pdf must be .pdf")

    # Save upload to a temp file
    tmp_dir = Path(tempfile.mkdtemp())
    html_tmp_path: Path | None = None
    docx_tmp_path: Path | None = None
    pdf_tmp_path: Path | None = None
    try:
        if html_file is not None:
            html_tmp_path = tmp_dir / f"article{html_suffix or '.htm'}"
            with html_tmp_path.open("wb") as f:
                shutil.copyfileobj(html_file.file, f)

        if source_docx is not None:
            docx_tmp_path = tmp_dir / f"article{docx_suffix or '.docx'}"
            with docx_tmp_path.open("wb") as f:
                shutil.copyfileobj(source_docx.file, f)

        if screenshots_pdf is not None:
            pdf_tmp_path = tmp_dir / "screenshots.pdf"
            with pdf_tmp_path.open("wb") as f:
                shutil.copyfileobj(screenshots_pdf.file, f)

        run_id = uuid.uuid4().hex[:8]
        t0 = time.perf_counter()
        try:
            output: Milestone1Output = run_pipeline(
                str(html_tmp_path) if html_tmp_path is not None else None,
                screenshots_pdf_path=str(pdf_tmp_path) if pdf_tmp_path else None,
                source_docx_path=str(docx_tmp_path) if docx_tmp_path is not None else None,
                span_coloring_level=span_coloring_level,
            )
        except RateLimitError as exc:
            logger.error("OpenAI rate limit / quota exceeded: %s", exc)
            raise HTTPException(
                status_code=429,
                detail=(
                    "OpenAI API quota exceeded. Highlighting and citation matching require a "
                    "working API key with available credits. Check your billing at "
                    "platform.openai.com/account/billing."
                ),
            )
        except AuthenticationError as exc:
            logger.error("OpenAI authentication failed: %s", exc)
            raise HTTPException(
                status_code=502,
                detail="OpenAI authentication failed. Check that OPENAI_API_KEY in .env is valid.",
            )
        except PermissionDeniedError as exc:
            logger.error("OpenAI permission denied: %s", exc)
            raise HTTPException(
                status_code=502,
                detail="OpenAI API permission denied. The API key cannot access the required model.",
            )
        except APIConnectionError as exc:
            logger.error("OpenAI connection error: %s", exc)
            raise HTTPException(
                status_code=502,
                detail="Could not reach the OpenAI API. Check your internet connection and try again.",
            )
        except APIError as exc:
            logger.exception("OpenAI API error: %s", exc)
            raise HTTPException(
                status_code=502,
                detail=f"OpenAI API error: {exc}",
            )
        docx_bytes = generate_docx(output)
        elapsed_s = time.perf_counter() - t0
        parse_duration_seconds = round(elapsed_s, 2)
        generated_at = datetime.now(timezone.utc).isoformat()
        output = output.model_copy(update={
            "parse_duration_seconds": parse_duration_seconds,
            "generated_at": generated_at,
            "from_cache": False,
            "cache_key": None,
        })

        # Persist JSON
        json_path = _STORAGE_DIR / f"result_{run_id}.json"
        json_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
        _state["json"] = json_path

        # Persist DOCX
        docx_path = _STORAGE_DIR / f"result_{run_id}.docx"
        docx_path.write_bytes(docx_bytes)
        _state["docx"] = docx_path

        # Persist lightweight run metadata for history listing.
        quote_count = sum(
            1 for s in output.segments
            if getattr(s.segment_type, "value", s.segment_type) == "quote"
        )
        meta_path = _STORAGE_DIR / f"result_{run_id}.meta.json"
        meta = {
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_file": (
                (source_docx.filename if source_docx is not None else None)
                or (html_file.filename if html_file is not None else None)
                or "Parsed Document"
            ),
            "segment_count": len(output.segments),
            "quote_count": quote_count,
            "parse_duration_seconds": parse_duration_seconds,
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=True), encoding="utf-8")

        logger.info(
            "parse_complete run_id=%s duration_s=%s segments=%s quote_segments=%s quote_table_rows=%s",
            run_id,
            parse_duration_seconds,
            len(output.segments),
            quote_count,
            len(output.quote_table),
        )

        return JSONResponse(content=json.loads(output.model_dump_json()))

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.get("/api/latest")
def get_latest() -> JSONResponse:
    """Return the latest parsed result as JSON (for frontend state restore on page load)."""
    path = _resolve("json")
    if path is None:
        raise HTTPException(status_code=404, detail="No result yet")
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
        # Mark as cached since it's being served from storage, not freshly generated
        content["from_cache"] = True
        content["cache_key"] = str(path.name)
        if not content.get("generated_at"):
            # Use file modification time as fallback
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
            content["generated_at"] = mtime
        return JSONResponse(content=content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/history")
def get_history(limit: int = 50) -> JSONResponse:
    limit = max(1, min(limit, 200))
    return JSONResponse(content={"runs": _history_from_storage(limit=limit)})


@app.delete("/api/history/{run_id}")
def delete_history_run(run_id: str) -> JSONResponse:
    deleted = _delete_run_files(run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Result not found")
    return JSONResponse(content={"deleted": True, "run_id": run_id})


@app.get("/api/output/json")
def download_json() -> JSONResponse:
    path = _resolve("json")
    if path is None:
        raise HTTPException(status_code=404, detail="No result yet — run /api/parse first")
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
        # Mark as cached since it's being served from storage
        content["from_cache"] = True
        content["cache_key"] = str(path.name)
        if not content.get("generated_at"):
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
            content["generated_at"] = mtime
        # Generate timestamped filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"evidence_{timestamp}.json"
        response = JSONResponse(content=content)
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/output/json/{run_id}")
def download_json_by_run_id(run_id: str) -> JSONResponse:
    path = _STORAGE_DIR / f"result_{run_id}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Result not found")
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
        # Mark as cached since it's being served from storage
        content["from_cache"] = True
        content["cache_key"] = str(path.name)
        if not content.get("generated_at"):
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
            content["generated_at"] = mtime
        # Generate timestamped filename with run_id
        timestamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y%m%d_%H%M%S")
        filename = f"evidence_{timestamp}_{run_id}.json"
        response = JSONResponse(content=content)
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/output/docx")
def download_docx() -> FileResponse:
    path = _resolve("docx")
    if path is None:
        raise HTTPException(status_code=404, detail="No result yet — run /api/parse first")
    # Generate timestamped filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"evidence_{timestamp}.docx"
    return FileResponse(
        path=str(path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )


@app.get("/api/output/docx/{run_id}")
def download_docx_by_run_id(run_id: str) -> FileResponse:
    path = _STORAGE_DIR / f"result_{run_id}.docx"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Result not found")
    # Generate timestamped filename with run_id
    timestamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y%m%d_%H%M%S")
    filename = f"evidence_{timestamp}_{run_id}.docx"
    return FileResponse(
        path=str(path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )
