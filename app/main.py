import logging
import os
from pathlib import Path

from fastapi import FastAPI, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .auth import (
    create_default_user,
    es_comunicaciones,
    ruta_solo_com,
)
from .csrf import CSRFMiddleware
from .database import Base, engine, get_db
from .processing import (
    nombre_fuente,
)
from .punto import obtener_config

logger = logging.getLogger("sintonia")

# Ruta base del proyecto
BASE_DIR = Path(__file__).resolve().parent.parent

# Crear tablas
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Sintonía", docs_url=None, redoc_url=None)

# Middleware de sesión (clave fija desde variable de entorno en producción)
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("Falta SECRET_KEY en las variables de entorno")

class PermisosMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        try:
            path = request.url.path
            if request.session.get("user") and ruta_solo_com(path) and not es_comunicaciones(request):
                return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
        except Exception as e:
            logger.info(f"Error en filtro de permisos ({request.url.path}): {e}")
            return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
        return await call_next(request)

# Orden: el último agregado es el más externo. La sesión debe ir por fuera.
app.add_middleware(PermisosMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Archivos estáticos (crea la carpeta si no existe)
static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Plantillas

jinja_env = Environment(
    loader=FileSystemLoader(str(BASE_DIR / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
    cache_size=0
)
# Asegurar filtro tojson
jinja_env.filters["fuente"] = nombre_fuente
_MESES = ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")

def _fecha_dia(d):
    return f"{d.day:02d}/{_MESES[d.month - 1]}"

def _rango_semana(lunes, domingo):
    return f"{_fecha_dia(lunes)} – {domingo.day:02d}/{_MESES[domingo.month - 1]}/{domingo.year}"

jinja_env.filters["fecha_dia"] = _fecha_dia
jinja_env.filters["rango_semana"] = _rango_semana
templates = Jinja2Templates(env=jinja_env)


@app.on_event("startup")
def on_startup():
    # Crear tablas nuevas si no existen
    Base.metadata.create_all(bind=engine)
    # Migración simple SQLite: columna cargado_por en cargas
    try:
        from sqlalchemy import text as sql_text
        with engine.connect() as conn:
            cols = [r[1] for r in conn.execute(sql_text("PRAGMA table_info(cargas)")).fetchall()]
            if "cargado_por" not in cols:
                conn.execute(sql_text("ALTER TABLE cargas ADD COLUMN cargado_por VARCHAR(100)"))
                conn.commit()
                logger.info("Columna cargas.cargado_por agregada")
            pcols = [r[1] for r in conn.execute(sql_text("PRAGMA table_info(personas)")).fetchall()]
            if "motivo" not in pcols:
                conn.execute(sql_text("ALTER TABLE personas ADD COLUMN motivo VARCHAR(100)"))
                conn.commit()
                logger.info("Columna personas.motivo agregada")
            if "motivo_detalle" not in pcols:
                conn.execute(sql_text("ALTER TABLE personas ADD COLUMN motivo_detalle VARCHAR(255)"))
                conn.commit()
                logger.info("Columna personas.motivo_detalle agregada")
            ucols = [r[1] for r in conn.execute(sql_text("PRAGMA table_info(usuarios)")).fetchall()]
            if ucols:
                if "delegacion" not in ucols:
                    conn.execute(sql_text("ALTER TABLE usuarios ADD COLUMN delegacion VARCHAR(40) DEFAULT 'comunicaciones'"))
                    conn.commit()
                    logger.info("Columna usuarios.delegacion agregada")
                if "activo" not in ucols:
                    conn.execute(sql_text("ALTER TABLE usuarios ADD COLUMN activo BOOLEAN DEFAULT 1"))
                    conn.commit()
                    logger.info("Columna usuarios.activo agregada")
            tcols = [r[1] for r in conn.execute(sql_text("PRAGMA table_info(turnos)")).fetchall()]
            if tcols and "rol" not in tcols:
                conn.execute(sql_text("ALTER TABLE turnos ADD COLUMN rol VARCHAR(20)"))
                conn.commit()
                logger.info("Columna turnos.rol agregada")
            ccols = [r[1] for r in conn.execute(sql_text("PRAGMA table_info(claves_enlace)")).fetchall()]
            if ccols and "plantilla" not in ccols:
                conn.execute(sql_text("ALTER TABLE claves_enlace ADD COLUMN plantilla VARCHAR(2000)"))
                conn.commit()
                logger.info("Columna claves_enlace.plantilla agregada")
            cfgcols = [r[1] for r in conn.execute(sql_text("PRAGMA table_info(config_punto)")).fetchall()]
            if cfgcols and "convivencia_fimlm" not in cfgcols:
                conn.execute(sql_text("ALTER TABLE config_punto ADD COLUMN convivencia_fimlm BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Columna config_punto.convivencia_fimlm agregada")
            for col in ("labor_comunicaciones", "labor_politica", "labor_juventudes", "labor_electoral", "labor_fimlm"):
                if cfgcols and col not in cfgcols:
                    conn.execute(sql_text(f"ALTER TABLE config_punto ADD COLUMN {col} VARCHAR(60) DEFAULT ''"))
                    conn.commit()
            ecols = [r[1] for r in conn.execute(sql_text("PRAGMA table_info(equipo_sonido)")).fetchall()]
            if ecols and "es_coordinador" not in ecols:
                conn.execute(sql_text("ALTER TABLE equipo_sonido ADD COLUMN es_coordinador BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Columna equipo_sonido.es_coordinador agregada")
    except Exception as e:
        logger.info(f"Migración: {e}")

    db = next(get_db())
    create_default_user(db)
    obtener_config(db)
    from .postulacion import sembrar_equipo
    sembrar_equipo(db)
    db.close()



from .routes.auth import router as auth_router
from .routes.censo import router as censo_router
from .routes.programacion import router as programacion_router
from .routes.sonido import router as sonido_router
from .routes.usuarios import router as usuarios_router

app.include_router(auth_router)
app.include_router(censo_router)
app.include_router(programacion_router)
app.include_router(sonido_router)
app.include_router(usuarios_router)
