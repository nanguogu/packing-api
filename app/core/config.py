"""Application configuration loaded from environment variables."""

from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://packing:packing123@localhost:5432/packing_db")
REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Box size presets (cm) - can be overridden via env
BOX_PRESETS: list[dict] = [
    {"name": "S", "length": 30, "width": 20, "height": 15},
    {"name": "M", "length": 50, "width": 40, "height": 30},
    {"name": "L", "length": 80, "width": 60, "height": 50},
    {"name": "XL", "length": 100, "width": 80, "height": 60},
]
