import os
import datetime
import sys
import uuid
import json
import logging
import shutil
import threading
from typing import Optional, List
from fastapi import FastAPI, Depends, BackgroundTasks, UploadFile, File, Form, HTTPException, status, Request
from fastapi.responses import RedirectResponse, PlainTextResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
from sqlmodel import Session, select

# Ensure parent directory is in path to import transcribe and config modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from app.database import init_db, get_session, engine, APIKey, TranscriptionJob, UsageLog
    from app.auth import get_api_key
    from app.api_rate_limiter import rate_limit
except ImportError:
    from database import init_db, get_session, engine, APIKey, TranscriptionJob, UsageLog
    from auth import get_api_key
    from api_rate_limiter import rate_limit
from schemas import TranscriptionSubmitResponse, TranscriptionJobStatusResponse
from transcribe import transcribe
from config import usage_tracker

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("transcription_api")

app = FastAPI(
    title="Agent-First Transcription API",
    description="A production-ready, async transcription API designed for AI agents. Provides language detection, speaker diarization, and English translation.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global lock to safely record usage stats per job sequentially
pipeline_lock = threading.Lock()

# Directory for temp uploads
UPLOAD_DIR = os.path.abspath("uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.on_event("startup")
def on_startup():
    init_db()
    logger.info("Database initialized.")

# --- Models ---
class KeyCreateRequest(BaseModel):
    owner: str
    rate_limit_rpm: Optional[int] = 60
    quota_limit_usd: Optional[float] = 10.0

class TranscribeUrlRequest(BaseModel):
    url: HttpUrl
    model: Optional[str] = None

# --- Background Task Worker ---
def process_transcription(
    job_id: str,
    api_key_id: int,
    file_path: Optional[str],
    url: Optional[str],
    model_override: Optional[str]
):
    with Session(engine) as session:
        job = session.get(TranscriptionJob, job_id)
        if not job:
            logger.error(f"Job {job_id} not found in database.")
            return

        job.status = "processing"
        session.add(job)
        session.commit()

        api_key = session.get(APIKey, api_key_id)

    try:
        # Secure the lock to reset/measure global usage_tracker accurately
        with pipeline_lock:
            usage_tracker.reset()
            
            logger.info(f"Starting transcription for job {job_id} using {file_path or url}")
            if file_path:
                result = transcribe(input_path=file_path, model=model_override)
            else:
                result = transcribe(url=str(url), model=model_override)

            summary = usage_tracker.summary()
            total_cost = summary.get("total_cost_usd", 0.0)

        with Session(engine) as session:
            job = session.get(TranscriptionJob, job_id)
            api_key = session.get(APIKey, api_key_id)
            
            # If the transcription script returns empty defaults or failed entirely
            if not result or not isinstance(result, dict) or "text" not in result:
                # Check if there was an implicit failure
                job.status = "failed"
                job.error_message = "Transcription pipeline executed but returned no result. Upstream models may be rate-limited or the media format is invalid."
            else:
                job.status = "completed"
                job.result_json = json.dumps(result, ensure_ascii=False)

            job.updated_at = datetime.datetime.utcnow()
            session.add(job)

            # Record usage log & deduct quota
            api_key.quota_used_usd = float(api_key.quota_used_usd) + total_cost
            session.add(api_key)

            # Create individual UsageLog rows
            for key, agg in summary.get("by_provider", {}).items():
                provider, model = key.split("/")
                log_entry = UsageLog(
                    api_key_id=api_key.id,
                    job_id=job.id,
                    provider=provider,
                    model=model,
                    input_tokens=agg.get("input_tokens", 0),
                    output_tokens=agg.get("output_tokens", 0),
                    cost_usd=agg.get("cost_usd", 0.0)
                )
                session.add(log_entry)

            if "chirp3" in summary:
                c3 = summary["chirp3"]
                log_entry = UsageLog(
                    api_key_id=api_key.id,
                    job_id=job.id,
                    provider="chirp3",
                    model="chirp_3",
                    audio_seconds=c3.get("minutes", 0.0) * 60.0,
                    cost_usd=c3.get("cost_usd", 0.0)
                )
                session.add(log_entry)

            session.commit()
            logger.info(f"Completed transcription for job {job_id}. Cost: ${total_cost:.4f}")

    except Exception as e:
        logger.exception(f"Error processing job {job_id}: {e}")
        with Session(engine) as session:
            job = session.get(TranscriptionJob, job_id)
            job.status = "failed"
            job.error_message = f"An unexpected error occurred during transcription processing: {str(e)}"
            job.updated_at = datetime.datetime.utcnow()
            session.add(job)
            session.commit()
    finally:
        # Cleanup local file if it was uploaded
        if file_path and os.path.exists(file_path):
            try:
                os.unlink(file_path)
                logger.info(f"Cleaned up temp upload file {file_path}")
            except Exception as cleanup_err:
                logger.error(f"Failed to delete temp file {file_path}: {cleanup_err}")

# --- Endpoints ---

@app.get("/health", status_code=status.HTTP_200_OK)
def health_check(session: Session = Depends(get_session)):
    """Health check for monitoring services."""
    try:
        # Check SQLite db access
        session.exec(select(APIKey)).first()
        # Verify transcription file dependency path exists
        whisper_chk = os.path.exists("transcribe.py")
        if not whisper_chk:
            return {"status": "unhealthy", "reason": "transcribe.py not found"}
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "reason": str(e)}
        )

@app.post("/v1/keys", response_model=APIKey)
def create_api_key(req: KeyCreateRequest, session: Session = Depends(get_session)):
    """Generate and register a new API key (Admin / Development endpoint)."""
    new_key = f"sk_{uuid.uuid4().hex}"
    api_key = APIKey(
        key=new_key,
        owner=req.owner,
        rate_limit_rpm=req.rate_limit_rpm,
        quota_limit_usd=req.quota_limit_usd,
        quota_used_usd=0.0
    )
    session.add(api_key)
    session.commit()
    session.refresh(api_key)
    return api_key

class KeyUpdateRequest(BaseModel):
    is_active: Optional[bool] = None
    rate_limit_rpm: Optional[int] = None
    quota_limit_usd: Optional[float] = None

@app.get("/v1/keys", response_model=List[APIKey])
def list_api_keys(session: Session = Depends(get_session)):
    """Retrieve all API keys in the system (Admin only)."""
    return session.exec(select(APIKey)).all()

@app.patch("/v1/keys/{key_id}", response_model=APIKey)
def update_api_key(key_id: int, req: KeyUpdateRequest, session: Session = Depends(get_session)):
    """Update an API key's status or limits (Admin only)."""
    api_key = session.get(APIKey, key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key not found")
    if req.is_active is not None:
        api_key.is_active = req.is_active
    if req.rate_limit_rpm is not None:
        api_key.rate_limit_rpm = req.rate_limit_rpm
    if req.quota_limit_usd is not None:
        api_key.quota_limit_usd = req.quota_limit_usd
    session.add(api_key)
    session.commit()
    session.refresh(api_key)
    return api_key

@app.post("/v1/transcribe", response_model=TranscriptionSubmitResponse, status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(rate_limit)])
async def submit_transcription(
    background_tasks: BackgroundTasks,
    request: Request,
    file: Optional[UploadFile] = File(None),
    url_req_str: Optional[str] = Form(None),  # Accept URL via form parameter
    model: Optional[str] = Form(None),
    api_key: APIKey = Depends(get_api_key),
    session: Session = Depends(get_session)
):
    """Submit a video/audio file (via upload) or public URL for asynchronous transcription.

    Returns a 202 Accepted status with a URL to check the status of the job.
    """
    url_req = None
    if url_req_str:
        try:
            url_req = json.loads(url_req_str)
        except Exception:
            # Fallback to plain URL string
            url_req = {"url": url_req_str}

    # Determine input source
    if not file and not url_req:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "MISSING_INPUT",
                    "message": "You must provide either an uploaded 'file' or a 'url' payload.",
                    "suggested_action": "Resubmit the request with a file attached or standard URL parameters.",
                    "documentation_url": "https://api.transcribe-agent.example.com/docs#transcription-requests"
                }
            }
        )

    job_id = str(uuid.uuid4())
    temp_file_path = None
    input_source_desc = ""

    # Process file upload
    if file:
        # Restrict uploads to 100MB
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 100 * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "error": {
                        "code": "FILE_TOO_LARGE",
                        "message": "File exceeds the maximum limit of 100MB.",
                        "suggested_action": "Compress the video or extract the audio track to fit within the 100MB limit.",
                        "documentation_url": "https://api.transcribe-agent.example.com/docs#file-limits"
                    }
                }
            )

        ext = os.path.splitext(file.filename)[1] or ".mp4"
        temp_file_path = os.path.join(UPLOAD_DIR, f"{job_id}{ext}")
        
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        input_source_desc = f"upload: {file.filename}"
    else:
        # Process URL payload
        input_source_desc = f"url: {url_req.get('url')}"
        model = model or url_req.get("model")

    # Log job in DB
    job = TranscriptionJob(
        id=job_id,
        api_key_id=api_key.id,
        status="pending",
        input_source=input_source_desc,
        model_override=model
    )
    session.add(job)
    session.commit()

    # Trigger async processor background worker
    background_tasks.add_task(
        process_transcription,
        job_id=job_id,
        api_key_id=api_key.id,
        file_path=temp_file_path,
        url=url_req.get("url") if url_req else None,
        model_override=model
    )

    status_endpoint_url = f"/v1/jobs/{job_id}"
    return {
        "job_id": job_id,
        "status": "pending",
        "status_url": status_endpoint_url,
        "message": "Transcription job accepted and queued."
    }

@app.get("/v1/jobs/{job_id}", response_model=TranscriptionJobStatusResponse)
def get_job_status(
    job_id: str,
    api_key: APIKey = Depends(get_api_key),
    session: Session = Depends(get_session)
):
    """Retrieve the status and results of a transcription job."""
    job = session.get(TranscriptionJob, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "JOB_NOT_FOUND",
                    "message": "The requested transcription job ID does not exist.",
                    "suggested_action": "Verify that the job ID was entered correctly.",
                    "documentation_url": "https://api.transcribe-agent.example.com/docs#job-polling"
                }
            }
        )

    # Ensure key owners can only view their own jobs
    if job.api_key_id != api_key.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "ACCESS_DENIED",
                    "message": "You do not have permission to view this job's status.",
                    "suggested_action": "Verify you are using the correct API key that submitted this job.",
                    "documentation_url": "https://api.transcribe-agent.example.com/docs#authorization"
                }
            }
        )

    response = {
        "job_id": job.id,
        "status": job.status,
        "input_source": job.input_source,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat()
    }

    if job.status == "completed":
        response["result"] = json.loads(job.result_json)
    elif job.status == "failed":
        response["error"] = {
            "code": "PIPELINE_ERROR",
            "message": job.error_message,
            "suggested_action": "Verify upstream services are operational or retry with a different audio file format."
        }

    return response

@app.get("/v1/usage")
def get_usage(
    api_key: APIKey = Depends(get_api_key),
    session: Session = Depends(get_session)
):
    """Programmatic retrieval of API key billing, limits, and usage logs."""
    logs_statement = select(UsageLog).where(UsageLog.api_key_id == api_key.id).order_by(UsageLog.created_at.desc())
    logs = session.exec(logs_statement).all()

    return {
        "owner": api_key.owner,
        "rate_limit_rpm": api_key.rate_limit_rpm,
        "quota_limit_usd": api_key.quota_limit_usd,
        "quota_used_usd": round(api_key.quota_used_usd, 6),
        "quota_remaining_usd": round(max(0.0, api_key.quota_limit_usd - api_key.quota_used_usd), 6),
        "total_requests_logged": len(logs),
        "usage_logs": [
            {
                "job_id": log.job_id,
                "provider": log.provider,
                "model": log.model,
                "input_tokens": log.input_tokens,
                "output_tokens": log.output_tokens,
                "audio_seconds": log.audio_seconds,
                "cost_usd": round(log.cost_usd, 6),
                "timestamp": log.created_at.isoformat()
            }
            for log in logs
        ]
    }

@app.get("/llms.txt", response_class=PlainTextResponse)
def get_llms_txt():
    """Serve agent-friendly llms.txt standard documentation."""
    llms_txt_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "llms.txt"))
    if os.path.exists(llms_txt_path):
        with open(llms_txt_path, "r", encoding="utf-8") as f:
            return f.read()
    return "Agent-First Transcription API Documentation. Visit /docs for OpenAPI specs."

@app.get("/.well-known/llms-txt")
def well_known_llms_txt():
    """Redirect .well-known standard path to /llms.txt."""
    return RedirectResponse(url="/llms.txt")

@app.get("/", response_class=HTMLResponse)
def index_admin_portal():
    """Serve a clean, white Postman-style developer playground UI."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    return HTMLResponse(content="<h3>Template not found</h3>", status_code=404)
