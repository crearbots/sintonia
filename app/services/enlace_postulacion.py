"""Vista y guardado del enlace público de postulación (Sonido y Baños)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from urllib.parse import quote

from fastapi import Request
from sqlalchemy.orm import Session

from ..festivos import es_festivo
from ..models import Alerta, AusenciaSemana, CambioPostulacion, Persona, Postulacion
from ..postulacion import (
    aplicar_semana,
    asegurar_equipo,
    asegurar_equipo_banos,
    clave_valida,
    enlace_abierto,
    enlace_abrir,
    enlace_bloqueado,
    enlace_fallo,
    enlace_persona,
    enlace_persona_set,
    enlace_valido,
    postulaciones_persona,
    tiene_semana,
)
from ..processing import normalizar_celular, normalizar_nombre
from ..punto import (
    NOMBRES_DIA,
    dias_semana,
    etiqueta_horario,
    horarios_del_dia,
    postulacion_tarde,
    semana_para_labor,
)

_MESES_CORTO = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")

_CONFIG = {
    "sonido": {
        "path": "/postular",
        "template": "postular.html",
        "fuente_alta": "Postulación Sonido",
        "texto_cambio": "pidió cambiar su postulación.",
    },
    "banos": {
        "path": "/postular-banos",
        "template": "postular.html",
        "fuente_alta": "Postulación Baños",
        "texto_cambio": "pidió cambiar su postulación de Baños.",
    },
}


def meta_plantilla_postular(labor: str) -> dict:
    nombre = "Sonido" if labor == "sonido" else "Baños"
    return {
        "labor": labor,
        "tiene_rol": labor == "sonido",
        "labor_nombre": nombre,
        "coordinador_etiqueta": "coordinador" if labor == "sonido" else "coordinadora",
    }


def path_enlace(labor: str) -> str:
    return _CONFIG[labor]["path"]


def template_enlace(labor: str) -> str:
    return _CONFIG[labor]["template"]


def etiqueta_rol(rol: str) -> str:
    if rol == "camara":
        return "Cámara"
    if rol == "banos":
        return "Baños"
    return "Sonido"


def linea_hueco(fecha: date, horario: str, rol: str, *, con_rol: bool = True) -> str:
    mes = _MESES_CORTO[fecha.month - 1].capitalize()
    base = (
        f"{NOMBRES_DIA[fecha.weekday()]} {fecha.day:02d}/{mes} · "
        f"{etiqueta_horario(horario)}"
    )
    if con_rol:
        base += f" · {etiqueta_rol(rol)}"
    return base


def lineas_detalle_cambio(detalle: str, labor: str = "sonido") -> list[str]:
    """Líneas para panel / resumen (Sonido incluye rol; Baños no)."""
    con_rol = labor != "banos"
    lineas = []
    for parte in (detalle or "").split(";"):
        if not parte or parte == "no-puede":
            continue
        partes = parte.split("|")
        f, h = partes[0], partes[1]
        rol = partes[2] if len(partes) > 2 else labor
        fecha = datetime.strptime(f, "%Y-%m-%d").date()
        lineas.append(linea_hueco(fecha, h, rol, con_rol=con_rol))
    lineas.sort()
    return lineas


def texto_huecos(huecos, labor: str = "sonido") -> str:
    if not huecos:
        return "sin horarios"
    con_rol = labor != "banos"
    return ", ".join(linea_hueco(f, h, rol, con_rol=con_rol) for f, h, rol in huecos)


def texto_vigente(db: Session, persona_id: int, lunes: date, labor: str = "sonido") -> str:
    if db.query(AusenciaSemana).filter(
        AusenciaSemana.persona_id == persona_id,
        AusenciaSemana.labor == labor,
        AusenciaSemana.semana_lunes == lunes,
    ).first():
        return "no podía apoyar esta semana"
    filas = postulaciones_persona(db, persona_id, lunes, labor)
    if not filas:
        return "sin horarios"
    con_rol = labor != "banos"
    return ", ".join(
        linea_hueco(p.fecha, p.horario, p.rol, con_rol=con_rol)
        for p in sorted(filas, key=lambda p: (p.fecha, p.horario, p.rol))
    )


def _linea_postulacion_persona(p: Postulacion, labor: str) -> str:
    con_rol = labor != "banos"
    return linea_hueco(p.fecha, p.horario, p.rol, con_rol=con_rol)


def _clave_marcado(p: Postulacion, labor: str) -> str:
    if labor == "banos":
        return f"{p.fecha.isoformat()}|{p.horario}"
    return f"{p.fecha.isoformat()}|{p.horario}|{p.rol}"


def construir_ctx_postular(
    db: Session,
    request: Request,
    labor: str,
    token: str,
    *,
    error: str = "",
    ok: str = "",
    cambiar: str = "",
    tarde: str = "",
) -> dict | None:
    """Contexto Jinja del formulario. None si el token no es válido."""
    if not enlace_valido(db, token, labor):
        return None
    abierto = enlace_abierto(request, token)
    ctx = {
        "invalido": False,
        "token": token,
        "abierto": abierto,
        "error": error,
        "ok": ok,
        "filas": [],
        "no_puede": False,
        "resumen": False,
        "lineas": [],
        "tarde": tarde == "1",
        "cambio_antes": None,
        "cambio_ahora": [],
        "cambio_no_puede": False,
        **meta_plantilla_postular(labor),
    }
    if labor == "sonido":
        ctx["mios"] = set()
    if not abierto:
        return ctx
    lunes = semana_para_labor(labor)
    dias = dias_semana(lunes)
    q = db.query(Postulacion).filter(Postulacion.labor == labor, Postulacion.semana_lunes == lunes)
    if labor == "banos":
        ocupados = {(p.fecha, p.horario) for p in q.all()}
    else:
        ocupados = {(p.fecha, p.horario, p.rol) for p in q.all()}
    pid = enlace_persona(request) if ok and not cambiar else None
    mios: set[str] = set()
    lineas: list[str] = []
    no_puede = False
    if pid:
        for p in postulaciones_persona(db, pid, lunes, labor):
            mios.add(_clave_marcado(p, labor))
            lineas.append(_linea_postulacion_persona(p, labor))
        lineas.sort()
        no_puede = db.query(AusenciaSemana).filter(
            AusenciaSemana.persona_id == pid,
            AusenciaSemana.labor == labor,
            AusenciaSemana.semana_lunes == lunes,
        ).first() is not None
        cambio = db.query(CambioPostulacion).filter(
            CambioPostulacion.persona_id == pid,
            CambioPostulacion.labor == labor,
            CambioPostulacion.semana_lunes == lunes,
            CambioPostulacion.estado == "pendiente",
        ).first()
        if cambio and ok and not cambiar:
            ctx["cambio_antes"] = list(lineas) if not no_puede else ["Esta semana no puedes apoyar."]
            ctx["cambio_no_puede"] = cambio.no_puede
            ctx["cambio_ahora"] = (
                lineas_detalle_cambio(cambio.detalle, labor) if not cambio.no_puede else []
            )
    filas = []
    for d in dias:
        if labor == "banos":
            slots = []
            for h in horarios_del_dia(d):
                valor = f"{d.isoformat()}|{h}"
                slots.append({
                    "horario": h,
                    "etiqueta": etiqueta_horario(h),
                    "valor": valor,
                    "libre": (d, h) not in ocupados,
                    "marcado": valor in mios,
                })
            filas.append({
                "fecha": f"{d.day:02d}/{_MESES_CORTO[d.month - 1].capitalize()}",
                "dia": NOMBRES_DIA[d.weekday()],
                "festivo": es_festivo(d) and d.weekday() < 5,
                "libre": any(s["libre"] for s in slots),
                "slots": slots,
            })
        else:
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
                "fecha": f"{d.day:02d}/{_MESES_CORTO[d.month - 1].capitalize()}",
                "festivo": es_festivo(d) and d.weekday() < 5,
                "libre": any(c["libre"] for s in slots for c in s["roles"]),
                "slots": slots,
            })
    ctx.update({
        "filas": filas,
        "no_puede": no_puede,
        "lineas": lineas,
        "resumen": bool(ok) and not cambiar,
    })
    if labor == "sonido":
        ctx["mios"] = mios
    return ctx


def revisar_celular_enlace(
    db: Session,
    request: Request,
    labor: str,
    token: str,
    celular: str,
    celular2: str,
) -> dict:
    if not enlace_valido(db, token, labor) or not enlace_abierto(request, token):
        return {"ok": False, "error": "Primero escribe la clave"}
    cel = normalizar_celular(celular)
    cel2 = normalizar_celular(celular2)
    if not cel or cel != cel2:
        return {"ok": False, "error": "Los dos celulares deben coincidir y tener 10 dígitos"}
    per = db.query(Persona).filter(Persona.celular == cel).first()
    mios: list[str] = []
    no_puede = False
    if per:
        lunes = semana_para_labor(labor)
        no_puede = db.query(AusenciaSemana).filter(
            AusenciaSemana.persona_id == per.id,
            AusenciaSemana.labor == labor,
            AusenciaSemana.semana_lunes == lunes,
        ).first() is not None
        mios = [
            _clave_marcado(p, labor)
            for p in db.query(Postulacion).filter(
                Postulacion.persona_id == per.id,
                Postulacion.labor == labor,
                Postulacion.semana_lunes == lunes,
            ).all()
        ]
    return {
        "ok": True,
        "existe": bool(per),
        "nombre": per.nombre if per else "",
        "mios": mios,
        "no_puede": no_puede,
    }


def _parse_huecos_form(form, lunes: date, labor: str) -> list[tuple[date, str, str]]:
    huecos = []
    if labor == "banos":
        for valor in form.getlist("hueco"):
            try:
                f, h = valor.split("|")
                fecha = datetime.strptime(f, "%Y-%m-%d").date()
                if not (lunes <= fecha <= lunes + timedelta(days=6)):
                    continue
                if h not in horarios_del_dia(fecha):
                    continue
                huecos.append((fecha, h, "banos"))
            except ValueError:
                continue
    else:
        for valor in form.getlist("hueco"):
            try:
                f, h, rol = valor.split("|")
                fecha = datetime.strptime(f, "%Y-%m-%d").date()
                if not (lunes <= fecha <= lunes + timedelta(days=6)) or rol not in ("sonido", "camara"):
                    continue
                if h not in horarios_del_dia(fecha):
                    continue
                huecos.append((fecha, h, rol))
            except ValueError:
                continue
    return huecos


def _extra_tarde_param() -> str:
    return "&tarde=1" if postulacion_tarde() else ""


def guardar_postulacion_enlace(
    request: Request,
    db: Session,
    labor: str,
    token: str,
    form,
    *,
    clave: str,
    celular: str,
    celular2: str,
    nombre: str,
    no_puedo: str,
) -> str:
    """URL de redirección tras POST del enlace."""
    base = path_enlace(labor)
    cfg = _CONFIG[labor]

    row = enlace_valido(db, token, labor)
    if not row:
        return f"{base}/{token}"

    if form.get("paso") == "clave":
        if enlace_bloqueado(request):
            return f"{base}/{token}?error=Demasiados+intentos.+Espera+15+minutos"
        if not clave_valida(db, clave, labor):
            if enlace_fallo(request):
                return f"{base}/{token}?error=Demasiados+intentos.+Espera+15+minutos"
            return f"{base}/{token}?error=Clave+incorrecta"
        enlace_abrir(request, token)
        return f"{base}/{token}"

    if form.get("paso") != "guardar":
        return f"{base}/{token}?cambiar=1"

    if not enlace_abierto(request, token):
        return f"{base}/{token}?error=Primero+escribe+la+clave"

    cel = normalizar_celular(celular)
    cel2 = normalizar_celular(celular2)
    if not cel or cel != cel2:
        return f"{base}/{token}?error=Los+dos+celulares+deben+coincidir+y+tener+10+dígitos"

    per = db.query(Persona).filter(Persona.celular == cel).first()
    if not per:
        nom = normalizar_nombre(nombre)
        if not nom:
            return f"{base}/{token}?error=No+estás+en+la+base.+Escribe+tu+nombre"
        nombre_eq = "Baños" if labor == "banos" else "Sonido"
        per = Persona(
            nombre=nom,
            celular=cel,
            estado="No instalada",
            pendiente_revision=False,
            fuente_ultima=cfg["fuente_alta"],
        )
        db.add(per)
        db.commit()
        db.refresh(per)
        db.add(Alerta(
            para_delegacion="comunicaciones",
            tipo="alta_nueva",
            texto=f"Persona nueva desde el enlace de {nombre_eq}: {nom} ({cel}). Revisar si ya tiene la App.",
            leida=False,
        ))
        db.commit()

    if labor == "banos":
        asegurar_equipo_banos(db, per.id)
    else:
        asegurar_equipo(db, per.id)
    db.commit()
    enlace_persona_set(request, per.id)

    lunes = semana_para_labor(labor)
    extra_tarde = _extra_tarde_param()
    huecos = [] if no_puedo else _parse_huecos_form(form, lunes, labor)

    if not no_puedo and not huecos:
        return (
            f"{base}/{token}?cambiar=1&error=Marca+al+menos+un+horario.+"
            "Si+no+puedes+esta+semana,+marca+esa+opción"
        )

    if tiene_semana(db, per.id, lunes, labor):
        ya_no = db.query(AusenciaSemana).filter(
            AusenciaSemana.persona_id == per.id,
            AusenciaSemana.labor == labor,
            AusenciaSemana.semana_lunes == lunes,
        ).first() is not None
        actuales = {
            (p.fecha, p.horario, p.rol)
            for p in postulaciones_persona(db, per.id, lunes, labor)
        }
        iguales = (bool(no_puedo) and ya_no) or (not no_puedo and not ya_no and set(huecos) == actuales)
        if iguales:
            cambio = db.query(CambioPostulacion).filter(
                CambioPostulacion.persona_id == per.id,
                CambioPostulacion.labor == labor,
                CambioPostulacion.semana_lunes == lunes,
                CambioPostulacion.estado == "pendiente",
            ).first()
            if cambio:
                db.query(Alerta).filter(Alerta.tipo == f"cambio-{cambio.id}").update({"leida": True})
                cambio.estado = "sin-cambio"
                db.commit()
            msg = quote("Solicitaste los mismos horarios de antes, así que no hay cambios en tu postulación")
            return f"{base}/{token}?ok={msg}{extra_tarde}"

        if no_puedo:
            detalle = "no-puede"
        elif labor == "banos":
            detalle = ";".join(f"{f.isoformat()}|{h}|banos" for f, h, _ in huecos)
        else:
            detalle = ";".join(f"{f.isoformat()}|{h}|{rol}" for f, h, rol in huecos)

        cambio = db.query(CambioPostulacion).filter(
            CambioPostulacion.persona_id == per.id,
            CambioPostulacion.labor == labor,
            CambioPostulacion.semana_lunes == lunes,
            CambioPostulacion.estado == "pendiente",
        ).first()
        if not cambio:
            cambio = CambioPostulacion(persona_id=per.id, labor=labor, semana_lunes=lunes)
            db.add(cambio)
        cambio.no_puede = bool(no_puedo)
        cambio.detalle = detalle
        db.commit()
        db.refresh(cambio)

        antes = texto_vigente(db, per.id, lunes, labor)
        ahora = "esta semana no puede apoyar" if no_puedo else texto_huecos(huecos, labor)
        texto = f"{per.nombre} {cfg['texto_cambio']} Antes: {antes}. Ahora: {ahora}."
        tipo = f"cambio-{cambio.id}"
        alerta = db.query(Alerta).filter(
            Alerta.para_delegacion == labor, Alerta.tipo == tipo,
        ).first()
        if alerta:
            alerta.texto = texto
            alerta.leida = False
        else:
            db.add(Alerta(para_delegacion=labor, tipo=tipo, leida=False, texto=texto))
        db.commit()
        msg = quote("Cambio enviado. El coordinador lo confirma.")
        if labor == "sonido":
            return f"{base}/{token}?ok={msg}{extra_tarde}"
        return f"{base}/{token}?ok={msg}"

    aplicar_semana(db, per.id, lunes, huecos, bool(no_puedo), labor)
    msg = (
        "Quedó guardado. Esta semana no puedes apoyar"
        if no_puedo
        else "Quedó guardado. No necesitas enviarlo otra vez"
    )
    return f"{base}/{token}?ok={quote(msg)}{extra_tarde}"