from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import _delegacion, require_com_user, require_login, templates
from ..models import (
    Alerta,
    DomingoPunto,
    Persona,
    Postulacion,
    Turno,
)
from ..punto import (
    dia_permitido,
    dias_semana,
    etiqueta_horario,
    horarios_del_dia,
    NOMBRES_DIA,
    obtener_config,
    ranking_anio,
    semana_vigente,
)

router = APIRouter()

@router.get("/programacion", response_class=HTMLResponse)
async def programacion_ver(request: Request, db: Session = Depends(get_db),
                           q: str = "", horario_sel: str = "", fecha_sel: str = "",
                           modo: str = "editar", error: str = "", ok: str = "",
                           confirmar: str = "", hl: str = ""):
    user = require_login(request)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    from ..services.programacion import vista_delegacion, vista_sonido, vista_banos
    delg = _delegacion(request)
    if delg == "sonido":
        ctx = vista_sonido(request, db, user, q, horario_sel, fecha_sel, modo, error, ok)
        request.session["n_alertas"] = len(ctx["alertas"])
        return templates.TemplateResponse(request, "programacion_sonido.html", ctx)
    if delg == "banos":
        ctx = vista_banos(request, db, user, q, horario_sel, fecha_sel, modo, error, ok)
        request.session["n_alertas"] = len(ctx["alertas"])
        return templates.TemplateResponse(request, "programacion_banos.html", ctx)
    ctx = vista_delegacion(
        request, db, user, delg, q, horario_sel, fecha_sel, modo, error, ok, confirmar, hl,
    )
    request.session["n_alertas"] = len(ctx["alertas"])
    return templates.TemplateResponse(request, "programacion.html", ctx)


@router.post("/programacion/banos/asignar")
async def banos_asignar(
    request: Request, db: Session = Depends(get_db),
    persona_id: int = Form(...), fecha: str = Form(...), horario: str = Form(...),
    modo: str = Form(""),
):
    from datetime import datetime as dt
    from datetime import timedelta as td
    from urllib.parse import quote
    user = require_login(request)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if _delegacion(request) != "banos":
        return RedirectResponse(url="/programacion", status_code=status.HTTP_303_SEE_OTHER)
    if modo != "editar":
        return RedirectResponse(url="/programacion?modo=foto&error=La+foto+no+se+edita", status_code=303)
    try:
        f = dt.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        return RedirectResponse(url="/programacion?error=Fecha+inválida", status_code=303)
    lunes = semana_vigente()
    if not (lunes <= f <= lunes + td(days=6)):
        return RedirectResponse(url="/programacion?error=Solo+se+edita+la+semana+vigente", status_code=303)
    if horario not in horarios_del_dia(f):
        return RedirectResponse(url="/programacion?error=Horario+no+válido+ese+día", status_code=303)
    persona = db.query(Persona).filter(Persona.id == persona_id).first()
    if not persona:
        return RedirectResponse(url="/programacion?error=Persona+no+encontrada", status_code=303)
    cupo = db.query(Turno).filter(
        Turno.delegacion == "banos", Turno.fecha == f, Turno.horario == horario
    ).first()
    if cupo:
        return RedirectResponse(url="/programacion?error=Ese+horario+ya+está+lleno", status_code=303)
    misma_hora = db.query(Turno).filter(
        Turno.persona_id == persona_id, Turno.fecha == f, Turno.horario == horario
    ).first()
    if misma_hora:
        from .sonido import _nombre_equipo
        equipo = _nombre_equipo(misma_hora.delegacion)
        aviso = quote(f"{persona.nombre} ya está a esa hora en {equipo}. No se le quita ese turno.")
        return RedirectResponse(url=f"/programacion?error={aviso}", status_code=303)
    db.add(Turno(
        persona_id=persona_id, fecha=f, horario=horario, delegacion="banos",
        semana_lunes=lunes, rol="banos",
    ))
    db.commit()
    return RedirectResponse(url=f"/programacion?modo=editar&ok=Listo&hl={fecha}-{horario}", status_code=303)


@router.post("/programacion/banos/confirmar")
async def banos_confirmar(
    request: Request, db: Session = Depends(get_db),
    persona_id: int = Form(...), fecha: str = Form(...), horario: str = Form(...),
    modo: str = Form(""), ajax: str = Form(""),
):
    from datetime import datetime as dt
    from datetime import timedelta as td
    from urllib.parse import quote

    from fastapi.responses import JSONResponse
    user = require_login(request)
    if not user:
        return JSONResponse({"ok": False, "error": "Sesión vencida"}) if ajax == "1" else RedirectResponse(url="/login", status_code=303)
    if _delegacion(request) != "banos":
        return JSONResponse({"ok": False, "error": "Solo Baños puede confirmar esto"}) if ajax == "1" else RedirectResponse(url="/programacion", status_code=303)

    def fallo(msg: str):
        if ajax == "1":
            return JSONResponse({"ok": False, "error": msg})
        return RedirectResponse(url="/postulaciones?modo=confirmar&error=" + quote(msg), status_code=303)

    try:
        f = dt.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        return fallo("Fecha inválida")
    lunes = semana_vigente()
    if not (lunes <= f <= lunes + td(days=6)):
        return fallo("Solo se edita la semana vigente")
    if horario not in horarios_del_dia(f):
        return fallo("Horario no válido ese día")
    persona = db.query(Persona).filter(Persona.id == persona_id).first()
    if not persona:
        return fallo("Persona no encontrada")
    cupo = db.query(Turno).filter(
        Turno.delegacion == "banos", Turno.fecha == f, Turno.horario == horario
    ).first()
    if cupo:
        return fallo("Ese horario ya está lleno. Quítalo en Armar semana para cambiarlo")
    misma_hora = db.query(Turno).filter(
        Turno.persona_id == persona_id, Turno.fecha == f, Turno.horario == horario
    ).first()
    if misma_hora:
        from .sonido import _nombre_equipo
        equipo = _nombre_equipo(misma_hora.delegacion)
        return fallo(f"{persona.nombre} ya está a esa hora en {equipo}. No se le quita ese turno.")
    from ..postulacion import asegurar_equipo_banos
    asegurar_equipo_banos(db, persona_id)
    db.add(Turno(
        persona_id=persona_id, fecha=f, horario=horario, delegacion="banos",
        semana_lunes=lunes, rol="banos",
    ))
    db.commit()
    if ajax == "1":
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/postulaciones?modo=confirmar&ok=Confirmado", status_code=303)


@router.post("/programacion/banos/quitar")
async def banos_quitar(
    request: Request, db: Session = Depends(get_db),
    turno_id: int = Form(...), modo: str = Form(""),
):
    user = require_login(request)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if _delegacion(request) != "banos":
        return RedirectResponse(url="/programacion", status_code=status.HTTP_303_SEE_OTHER)
    if modo != "editar":
        return RedirectResponse(url="/programacion?modo=foto&error=La+foto+no+se+edita", status_code=303)
    turno = db.query(Turno).filter(Turno.id == turno_id, Turno.delegacion == "banos").first()
    if not turno:
        return RedirectResponse(url="/programacion?error=Turno+no+existe", status_code=303)
    hl = f"{turno.fecha.isoformat()}-{turno.horario}"
    db.delete(turno)
    db.commit()
    return RedirectResponse(url=f"/programacion?modo=editar&ok=Quitado&hl={hl}", status_code=303)


@router.post("/programacion/banos/alta")
async def banos_alta(
    request: Request, db: Session = Depends(get_db),
    nombre: str = Form(...), celular: str = Form(...),
    fecha: str = Form(...), horario: str = Form(...), modo: str = Form(""),
):
    from ..processing import normalizar_celular, normalizar_nombre
    from datetime import datetime as dt
    user = require_login(request)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if _delegacion(request) != "banos":
        return RedirectResponse(url="/programacion", status_code=status.HTTP_303_SEE_OTHER)
    if modo != "editar":
        return RedirectResponse(url="/programacion?modo=foto&error=La+foto+no+se+edita", status_code=303)
    cel = normalizar_celular(celular)
    nom = normalizar_nombre(nombre)
    if not cel or not nom:
        return RedirectResponse(
            url=f"/programacion?error=Nombre+y+celular+de+10+dígitos&fecha_sel={fecha}&horario_sel={horario}",
            status_code=303,
        )
    per = db.query(Persona).filter(Persona.celular == cel).first()
    if not per:
        per = Persona(nombre=nom, celular=cel, estado="No instalada", pendiente_revision=False, fuente_ultima="Baños")
        db.add(per)
        db.commit()
        db.refresh(per)
        db.add(Alerta(
            para_delegacion="comunicaciones", tipo="alta_nueva",
            texto=f"Persona nueva registrada desde Baños: {nom} ({cel}). Revisar si ya tiene la App.",
            leida=False,
        ))
        db.commit()
    try:
        f = dt.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        return RedirectResponse(url="/programacion?error=Fecha+inválida", status_code=303)
    lunes = semana_vigente()
    db.add(Turno(
        persona_id=per.id, fecha=f, horario=horario, delegacion="banos",
        semana_lunes=lunes,
    ))
    db.commit()
    return RedirectResponse(url=f"/programacion?modo=editar&ok=Listo&hl={fecha}-{horario}", status_code=303)
async def programacion_tutorial(request: Request):
    if require_login(request):
        request.session["tut_prog"] = True
    return RedirectResponse(url="/programacion", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/programacion/asignar")
async def programacion_asignar(
    request: Request, db: Session = Depends(get_db),
    persona_id: int = Form(...), fecha: str = Form(...), horario: str = Form(...),
    confirmar_fimlm: str = Form(""),
):
    from datetime import datetime as dt
    user = require_login(request)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if _delegacion(request) == "sonido":
        return RedirectResponse(url="/programacion", status_code=status.HTTP_303_SEE_OTHER)
    delg = _delegacion(request)
    try:
        f = dt.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        return RedirectResponse(url="/programacion?error=Fecha+inválida", status_code=303)
    from datetime import timedelta as td
    lunes = semana_vigente()
    if not (lunes <= f <= lunes + td(days=6)):
        return RedirectResponse(url="/programacion?error=Solo+se+edita+la+semana+vigente", status_code=303)
    if horario not in horarios_del_dia(f):
        return RedirectResponse(url="/programacion?error=Horario+no+válido+ese+día", status_code=303)
    cfg = obtener_config(db)
    domingos = {r.fecha for r in db.query(DomingoPunto).all()}
    if not dia_permitido(cfg, f, domingos, delg):
        return RedirectResponse(url="/programacion?error=Ese+día+no+está+habilitado+para+tu+delegación", status_code=303)
    if delg != "fimlm" and not cfg.convivencia_fimlm and db.query(Turno).filter(
        Turno.fecha == f, Turno.horario == horario, Turno.delegacion == "fimlm"
    ).first():
        return RedirectResponse(
            url=f"/programacion?modo=editar&error=Ese+horario+ya+está+reservado+por+FIMLM&fecha_sel={fecha}&horario_sel={horario}",
            status_code=303,
        )
    persona = db.query(Persona).filter(Persona.id == persona_id).first()
    if not persona:
        return RedirectResponse(url="/programacion?error=Persona+no+encontrada", status_code=303)
    ya = db.query(Turno).filter(Turno.persona_id == persona_id, Turno.fecha == f, Turno.horario == horario).first()
    if ya:
        from .sonido import _nombre_equipo
        equipo = _nombre_equipo(ya.delegacion)
        from urllib.parse import quote
        aviso = quote(f"{persona.nombre} ya está a esa hora en {equipo}. No se le quita ese turno.")
        return RedirectResponse(
            url=f"/programacion?modo=editar&fecha_sel={fecha}&horario_sel={horario}&error={aviso}",
            status_code=303,
        )
    if delg != "fimlm":
        n = db.query(Turno).filter(
            Turno.persona_id == persona_id, Turno.semana_lunes == lunes, Turno.delegacion != "sonido"
        ).count()
        if n >= 2:
            return RedirectResponse(url="/programacion?error=No+se+puede+programar+a+esta+persona:+ya+tiene+el+máximo+de+2+turnos+esta+semana", status_code=303)
    otros = db.query(Turno).filter(
        Turno.fecha == f, Turno.horario == horario, Turno.delegacion != "fimlm", Turno.delegacion != "sonido"
    ).all() if delg == "fimlm" and not cfg.convivencia_fimlm else []
    if delg == "fimlm" and otros and confirmar_fimlm != "si":
        nombres = []
        for t in otros:
            per = db.query(Persona).filter(Persona.id == t.persona_id).first()
            if per:
                nombres.append(per.nombre)
        qs = "&".join([
            f"fecha_sel={fecha}", f"horario_sel={horario}",
            f"confirmar={persona_id}",
            "error=" + ("FIMLM+tomará+el+horario.+Saldrán:+" + ",+".join(nombres)).replace(" ", "+"),
        ])
        return RedirectResponse(url=f"/programacion?{qs}", status_code=303)
    if delg == "fimlm" and otros:
        from collections import defaultdict
        por_del = defaultdict(list)
        for t in otros:
            per = db.query(Persona).filter(Persona.id == t.persona_id).first()
            por_del[t.delegacion].append(per.nombre if per else "?")
            db.delete(t)
        for ddeleg, noms in por_del.items():
            db.add(Alerta(
                para_delegacion=ddeleg, tipo="fimlm_desplazo",
                texto=f"FIMLM tomó el {f.strftime('%d/%m')} {etiqueta_horario(horario)}. Salieron: {', '.join(noms)}.",
                leida=False,
            ))
    db.add(Turno(persona_id=persona_id, fecha=f, horario=horario, delegacion=delg, semana_lunes=lunes))
    db.commit()
    return RedirectResponse(url=f"/programacion?ok=Listo&modo=editar&fecha_sel={fecha}&horario_sel={horario}&hl={fecha}-{horario}", status_code=303)


@router.post("/programacion/quitar")
async def programacion_quitar(request: Request, db: Session = Depends(get_db), turno_id: int = Form(...)):
    user = require_login(request)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    delg = _delegacion(request)
    turno = db.query(Turno).filter(Turno.id == turno_id).first()
    if not turno:
        return RedirectResponse(url="/programacion?error=Turno+no+existe", status_code=303)
    if turno.delegacion != delg and delg != "fimlm":
        return RedirectResponse(url="/programacion?error=Solo+quitas+a+los+que+puso+tu+delegación", status_code=303)
    hl = f"{turno.fecha}-{turno.horario}"
    db.delete(turno)
    db.commit()
    return RedirectResponse(url=f"/programacion?modo=editar&hl={hl}", status_code=303)


@router.post("/programacion/alta")
async def programacion_alta(
    request: Request, db: Session = Depends(get_db),
    nombre: str = Form(...), celular: str = Form(...),
    fecha: str = Form(...), horario: str = Form(...),
):

    from ..processing import normalizar_celular, normalizar_nombre
    user = require_login(request)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    delg = _delegacion(request)
    cel = normalizar_celular(celular)
    nom = normalizar_nombre(nombre)
    if not cel or not nom:
        return RedirectResponse(url="/programacion?error=Nombre+y+celular+de+10+dígitos", status_code=303)
    per = db.query(Persona).filter(Persona.celular == cel).first()
    if not per:
        per = Persona(nombre=nom, celular=cel, estado="No instalada", pendiente_revision=False, fuente_ultima="Programación")
        db.add(per)
        db.commit()
        db.refresh(per)
        db.add(Alerta(para_delegacion="comunicaciones", tipo="alta_nueva",
                      texto=f"Persona nueva registrada desde la delegación {delg}: {nom} ({cel}). Revisar si ya tiene la App.", leida=False))
        db.commit()
    # reutilizar asignar
    form_redirect = await programacion_asignar(request, db, persona_id=per.id, fecha=fecha, horario=horario, confirmar_fimlm="")
    return form_redirect

@router.post("/alertas/leer")
async def alertas_leer(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    delg = request.session.get("delegacion") or "comunicaciones"
    db.query(Alerta).filter(
        Alerta.para_delegacion == delg,
        Alerta.leida == False,
        ~Alerta.tipo.like("cambio-%"),
        ~Alerta.tipo.like("silencio|%"),
    ).update({"leida": True}, synchronize_session=False)
    db.commit()
    request.session["n_alertas"] = 0
    return RedirectResponse(url="/dashboard", status_code=303)



@router.get("/config-punto", response_class=HTMLResponse)
async def config_punto_ver(request: Request, db: Session = Depends(get_db), ok: str = "", error: str = ""):
    user, redir = require_com_user(request)
    if redir:
        return redir
    cfg = obtener_config(db)
    doms = db.query(DomingoPunto).order_by(DomingoPunto.fecha).all()
    return templates.TemplateResponse(request, "config_punto.html", {
        "user": user, "cfg": cfg, "domingos": doms, "ok": ok, "error": error,
    })


@router.post("/config-punto")
async def config_punto_guardar(
    request: Request, db: Session = Depends(get_db),
    vigencia_inicio: str = Form(""),
    vigencia_fin: str = Form(""),
    lun: str = Form(""),
    mar: str = Form(""),
    mie: str = Form(""),
    jue: str = Form(""),
    vie: str = Form(""),
    sab: str = Form(""),
    confirmar_bajas: str = Form(""),
    convivencia_fimlm: str = Form(""),
    labor_comunicaciones: str = Form(""),
    labor_politica: str = Form(""),
    labor_juventudes: str = Form(""),
    labor_electoral: str = Form(""),
    labor_fimlm: str = Form(""),
):
    from datetime import datetime as dt
    user, redir = require_com_user(request)
    if redir:
        return redir
    cfg = obtener_config(db)
    nuevos = {
        "lun": lun == "on", "mar": mar == "on", "mie": mie == "on",
        "jue": jue == "on", "vie": vie == "on", "sab": sab == "on",
    }
    nombres = {"lun": "lunes", "mar": "martes", "mie": "miércoles", "jue": "jueves", "vie": "viernes", "sab": "sábado"}
    wd_map = {"lun": 0, "mar": 1, "mie": 2, "jue": 3, "vie": 4, "sab": 5}
    apagados = [k for k, v in nuevos.items() if getattr(cfg, k) and not v]
    lunes_ok = semana_vigente()
    afectados = []
    if apagados:
        wds = {wd_map[k] for k in apagados}
        turnos = db.query(Turno).filter(Turno.semana_lunes >= lunes_ok).all()
        afectados = [t for t in turnos if t.fecha.weekday() in wds]
    convivencia_nueva = convivencia_fimlm == "on"
    sacar_conv = []
    if cfg.convivencia_fimlm and not convivencia_nueva:
        from collections import defaultdict
        grupos = defaultdict(list)
        for tno in db.query(Turno).filter(Turno.semana_lunes >= lunes_ok, Turno.delegacion != "sonido").all():
            grupos[(tno.fecha, tno.horario)].append(tno)
        for grupo in grupos.values():
            if any(t.delegacion == "fimlm" for t in grupo):
                sacar_conv.extend(t for t in grupo if t.delegacion != "fimlm")
    if (afectados or sacar_conv) and confirmar_bajas != "si":
        partes = []
        if afectados:
            dias = ", ".join(nombres[k] for k in apagados)
            partes.append(f"Hay {len(afectados)} persona(s) en {dias} de la semana abierta")
        if sacar_conv:
            partes.append(f"Al cerrar la convivencia salen {len(sacar_conv)} persona(s) de las horas donde también está FIMLM")
        return RedirectResponse(
            url="/config-punto?error=" + (
                ". ".join(partes) + ". Si guardas, se quitan. Vuelve a guardar marcando la casilla de confirmación."
            ).replace(" ", "+"),
            status_code=303,
        )
    try:
        cfg.vigencia_inicio = dt.strptime(vigencia_inicio, "%Y-%m-%d").date() if vigencia_inicio else None
        cfg.vigencia_fin = dt.strptime(vigencia_fin, "%Y-%m-%d").date() if vigencia_fin else None
    except ValueError:
        return RedirectResponse(url="/config-punto?error=Fechas+inválidas", status_code=303)
    for k, v in nuevos.items():
        setattr(cfg, k, v)
    cfg.convivencia_fimlm = convivencia_nueva
    cfg.labor_comunicaciones = labor_comunicaciones.strip()[:60]
    cfg.labor_politica = labor_politica.strip()[:60]
    cfg.labor_juventudes = labor_juventudes.strip()[:60]
    cfg.labor_electoral = labor_electoral.strip()[:60]
    cfg.labor_fimlm = labor_fimlm.strip()[:60]
    for tno in afectados:
        db.delete(tno)
    if sacar_conv:
        from collections import defaultdict
        por_del = defaultdict(list)
        for tno in sacar_conv:
            per = db.query(Persona).filter(Persona.id == tno.persona_id).first()
            por_del[tno.delegacion].append(
                f"{per.nombre if per else '?'} el {tno.fecha.strftime('%d/%m')} {etiqueta_horario(tno.horario)}"
            )
            db.delete(tno)
        for ddeleg, noms in por_del.items():
            db.add(Alerta(
                para_delegacion=ddeleg, tipo="convivencia_fin",
                texto="Se cerró la convivencia con FIMLM. Salieron: " + ", ".join(noms) + ".",
                leida=False,
            ))
    db.commit()
    extra = ""
    if afectados:
        extra += f". Se quitaron {len(afectados)} asignaciones."
    if sacar_conv:
        extra += f" Salieron {len(sacar_conv)} de las horas compartidas con FIMLM."
    return RedirectResponse(url="/config-punto?ok=Guardado" + extra.replace(" ", "+"), status_code=303)


@router.post("/config-punto/domingo")
async def config_punto_domingo(request: Request, db: Session = Depends(get_db), fecha: str = Form(...)):
    from datetime import datetime as dt
    user, redir = require_com_user(request)
    if redir:
        return redir
    try:
        f = dt.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        return RedirectResponse(url="/config-punto?error=Fecha+inválida", status_code=303)
    if f.weekday() != 6:
        return RedirectResponse(url="/config-punto?error=Esa+fecha+no+es+domingo", status_code=303)
    if not db.query(DomingoPunto).filter(DomingoPunto.fecha == f).first():
        db.add(DomingoPunto(fecha=f))
        db.commit()
        from ..auth import DELEGACIONES
        from ..punto import MESES_ES
        tipo = f"domingo-{f.isoformat()}"
        texto = (
            f"Atención equipos: el domingo {f.day} de {MESES_ES[f.month-1]} "
            "el punto de información estará activo. Convocá a la mayor cantidad "
            "de colaboradores de tu delegación; contamos con su apoyo."
        )
        for delg in DELEGACIONES:
            if delg == "fimlm":
                continue
            if not db.query(Alerta).filter(Alerta.para_delegacion == delg, Alerta.tipo == tipo).first():
                db.add(Alerta(para_delegacion=delg, tipo=tipo, texto=texto, leida=False))
        db.commit()
    return RedirectResponse(url="/config-punto?ok=Domingo+habilitado", status_code=303)


@router.post("/config-punto/domingo/{did}/quitar")
async def config_punto_domingo_quitar(request: Request, did: int, db: Session = Depends(get_db)):
    user, redir = require_com_user(request)
    if redir:
        return redir
    row = db.query(DomingoPunto).filter(DomingoPunto.id == did).first()
    if row:
        lunes_ok = semana_vigente()
        for tno in db.query(Turno).filter(Turno.fecha == row.fecha, Turno.semana_lunes >= lunes_ok).all():
            db.delete(tno)
        db.delete(row)
        db.commit()
    return RedirectResponse(url="/config-punto?ok=Domingo+quitado", status_code=303)



@router.get("/podio", response_class=HTMLResponse)
async def podio_ver(request: Request, db: Session = Depends(get_db), persona_id: int = 0):
    user, redir = require_com_user(request)
    if redir:
        return redir
    ranking, _ = ranking_anio(db)
    detalle = next((r for r in ranking if r["persona"].id == persona_id), None)
    return templates.TemplateResponse(request, "podio.html", {
        "user": user,
        "ranking": ranking,
        "detalle": detalle,
        "anio": semana_vigente().year,
    })


def _enlace_banos(db, token: str):
    from ..postulacion import clave_vigente
    clave = clave_vigente(db, "banos")
    if clave.token != token:
        return None
    return clave


@router.get("/postular-banos/{token}", response_class=HTMLResponse)
async def postular_banos_ver(request: Request, token: str, db: Session = Depends(get_db), error: str = "", ok: str = "", cambiar: str = ""):
    from ..festivos import es_festivo
    from ..postulacion import enlace_abierto, enlace_persona
    clave = _enlace_banos(db, token)
    if not clave:
        return templates.TemplateResponse(request, "postular_banos.html", {"invalido": True})
    abierto = enlace_abierto(request, token)
    ctx = {"invalido": False, "token": token, "abierto": abierto, "error": error, "ok": ok, "filas": [], "no_puede": False, "resumen": False, "lineas": []}
    if abierto:
        lunes = semana_vigente()
        dias = dias_semana(lunes)
        ocupados = {
            (p.fecha, p.horario)
            for p in db.query(Postulacion).filter(
                Postulacion.labor == "banos", Postulacion.semana_lunes == lunes,
            ).all()
        }
        pid = enlace_persona(request) if ok and not cambiar else None
        mios = set()
        lineas = []
        no_puede = False
        if pid:
            from ..postulacion import postulaciones_persona
            for p in postulaciones_persona(db, pid, lunes, "banos"):
                mios.add(f"{p.fecha.isoformat()}|{p.horario}")
                lineas.append(
                    f"{NOMBRES_DIA[p.fecha.weekday()]} {p.fecha.day:02d}/"
                    f"{['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'][p.fecha.month-1].capitalize()} · "
                    f"{etiqueta_horario(p.horario)}"
                )
            lineas.sort()
            from ..models import AusenciaSemana
            no_puede = db.query(AusenciaSemana).filter(
                AusenciaSemana.persona_id == pid, AusenciaSemana.labor == "banos", AusenciaSemana.semana_lunes == lunes,
            ).first() is not None
        filas = []
        for d in dias:
            slots = []
            for h in horarios_del_dia(d):
                valor = f"{d.isoformat()}|{h}"
                slots.append({
                    "horario": h, "etiqueta": etiqueta_horario(h),
                    "valor": valor, "libre": (d, h) not in ocupados,
                    "marcado": valor in mios,
                })
            filas.append({
                "fecha": f"{d.day:02d}/{['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'][d.month-1].capitalize()}",
                "dia": NOMBRES_DIA[d.weekday()],
                "festivo": es_festivo(d) and d.weekday() < 5,
                "libre": any(s["libre"] for s in slots),
                "slots": slots,
            })
        ctx.update({"filas": filas, "no_puede": no_puede, "lineas": lineas, "resumen": bool(ok) and not cambiar})
    return templates.TemplateResponse(request, "postular_banos.html", ctx)


@router.post("/postular-banos/{token}/revisar")
async def postular_banos_revisar(
    request: Request, token: str, db: Session = Depends(get_db),
    celular: str = Form(""), celular2: str = Form(""),
):
    from ..processing import normalizar_celular
    from ..postulacion import enlace_abierto
    if not _enlace_banos(db, token) or not enlace_abierto(request, token):
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
            AusenciaSemana.persona_id == per.id, AusenciaSemana.labor == "banos", AusenciaSemana.semana_lunes == lunes,
        ).first() is not None
        mios = [
            f"{p.fecha.isoformat()}|{p.horario}"
            for p in db.query(Postulacion).filter(
                Postulacion.persona_id == per.id, Postulacion.labor == "banos", Postulacion.semana_lunes == lunes,
            ).all()
        ]
    return {"ok": True, "existe": bool(per), "nombre": per.nombre if per else "", "mios": mios, "no_puede": no_puede}


@router.post("/postular-banos/{token}")
async def postular_banos_guardar(
    request: Request, token: str, db: Session = Depends(get_db),
    clave: str = Form(""), celular: str = Form(""), celular2: str = Form(""),
    nombre: str = Form(""), no_puedo: str = Form(""),
):
    from datetime import datetime as dt
    from datetime import timedelta as td
    from urllib.parse import quote
    from ..postulacion import (
        aplicar_semana, clave_valida, enlace_abierto, enlace_abrir, enlace_bloqueado,
        enlace_fallo, enlace_persona_set, postulaciones_persona, tiene_semana,
    )
    from ..processing import normalizar_celular, normalizar_nombre
    row = _enlace_banos(db, token)
    if not row:
        return RedirectResponse(url=f"/postular-banos/{token}", status_code=303)
    form = await request.form()
    if form.get("paso") == "clave":
        if enlace_bloqueado(request):
            return RedirectResponse(url=f"/postular-banos/{token}?error=Demasiados+intentos.+Espera+15+minutos", status_code=303)
        if not clave_valida(db, clave, "banos"):
            bloqueo = enlace_fallo(request)
            if bloqueo:
                return RedirectResponse(url=f"/postular-banos/{token}?error=Demasiados+intentos.+Espera+15+minutos", status_code=303)
            return RedirectResponse(url=f"/postular-banos/{token}?error=Clave+incorrecta", status_code=303)
        enlace_abrir(request, token)
        return RedirectResponse(url=f"/postular-banos/{token}", status_code=303)
    if form.get("paso") != "guardar":
        return RedirectResponse(url=f"/postular-banos/{token}", status_code=303)
    if not enlace_abierto(request, token):
        return RedirectResponse(url=f"/postular-banos/{token}?error=Primero+escribe+la+clave", status_code=303)
    cel = normalizar_celular(celular)
    cel2 = normalizar_celular(celular2)
    if not cel or cel != cel2:
        return RedirectResponse(url=f"/postular-banos/{token}?error=Los+dos+celulares+deben+coincidir+y+tener+10+dígitos", status_code=303)
    per = db.query(Persona).filter(Persona.celular == cel).first()
    if not per:
        nom = normalizar_nombre(nombre)
        if not nom:
            return RedirectResponse(url=f"/postular-banos/{token}?error=No+estás+en+la+base.+Escribe+tu+nombre", status_code=303)
        per = Persona(nombre=nom, celular=cel, estado="No instalada", pendiente_revision=False, fuente_ultima="Postulación Baños")
        db.add(per)
        db.commit()
        db.refresh(per)
        db.add(Alerta(
            para_delegacion="comunicaciones", tipo="alta_nueva",
            texto=f"Persona nueva desde el enlace de Baños: {nom} ({cel}). Revisar si ya tiene la App.",
            leida=False,
        ))
        db.commit()
    from ..postulacion import asegurar_equipo_banos
    asegurar_equipo_banos(db, per.id)
    db.commit()
    enlace_persona_set(request, per.id)
    lunes = semana_vigente()
    huecos = []
    if not no_puedo:
        for valor in form.getlist("hueco"):
            try:
                f, h = valor.split("|")
                fecha = dt.strptime(f, "%Y-%m-%d").date()
                if not (lunes <= fecha <= lunes + td(days=6)):
                    continue
                if h not in horarios_del_dia(fecha):
                    continue
                huecos.append((fecha, h, "banos"))
            except ValueError:
                continue
    if not no_puedo and not huecos:
        return RedirectResponse(
            url=f"/postular-banos/{token}?error=Marca+al+menos+un+horario.+Si+no+puedes+esta+semana,+marca+esa+opción",
            status_code=303,
        )
    if tiene_semana(db, per.id, lunes, "banos"):
        from ..models import AusenciaSemana, CambioPostulacion
        ya_no = db.query(AusenciaSemana).filter(
            AusenciaSemana.persona_id == per.id,
            AusenciaSemana.labor == "banos",
            AusenciaSemana.semana_lunes == lunes,
        ).first() is not None
        actuales = {
            (p.fecha, p.horario, "banos")
            for p in postulaciones_persona(db, per.id, lunes, "banos")
        }
        iguales = (bool(no_puedo) and ya_no) or (not no_puedo and not ya_no and set(huecos) == actuales)
        if iguales:
            cambio = db.query(CambioPostulacion).filter(
                CambioPostulacion.persona_id == per.id,
                CambioPostulacion.labor == "banos",
                CambioPostulacion.semana_lunes == lunes,
                CambioPostulacion.estado == "pendiente",
            ).first()
            if cambio:
                db.query(Alerta).filter(Alerta.tipo == f"cambio-{cambio.id}").update({"leida": True})
                cambio.estado = "sin-cambio"
                db.commit()
            return RedirectResponse(
                url=f"/postular-banos/{token}?ok={quote('Solicitaste los mismos horarios de antes, así que no hay cambios en tu postulación')}",
                status_code=303,
            )
        detalle = "no-puede" if no_puedo else ";".join(f"{f.isoformat()}|{h}|banos" for f, h, _ in huecos)
        cambio = db.query(CambioPostulacion).filter(
            CambioPostulacion.persona_id == per.id,
            CambioPostulacion.labor == "banos",
            CambioPostulacion.semana_lunes == lunes,
            CambioPostulacion.estado == "pendiente",
        ).first()
        if not cambio:
            cambio = CambioPostulacion(persona_id=per.id, labor="banos", semana_lunes=lunes)
            db.add(cambio)
        cambio.no_puede = bool(no_puedo)
        cambio.detalle = detalle
        db.commit()
        db.refresh(cambio)
        ahora = "esta semana no puede apoyar" if no_puedo else ", ".join(
            f"{NOMBRES_DIA[f.weekday()]} {etiqueta_horario(h)}" for f, h, _ in huecos
        )
        texto = f"{per.nombre} pidió cambiar su postulación de Baños. Ahora: {ahora}."
        alerta = db.query(Alerta).filter(Alerta.para_delegacion == "banos", Alerta.tipo == f"cambio-{cambio.id}").first()
        if alerta:
            alerta.texto = texto
            alerta.leida = False
        else:
            db.add(Alerta(para_delegacion="banos", tipo=f"cambio-{cambio.id}", leida=False, texto=texto))
        db.commit()
        return RedirectResponse(url=f"/postular-banos/{token}?ok={quote('Cambio enviado. El coordinador lo confirma.')}", status_code=303)
    aplicar_semana(db, per.id, lunes, huecos, bool(no_puedo), "banos")
    db.commit()
    msg = "Quedó guardado. Esta semana no puedes apoyar" if no_puedo else "Quedó guardado. No necesitas enviarlo otra vez"
    return RedirectResponse(url=f"/postular-banos/{token}?ok={quote(msg)}", status_code=303)
