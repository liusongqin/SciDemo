from pathlib import Path
from pydantic import BaseModel
import os
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseModel):
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai-compatible")
    llm_model: str = os.getenv("LLM_MODEL", "Qwen3.5-9B")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8001/v1")
    database: Path = Path(os.getenv("SCIDEMO_DATABASE", "data/scidemo.db"))
    timeout_seconds: int = int(os.getenv("TASK_TIMEOUT_SECONDS", "60"))
    max_tool_steps: int = int(os.getenv("MAX_TOOL_STEPS", "12"))
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "90"))
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "800"))
    llm_fallback_to_mock: bool = os.getenv("LLM_FALLBACK_TO_MOCK", "false").lower()=="true"

settings = Settings()
