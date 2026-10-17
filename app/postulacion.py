"""Reglas del enlace de postulación. Sin pantallas: las usa la fase 2."""

import secrets
from datetime import date

from sqlalchemy.orm import Session

from .models import ClaveEnlace, Postulacion
from .punto import hoy_colombia, lunes_de


def mes_vigente(hoy: date | None = None) -> str:
    hoy = hoy or hoy_colombia()
    return f"{hoy.year:04d}-{hoy.month:02d}"


def _clave_corta() -> str:
    return f"{secrets.randbelow(10000):04d}"


def _token() -> str:
    return secrets.token_urlsafe(9)


PLANTILLA_BASE = (
    "Hola, equipo de Sonido.\n"
    "Para esta semana pueden postularse en este enlace:\n"
    "{enlace}\n\n"
    "Clave: {clave}\n\n"
    "Entran, escriben su celular dos veces y marcan los horarios en los que pueden. "
    "Si esta semana no pueden apoyar, también lo indican ahí."
)


def texto_plantilla(row: ClaveEnlace, enlace: str) -> str:
    base = (row.plantilla or "").strip() or PLANTILLA_BASE
    return base.replace("{enlace}", enlace).replace("{clave}", row.clave)


def guardar_plantilla(db: Session, texto: str, enlace: str, labor: str = "sonido") -> ClaveEnlace:
    row = clave_vigente(db, labor)
    limpio = (texto or "").strip()
    limpio = limpio.replace(enlace, "{enlace}").replace(row.clave, "{clave}")
    row.plantilla = limpio[:2000] if limpio else None
    db.commit()
    db.refresh(row)
    return row


def clave_vigente(db: Session, labor: str = "sonido", hoy: date | None = None) -> ClaveEnlace:
    """Una clave por labor y mes. El token del enlace no cambia."""
    mes = mes_vigente(hoy)
    row = db.query(ClaveEnlace).filter(ClaveEnlace.labor == labor, ClaveEnlace.mes == mes).first()
    if row:
        return row
    anterior = (
        db.query(ClaveEnlace)
        .filter(ClaveEnlace.labor == labor)
        .order_by(ClaveEnlace.mes.desc())
        .first()
    )
    row = ClaveEnlace(
        labor=labor,
        mes=mes,
        clave=_clave_corta(),
        token=anterior.token if anterior else _token(),
        plantilla=anterior.plantilla if anterior else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def regenerar_clave(db: Session, labor: str = "sonido") -> ClaveEnlace:
    row = clave_vigente(db, labor)
    row.clave = _clave_corta()
    db.commit()
    db.refresh(row)
    return row


def clave_valida(db: Session, clave: str, labor: str = "sonido") -> bool:
    row = clave_vigente(db, labor)
    return (clave or "").strip() == row.clave


def marcar_postulacion(
    db: Session, persona_id: int, fecha: date, horario: str, rol: str, labor: str = "sonido",
) -> Postulacion:
    """Una sola fila por persona y hueco. Si ya existe, se deja esa."""
    lunes = lunes_de(fecha)
    row = db.query(Postulacion).filter(
        Postulacion.persona_id == persona_id,
        Postulacion.labor == labor,
        Postulacion.fecha == fecha,
        Postulacion.horario == horario,
        Postulacion.rol == rol,
    ).first()
    if row:
        return row
    row = Postulacion(
        persona_id=persona_id, labor=labor, fecha=fecha, horario=horario,
        rol=rol, semana_lunes=lunes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def quitar_postulacion(
    db: Session, persona_id: int, fecha: date, horario: str, rol: str, labor: str = "sonido",
) -> None:
    db.query(Postulacion).filter(
        Postulacion.persona_id == persona_id,
        Postulacion.labor == labor,
        Postulacion.fecha == fecha,
        Postulacion.horario == horario,
        Postulacion.rol == rol,
    ).delete()
    db.commit()


def postulaciones_persona(db: Session, persona_id: int, lunes: date, labor: str = "sonido") -> list[Postulacion]:
    return db.query(Postulacion).filter(
        Postulacion.persona_id == persona_id,
        Postulacion.labor == labor,
        Postulacion.semana_lunes == lunes,
    ).all()


def asegurar_equipo(db: Session, persona_id: int, hoy: date | None = None):
    """Entra a la lista. Si estaba dado de baja, vuelve a contar desde hoy."""
    from .models import EquipoSonido
    hoy = hoy or hoy_colombia()
    row = db.query(EquipoSonido).filter(EquipoSonido.persona_id == persona_id).first()
    if row:
        if not row.activo:
            row.activo = True
            row.desde = hoy
        return row
    row = EquipoSonido(persona_id=persona_id, desde=hoy, activo=True)
    db.add(row)
    return row


def sembrar_equipo(db: Session) -> None:
    """Quien ya diligenció el enlace entra a la lista. El conteo empieza hoy."""
    from .models import AusenciaSemana, EquipoSonido, Postulacion
    hoy = hoy_colombia()
    ids = {p.persona_id for p in db.query(Postulacion.persona_id).filter(Postulacion.labor == "sonido").all()}
    ids |= {a.persona_id for a in db.query(AusenciaSemana.persona_id).filter(AusenciaSemana.labor == "sonido").all()}
    ya = {r.persona_id for r in db.query(EquipoSonido.persona_id).all()}
    for pid in ids - ya:
        db.add(EquipoSonido(persona_id=pid, desde=hoy, activo=True))
    if ids - ya:
        db.commit()


def semanas_en_silencio(db: Session, persona_id: int, desde: date, abierta: date) -> int:
    """Semanas cerradas seguidas sin postular ni decir que no puede."""
    from datetime import timedelta
    from .models import AusenciaSemana, Postulacion
    if not desde:
        return 0
    lunes = abierta - timedelta(days=7)
    n = 0
    while lunes >= desde and n < 52:
        respondio = db.query(Postulacion.id).filter(
            Postulacion.persona_id == persona_id,
            Postulacion.labor == "sonido",
            Postulacion.semana_lunes == lunes,
        ).first() or db.query(AusenciaSemana.id).filter(
            AusenciaSemana.persona_id == persona_id,
            AusenciaSemana.labor == "sonido",
            AusenciaSemana.semana_lunes == lunes,
        ).first()
        if respondio:
            break
        n += 1
        lunes -= timedelta(days=7)
    return n


def generar_alertas_silencio(db: Session) -> None:
    """Lunes a viernes: una alerta a las 4 semanas cerradas y otra a las 8."""
    from datetime import timedelta
    from .models import Alerta, EquipoSonido, Persona
    hoy = hoy_colombia()
    if hoy.weekday() >= 5:
        return
    abierta = lunes_de(hoy)
    for row in db.query(EquipoSonido).filter(EquipoSonido.activo == True).all():
        n = semanas_en_silencio(db, row.persona_id, row.desde, abierta)
        for umbral in (4, 8):
            if n < umbral:
                continue
            cruce = abierta - timedelta(days=7 * umbral)
            tipo = f"silencio|{umbral}|{row.persona_id}|{cruce.isoformat()}"
            if db.query(Alerta.id).filter(Alerta.tipo == tipo).first():
                continue
            per = db.query(Persona).filter(Persona.id == row.persona_id).first()
            nombre = per.nombre if per else "Un colaborador"
            db.add(Alerta(
                para_delegacion="sonido",
                tipo=tipo,
                texto=f"{nombre} lleva {umbral} semanas sin diligenciar el formulario de Sonido.",
                leida=False,
            ))
    db.commit()


def tiene_semana(db: Session, persona_id: int, lunes: date, labor: str = "sonido") -> bool:
    from .models import AusenciaSemana
    if postulaciones_persona(db, persona_id, lunes, labor):
        return True
    return db.query(AusenciaSemana).filter(
        AusenciaSemana.persona_id == persona_id,
        AusenciaSemana.labor == labor,
        AusenciaSemana.semana_lunes == lunes,
    ).first() is not None


def aplicar_semana(
    db: Session, persona_id: int, lunes: date, huecos: list[tuple[date, str, str]],
    no_puede: bool, labor: str = "sonido",
) -> None:
    """Deja la semana como la persona la marcó. Una fila por hueco, sin duplicar."""
    from .models import AusenciaSemana
    db.query(Postulacion).filter(
        Postulacion.persona_id == persona_id,
        Postulacion.labor == labor,
        Postulacion.semana_lunes == lunes,
    ).delete()
    db.query(AusenciaSemana).filter(
        AusenciaSemana.persona_id == persona_id,
        AusenciaSemana.labor == labor,
        AusenciaSemana.semana_lunes == lunes,
    ).delete()
    if no_puede:
        db.add(AusenciaSemana(persona_id=persona_id, labor=labor, semana_lunes=lunes))
    else:
        for fecha, horario, rol in huecos:
            db.add(Postulacion(
                persona_id=persona_id, labor=labor, fecha=fecha, horario=horario,
                rol=rol, semana_lunes=lunes,
            ))
    db.commit()
