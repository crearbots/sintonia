import logging
import os

import bcrypt
from fastapi import Request

from sqlalchemy.orm import Session

from .models import Usuario

logger = logging.getLogger("sintonia")

DEFAULT_USER = os.getenv("APP_USER")
DEFAULT_PASSWORD = os.getenv("APP_PASSWORD")
if not DEFAULT_USER or not DEFAULT_PASSWORD:
    raise RuntimeError("Faltan APP_USER y APP_PASSWORD en las variables de entorno")

DELEGACIONES = (
    "comunicaciones",
    "politica",
    "juventudes",
    "electoral",
    "fimlm",
    "sonido",
    "banos",
)

COM_ONLY_PREFIXES = (
    "/upload",
    "/personas",
    "/persona/",
    "/pendiente/",
    "/seguimiento/",
    "/cargas",
    "/informe",
    "/dato-nacional",
    "/backup",
    "/usuarios",
    "/config-punto",
    "/podio",
)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_default_user(db: Session):
    user = db.query(Usuario).filter(Usuario.usuario == DEFAULT_USER).first()
    if not user:
        user = Usuario(
            usuario=DEFAULT_USER,
            password_hash=hash_password(DEFAULT_PASSWORD),
            delegacion="comunicaciones",
            activo=True,
        )
        db.add(user)
        db.commit()
        logger.info(f"Usuario creado: {DEFAULT_USER} (comunicaciones)")
        return
    if not getattr(user, "delegacion", None):
        user.delegacion = "comunicaciones"
    if getattr(user, "activo", None) is None:
        user.activo = True
    if not verify_password(DEFAULT_PASSWORD, user.password_hash):
        user.password_hash = hash_password(DEFAULT_PASSWORD)
        logger.info(f"Contraseña actualizada para: {DEFAULT_USER}")
    db.commit()


def authenticate_user(db: Session, username: str, password: str) -> Usuario | None:
    user = db.query(Usuario).filter(Usuario.usuario == username).first()
    if not user or not user.activo:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def session_delegacion(request: Request) -> str:
    return request.session.get("delegacion") or "comunicaciones"


def es_comunicaciones(request: Request) -> bool:
    return session_delegacion(request) == "comunicaciones"


def ruta_solo_com(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in COM_ONLY_PREFIXES)
