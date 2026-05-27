import datetime
import uuid
from typing import Optional, List
from sqlmodel import Field, SQLModel, create_engine, Session, select

DATABASE_URL = "sqlite:///./app.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

class APIKey(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)
    owner: str
    is_active: bool = Field(default=True)
    rate_limit_rpm: int = Field(default=60)
    quota_limit_usd: float = Field(default=10.0)
    quota_used_usd: float = Field(default=0.0)
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)

class TranscriptionJob(SQLModel, table=True):
    id: str = Field(primary_key=True, default_factory=lambda: str(uuid.uuid4()))
    api_key_id: int = Field(foreign_key="apikey.id")
    status: str = Field(default="pending")  # pending, processing, completed, failed
    input_source: str  # file name or URL
    model_override: Optional[str] = None
    result_json: Optional[str] = Field(default=None)  # JSON representation of TranscriptionResult
    error_message: Optional[str] = None
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)
    updated_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)

class UsageLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    api_key_id: int = Field(foreign_key="apikey.id")
    job_id: str = Field(foreign_key="transcriptionjob.id")
    provider: str  # google, whisper, chirp3
    model: str
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    audio_seconds: float = Field(default=0.0)
    cost_usd: float = Field(default=0.0)
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)


def init_db():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session
