from datetime import datetime as dt

from ..festivos import es_festivo
from ..models import Alerta, AusenciaSemana, DomingoPunto, Persona, Postulacion, Turno
from ..processing import normalizar_celular
from ..punto import (
    NOMBRES_DIA,
    dia_permitido,
    dias_semana,
    etiqueta_horario,
    generar_alertas_domingo,
    generar_alertas_festivo,
    horarios_del_dia,
    obtener_config,
    punto_apagado_para,
    ranking_anio,
    semana_vigente,
)


def _buscar_personas(db, q: str):
    if not q.strip():
        return []
    like = f"%{q.strip()}%"
    cel = normalizar_celular(q)
    qq = db.query(Persona).filter(Persona.nombre.ilike(like))
    if cel:
        qq = db.query(Persona).filter((Persona.nombre.ilike(like)) | (Persona.celular == cel))
    return qq.order_by(Persona.nombre).limit(20).all()


def _scores(cerrados, anio, fecha_sel, horario_sel):
    score_hora = {}
    score_anio = {}
    wd_sel = None
    try:
        if fecha_sel and horario_sel:
            wd_sel = dt.strptime(fecha_sel, "%Y-%m-%d").date().weekday()
    except ValueError:
        wd_sel = None
    for ct in cerrados:
        if ct.fecha.year == anio:
            score_anio[ct.persona_id] = score_anio.get(ct.persona_id, 0) + 1
        if wd_sel is not None and ct.fecha.weekday() == wd_sel and ct.horario == horario_sel:
            score_hora[ct.persona_id] = score_hora.get(ct.persona_id, 0) + 1
    return score_hora, score_anio


def vista_sonido(request, db, user, q, horario_sel, fecha_sel, modo, error, ok):
    generar_alertas_festivo(db)
    lunes = semana_vigente()
    dias = dias_semana(lunes)
    turnos = db.query(Turno).filter(Turno.delegacion == "sonido", Turno.semana_lunes == lunes).all()
    personas_map = {p.id: p for p in db.query(Persona).all()} if turnos else {}
    ocupado = {(t.fecha, t.horario, t.rol or "sonido"): t for t in turnos}
    filas = []
    for d in dias:
        festivo = es_festivo(d) and d.weekday() < 5
        roles = ["sonido", "camara"] if d.weekday() in (2, 6) else ["sonido"]
        slots = []
        for h in horarios_del_dia(d):
            cups = []
            for rol in roles:
                t = ocupado.get((d, h, rol))
                per = personas_map.get(t.persona_id) if t else None
                cups.append({
                    "rol": rol,
                    "rol_txt": "Cámara" if rol == "camara" else "Sonido",
                    "turno": t,
                    "persona": per,
                })
            slots.append({"horario": h, "etiqueta": etiqueta_horario(h), "roles": cups})
        filas.append({
            "fecha": d,
            "dia": NOMBRES_DIA[d.weekday()],
            "festivo": festivo,
            "slots": slots,
        })
    rol_sel = request.query_params.get("rol", "")
    editando = (modo or "editar") != "foto"
    if not editando:
        fecha_sel = ""
        horario_sel = ""
        rol_sel = ""
        q = ""
    busqueda = _buscar_personas(db, q) if editando else []
    ids_mios = {t.persona_id for t in db.query(Turno).filter(Turno.delegacion == "sonido").all()}
    mis_cols = db.query(Persona).filter(Persona.id.in_(ids_mios)).all() if ids_mios else []
    cerrados = db.query(Turno).filter(Turno.delegacion == "sonido", Turno.semana_lunes < lunes).all()
    score_hora, score_anio = _scores(cerrados, lunes.year, fecha_sel, horario_sel)
    mis_cols.sort(key=lambda p: (-score_hora.get(p.id, 0), -score_anio.get(p.id, 0), (p.nombre or "").lower()))
    alertas = db.query(Alerta).filter(Alerta.para_delegacion == "sonido", Alerta.leida == False).order_by(Alerta.id.desc()).all()
    ausencias = db.query(AusenciaSemana).filter(
        AusenciaSemana.labor == "sonido", AusenciaSemana.semana_lunes == lunes,
    ).all()
    no_pueden = [personas_map.get(a.persona_id) or db.query(Persona).filter(Persona.id == a.persona_id).first() for a in ausencias]
    no_pueden = [p for p in no_pueden if p]
    posts = db.query(Postulacion).filter(Postulacion.labor == "sonido", Postulacion.semana_lunes == lunes).all()
    if posts and not personas_map:
        personas_map = {p.id: p for p in db.query(Persona).filter(Persona.id.in_({x.persona_id for x in posts})).all()}
    por_cupo = {}
    for p in posts:
        por_cupo.setdefault((p.fecha, p.horario, p.rol), []).append(personas_map.get(p.persona_id))
    postulados = []
    for clave, gente in por_cupo.items():
        fecha, horario, rol = clave
        nombres = [g for g in gente if g]
        nombres.sort(key=lambda g: (g.nombre or "").lower())
        asignado = ocupado.get((fecha, horario, rol))
        asignado_nom = personas_map.get(asignado.persona_id).nombre if asignado and personas_map.get(asignado.persona_id) else ""
        postulados.append({
            "fecha": fecha, "horario": horario, "rol": rol,
            "etiqueta": f"{NOMBRES_DIA[fecha.weekday()]} {fecha.day:02d} · {etiqueta_horario(horario)} · {'Cámara' if rol == 'camara' else 'Sonido'}",
            "gente": nombres,
            "lleno": (fecha, horario, rol) in ocupado,
            "asignado": asignado_nom,
        })
    postulados.sort(key=lambda x: (x["fecha"], x["horario"], x["rol"]))
    _rank, puesto = ranking_anio(db)
    return {
        "user": user, "alertas": alertas,
        "lunes": lunes, "domingo": dias[-1], "filas": filas,
        "q": q, "busqueda": busqueda, "mis_cols": mis_cols, "puesto": puesto,
        "fecha_sel": fecha_sel, "horario_sel": horario_sel, "rol_sel": rol_sel,
        "modo": "foto" if not editando else "editar",
        "error": error, "ok": ok,
        "busqueda_vacia": bool(q.strip()) and not busqueda,
        "no_pueden": no_pueden, "postulados": postulados,
    }


def vista_delegacion(request, db, user, delg, q, horario_sel, fecha_sel, modo, error, ok, confirmar, hl):
    generar_alertas_festivo(db)
    generar_alertas_domingo(db)
    lunes = semana_vigente()
    dias = dias_semana(lunes)
    turnos = db.query(Turno).filter(Turno.semana_lunes == lunes, Turno.delegacion != "sonido").all()
    personas_map = {p.id: p for p in db.query(Persona).all()} if turnos else {}
    ids_mios = {t.persona_id for t in db.query(Turno).filter(Turno.delegacion == delg).all()}
    mis_cols = db.query(Persona).filter(Persona.id.in_(ids_mios)).all() if ids_mios else []
    cerrados = db.query(Turno).filter(Turno.delegacion == delg, Turno.semana_lunes < lunes).all()
    score_hora, score_anio = _scores(cerrados, lunes.year, fecha_sel, horario_sel)
    mis_cols.sort(key=lambda p: (
        -score_hora.get(p.id, 0),
        -score_anio.get(p.id, 0),
        (p.nombre or "").lower(),
    ))
    busqueda = _buscar_personas(db, q)
    ya_llenos = set()
    if delg not in ("fimlm", "sonido"):
        conteo_semana = {}
        for t in turnos:
            conteo_semana[t.persona_id] = conteo_semana.get(t.persona_id, 0) + 1
        ya_llenos = {pid for pid, n in conteo_semana.items() if n >= 2}
        mis_cols = [p for p in mis_cols if p.id not in ya_llenos]
        busqueda_llena = [p for p in busqueda if p.id in ya_llenos]
        busqueda = [p for p in busqueda if p.id not in ya_llenos]
    else:
        busqueda_llena = []
    cfg = obtener_config(db)
    domingos = {r.fecha for r in db.query(DomingoPunto).all()}
    apagado = punto_apagado_para(cfg, domingos, delg, dias)
    por_dia = []
    for d in dias:
        if not dia_permitido(cfg, d, domingos, delg):
            continue
        slots = []
        for h in horarios_del_dia(d):
            gente = []
            for t in turnos:
                if t.fecha == d and t.horario == h:
                    per = personas_map.get(t.persona_id)
                    if per:
                        gente.append({"turno": t, "persona": per, "mio": t.delegacion == delg})
            reservado = any(g["turno"].delegacion == "fimlm" for g in gente)
            slots.append({
                "horario": h, "etiqueta": etiqueta_horario(h), "gente": gente,
                "reservado_fimlm": reservado and delg != "fimlm" and not cfg.convivencia_fimlm,
                "comparte_fimlm": reservado and delg != "fimlm" and cfg.convivencia_fimlm,
            })
        por_dia.append({
            "fecha": d,
            "nombre": NOMBRES_DIA[d.weekday()],
            "festivo": es_festivo(d) and d.weekday() < 5,
            "slots": slots,
            "tiene": any(s["gente"] for s in slots),
        })
    dias_foto = []
    for d in dias:
        slots = []
        for h in horarios_del_dia(d):
            gente = []
            for t in turnos:
                if t.fecha == d and t.horario == h:
                    per = personas_map.get(t.persona_id)
                    if per:
                        gente.append({"turno": t, "persona": per, "mio": t.delegacion == delg})
            slots.append({"horario": h, "etiqueta": etiqueta_horario(h), "gente": gente})
        habilitado = dia_permitido(cfg, d, domingos, "comunicaciones")
        hay_gente = any(s["gente"] for s in slots)
        if not habilitado and not hay_gente:
            continue
        dias_foto.append({
            "fecha": d,
            "nombre": NOMBRES_DIA[d.weekday()],
            "festivo": es_festivo(d) and d.weekday() < 5,
            "slots": slots,
        })
    presentes = []
    for dia in dias_foto:
        for s in dia["slots"]:
            for g in s["gente"]:
                ddel = g["turno"].delegacion
                if ddel not in presentes and ddel != "sonido":
                    presentes.append(ddel)
    siglas = {
        "comunicaciones": ("COM", "com"),
        "politica": ("POL", "pol"),
        "juventudes": ("JUV", "juv"),
        "electoral": ("ELE", "ele"),
        "fimlm": ("FIM", "fim"),
    }
    leyenda = []
    for ddel in presentes:
        texto = (getattr(cfg, f"labor_{ddel}", "") or "").strip()
        if not texto or ddel not in siglas:
            continue
        leyenda.append({"sigla": siglas[ddel][0], "clase": siglas[ddel][1], "texto": texto})
    leyenda_cols = [leyenda[i:i + 3] for i in range(0, len(leyenda), 3)]
    dias_vista = por_dia if modo != "foto" else [x for x in por_dia if x["tiene"]]
    alertas = db.query(Alerta).filter(Alerta.para_delegacion == delg, Alerta.leida == False).order_by(Alerta.id.desc()).all()
    _rank, puesto = ranking_anio(db)
    return {
        "user": user, "delg": delg, "es_fimlm": delg == "fimlm",
        "lunes": lunes, "domingo": dias[-1], "dias": dias_vista, "todos_dias": por_dia,
        "dias_foto": dias_foto,
        "leyenda": leyenda,
        "leyenda_cols": leyenda_cols,
        "mis_cols": mis_cols, "busqueda": busqueda, "q": q,
        "fecha_sel": fecha_sel, "horario_sel": horario_sel, "modo": modo or "editar",
        "error": error, "ok": ok, "alertas": alertas, "tutorial": not request.session.get("tut_prog"),
        "confirmar": confirmar, "hl": hl,
        "busqueda_vacia": bool(q.strip()) and not busqueda and not busqueda_llena,
        "busqueda_llena": busqueda_llena,
        "punto_apagado": apagado,
        "puesto": puesto,
        "convivencia": cfg.convivencia_fimlm,
    }
