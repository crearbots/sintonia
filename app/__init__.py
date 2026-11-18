"""Sintonía — carga variables de `.env` antes de importar módulos que leen el entorno."""
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")