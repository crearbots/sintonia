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

def _exige_com(request: Request):
    user = require_login(request)
    if not user:
        return None, RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if not es_comunicaciones(request):
        return None, RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return user, None


@router.get("/usuarios", response_class=HTMLResponse)
async def usuarios_lista(request: Request, db: Session = Depends(get_db), ok: str = "", error: str = ""):
    user, redir = _exige_com(request)
    if redir:
        return redir
    cuentas = db.query(Usuario).order_by(Usuario.usuario.asc()).all()
    return templates.TemplateResponse(
        request,
        "usuarios.html",
        {
            "user": user,
            "cuentas": cuentas,
            "delegaciones": DELEGACIONES,
            "ok": ok,
            "error": error,
        },
    )


@router.post("/usuarios", response_class=HTMLResponse)
async def usuarios_crear(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
    delegacion: str = Form(...),
):
    user, redir = _exige_com(request)
    if redir:
        return redir
    username = (username or "").strip().lower()
    delegacion = (delegacion or "").strip().lower()
    if not username or not password:
        return RedirectResponse(url="/usuarios?error=Usuario+y+contraseña+son+obligatorios", status_code=status.HTTP_303_SEE_OTHER)
    if delegacion not in DELEGACIONES:
        return RedirectResponse(url="/usuarios?error=Delegación+no+válida", status_code=status.HTTP_303_SEE_OTHER)
    if delegacion == "comunicaciones":
        return RedirectResponse(url="/usuarios?error=Ya+existe+la+cuenta+de+Comunicaciones", status_code=status.HTTP_303_SEE_OTHER)
    if db.query(Usuario).filter(Usuario.usuario == username).first():
        return RedirectResponse(url="/usuarios?error=Ese+usuario+ya+existe", status_code=status.HTTP_303_SEE_OTHER)
    db.add(Usuario(
        usuario=username,
        password_hash=hash_password(password),
        delegacion=delegacion,
        activo=True,
    ))
    db.commit()
    return RedirectResponse(url="/usuarios?ok=Usuario+creado", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/usuarios/{usuario_id}/eliminar")
async def usuarios_eliminar(request: Request, usuario_id: int, db: Session = Depends(get_db)):
    user, redir = _exige_com(request)
    if redir:
        return redir
    cuenta = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not cuenta:
        return RedirectResponse(url="/usuarios?error=No+existe", status_code=status.HTTP_303_SEE_OTHER)
    if cuenta.usuario == user:
        return RedirectResponse(url="/usuarios?error=No+puedes+eliminar+la+cuenta+con+la+que+entraste", status_code=status.HTTP_303_SEE_OTHER)
    if (cuenta.delegacion or "") == "comunicaciones":
        otras = db.query(Usuario).filter(
            Usuario.delegacion == "comunicaciones", Usuario.id != cuenta.id
        ).count()
        if otras < 1:
            return RedirectResponse(url="/usuarios?error=Debe+quedar+al+menos+una+cuenta+de+Comunicaciones", status_code=status.HTTP_303_SEE_OTHER)
    db.delete(cuenta)
    db.commit()
    return RedirectResponse(url="/usuarios?ok=Usuario+eliminado", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/usuarios/{usuario_id}/desactivar")
async def usuarios_desactivar(request: Request, usuario_id: int, db: Session = Depends(get_db)):
    user, redir = _exige_com(request)
    if redir:
        return redir
    cuenta = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not cuenta:
        return RedirectResponse(url="/usuarios?error=No+existe", status_code=status.HTTP_303_SEE_OTHER)
    if cuenta.usuario == user or (cuenta.delegacion or "") == "comunicaciones":
        return RedirectResponse(url="/usuarios?error=La+cuenta+de+Comunicaciones+no+se+desactiva", status_code=status.HTTP_303_SEE_OTHER)
    cuenta.activo = False
    db.commit()
    return RedirectResponse(url="/usuarios?ok=Usuario+desactivado", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/usuarios/{usuario_id}/activar")
async def usuarios_activar(request: Request, usuario_id: int, db: Session = Depends(get_db)):
    user, redir = _exige_com(request)
    if redir:
        return redir
    cuenta = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if cuenta:
        cuenta.activo = True
        db.commit()
    return RedirectResponse(url="/usuarios?ok=Usuario+activado", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/usuarios/{usuario_id}/clave")
async def usuarios_clave(
    request: Request,
    usuario_id: int,
    db: Session = Depends(get_db),
    password: str = Form(...),
):
    user, redir = _exige_com(request)
    if redir:
        return redir
    if not (password or "").strip():
        return RedirectResponse(url="/usuarios?error=La+clave+no+puede+ir+vacía", status_code=status.HTTP_303_SEE_OTHER)
    cuenta = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not cuenta:
        return RedirectResponse(url="/usuarios?error=No+existe", status_code=status.HTTP_303_SEE_OTHER)
    cuenta.password_hash = hash_password(password.strip())
    db.commit()
    return RedirectResponse(url="/usuarios?ok=Clave+restablecida.+Compártela+por+WhatsApp+al+grupo", status_code=status.HTTP_303_SEE_OTHER)

