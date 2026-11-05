from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .processing import nombre_fuente

BASE_DIR = Path(__file__).resolve().parent.parent

jinja_env = Environment(
    loader=FileSystemLoader(str(BASE_DIR / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
    cache_size=0,
)
jinja_env.filters["fuente"] = nombre_fuente

_MESES = ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")


def _fecha_dia(d):
    return f"{d.day:02d}/{_MESES[d.month - 1]}"


def _rango_semana(lunes, domingo):
    return f"{_fecha_dia(lunes)} – {domingo.day:02d}/{_MESES[domingo.month - 1]}/{domingo.year}"


jinja_env.filters["fecha_dia"] = _fecha_dia
jinja_env.filters["rango_semana"] = _rango_semana
templates = Jinja2Templates(env=jinja_env)


def require_login(request):
    user = request.session.get("user")
    if not user:
        return None
    return user


def require_com_user(request):
    from fastapi.responses import RedirectResponse
    from starlette import status
    user = require_login(request)
    if not user:
        return None, RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    from .auth import es_comunicaciones
    if not es_comunicaciones(request):
        return None, RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return user, None


def _delegacion(request) -> str:
    return request.session.get("delegacion") or "comunicaciones"
