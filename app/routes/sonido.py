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
    semana_vigente,
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
    lunes = semana_vigente()
    clave = clave_vigente(db, labor)
    base = str(request.base_url).rstrip("/")
    enlace = f"{base}/postular-banos/{clave.token}" if labor == "banos" else f"{base}/postular/{clave.token}"
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
            "etiqueta": f"{NOMBRES_DIA[fecha.weekday()]} {fecha.day:02d} · {etiqueta_horario(horario)} · {_etiqueta_rol(rol)}",
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
    from ..postulacion import clave_vigente, guardar_plantilla
    delg = _delegacion(request)
    labor = "banos" if delg == "banos" else "sonido"
    clave = clave_vigente(db, labor)
    base = str(request.base_url).rstrip("/")
    enlace = f"{base}/postular-banos/{clave.token}" if labor == "banos" else f"{base}/postular/{clave.token}"
    guardar_plantilla(db, plantilla, enlace, labor)
    return RedirectResponse(url="/postulaciones?modo=link&ok=Mensaje+guardado", status_code=303)


def _enlace_sonido(db, token: str):
    from ..postulacion import clave_vigente
    clave = clave_vigente(db, "sonido")
    if clave.token != token:
        return None
    return clave


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
        antes = _texto_vigente(db, cambio.persona_id, cambio.semana_lunes, labor)
        ahora = ["Esta semana no puede apoyar."] if cambio.no_puede else _lineas_detalle(cambio.detalle)
        alerta.vista = {
            "id": cambio.id,
            "nombre": per.nombre if per else "Una persona",
            "antes": [parte.strip() for parte in antes.split(",") if parte.strip()],
            "ahora": ahora,
            "ahora_no": cambio.no_puede,
            "labor": "Baños" if labor == "banos" else "Sonido",
            "accion": "/programacion/banos/cambio" if labor == "banos" else "/programacion/sonido/cambio",
        }


def _etiqueta_rol(rol: str) -> str:
    if rol == "camara":
        return "Cámara"
    if rol == "banos":
        return "Baños"
    return "Sonido"


def _linea_hueco(fecha, horario, rol):
    mes = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"][fecha.month - 1].capitalize()
    return f"{NOMBRES_DIA[fecha.weekday()]} {fecha.day:02d}/{mes} · {etiqueta_horario(horario)} · {_etiqueta_rol(rol)}"


def _lineas_detalle(detalle: str) -> list[str]:
    from datetime import datetime as dt
    lineas = []
    for parte in (detalle or "").split(";"):
        if not parte or parte == "no-puede":
            continue
        f, h, rol = parte.split("|")
        lineas.append(_linea_hueco(dt.strptime(f, "%Y-%m-%d").date(), h, rol))
    lineas.sort()
    return lineas


def _texto_huecos(huecos) -> str:
    if not huecos:
        return "sin horarios"
    return ", ".join(_linea_hueco(f, h, rol) for f, h, rol in huecos)


def _texto_vigente(db, persona_id: int, lunes, labor: str = "sonido") -> str:
    from ..postulacion import postulaciones_persona
    if db.query(AusenciaSemana).filter(
        AusenciaSemana.persona_id == persona_id,
        AusenciaSemana.labor == labor,
        AusenciaSemana.semana_lunes == lunes,
    ).first():
        return "no podía apoyar esta semana"
    filas = postulaciones_persona(db, persona_id, lunes, labor)
    if not filas:
        return "sin horarios"
    return ", ".join(_linea_hueco(p.fecha, p.horario, p.rol) for p in sorted(filas, key=lambda p: (p.fecha, p.horario, p.rol)))


def _postular_abierto(request: Request, token: str, clave) -> bool:
    from ..postulacion import enlace_abierto
    return enlace_abierto(request, token)


@router.get("/postular/{token}", response_class=HTMLResponse)
async def postular_ver(request: Request, token: str, db: Session = Depends(get_db), error: str = "", ok: str = "", cambiar: str = "", tarde: str = ""):
    clave = _enlace_sonido(db, token)
    if not clave:
        return templates.TemplateResponse(request, "postular.html", {"invalido": True})
    abierto = _postular_abierto(request, token, clave)
    ctx = {"invalido": False, "token": token, "abierto": abierto, "error": error, "ok": ok, "filas": [], "mios": set(), "no_puede": False, "resumen": False, "lineas": [], "cambio_antes": None, "cambio_ahora": [], "cambio_no_puede": False, "tarde": tarde == "1"}
    if abierto:
        from ..festivos import es_festivo
        from ..models import AusenciaSemana, Postulacion
        from ..postulacion import enlace_persona
        lunes = semana_vigente()
        dias = dias_semana(lunes)
        ocupados = {
            (p.fecha, p.horario, p.rol)
            for p in db.query(Postulacion).filter(
                Postulacion.labor == "sonido", Postulacion.semana_lunes == lunes,
            ).all()
        }
        pid = enlace_persona(request) if ok and not cambiar else None
        mios = set()
        lineas = []
        no_puede = False
        if pid:
            from ..postulacion import postulaciones_persona
            for p in postulaciones_persona(db, pid, lunes):
                mios.add(f"{p.fecha.isoformat()}|{p.horario}|{p.rol}")
                lineas.append(f"{NOMBRES_DIA[p.fecha.weekday()]} {p.fecha.day:02d}/{['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'][p.fecha.month-1].capitalize()} · {etiqueta_horario(p.horario)} · {'Cámara' if p.rol == 'camara' else 'Sonido'}")
            lineas.sort()
            no_puede = db.query(AusenciaSemana).filter(
                AusenciaSemana.persona_id == pid, AusenciaSemana.labor == "sonido", AusenciaSemana.semana_lunes == lunes,
            ).first() is not None
            from ..models import CambioPostulacion
            cambio = db.query(CambioPostulacion).filter(
                CambioPostulacion.persona_id == pid,
                CambioPostulacion.labor == "sonido",
                CambioPostulacion.semana_lunes == lunes,
                CambioPostulacion.estado == "pendiente",
            ).first()
            if cambio and ok and not cambiar:
                ctx["cambio_antes"] = list(lineas) if not no_puede else ["Esta semana no puedes apoyar."]
                ctx["cambio_no_puede"] = cambio.no_puede
                ctx["cambio_ahora"] = _lineas_detalle(cambio.detalle) if not cambio.no_puede else []
        filas = []
        for d in dias:
            roles = ["sonido", "camara"] if d.weekday() in (2, 6) else ["sonido"]
            slots = []
            for h in horarios_del_dia(d):
                cups = []
                for rol in roles:
                    cups.append({
                        "rol": rol,
                        "rol_txt": "Cámara" if rol == "camara" else "Sonido",
                        "valor": f"{d.isoformat()}|{h}|{rol}",
                "marcado": False,
                        "libre": (d, h, rol) not in ocupados,
                    })
                slots.append({"etiqueta": etiqueta_horario(h), "roles": cups})
            filas.append({
                "dia": NOMBRES_DIA[d.weekday()],
                "fecha": f"{d.day:02d}/{['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'][d.month-1].capitalize()}",
                "festivo": es_festivo(d) and d.weekday() < 5,
                "libre": any(c["libre"] for s in slots for c in s["roles"]),
                "slots": slots,
            })
        ctx.update({
            "filas": filas, "mios": mios, "no_puede": no_puede,
            "resumen": bool(ok) and not cambiar, "lineas": lineas,
        })
    return templates.TemplateResponse(request, "postular.html", ctx)


@router.post("/postular/{token}/revisar")
async def postular_revisar(
    request: Request, token: str, db: Session = Depends(get_db),
    celular: str = Form(""), celular2: str = Form(""),
):
    from ..processing import normalizar_celular
    if not _enlace_sonido(db, token) or not _postular_abierto(request, token, _enlace_sonido(db, token)):
        return {"ok": False, "error": "Primero escribe la clave"}
    cel = normalizar_celular(celular)
    cel2 = normalizar_celular(celular2)
    if not cel or cel != cel2:
        return {"ok": False, "error": "Los dos celulares deben coincidir y tener 10 dígitos"}
    per = db.query(Persona).filter(Persona.celular == cel).first()
    mios = []
    no_puede = False
    if per:
        from ..models import AusenciaSemana, Postulacion
        lunes = semana_vigente()
        no_puede = db.query(AusenciaSemana).filter(
            AusenciaSemana.persona_id == per.id, AusenciaSemana.labor == "sonido", AusenciaSemana.semana_lunes == lunes,
        ).first() is not None
        mios = [
            f"{p.fecha.isoformat()}|{p.horario}|{p.rol}"
            for p in db.query(Postulacion).filter(
                Postulacion.persona_id == per.id, Postulacion.labor == "sonido", Postulacion.semana_lunes == lunes,
            ).all()
        ]
    return {"ok": True, "existe": bool(per), "nombre": per.nombre if per else "", "mios": mios, "no_puede": no_puede}


def _ok_postular(no_puedo) -> str:
    from urllib.parse import quote
    base = "Quedó guardado. Esta semana no puedes apoyar" if no_puedo else "Quedó guardado. No necesitas enviarlo otra vez"
    return quote(base)


def _tarde_domingo() -> bool:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ahora = datetime.now(ZoneInfo("America/Bogota"))
    return ahora.weekday() == 6 and ahora.hour >= 14


@router.post("/postular/{token}")
async def postular_guardar(
    request: Request, token: str, db: Session = Depends(get_db),
    clave: str = Form(""), celular: str = Form(""), celular2: str = Form(""),
    nombre: str = Form(""), no_puedo: str = Form(""),
):
    from ..postulacion import (
        aplicar_semana,
        clave_valida,
        enlace_abrir,
        enlace_bloqueado,
        enlace_fallo,
        enlace_persona_set,
        postulaciones_persona,
        tiene_semana,
    )
    from ..processing import normalizar_celular, normalizar_nombre
    row = _enlace_sonido(db, token)
    if not row:
        return RedirectResponse(url=f"/postular/{token}", status_code=303)
    form = await request.form()
    if form.get("paso") == "clave":
        if enlace_bloqueado(request):
            return RedirectResponse(url=f"/postular/{token}?error=Demasiados+intentos.+Espera+15+minutos", status_code=303)
        if not clave_valida(db, clave):
            bloqueo = enlace_fallo(request)
            if bloqueo:
                return RedirectResponse(url=f"/postular/{token}?error=Demasiados+intentos.+Espera+15+minutos", status_code=303)
            return RedirectResponse(url=f"/postular/{token}?error=Clave+incorrecta", status_code=303)
        enlace_abrir(request, token)
        return RedirectResponse(url=f"/postular/{token}", status_code=303)
    if form.get("paso") != "guardar":
        return RedirectResponse(url=f"/postular/{token}?cambiar=1", status_code=303)
    if not _postular_abierto(request, token, row):
        return RedirectResponse(url=f"/postular/{token}?error=Primero+escribe+la+clave", status_code=303)
    cel = normalizar_celular(celular)
    cel2 = normalizar_celular(celular2)
    if not cel or cel != cel2:
        return RedirectResponse(url=f"/postular/{token}?error=Los+dos+celulares+deben+coincidir+y+tener+10+dígitos", status_code=303)
    per = db.query(Persona).filter(Persona.celular == cel).first()
    if not per:
        nom = normalizar_nombre(nombre)
        if not nom:
            return RedirectResponse(url=f"/postular/{token}?error=No+estás+en+la+base.+Escribe+tu+nombre", status_code=303)
        per = Persona(nombre=nom, celular=cel, estado="No instalada", pendiente_revision=False, fuente_ultima="Postulación Sonido")
        db.add(per)
        db.commit()
        db.refresh(per)
        db.add(Alerta(
            para_delegacion="comunicaciones", tipo="alta_nueva",
            texto=f"Persona nueva desde el enlace de Sonido: {nom} ({cel}). Revisar si ya tiene la App.",
            leida=False,
        ))
        db.commit()
    from ..postulacion import asegurar_equipo
    asegurar_equipo(db, per.id)
    db.commit()
    enlace_persona_set(request, per.id)
    lunes = semana_vigente()
    huecos = []
    if not no_puedo:
        from datetime import datetime as dt
        from datetime import timedelta as td
        for valor in form.getlist("hueco"):
            try:
                f, h, rol = valor.split("|")
                fecha = dt.strptime(f, "%Y-%m-%d").date()
                if not (lunes <= fecha <= lunes + td(days=6)) or rol not in ("sonido", "camara"):
                    continue
                if h not in horarios_del_dia(fecha):
                    continue
                huecos.append((fecha, h, rol))
            except ValueError:
                continue
    if not no_puedo and not huecos:
        return RedirectResponse(
            url=f"/postular/{token}?cambiar=1&error=Marca+al+menos+un+horario.+Si+no+puedes+esta+semana,+marca+esa+opción",
            status_code=303,
        )
    if tiene_semana(db, per.id, lunes):
        from ..models import AusenciaSemana, CambioPostulacion
        ya_no = db.query(AusenciaSemana).filter(
            AusenciaSemana.persona_id == per.id, AusenciaSemana.labor == "sonido", AusenciaSemana.semana_lunes == lunes,
        ).first() is not None
        actuales = {
            (p.fecha, p.horario, p.rol)
            for p in postulaciones_persona(db, per.id, lunes)
        }
        iguales = (bool(no_puedo) and ya_no) or (not no_puedo and not ya_no and set(huecos) == actuales)
        if iguales:
            cambio = db.query(CambioPostulacion).filter(
                CambioPostulacion.persona_id == per.id,
                CambioPostulacion.labor == "sonido",
                CambioPostulacion.semana_lunes == lunes,
                CambioPostulacion.estado == "pendiente",
            ).first()
            if cambio:
                db.query(Alerta).filter(Alerta.tipo == f"cambio-{cambio.id}").update({"leida": True})
                cambio.estado = "sin-cambio"
                db.commit()
            return RedirectResponse(
                url=f"/postular/{token}?ok=" + __import__("urllib.parse", fromlist=["quote"]).quote("Solicitaste los mismos horarios de antes, así que no hay cambios en tu postulación") + ("&tarde=1" if _tarde_domingo() else ""),
                status_code=303,
            )
        detalle = "no-puede" if no_puedo else ";".join(f"{f.isoformat()}|{h}|{rol}" for f, h, rol in huecos)
        cambio = db.query(CambioPostulacion).filter(
            CambioPostulacion.persona_id == per.id,
            CambioPostulacion.labor == "sonido",
            CambioPostulacion.semana_lunes == lunes,
            CambioPostulacion.estado == "pendiente",
        ).first()
        if not cambio:
            cambio = CambioPostulacion(persona_id=per.id, labor="sonido", semana_lunes=lunes)
            db.add(cambio)
        cambio.no_puede = bool(no_puedo)
        cambio.detalle = detalle
        db.commit()
        db.refresh(cambio)
        tipo = f"cambio-{cambio.id}"
        antes = _texto_vigente(db, per.id, lunes)
        ahora = "esta semana no puede apoyar" if no_puedo else _texto_huecos(huecos)
        texto = f"{per.nombre} pidió cambiar su postulación. Antes: {antes}. Ahora: {ahora}."
        alerta = db.query(Alerta).filter(Alerta.para_delegacion == "sonido", Alerta.tipo == tipo).first()
        if alerta:
            alerta.texto = texto
            alerta.leida = False
        else:
            db.add(Alerta(para_delegacion="sonido", tipo=tipo, leida=False, texto=texto))
        db.commit()
        return RedirectResponse(url=f"/postular/{token}?ok=" + _ok_postular(no_puedo) + ("&tarde=1" if _tarde_domingo() else ""), status_code=303)
    aplicar_semana(db, per.id, lunes, huecos, bool(no_puedo))
    return RedirectResponse(url=f"/postular/{token}?ok=" + _ok_postular(no_puedo) + ("&tarde=1" if _tarde_domingo() else ""), status_code=303)


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

