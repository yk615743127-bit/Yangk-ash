\
from __future__ import annotations

import json
import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DAILY_DIR = DATA_DIR / "daily"
OUTPUT_DIR = PROJECT_ROOT / "output"
CONFIG_PATH = PROJECT_ROOT / "config.json"
ENV_PATH = PROJECT_ROOT / ".env"


def load_settings() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_tushare_token() -> str:
    load_dotenv(ENV_PATH)
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token or token == "your_tushare_token_here":
        raise RuntimeError(
            "未检测到 TUSHARE_TOKEN。请复制 .env.example 为 .env，并填写你的 Tushare Token。"
        )
    return token
