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

@router.get("/programacion", response_class=HTMLResponse)
async def programacion_ver(request: Request, db: Session = Depends(get_db),
                           q: str = "", horario_sel: str = "", fecha_sel: str = "",
                           modo: str = "editar", error: str = "", ok: str = "",
                           confirmar: str = "", hl: str = ""):
    user = require_login(request)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    from ..services.programacion import vista_delegacion, vista_sonido
    delg = _delegacion(request)
    if delg == "sonido":
        ctx = vista_sonido(request, db, user, q, horario_sel, fecha_sel, modo, error, ok)
        request.session["n_alertas"] = len(ctx["alertas"])
        return templates.TemplateResponse(request, "programacion_sonido.html", ctx)
    ctx = vista_delegacion(
        request, db, user, delg, q, horario_sel, fecha_sel, modo, error, ok, confirmar, hl,
    )
    request.session["n_alertas"] = len(ctx["alertas"])
    return templates.TemplateResponse(request, "programacion.html", ctx)


@router.post("/programacion/tutorial-ok")
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
    from datetime import datetime as dt
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
        from ..punto import MESES_ES, NOMBRES_DIA
        from ..auth import DELEGACIONES
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
