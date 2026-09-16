import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv  # pyright: ignore[reportMissingImports]

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    db_path: str = str(ROOT / "data" / "deskline.sqlite3")
    api_key: str = ""
    public_key: str = ""
    connection_id: str = ""
    voice: str = "Telnyx.KokoroTTS.af"
    max_call_seconds: int = 180

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env")
        return cls(
            db_path=os.getenv("DESKLINE_DB", cls.db_path),
            api_key=os.getenv("TELNYX_API_KEY", ""),
            public_key=os.getenv("TELNYX_PUBLIC_KEY", ""),
            connection_id=os.getenv("TELNYX_CONNECTION_ID", ""),
            voice=os.getenv("TELNYX_VOICE", cls.voice) or cls.voice,
        )

    def validate(self):
        required = {
            "TELNYX_API_KEY": self.api_key,
            "TELNYX_PUBLIC_KEY": self.public_key,
            "TELNYX_CONNECTION_ID": self.connection_id,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise RuntimeError("Missing settings: " + ", ".join(missing))
