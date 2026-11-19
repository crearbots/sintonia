from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import _delegacion, require_login, templates
from ..models import (
    Alerta,
    AusenciaSemana,
    Persona,
    Postulacion,
    Turno,
)
from ..punto import (
    NOMBRES_DIA,
    dias_semana,
    etiqueta_horario,
    horarios_del_dia,
    postulacion_tarde,
    semana_para_labor,
    semana_vigente,
)
from ..services.enlace_postulacion import (
    construir_ctx_postular,
    etiqueta_rol,
    guardar_postulacion_enlace,
    lineas_detalle_cambio,
    meta_plantilla_postular,
    revisar_celular_enlace,
    template_enlace,
    texto_vigente,
)

router = APIRouter()

def _nombre_equipo(delegacion: str) -> str:
    return {
        "comunicaciones": "Comunicaciones",
        "politica": "Política",
        "juventudes": "Juventudes",
        "electoral": "Electoral",
        "fimlm": "FIMLM",
        "sonido": "Sonido",
        "banos": "Baños",
    }.get(delegacion or "", delegacion or "otro equipo")


def _sonido_ok(request: Request):
    user = require_login(request)
    if not user:
        return None, RedirectResponse(url="/login", status_code=303)
    if _delegacion(request) not in ("sonido", "banos"):
        return None, RedirectResponse(url="/programacion", status_code=303)
    return user, None


@router.get("/programacion/sonido/enlace", response_class=HTMLResponse)
async def sonido_enlace(request: Request):
    return RedirectResponse(url="/postulaciones?modo=link", status_code=303)


@router.get("/postulaciones", response_class=HTMLResponse)
async def postulaciones_ver(request: Request, db: Session = Depends(get_db), ok: str = "", error: str = "", modo: str = "confirmar", q: str = "", filtro: str = "todos", alta: str = "", ver: int = 0):
    from ..models import EquipoSonido
    from ..postulacion import (
        clave_vigente,
        generar_alertas_silencio,
        semanas_en_silencio,
        sembrar_equipo,
        sembrar_equipo_banos,
        texto_plantilla,
        url_enlace_postulacion,
    )
    user, redir = _sonido_ok(request)
    if redir:
        return redir
    delg = _delegacion(request)
    labor = "banos" if delg == "banos" else "sonido"
    if labor == "banos":
        from ..models import EquipoBanos
        sembrar_equipo_banos(db)
    else:
        sembrar_equipo(db)
    generar_alertas_silencio(db)
    lunes = semana_para_labor(labor)
    clave = clave_vigente(db, labor)
    enlace = url_enlace_postulacion(str(request.base_url), labor, clave.token)
    ocupado = {
        (t.fecha, t.horario, t.rol or labor)
        for t in db.query(Turno).filter(Turno.delegacion == labor, Turno.semana_lunes == lunes).all()
    }
    posts = db.query(Postulacion).filter(Postulacion.labor == labor, Postulacion.semana_lunes == lunes).all()
    ids = {p.persona_id for p in posts}
    ausencias = db.query(AusenciaSemana).filter(AusenciaSemana.labor == labor, AusenciaSemana.semana_lunes == lunes).all()
    ids |= {a.persona_id for a in ausencias}
    personas = {p.id: p for p in db.query(Persona).filter(Persona.id.in_(ids)).all()} if ids else {}
    por_cupo = {}
    for p in posts:
        por_cupo.setdefault((p.fecha, p.horario, p.rol), []).append(personas.get(p.persona_id))
    postulados = []
    conteo = {}
    for turno in db.query(Turno).filter(Turno.semana_lunes == lunes).all():
        conteo[turno.persona_id] = conteo.get(turno.persona_id, 0) + 1
    for (fecha, horario, rol), gente in por_cupo.items():
        nombres = [g for g in gente if g]
        items = [{"id": g.id, "nombre": g.nombre, "turnos": conteo.get(g.id, 0), "sugerido": False, "mostrar_carga": False} for g in nombres]
        if len(items) > 1:
            menor = min(x["turnos"] for x in items)
            hay_unico = sum(1 for x in items if x["turnos"] == menor) == 1
            for x in items:
                x["mostrar_carga"] = True
                x["sugerido"] = hay_unico and x["turnos"] == menor
            items.sort(key=lambda x: (x["turnos"], (x["nombre"] or "").lower()))
        else:
            items.sort(key=lambda x: (x["nombre"] or "").lower())
        postulados.append({
            "fecha": fecha, "horario": horario, "rol": rol,
            "etiqueta": f"{NOMBRES_DIA[fecha.weekday()]} {fecha.day:02d} · {etiqueta_horario(horario)} · {etiqueta_rol(rol)}",
            "gente": items,
            "lleno": (fecha, horario, rol) in ocupado,
        })
    postulados.sort(key=lambda x: (x["fecha"], x["horario"], x["rol"]))
    no_pueden = [personas.get(a.persona_id) for a in ausencias]
    no_pueden = [p for p in no_pueden if p]
    respondieron = {p.persona_id for p in posts} | {a.persona_id for a in ausencias}
    if labor == "banos":
        from ..models import EquipoBanos
        equipo = db.query(EquipoBanos).filter(EquipoBanos.activo == True).all()
    else:
        equipo = db.query(EquipoSonido).filter(EquipoSonido.activo == True).all()
    ids_equipo = {r.persona_id for r in equipo}
    gente_equipo = {p.id: p for p in db.query(Persona).filter(Persona.id.in_(ids_equipo)).all()} if ids_equipo else {}
    sin_indicar = []
    for row in equipo:
        if row.es_coordinador or row.persona_id in respondieron:
            continue
        per = gente_equipo.get(row.persona_id)
        if not per:
            continue
        cerradas = semanas_en_silencio(db, row.persona_id, row.desde, lunes)
        sin_indicar.append({
            "id": per.id, "nombre": per.nombre,
            "semanas": cerradas,
        })
    sin_indicar.sort(key=lambda x: (-x["semanas"], (x["nombre"] or "").lower()))
    no_pueden_ids = {a.persona_id for a in ausencias}
    postularon_ids = {p.persona_id for p in posts}
    miembros = []
    for row in equipo:
        per = gente_equipo.get(row.persona_id)
        if not per:
            continue
        if row.persona_id in postularon_ids:
            estado = "Postuló"
            semanas = 0
        elif row.persona_id in no_pueden_ids:
            estado = "No puede"
            semanas = 0
        elif row.es_coordinador:
            estado = "Coordinador"
            semanas = 0
        else:
            estado = "No indicó"
            semanas = semanas_en_silencio(db, row.persona_id, row.desde, lunes)
        miembros.append({"id": per.id, "nombre": per.nombre, "estado": estado, "semanas": semanas, "coordinador": bool(row.es_coordinador)})
    miembros.sort(key=lambda p: (p["estado"] != "No indicó", (p["nombre"] or "").lower()))
    indicaron = sum(1 for p in miembros if p["estado"] in ("Postuló", "No puede"))
    filtro = filtro if filtro in ("todos", "no", "si") else "todos"
    if filtro == "no":
        lista = [p for p in miembros if p["estado"] == "No indicó"]
    elif filtro == "si":
        lista = [p for p in miembros if p["estado"] in ("Postuló", "No puede")]
    else:
        lista = list(miembros)
    q = (q or "").strip()
    ver_id = ver or None
    if q:
        import re

        from ..processing import normalizar_celular
        digitos = re.sub(r"\D", "", q)
        if len(digitos) >= 12 and digitos.startswith("57"):
            digitos = digitos[2:]
        exacto = normalizar_celular(q)
        texto = q.lower()
        def coincide(p):
            per = gente_equipo.get(p["id"])
            cel = (per.celular or "") if per else ""
            if exacto and cel == exacto:
                return True
            if digitos and digitos in cel:
                return True
            return texto in (p["nombre"] or "").lower()
        lista = [p for p in lista if coincide(p)]
        if len(lista) == 1:
            ver_id = lista[0]["id"]
    alta = (alta or "").strip()
    alta_estado = ""
    alta_persona = None
    alta_cel = ""
    if alta:
        from ..processing import normalizar_celular
        alta_cel = normalizar_celular(alta) or ""
        if not alta_cel:
            alta_estado = "invalido"
        else:
            hallada = db.query(Persona).filter(Persona.celular == alta_cel).first()
            if hallada and hallada.id in ids_equipo:
                alta_estado = "ya"
                alta_persona = {"id": hallada.id, "nombre": hallada.nombre}
                ver_id = hallada.id
            elif hallada:
                alta_estado = "candidato"
                alta_persona = {"id": hallada.id, "nombre": hallada.nombre}
            else:
                alta_estado = "nueva"
    return templates.TemplateResponse(request, "postulaciones.html", {
        "user": user, "ok": ok, "error": error, "enlace": enlace, "clave_mes": clave.clave,
        "plantilla": texto_plantilla(clave, enlace), "postulados": postulados, "no_pueden": no_pueden,
        "lunes": lunes, "domingo": lunes + __import__("datetime").timedelta(days=6),
        "modo": modo if modo in ("link", "equipo", "confirmar") else "confirmar",
        "sin_indicar": sin_indicar, "miembros": miembros, "lista": lista, "indicaron": indicaron,
        "q": q, "filtro": filtro, "ver": ver_id, "alta": alta, "alta_estado": alta_estado,
        "alta_persona": alta_persona, "alta_cel": alta_cel,
        "accion_confirmar": "/programacion/banos/confirmar" if labor == "banos" else "/programacion/sonido/asignar",
        "nombre_equipo": "Baños" if labor == "banos" else "Sonido",
    })


@router.post("/programacion/sonido/clave")
async def sonido_regenerar_clave(request: Request, db: Session = Depends(get_db)):
    user, redir = _sonido_ok(request)
    if redir:
        return redir
    from ..postulacion import regenerar_clave
    delg = _delegacion(request)
    labor = "banos" if delg == "banos" else "sonido"
    row = regenerar_clave(db, labor)
    return RedirectResponse(url=f"/postulaciones?modo=link&ok=Clave+nueva:+{row.clave}", status_code=303)


@router.post("/programacion/sonido/plantilla")
async def sonido_guardar_plantilla(
    request: Request, plantilla: str = Form(""), db: Session = Depends(get_db),
):
    user, redir = _sonido_ok(request)
    if redir:
        return redir
    from ..postulacion import clave_vigente, guardar_plantilla, url_enlace_postulacion
    delg = _delegacion(request)
    labor = "banos" if delg == "banos" else "sonido"
    clave = clave_vigente(db, labor)
    enlace = url_enlace_postulacion(str(request.base_url), labor, clave.token)
    guardar_plantilla(db, plantilla, enlace, labor)
    return RedirectResponse(url="/postulaciones?modo=link&ok=Mensaje+guardado", status_code=303)


def _vistas_silencio(db, alertas) -> None:
    from ..models import Persona
    for alerta in alertas:
        alerta.silencio = None
        if not (alerta.tipo or "").startswith("silencio|"):
            continue
        partes = alerta.tipo.split("|")
        if len(partes) < 3:
            continue
        try:
            umbral = int(partes[1])
            pid = int(partes[2])
        except ValueError:
            continue
        per = db.query(Persona).filter(Persona.id == pid).first()
        alerta.silencio = {
            "persona_id": pid,
            "nombre": per.nombre if per else "Colaborador",
            "celular": per.celular if per else "",
            "umbral": umbral,
        }


@router.post("/programacion/sonido/equipo")
async def sonido_equipo(
    request: Request, db: Session = Depends(get_db),
    persona_id: int = Form(0), accion: str = Form(...),
    nombre: str = Form(""), celular: str = Form(""), ajax: str = Form(""),
):
    from fastapi.responses import JSONResponse

    from ..models import EquipoBanos, EquipoSonido
    from ..postulacion import asegurar_equipo, asegurar_equipo_banos
    from ..processing import normalizar_celular, normalizar_nombre
    user, redir = _sonido_ok(request)
    if redir:
        return JSONResponse({"ok": False, "error": "Sesión vencida"}) if ajax == "1" else redir
    delg = _delegacion(request)
    labor = "banos" if delg == "banos" else "sonido"
    Modelo = EquipoBanos if labor == "banos" else EquipoSonido
    asegurar = asegurar_equipo_banos if labor == "banos" else asegurar_equipo
    nombre = "Baños" if labor == "banos" else "Sonido"
    if accion == "crear":
        cel = normalizar_celular(celular)
        nom = normalizar_nombre(nombre)
        if not cel:
            return RedirectResponse(url="/postulaciones?modo=equipo&error=El+celular+debe+tener+10+dígitos", status_code=303)
        if not nom:
            return RedirectResponse(url=f"/postulaciones?modo=equipo&alta={cel}&error=Escribe+el+nombre", status_code=303)
        per = db.query(Persona).filter(Persona.celular == cel).first()
        if not per:
            per = Persona(nombre=nom, celular=cel, estado="No instalada", pendiente_revision=False, fuente_ultima=f"Alta {nombre}")
            db.add(per)
            db.commit()
            db.refresh(per)
            db.add(Alerta(
                para_delegacion="comunicaciones", tipo="alta_nueva",
                texto=f"Persona nueva desde el equipo de {nombre}: {nom} ({cel}). Revisar si ya tiene la App.",
                leida=False,
            ))
        asegurar(db, per.id)
        db.commit()
        return RedirectResponse(url=f"/postulaciones?modo=equipo&ok=Agregado+al+equipo&ver={per.id}", status_code=303)
    if accion == "agregar":
        if not db.query(Persona).filter(Persona.id == persona_id).first():
            return RedirectResponse(url="/postulaciones?modo=equipo&error=No+existe", status_code=303)
        asegurar(db, persona_id)
        db.commit()
        return RedirectResponse(url=f"/postulaciones?modo=equipo&ok=Agregado+al+equipo&ver={persona_id}", status_code=303)
    row = db.query(Modelo).filter(Modelo.persona_id == persona_id, Modelo.activo == True).first()
    if not row:
        return RedirectResponse(url="/postulaciones?modo=equipo&error=No+está+en+el+equipo", status_code=303)
    if accion == "coordinador":
        row.es_coordinador = not bool(row.es_coordinador)
        db.commit()
        msg = "Ahora+es+coordinador" if row.es_coordinador else "Ya+no+es+coordinador"
        return RedirectResponse(url=f"/postulaciones?modo=equipo&ok={msg}&ver={persona_id}", status_code=303)
    row.activo = False
    db.commit()
    if ajax == "1":
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/postulaciones?modo=equipo&ok=Salio+del+equipo", status_code=303)


@router.post("/programacion/sonido/silencio")
async def sonido_silencio(
    request: Request, db: Session = Depends(get_db),
    alerta_id: int = Form(...), accion: str = Form(...),
):
    from ..models import EquipoSonido
    user, redir = _sonido_ok(request)
    if redir:
        return redir
    alerta = db.query(Alerta).filter(
        Alerta.id == alerta_id, Alerta.para_delegacion == "sonido", Alerta.tipo.like("silencio|%"),
    ).first()
    if not alerta:
        return RedirectResponse(url="/dashboard", status_code=303)
    if accion == "sacar":
        partes = alerta.tipo.split("|")
        try:
            pid = int(partes[2])
        except (IndexError, ValueError):
            pid = None
        if pid:
            row = db.query(EquipoSonido).filter(EquipoSonido.persona_id == pid, EquipoSonido.activo == True).first()
            if row:
                row.activo = False
    alerta.leida = True
    db.commit()
    return RedirectResponse(url="/dashboard", status_code=303)


def _vistas_cambio(db, alertas) -> None:
    from ..models import CambioPostulacion, Persona
    for alerta in alertas:
        alerta.vista = None
        if not (alerta.tipo or "").startswith("cambio-"):
            continue
        try:
            cid = int(alerta.tipo.split("-", 1)[1])
        except ValueError:
            continue
        cambio = db.query(CambioPostulacion).filter(CambioPostulacion.id == cid, CambioPostulacion.estado == "pendiente").first()
        if not cambio:
            continue
        labor = cambio.labor or "sonido"
        per = db.query(Persona).filter(Persona.id == cambio.persona_id).first()
        antes = texto_vigente(db, cambio.persona_id, cambio.semana_lunes, labor)
        ahora = (
            ["Esta semana no puede apoyar."]
            if cambio.no_puede
            else lineas_detalle_cambio(cambio.detalle, labor)
        )
        alerta.vista = {
            "id": cambio.id,
            "nombre": per.nombre if per else "Una persona",
            "antes": [parte.strip() for parte in antes.split(",") if parte.strip()],
            "ahora": ahora,
            "ahora_no": cambio.no_puede,
            "labor": "Baños" if labor == "banos" else "Sonido",
            "accion": "/programacion/banos/cambio" if labor == "banos" else "/programacion/sonido/cambio",
        }


@router.get("/postular/{token}", response_class=HTMLResponse)
async def postular_ver(
    request: Request, token: str, db: Session = Depends(get_db),
    error: str = "", ok: str = "", cambiar: str = "", tarde: str = "",
):
    ctx = construir_ctx_postular(
        db, request, "sonido", token, error=error, ok=ok, cambiar=cambiar, tarde=tarde,
    )
    if ctx is None:
        return templates.TemplateResponse(
            request, template_enlace("sonido"), {"invalido": True, **meta_plantilla_postular("sonido")},
        )
    return templates.TemplateResponse(request, template_enlace("sonido"), ctx)


@router.post("/postular/{token}/revisar")
async def postular_revisar(
    request: Request, token: str, db: Session = Depends(get_db),
    celular: str = Form(""), celular2: str = Form(""),
):
    return revisar_celular_enlace(db, request, "sonido", token, celular, celular2)


@router.post("/postular/{token}")
async def postular_guardar(
    request: Request, token: str, db: Session = Depends(get_db),
    clave: str = Form(""), celular: str = Form(""), celular2: str = Form(""),
    nombre: str = Form(""), no_puedo: str = Form(""),
):
    form = await request.form()
    url = guardar_postulacion_enlace(
        request, db, "sonido", token, form,
        clave=clave, celular=celular, celular2=celular2, nombre=nombre, no_puedo=no_puedo,
    )
    return RedirectResponse(url=url, status_code=303)


@router.post("/programacion/sonido/cambio")
async def sonido_cambio(
    request: Request, db: Session = Depends(get_db),
    cambio_id: int = Form(...), decision: str = Form(...),
):
    from datetime import datetime as dt

    from ..models import CambioPostulacion
    from ..postulacion import aplicar_semana
    user, redir = _sonido_ok(request)
    if redir:
        return redir
    cambio = db.query(CambioPostulacion).filter(CambioPostulacion.id == cambio_id, CambioPostulacion.estado == "pendiente").first()
    if not cambio or (cambio.labor or "sonido") != "sonido":
        return RedirectResponse(url="/dashboard", status_code=303)
    if _delegacion(request) != "sonido":
        return RedirectResponse(url="/dashboard", status_code=303)
    if decision == "aceptar":
        huecos = []
        if not cambio.no_puede:
            for parte in (cambio.detalle or "").split(";"):
                if not parte:
                    continue
                f, h, rol = parte.split("|")
                huecos.append((dt.strptime(f, "%Y-%m-%d").date(), h, rol))
        aplicar_semana(db, cambio.persona_id, cambio.semana_lunes, huecos, cambio.no_puede, "sonido")
        cambio.estado = "aceptado"
    else:
        cambio.estado = "rechazado"
    db.query(Alerta).filter(Alerta.tipo == f"cambio-{cambio.id}", Alerta.para_delegacion == "sonido").update({"leida": True})
    db.commit()
    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/programacion/banos/cambio")
async def banos_cambio(
    request: Request, db: Session = Depends(get_db),
    cambio_id: int = Form(...), decision: str = Form(...),
):
    from datetime import datetime as dt

    from ..models import CambioPostulacion
    from ..postulacion import aplicar_semana
    user, redir = _sonido_ok(request)
    if redir:
        return redir
    if _delegacion(request) != "banos":
        return RedirectResponse(url="/dashboard", status_code=303)
    cambio = db.query(CambioPostulacion).filter(
        CambioPostulacion.id == cambio_id,
        CambioPostulacion.estado == "pendiente",
        CambioPostulacion.labor == "banos",
    ).first()
    if not cambio:
        return RedirectResponse(url="/dashboard", status_code=303)
    if decision == "aceptar":
        huecos = []
        if not cambio.no_puede:
            for parte in (cambio.detalle or "").split(";"):
                if not parte:
                    continue
                f, h, rol = parte.split("|")
                huecos.append((dt.strptime(f, "%Y-%m-%d").date(), h, rol))
        aplicar_semana(db, cambio.persona_id, cambio.semana_lunes, huecos, cambio.no_puede, "banos")
        cambio.estado = "aceptado"
    else:
        cambio.estado = "rechazado"
    db.query(Alerta).filter(Alerta.tipo == f"cambio-{cambio.id}", Alerta.para_delegacion == "banos").update({"leida": True})
    db.commit()
    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/programacion/sonido/asignar")
async def sonido_asignar(
    request: Request, db: Session = Depends(get_db),
    persona_id: int = Form(...), fecha: str = Form(...), horario: str = Form(...), rol: str = Form(...),
    modo: str = Form(""), origen: str = Form(""), ajax: str = Form(""),
):
    from datetime import datetime as dt
    from datetime import timedelta as td
    from urllib.parse import quote

    from fastapi.responses import JSONResponse
    user, redir = _sonido_ok(request)
    if redir:
        return JSONResponse({"ok": False, "error": "Sesión vencida"}) if ajax == "1" else redir
    volver = "/postulaciones?modo=confirmar" if origen == "postulaciones" else "/programacion?modo=editar"

    def fallo(msg: str):
        if ajax == "1":
            return JSONResponse({"ok": False, "error": msg})
        return RedirectResponse(url=volver + "?error=" + quote(msg), status_code=303)

    if _delegacion(request) != "sonido":
        return JSONResponse({"ok": False, "error": "Solo Sonido puede confirmar esto"}) if ajax == "1" else RedirectResponse(url="/programacion", status_code=303)
    if (rol or "").strip().lower() == "banos":
        return JSONResponse({"ok": False, "error": "Ese horario es de Baños"}) if ajax == "1" else RedirectResponse(url="/programacion", status_code=303)
    if modo != "editar":
        return fallo("La foto no se edita. Pasa a Armar semana")
    rol = (rol or "").strip().lower()
    try:
        f = dt.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        return fallo("Fecha inválida")
    lunes = semana_vigente()
    if not (lunes <= f <= lunes + td(days=6)):
        return fallo("Solo se edita la semana vigente")
    if horario not in horarios_del_dia(f):
        return fallo("Horario no válido ese día")
    if rol not in ("sonido", "camara") or (rol == "camara" and f.weekday() not in (2, 6)):
        return fallo("Ese rol no aplica en ese día")
    persona = db.query(Persona).filter(Persona.id == persona_id).first()
    if not persona:
        return fallo("Persona no encontrada")
    cupo = db.query(Turno).filter(
        Turno.delegacion == "sonido", Turno.fecha == f, Turno.horario == horario, Turno.rol == rol
    ).first()
    if cupo:
        return fallo("Ese cupo ya está lleno. Quítalo en Armar semana para cambiarlo")
    misma_hora = db.query(Turno).filter(
        Turno.persona_id == persona_id, Turno.fecha == f, Turno.horario == horario
    ).first()
    if misma_hora:
        equipo = _nombre_equipo(misma_hora.delegacion)
        return fallo(f"{persona.nombre} ya está a esa hora en {equipo}. No se le quita ese turno.")
    db.add(Turno(
        persona_id=persona_id, fecha=f, horario=horario, delegacion="sonido",
        semana_lunes=lunes, rol=rol,
    ))
    db.commit()
    if ajax == "1":
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/postulaciones?modo=confirmar&ok=Confirmado" if origen == "postulaciones" else f"/programacion?modo=editar&ok=Listo&hl={fecha}-{horario}-{rol}", status_code=303)


@router.post("/programacion/sonido/quitar")
async def sonido_quitar(
    request: Request, db: Session = Depends(get_db),
    turno_id: int = Form(...), modo: str = Form(""),
):
    user, redir = _sonido_ok(request)
    if redir:
        return redir
    if modo != "editar":
        return RedirectResponse(url="/programacion?modo=foto&error=La+foto+no+se+edita.+Pasa+a+Armar+semana", status_code=303)
    turno = db.query(Turno).filter(Turno.id == turno_id, Turno.delegacion == "sonido").first()
    if not turno:
        return RedirectResponse(url="/programacion?error=Turno+no+existe", status_code=303)
    hl = f"{turno.fecha.isoformat()}-{turno.horario}-{turno.rol or 'sonido'}"
    db.delete(turno)
    db.commit()
    return RedirectResponse(url=f"/programacion?modo=editar&ok=Quitado&hl={hl}", status_code=303)


@router.post("/programacion/sonido/alta")
async def sonido_alta(
    request: Request, db: Session = Depends(get_db),
    nombre: str = Form(...), celular: str = Form(...),
    fecha: str = Form(...), horario: str = Form(...), rol: str = Form(...), modo: str = Form(""),
):
    from ..processing import normalizar_celular, normalizar_nombre
    user, redir = _sonido_ok(request)
    if redir:
        return redir
    if modo != "editar":
        return RedirectResponse(url="/programacion?modo=foto&error=La+foto+no+se+edita.+Pasa+a+Armar+semana", status_code=303)
    cel = normalizar_celular(celular)
    nom = normalizar_nombre(nombre)
    if not cel or not nom:
        return RedirectResponse(
            url=f"/programacion?error=Nombre+y+celular+de+10+dígitos&fecha_sel={fecha}&horario_sel={horario}&rol={rol}",
            status_code=303,
        )
    per = db.query(Persona).filter(Persona.celular == cel).first()
    if not per:
        per = Persona(nombre=nom, celular=cel, estado="No instalada", pendiente_revision=False, fuente_ultima="Sonido")
        db.add(per)
        db.commit()
        db.refresh(per)
        db.add(Alerta(
            para_delegacion="comunicaciones", tipo="alta_nueva",
            texto=f"Persona nueva registrada desde Sonido: {nom} ({cel}). Revisar si ya tiene la App.",
            leida=False,
        ))
        db.commit()
    return await sonido_asignar(request, db, persona_id=per.id, fecha=fecha, horario=horario, rol=rol, modo="editar")

