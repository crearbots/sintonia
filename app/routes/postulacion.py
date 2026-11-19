"""Enlaces públicos de postulación por labor (Sonido y Baños)."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import templates
from ..services.enlace_postulacion import (
    construir_ctx_postular,
    guardar_postulacion_enlace,
    meta_plantilla_postular,
    revisar_celular_enlace,
    template_enlace,
)

router = APIRouter()


@router.get("/postular-banos/{token}", response_class=HTMLResponse)
async def postular_banos_ver(
    request: Request, token: str, db: Session = Depends(get_db),
    error: str = "", ok: str = "", cambiar: str = "", tarde: str = "",
):
    ctx = construir_ctx_postular(
        db, request, "banos", token, error=error, ok=ok, cambiar=cambiar, tarde=tarde,
    )
    if ctx is None:
        return templates.TemplateResponse(
            request, template_enlace("banos"), {"invalido": True, **meta_plantilla_postular("banos")},
        )
    return templates.TemplateResponse(request, template_enlace("banos"), ctx)


@router.post("/postular-banos/{token}/revisar")
async def postular_banos_revisar(
    request: Request, token: str, db: Session = Depends(get_db),
    celular: str = Form(""), celular2: str = Form(""),
):
    return revisar_celular_enlace(db, request, "banos", token, celular, celular2)


@router.post("/postular-banos/{token}")
async def postular_banos_guardar(
    request: Request, token: str, db: Session = Depends(get_db),
    clave: str = Form(""), celular: str = Form(""), celular2: str = Form(""),
    nombre: str = Form(""), no_puedo: str = Form(""),
):
    form = await request.form()
    url = guardar_postulacion_enlace(
        request, db, "banos", token, form,
        clave=clave, celular=celular, celular2=celular2, nombre=nombre, no_puedo=no_puedo,
    )
    return RedirectResponse(url=url, status_code=303)