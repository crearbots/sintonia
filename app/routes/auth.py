from fastapi import APIRouter, Request, Depends, Form, status, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import templates, require_login, require_com_user, _delegacion
from ..auth import authenticate_user, es_comunicaciones, DELEGACIONES, hash_password
from ..models import (
    Usuario, Persona, Carga, DatoNacional, Turno, Alerta, ConfigPunto,
    DomingoPunto, ClaveEnlace, Postulacion, AusenciaSemana,
)
from ..punto import (
    semana_vigente, dias_semana, horarios_del_dia, etiqueta_horario,
    NOMBRES_DIA, obtener_config, dia_permitido, punto_apagado_para,
    generar_alertas_festivo, generar_alertas_domingo, ranking_anio,
)
from ..festivos import es_festivo
from ..processing import procesar_excel, FUENTES_VALIDAS, FUENTE_PRESENCIAL_ANTERIOR, nombre_fuente, EQUIPOS_CARGA, MOTIVOS_NO_INSTALADA

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if request.session.get("user"):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("user"):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    cuenta = authenticate_user(db, username, password)
    if cuenta:
        request.session["user"] = cuenta.usuario
        request.session["delegacion"] = cuenta.delegacion or "comunicaciones"
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "login.html", {"error": "Usuario o contraseña incorrectos"}
    )


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
