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
