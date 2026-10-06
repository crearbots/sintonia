from sqlalchemy import Column, Integer, String, DateTime, Boolean, Date, UniqueConstraint
from sqlalchemy.sql import func
from .database import Base


class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, index=True)
    usuario = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    delegacion = Column(String(40), nullable=False, default="comunicaciones")
    activo = Column(Boolean, default=True, nullable=False)


class Persona(Base):
    __tablename__ = "personas"

    id = Column(Integer, primary_key=True, index=True)
    celular = Column(String(15), unique=True, nullable=True, index=True)
    nombre = Column(String(150), nullable=False)
    estado = Column(String(20), nullable=False, default="Instalada")
    fuente_ultima = Column(String(100), nullable=True)
    fecha_listado = Column(Date, nullable=True)  # Fecha real del listado (elegida al subir)
    fecha_primera_carga = Column(DateTime(timezone=True), server_default=func.now())
    fecha_ultima_carga = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    pendiente_revision = Column(Boolean, default=False, nullable=False)
    motivo = Column(String(100), nullable=True)  # Solo para No instalada
    motivo_detalle = Column(String(255), nullable=True)  # Si motivo = Otro


class Carga(Base):
    __tablename__ = "cargas"

    id = Column(Integer, primary_key=True, index=True)
    nombre_archivo = Column(String(255), nullable=False)
    fuente = Column(String(100), nullable=False)
    fecha_listado = Column(Date, nullable=True)  # Fecha real del listado
    fecha_carga = Column(DateTime(timezone=True), server_default=func.now())
    total_registros = Column(Integer, default=0)
    nuevos = Column(Integer, default=0)
    actualizados = Column(Integer, default=0)
    pendientes = Column(Integer, default=0)
    cargado_por = Column(String(100), nullable=True)


class DatoNacional(Base):
    """Registro del número oficial reportado por la sede nacional."""
    __tablename__ = "datos_nacionales"

    id = Column(Integer, primary_key=True, index=True)
    fecha_dato = Column(Date, nullable=False)
    total_reportado = Column(Integer, nullable=False)
    nota = Column(String(255), nullable=True)
    fecha_registro = Column(DateTime(timezone=True), server_default=func.now())


class Turno(Base):
    __tablename__ = "turnos"

    id = Column(Integer, primary_key=True, index=True)
    persona_id = Column(Integer, nullable=False, index=True)
    fecha = Column(Date, nullable=False, index=True)
    horario = Column(String(5), nullable=False)
    delegacion = Column(String(40), nullable=False)
    semana_lunes = Column(Date, nullable=False, index=True)
    rol = Column(String(20), nullable=True)
    creado_en = Column(DateTime(timezone=True), server_default=func.now())


class Alerta(Base):
    __tablename__ = "alertas"

    id = Column(Integer, primary_key=True, index=True)
    para_delegacion = Column(String(40), nullable=False, index=True)
    tipo = Column(String(40), nullable=False)
    texto = Column(String(400), nullable=False)
    leida = Column(Boolean, default=False, nullable=False)
    creado_en = Column(DateTime(timezone=True), server_default=func.now())


class ConfigPunto(Base):
    __tablename__ = "config_punto"

    id = Column(Integer, primary_key=True)
    vigencia_inicio = Column(Date, nullable=True)
    vigencia_fin = Column(Date, nullable=True)
    lun = Column(Boolean, default=True, nullable=False)
    mar = Column(Boolean, default=True, nullable=False)
    mie = Column(Boolean, default=True, nullable=False)
    jue = Column(Boolean, default=True, nullable=False)
    vie = Column(Boolean, default=True, nullable=False)
    sab = Column(Boolean, default=True, nullable=False)


class DomingoPunto(Base):
    __tablename__ = "domingos_punto"

    id = Column(Integer, primary_key=True)
    fecha = Column(Date, unique=True, nullable=False)


class ClaveEnlace(Base):
    """Clave corta del enlace público. El enlace no cambia; la clave sí."""

    __tablename__ = "claves_enlace"

    id = Column(Integer, primary_key=True)
    labor = Column(String(40), nullable=False, default="sonido")
    mes = Column(String(7), nullable=False)
    clave = Column(String(8), nullable=False)
    token = Column(String(40), nullable=False)
    plantilla = Column(String(2000), nullable=True)
    actualizada_en = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("labor", "mes", name="uq_clave_labor_mes"),)


class Postulacion(Base):
    """Disponibilidad. No ocupa el cupo: el turno nace cuando el coordinador confirma."""

    __tablename__ = "postulaciones"

    id = Column(Integer, primary_key=True)
    persona_id = Column(Integer, nullable=False, index=True)
    labor = Column(String(40), nullable=False, default="sonido")
    fecha = Column(Date, nullable=False, index=True)
    horario = Column(String(5), nullable=False)
    rol = Column(String(20), nullable=False, default="sonido")
    semana_lunes = Column(Date, nullable=False, index=True)
    creado_en = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "persona_id", "labor", "fecha", "horario", "rol",
            name="uq_postulacion_hueco",
        ),
    )


class AusenciaSemana(Base):
    """El colaborador avisa que esa semana no puede. No marca horarios."""

    __tablename__ = "ausencias_semana"

    id = Column(Integer, primary_key=True)
    persona_id = Column(Integer, nullable=False, index=True)
    labor = Column(String(40), nullable=False, default="sonido")
    semana_lunes = Column(Date, nullable=False, index=True)
    creado_en = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("persona_id", "labor", "semana_lunes", name="uq_ausencia_semana"),
    )
