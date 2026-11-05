from datetime import date, datetime, timedelta, timezone

from .festivos import es_festivo

TZ_CO = timezone(timedelta(hours=-5))
NOMBRES_DIA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def hoy_colombia() -> date:
    return datetime.now(TZ_CO).date()


def lunes_de(d: date) -> date:
    return d - timedelta(days=d.weekday())


def semana_vigente(hoy: date | None = None) -> date:
    """Sábado o domingo: se edita la semana siguiente."""
    hoy = hoy or hoy_colombia()
    if hoy.weekday() >= 5:
        return lunes_de(hoy + timedelta(days=7))
    return lunes_de(hoy)


def dias_semana(lunes: date) -> list[date]:
    return [lunes + timedelta(days=i) for i in range(7)]


def horarios_del_dia(d: date) -> list[str]:
    wd = d.weekday()
    if wd == 5:
        return ["17:00"]
    if wd == 6:
        return ["08:00", "11:00"]
    if es_festivo(d):
        return ["08:00", "17:00"]
    return ["07:00", "18:00"]


def etiqueta_horario(h: str) -> str:
    return {"07:00": "7:00 a.m.", "08:00": "8:00 a.m.", "11:00": "11:00 a.m.",
            "17:00": "5:00 p.m.", "18:00": "6:00 p.m."}.get(h, h)


DIAS_CFG = ("lun", "mar", "mie", "jue", "vie", "sab")


def obtener_config(db):
    from .models import ConfigPunto
    cfg = db.query(ConfigPunto).first()
    if not cfg:
        cfg = ConfigPunto(
            vigencia_inicio=date(2026, 9, 28),
            vigencia_fin=date(2027, 10, 31),
            lun=True, mar=True, mie=True, jue=True, vie=True, sab=True,
        )
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def dia_permitido(cfg, fecha: date, domingos: set, delegacion: str) -> bool:
    if delegacion == "fimlm":
        return True
    if cfg.vigencia_inicio and fecha < cfg.vigencia_inicio:
        return False
    if cfg.vigencia_fin and fecha > cfg.vigencia_fin:
        return False
    wd = fecha.weekday()
    if wd == 6:
        return fecha in domingos
    flags = [cfg.lun, cfg.mar, cfg.mie, cfg.jue, cfg.vie, cfg.sab]
    return bool(flags[wd])


def punto_apagado_para(cfg, domingos: set, delegacion: str, dias: list) -> bool:
    if delegacion == "fimlm":
        return False
    return not any(dia_permitido(cfg, d, domingos, delegacion) for d in dias)



MESES_ES = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]


def generar_alertas_festivo(db) -> None:
    from .auth import DELEGACIONES
    from .models import Alerta
    lunes = semana_vigente()
    for d in dias_semana(lunes):
        if not (es_festivo(d) and d.weekday() < 5):
            continue
        tipo = f"festivo-{d.isoformat()}"
        dia_txt = f"{NOMBRES_DIA[d.weekday()].lower()} {d.day} de {MESES_ES[d.month-1]}"
        texto = (
            f"El {dia_txt} es festivo. Avisa a tus colaboradores: "
            "ese día el horario es 8:00 a.m. y 5:00 p.m."
        )
        for delg in DELEGACIONES:
            existe = db.query(Alerta).filter(Alerta.para_delegacion == delg, Alerta.tipo == tipo).first()
            if not existe:
                db.add(Alerta(para_delegacion=delg, tipo=tipo, texto=texto, leida=False))
    db.commit()



def ranking_anio(db, anio: int | None = None):
    """Turnos de semanas cerradas en el año calendario. Lista (persona, total, por_delegacion)."""
    from collections import defaultdict

    from .models import Persona, Turno
    lunes = semana_vigente()
    anio = anio or lunes.year
    filas = db.query(Turno).filter(Turno.semana_lunes < lunes).all()
    tot = defaultdict(int)
    por = defaultdict(lambda: defaultdict(int))
    for tno in filas:
        if tno.fecha.year != anio:
            continue
        tot[tno.persona_id] += 1
        por[tno.persona_id][tno.delegacion] += 1
    orden = sorted(tot.items(), key=lambda x: (-x[1], x[0]))
    ids = [i for i, _ in orden[:30]]
    personas = {p.id: p for p in db.query(Persona).filter(Persona.id.in_(ids)).all()} if ids else {}
    ranking = []
    puesto = {}
    for i, (pid, n) in enumerate(orden[:30], start=1):
        per = personas.get(pid)
        if not per:
            continue
        ranking.append({
            "puesto": i,
            "persona": per,
            "total": n,
            "por": dict(por[pid]),
        })
        puesto[pid] = i
    return ranking, puesto



def generar_alertas_domingo(db) -> None:
    """Solo el sábado: recordar el domingo de la semana que se está armando."""
    from .auth import DELEGACIONES
    from .models import Alerta, DomingoPunto
    if hoy_colombia().weekday() != 5:
        return
    lunes = semana_vigente()
    domingo = lunes + __import__("datetime").timedelta(days=6)
    if not db.query(DomingoPunto).filter(DomingoPunto.fecha == domingo).first():
        return
    tipo = f"domingo-semana-{domingo.isoformat()}"
    texto = (
        f"Atención equipos: el domingo {domingo.day} de {MESES_ES[domingo.month-1]} "
        "el punto de información estará activo. Convocá a la mayor cantidad "
        "de colaboradores de tu delegación; contamos con su apoyo."
    )
    for delg in DELEGACIONES:
        if delg == "fimlm":
            continue
        ya = db.query(Alerta).filter(
            Alerta.para_delegacion == delg,
            Alerta.tipo.in_([tipo, f"domingo-{domingo.isoformat()}"]),
        ).first()
        if not ya:
            db.add(Alerta(para_delegacion=delg, tipo=tipo, texto=texto, leida=False))
    db.commit()
