from fastapi import FastAPI, Request, Depends, Form, status, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from jinja2 import Environment, FileSystemLoader, select_autoescape
import secrets
import os
import tempfile
from pathlib import Path

from .database import engine, get_db, Base
from .models import Usuario, Persona, Carga, DatoNacional, Turno, Alerta, ConfigPunto, DomingoPunto, ClaveEnlace, Postulacion, AusenciaSemana
from .auth import (
    create_default_user,
    authenticate_user,
    DEFAULT_USER,
    es_comunicaciones,
    ruta_solo_com,
    DELEGACIONES,
    hash_password,
)
from .punto import semana_vigente, dias_semana, horarios_del_dia, etiqueta_horario, NOMBRES_DIA, obtener_config, dia_permitido, punto_apagado_para, generar_alertas_festivo, generar_alertas_domingo, ranking_anio
from .festivos import es_festivo
from .processing import procesar_excel, FUENTES_VALIDAS, FUENTE_PRESENCIAL_ANTERIOR, nombre_fuente, EQUIPOS_CARGA, MOTIVOS_NO_INSTALADA

# Ruta base del proyecto
BASE_DIR = Path(__file__).resolve().parent.parent

# Crear tablas
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Sintonía", docs_url=None, redoc_url=None)

# Middleware de sesión (clave fija desde variable de entorno en producción)
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Archivos estáticos (crea la carpeta si no existe)
static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Plantillas
from jinja2.utils import htmlsafe_json_dumps
import json

jinja_env = Environment(
    loader=FileSystemLoader(str(BASE_DIR / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
    cache_size=0
)
# Asegurar filtro tojson
jinja_env.filters["fuente"] = nombre_fuente
_MESES = ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")

def _fecha_dia(d):
    return f"{d.day:02d}/{_MESES[d.month - 1]}"

def _rango_semana(lunes, domingo):
    return f"{_fecha_dia(lunes)} – {domingo.day:02d}/{_MESES[domingo.month - 1]}/{domingo.year}"

jinja_env.filters["fecha_dia"] = _fecha_dia
jinja_env.filters["rango_semana"] = _rango_semana
templates = Jinja2Templates(env=jinja_env)


@app.on_event("startup")
def on_startup():
    # Crear tablas nuevas si no existen
    Base.metadata.create_all(bind=engine)
    # Migración simple SQLite: columna cargado_por en cargas
    try:
        from sqlalchemy import text as sql_text
        with engine.connect() as conn:
            cols = [r[1] for r in conn.execute(sql_text("PRAGMA table_info(cargas)")).fetchall()]
            if "cargado_por" not in cols:
                conn.execute(sql_text("ALTER TABLE cargas ADD COLUMN cargado_por VARCHAR(100)"))
                conn.commit()
                print("Columna cargas.cargado_por agregada")
            pcols = [r[1] for r in conn.execute(sql_text("PRAGMA table_info(personas)")).fetchall()]
            if "motivo" not in pcols:
                conn.execute(sql_text("ALTER TABLE personas ADD COLUMN motivo VARCHAR(100)"))
                conn.commit()
                print("Columna personas.motivo agregada")
            if "motivo_detalle" not in pcols:
                conn.execute(sql_text("ALTER TABLE personas ADD COLUMN motivo_detalle VARCHAR(255)"))
                conn.commit()
                print("Columna personas.motivo_detalle agregada")
            ucols = [r[1] for r in conn.execute(sql_text("PRAGMA table_info(usuarios)")).fetchall()]
            if ucols:
                if "delegacion" not in ucols:
                    conn.execute(sql_text("ALTER TABLE usuarios ADD COLUMN delegacion VARCHAR(40) DEFAULT 'comunicaciones'"))
                    conn.commit()
                    print("Columna usuarios.delegacion agregada")
                if "activo" not in ucols:
                    conn.execute(sql_text("ALTER TABLE usuarios ADD COLUMN activo BOOLEAN DEFAULT 1"))
                    conn.commit()
                    print("Columna usuarios.activo agregada")
            tcols = [r[1] for r in conn.execute(sql_text("PRAGMA table_info(turnos)")).fetchall()]
            if tcols and "rol" not in tcols:
                conn.execute(sql_text("ALTER TABLE turnos ADD COLUMN rol VARCHAR(20)"))
                conn.commit()
                print("Columna turnos.rol agregada")
            ccols = [r[1] for r in conn.execute(sql_text("PRAGMA table_info(claves_enlace)")).fetchall()]
            if ccols and "plantilla" not in ccols:
                conn.execute(sql_text("ALTER TABLE claves_enlace ADD COLUMN plantilla VARCHAR(2000)"))
                conn.commit()
                print("Columna claves_enlace.plantilla agregada")
            cfgcols = [r[1] for r in conn.execute(sql_text("PRAGMA table_info(config_punto)")).fetchall()]
            if cfgcols and "convivencia_fimlm" not in cfgcols:
                conn.execute(sql_text("ALTER TABLE config_punto ADD COLUMN convivencia_fimlm BOOLEAN DEFAULT 0"))
                conn.commit()
                print("Columna config_punto.convivencia_fimlm agregada")
            for col in ("labor_comunicaciones", "labor_politica", "labor_juventudes", "labor_electoral", "labor_fimlm"):
                if cfgcols and col not in cfgcols:
                    conn.execute(sql_text(f"ALTER TABLE config_punto ADD COLUMN {col} VARCHAR(60) DEFAULT ''"))
                    conn.commit()
    except Exception as e:
        print(f"Migración: {e}")

    db = next(get_db())
    create_default_user(db)
    obtener_config(db)
    db.close()


def require_login(request: Request):
    user = request.session.get("user")
    if not user:
        return None
    return user

def require_com_user(request: Request):
    user = require_login(request)
    if not user:
        return None, RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if not es_comunicaciones(request):
        return None, RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return user, None



@app.middleware("http")
async def solo_comunicaciones(request: Request, call_next):
    try:
        path = request.url.path
        if request.session.get("user") and ruta_solo_com(path) and not es_comunicaciones(request):
            return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    except Exception:
        pass
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if request.session.get("user"):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("user"):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    cuenta = authenticate_user(db, username, password)
    if cuenta:
        request.session["user"] = cuenta.usuario
        request.session["delegacion"] = cuenta.delegacion or "comunicaciones"
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "login.html", {"error": "Usuario o contraseña incorrectos"}
    )


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    from datetime import datetime, timedelta
    from collections import defaultdict

    user = require_login(request)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    generar_alertas_festivo(db)
    generar_alertas_domingo(db)

    total_instaladas = db.query(Persona).filter(
        Persona.pendiente_revision == False,
        Persona.estado == "Instalada"
    ).count()

    total_no_instalada = db.query(Persona).filter(
        Persona.estado == "No instalada"
    ).count()

    # Pendientes de celular (Instalada sin celular válido)
    pendientes_celular = db.query(Persona).filter(
        Persona.pendiente_revision == True,
        Persona.estado == "Instalada",
    ).order_by(Persona.id.desc()).all()

    # Seguimiento No instalada sin motivo asignado
    seguimiento_no_instalada = db.query(Persona).filter(
        Persona.estado == "No instalada",
        (Persona.motivo.is_(None) | (Persona.motivo == "")),
    ).order_by(Persona.id.desc()).all()

    pendientes = pendientes_celular  # compat

    meta = 975

    # Título de periodo automático (mes actual)
    MESES_TITULO = {
        1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
        5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
        9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
    }
    ahora = datetime.now()
    periodo_titulo = f"Resultados · {MESES_TITULO[ahora.month]} {ahora.year}"

    # === Datos para gráfica diaria ===
    MESES_ES = {
        1: "ene", 2: "feb", 3: "mar", 4: "abr", 5: "may", 6: "jun",
        7: "jul", 8: "ago", 9: "sep", 10: "oct", 11: "nov", 12: "dic"
    }

    personas = db.query(Persona).filter(
        Persona.pendiente_revision == False,
        Persona.estado == "Instalada"
    ).all()

    labels = []
    data = []
    data_nacional = []

    internos_por_dia = {}
    if personas:
        dias = defaultdict(int)
        for p in personas:
            if p.fecha_listado:
                clave = p.fecha_listado.strftime("%Y-%m-%d")
            elif p.fecha_primera_carga:
                fecha = p.fecha_primera_carga
                if hasattr(fecha, "tzinfo") and fecha.tzinfo is not None:
                    fecha = fecha.replace(tzinfo=None)
                clave = fecha.strftime("%Y-%m-%d")
            else:
                continue
            dias[clave] += 1
        acum = 0
        for clave in sorted(dias.keys()):
            acum += dias[clave]
            internos_por_dia[clave] = acum

    todos_nac = db.query(DatoNacional).order_by(
        DatoNacional.fecha_dato.asc(), DatoNacional.id.asc()
    ).all()
    nacional_por_dia = {}
    for d in todos_nac:
        if d.fecha_dato:
            nacional_por_dia[d.fecha_dato.strftime("%Y-%m-%d")] = d.total_reportado

    fechas = sorted(set(internos_por_dia) | set(nacional_por_dia))
    last_int = 0
    last_nac = None
    chart_keys = []
    incrementos_int = []
    incrementos_nac = []
    prev_int = 0
    prev_nac_val = None
    for clave in fechas:
        if clave in internos_por_dia:
            last_int = internos_por_dia[clave]
        if clave in nacional_por_dia:
            last_nac = nacional_por_dia[clave]
        fecha_dia = datetime.strptime(clave, "%Y-%m-%d")
        labels.append(f"{fecha_dia.day:02d} {MESES_ES[fecha_dia.month]}")
        data.append(last_int)
        data_nacional.append(last_nac)
        chart_keys.append(clave)
        incrementos_int.append(last_int - prev_int)
        if last_nac is None or prev_nac_val is None:
            incrementos_nac.append(0)
        else:
            incrementos_nac.append(last_nac - prev_nac_val)
        prev_int = last_int
        if last_nac is not None:
            prev_nac_val = last_nac

    def _mediana(vals):
        vals = sorted(v for v in vals if v > 0)
        if not vals:
            return 1
        m = len(vals) // 2
        return vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) / 2

    med_i = _mediana(incrementos_int)
    med_n = _mediana(incrementos_nac)
    picos_interno = [(inc > 30) for inc in incrementos_int]
    picos_nacional = [(inc > 30) for inc in incrementos_nac]

    from datetime import datetime as _dt
    cargas_todas = db.query(Carga).all()
    detalle_dias = {}
    for i, clave in enumerate(chart_keys):
        cargas_dia = [
            {
                "archivo": c.nombre_archivo,
                "fuente": nombre_fuente(c.fuente),
                "nuevos": c.nuevos or 0,
                "equipo": c.cargado_por or "",
            }
            for c in cargas_todas
            if c.fecha_listado and c.fecha_listado.strftime("%Y-%m-%d") == clave
        ]
        nota_nac = None
        total_nac = nacional_por_dia.get(clave)
        if total_nac is not None:
            for d in todos_nac:
                if d.fecha_dato and d.fecha_dato.strftime("%Y-%m-%d") == clave:
                    nota_nac = d.nota
        detalle_dias[clave] = {
            "nuevas_internas": incrementos_int[i],
            "cargas": cargas_dia,
            "nacional": total_nac,
            "nacional_delta": incrementos_nac[i],
            "nota": nota_nac,
            "pico_interno": picos_interno[i],
            "pico_nacional": picos_nacional[i],
        }

    dato_nacional = todos_nac[-1] if todos_nac else None
    dato_nacional_prev = todos_nac[-2] if len(todos_nac) > 1 else None
    alerta_curva = None
    if dato_nacional and dato_nacional_prev:
        if dato_nacional.total_reportado == dato_nacional_prev.total_reportado:
            alerta_curva = "Sin avance en el último reporte de sede nacional"
        elif dato_nacional.total_reportado < dato_nacional_prev.total_reportado:
            alerta_curva = "El último reporte de sede nacional bajó respecto al anterior"
    if data and len(data) >= 2 and data[-1] == data[-2] and dato_nacional and data[-1] < dato_nacional.total_reportado:
        extra = "El registro interno no está alcanzando el ritmo de sede nacional"
        alerta_curva = f"{alerta_curva} · {extra}" if alerta_curva else extra
    diferencia_nacional = None
    if dato_nacional:
        diferencia_nacional = dato_nacional.total_reportado - total_instaladas

    # Avance principal: sede nacional si existe; si no, registro interno
    usa_dato_nacional = dato_nacional is not None
    if usa_dato_nacional:
        avance_principal = dato_nacional.total_reportado
    else:
        avance_principal = total_instaladas

    porcentaje = round((avance_principal / meta) * 100, 1) if meta > 0 else 0
    if porcentaje > 100:
        porcentaje_barra = 100
    else:
        porcentaje_barra = porcentaje

    # Color de barra según cercanía a la meta
    if porcentaje >= 100:
        barra_color = "bg-green-600"
        barra_track = "bg-green-100"
    elif porcentaje >= 70:
        barra_color = "bg-green-500"
        barra_track = "bg-green-50"
    elif porcentaje >= 40:
        barra_color = "bg-amber-400"
        barra_track = "bg-amber-50"
    else:
        barra_color = "bg-red-500"
        barra_track = "bg-red-50"

    delg = request.session.get("delegacion") or "comunicaciones"
    alertas = db.query(Alerta).filter(Alerta.para_delegacion == delg, Alerta.leida == False).order_by(Alerta.id.desc()).all()
    _vistas_cambio(db, alertas)
    request.session["n_alertas"] = len(alertas)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "alertas": alertas,
            "total_instaladas": total_instaladas,
            "total_no_instalada": total_no_instalada,
            "meta": meta,
            "porcentaje": porcentaje,
            "porcentaje_barra": porcentaje_barra,
            "avance_principal": avance_principal,
            "usa_dato_nacional": usa_dato_nacional,
            "barra_color": barra_color,
            "barra_track": barra_track,
            "periodo_titulo": periodo_titulo,
            "pendientes": pendientes_celular,
            "pendientes_celular": pendientes_celular,
            "seguimiento_no_instalada": seguimiento_no_instalada,
            "chart_labels": labels,
            "chart_data": data,
            "chart_data_nacional": data_nacional,
            "chart_keys": chart_keys,
            "picos_interno": picos_interno,
            "picos_nacional": picos_nacional,
            "detalle_dias": detalle_dias,
            "alerta_curva": alerta_curva,
            "dato_nacional": dato_nacional,
            "diferencia_nacional": diferencia_nacional,
        }
    )


# ========== ETAPA 3: Carga de Excel ==========

@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    user, redir = require_com_user(request)
    if redir:
        return redir
    return templates.TemplateResponse(
        request,
        "upload.html",
        {
            "user": user,
            "fuentes": FUENTES_VALIDAS,
            "equipos": EQUIPOS_CARGA,
            "error": None
        }
    )


@app.post("/upload", response_class=HTMLResponse)
async def upload_submit(
    request: Request,
    fuente: str = Form(...),
    fecha_listado: str = Form(...),
    cargado_por: str = Form(...),
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    user, redir = require_com_user(request)
    if redir:
        return redir
    # Validaciones básicas
    if fuente not in FUENTES_VALIDAS:
        return templates.TemplateResponse(
            request, "upload.html",
            {"user": user, "fuentes": FUENTES_VALIDAS, "equipos": EQUIPOS_CARGA, "error": "Fuente no válida."}
        )

    if cargado_por not in EQUIPOS_CARGA:
        return templates.TemplateResponse(
            request, "upload.html",
            {"user": user, "fuentes": FUENTES_VALIDAS, "equipos": EQUIPOS_CARGA, "error": "Equipo no válido."}
        )

    if not archivo.filename:
        return templates.TemplateResponse(
            request, "upload.html",
            {"user": user, "fuentes": FUENTES_VALIDAS, "equipos": EQUIPOS_CARGA, "error": "Debes seleccionar un archivo."}
        )

    # Solo aceptar .xlsx
    if not archivo.filename.lower().endswith((".xlsx", ".xls")):
        return templates.TemplateResponse(
            request, "upload.html",
            {"user": user, "fuentes": FUENTES_VALIDAS,
             "error": "Solo se permiten archivos Excel (.xlsx)."}
        )

    # Guardar temporalmente
    try:
        suffix = Path(archivo.filename).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await archivo.read()
            # Límite de tamaño ~5 MB
            if len(content) > 5 * 1024 * 1024:
                return templates.TemplateResponse(
                    request, "upload.html",
                    {"user": user, "fuentes": FUENTES_VALIDAS,
                     "error": "El archivo es demasiado grande (máximo 5 MB)."}
                )
            tmp.write(content)
            tmp_path = tmp.name

        # Parsear fecha del listado
        from datetime import datetime as dt
        try:
            fecha_obj = dt.strptime(fecha_listado, "%Y-%m-%d").date()
        except ValueError:
            return templates.TemplateResponse(
                request, "upload.html",
                {"user": user, "fuentes": FUENTES_VALIDAS,
                 "error": "Fecha del listado inválida."}
            )

        # Procesar
        resumen = procesar_excel(
            file_path=tmp_path,
            fuente=fuente,
            nombre_archivo=archivo.filename,
            fecha_listado=fecha_obj,
            db=db,
            cargado_por=cargado_por,
        )

    except Exception as e:
        return templates.TemplateResponse(
            request, "upload.html",
            {"user": user, "fuentes": FUENTES_VALIDAS,
             "error": f"Error al procesar el archivo: {str(e)}"}
        )
    finally:
        # Limpiar archivo temporal
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "user": user,
            "resumen": resumen,
            "nombre_archivo": archivo.filename,
            "fuente": fuente
        }
    )


# ========== ETAPA 4: Edición de pendientes ==========

@app.get("/pendiente/{persona_id}", response_class=HTMLResponse)
async def editar_pendiente_page(request: Request, persona_id: int, db: Session = Depends(get_db)):
    user, redir = require_com_user(request)
    if redir:
        return redir
    persona = db.query(Persona).filter(
        Persona.id == persona_id,
        Persona.pendiente_revision == True
    ).first()

    if not persona:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(
        request,
        "editar_pendiente.html",
        {
            "user": user,
            "persona": persona,
            "error": None
        }
    )


@app.post("/pendiente/{persona_id}", response_class=HTMLResponse)
async def editar_pendiente_submit(
    request: Request,
    persona_id: int,
    nombre: str = Form(...),
    celular: str = Form(...),
    db: Session = Depends(get_db)
):
    from .processing import normalizar_celular, normalizar_nombre

    user, redir = require_com_user(request)
    if redir:
        return redir
    persona = db.query(Persona).filter(
        Persona.id == persona_id,
        Persona.pendiente_revision == True
    ).first()

    if not persona:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    nombre_limpio = normalizar_nombre(nombre)
    celular_limpio = normalizar_celular(celular)

    if not nombre_limpio:
        return templates.TemplateResponse(
            request, "editar_pendiente.html",
            {"user": user, "persona": persona, "error": "El nombre no puede estar vacío."}
        )

    if not celular_limpio:
        return templates.TemplateResponse(
            request, "editar_pendiente.html",
            {"user": user, "persona": persona,
             "error": "El celular debe tener exactamente 10 dígitos válidos (ejemplo: 3115712505)."}
        )

    # Verificar que el celular no esté ya usado por otra persona
    existente = db.query(Persona).filter(
        Persona.celular == celular_limpio,
        Persona.id != persona.id
    ).first()

    if existente:
        return templates.TemplateResponse(
            request, "editar_pendiente.html",
            {"user": user, "persona": persona,
             "error": f"Ese celular ya está registrado a nombre de: {existente.nombre}"}
        )

    # Actualizar y quitar de pendientes
    persona.nombre = nombre_limpio
    persona.celular = celular_limpio
    persona.pendiente_revision = False
    db.commit()

    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


# ========== ETAPA 6: Informe semanal ==========

@app.get("/informe", response_class=HTMLResponse)
async def generar_informe(request: Request, db: Session = Depends(get_db)):
    from datetime import datetime, timedelta
    from sqlalchemy import func as sqlfunc

    user, redir = require_com_user(request)
    if redir:
        return redir
    # Total acumulado
    total_instaladas = db.query(Persona).filter(
        Persona.pendiente_revision == False,
        Persona.estado == "Instalada"
    ).count()

    meta = 975

    # Semana actual (lunes a domingo) — cruza meses sin problema
    hoy = datetime.now().date()
    lunes = hoy - timedelta(days=hoy.weekday())
    domingo = lunes + timedelta(days=6)

    MESES = {
        1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
        5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
        9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"
    }

    def formato_fecha(d):
        return f"{d.day} de {MESES[d.month]}"

    rango_semana = f"{formato_fecha(lunes)} al {formato_fecha(domingo)}"

    # Fuentes de la semana = cargas hechas lun-dom (solo personas NUEVAS)
    cargas_semana = db.query(Carga).filter(
        sqlfunc.date(Carga.fecha_carga) >= lunes,
        sqlfunc.date(Carga.fecha_carga) <= domingo,
    ).all()

    desglose = {}
    for c in cargas_semana:
        if not c.fuente:
            continue
        clave = nombre_fuente(c.fuente)
        desglose[clave] = desglose.get(clave, 0) + (c.nuevos or 0)
    # Quitar fuentes en cero para no ensuciar el mensaje
    desglose = {f: n for f, n in desglose.items() if n > 0}

    # Dato sede nacional (si existe)
    datos_nac = db.query(DatoNacional).order_by(
        DatoNacional.fecha_dato.desc(), DatoNacional.id.desc()
    ).all()
    dato_nacional = datos_nac[0] if datos_nac else None
    dato_nacional_prev = datos_nac[1] if len(datos_nac) > 1 else None

    if dato_nacional:
        avance = dato_nacional.total_reportado
        etiqueta_avance = "sede nacional"
    else:
        avance = total_instaladas
        etiqueta_avance = "registro interno"

    porcentaje = round((avance / meta) * 100, 1) if meta > 0 else 0

    total_no_instalada = db.query(Persona).filter(
        Persona.estado == "No instalada"
    ).count()

    alerta = []
    linea_delta = []
    if dato_nacional and dato_nacional_prev:
        actual = dato_nacional.total_reportado
        anterior = dato_nacional_prev.total_reportado
        delta = actual - anterior
        signo = f"+{delta}" if delta > 0 else str(delta)
        linea_delta = [f"• Descargas nuevas de esta semana: {signo} ({anterior} → {actual})"]
        if actual == anterior:
            racha = 1
            for i in range(1, len(datos_nac)):
                if datos_nac[i].total_reportado == actual:
                    racha += 1
                else:
                    break
            extra = ""
            if racha >= 2:
                extra = f" Van {racha} reportes seguidos sin aumento."
            alerta = [
                "",
                "⚠️ *Atención: el dato de sede nacional no aumentó.*",
                f"Sigue en {actual}.{extra} Se sugiere buscar nuevas estrategias que promuevan la instalación de la App.",
            ]
        elif actual < anterior:
            alerta = [
                "",
                "⚠️ *Atención: el dato de sede nacional bajó.*",
                f"Pasó de {anterior} a {actual} ({delta}). Revisar posibles desinstalaciones.",
            ]

    lineas = [
        "📊 *Informe Semanal – Sintonía*",
        f"📅 Semana: {rango_semana}",
        "",
        f"🎯 *Avance del equipo ({etiqueta_avance})*",
        f"• {avance} / {meta} personas",
        f"• {porcentaje} %",
    ]
    lineas.extend(linea_delta)
    lineas.extend(alerta)
    lineas.extend([
        "",
        f"🗂 Registro interno: {total_instaladas} personas censadas",
        f"⏳ Sin instalar la App: {total_no_instalada} personas",
    ])

    if desglose:
        lineas.extend(["", "📥 *Fuentes de la semana:*"])
        for fuente, cant in desglose.items():
            lineas.append(f"• {fuente}: {cant}")

    texto_informe = "\n".join(lineas)

    return templates.TemplateResponse(
        request,
        "informe.html",
        {
            "user": user,
            "texto_informe": texto_informe,
            "rango_semana": rango_semana,
            "total_instaladas": total_instaladas,
            "porcentaje": porcentaje,
        }
    )








@app.post("/pendiente/{persona_id}/eliminar")
async def eliminar_pendiente(
    request: Request,
    persona_id: int,
    db: Session = Depends(get_db),
):
    user, redir = require_com_user(request)
    if redir:
        return redir
    persona = db.query(Persona).filter(
        Persona.id == persona_id,
        Persona.pendiente_revision == True,
    ).first()

    if persona:
        db.delete(persona)
        db.commit()

    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)



@app.get("/seguimiento/{persona_id}", response_class=HTMLResponse)
async def seguimiento_page(request: Request, persona_id: int, db: Session = Depends(get_db)):
    user, redir = require_com_user(request)
    if redir:
        return redir
    persona = db.query(Persona).filter(
        Persona.id == persona_id,
        Persona.estado == "No instalada",
    ).first()
    if not persona:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(
        request,
        "seguimiento.html",
        {
            "user": user,
            "persona": persona,
            "motivos": MOTIVOS_NO_INSTALADA,
            "error": None,
        },
    )


@app.post("/seguimiento/{persona_id}", response_class=HTMLResponse)
async def seguimiento_submit(
    request: Request,
    persona_id: int,
    motivo: str = Form(...),
    motivo_detalle: str = Form(""),
    celular: str = Form(""),
    db: Session = Depends(get_db),
):
    from .processing import normalizar_celular, normalizar_nombre

    user, redir = require_com_user(request)
    if redir:
        return redir
    persona = db.query(Persona).filter(
        Persona.id == persona_id,
        Persona.estado == "No instalada",
    ).first()
    if not persona:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    if motivo not in MOTIVOS_NO_INSTALADA:
        return templates.TemplateResponse(
            request, "seguimiento.html",
            {"user": user, "persona": persona, "motivos": MOTIVOS_NO_INSTALADA,
             "error": "Motivo no válido."},
        )

    if motivo == "Otro" and not (motivo_detalle or "").strip():
        return templates.TemplateResponse(
            request, "seguimiento.html",
            {"user": user, "persona": persona, "motivos": MOTIVOS_NO_INSTALADA,
             "error": "Si eliges «Otro», describe la situación."},
        )

    cel = normalizar_celular(celular) if celular else None
    if cel:
        otro = db.query(Persona).filter(Persona.celular == cel, Persona.id != persona.id).first()
        if otro:
            return templates.TemplateResponse(
                request, "seguimiento.html",
                {"user": user, "persona": persona, "motivos": MOTIVOS_NO_INSTALADA,
                 "error": f"Ese celular ya está registrado a: {otro.nombre}"},
            )
        persona.celular = cel
        persona.pendiente_revision = False
    elif persona.pendiente_revision:
        # Sigue sin celular válido
        pass

    persona.motivo = motivo
    persona.motivo_detalle = (motivo_detalle or "").strip() or None
    db.commit()

    return RedirectResponse(
        url="/personas?estado=no_instalada",
        status_code=status.HTTP_303_SEE_OTHER,
    )

# ========== Dato oficial sede nacional ==========

@app.get("/dato-nacional", response_class=HTMLResponse)
async def dato_nacional_page(request: Request, db: Session = Depends(get_db)):
    user, redir = require_com_user(request)
    if redir:
        return redir
    ultimo = db.query(DatoNacional).order_by(
        DatoNacional.fecha_dato.desc(), DatoNacional.id.desc()
    ).first()
    historial = db.query(DatoNacional).order_by(
        DatoNacional.fecha_dato.desc(), DatoNacional.id.desc()
    ).limit(10).all()

    return templates.TemplateResponse(
        request,
        "dato_nacional.html",
        {"user": user, "ultimo": ultimo, "historial": historial, "error": None},
    )


@app.post("/dato-nacional", response_class=HTMLResponse)
async def dato_nacional_submit(
    request: Request,
    fecha_dato: str = Form(...),
    total_reportado: int = Form(...),
    nota: str = Form(""),
    db: Session = Depends(get_db),
):
    from datetime import datetime as dt

    user, redir = require_com_user(request)
    if redir:
        return redir
    try:
        fecha = dt.strptime(fecha_dato, "%Y-%m-%d").date()
    except ValueError:
        ultimo = db.query(DatoNacional).order_by(DatoNacional.fecha_dato.desc()).first()
        historial = db.query(DatoNacional).order_by(DatoNacional.fecha_dato.desc()).limit(10).all()
        return templates.TemplateResponse(
            request, "dato_nacional.html",
            {"user": user, "ultimo": ultimo, "historial": historial, "error": "Fecha inválida."},
        )

    if total_reportado < 0:
        ultimo = db.query(DatoNacional).order_by(DatoNacional.fecha_dato.desc()).first()
        historial = db.query(DatoNacional).order_by(DatoNacional.fecha_dato.desc()).limit(10).all()
        return templates.TemplateResponse(
            request, "dato_nacional.html",
            {"user": user, "ultimo": ultimo, "historial": historial, "error": "El total no puede ser negativo."},
        )

    registro = DatoNacional(
        fecha_dato=fecha,
        total_reportado=total_reportado,
        nota=(nota or "").strip() or None,
    )
    db.add(registro)
    db.commit()

    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

# ========== Listado consultable de personas ==========


@app.get("/persona/{persona_id}", response_class=HTMLResponse)
async def editar_persona_page(request: Request, persona_id: int, db: Session = Depends(get_db)):
    user, redir = require_com_user(request)
    if redir:
        return redir
    persona = db.query(Persona).filter(Persona.id == persona_id).first()
    if not persona:
        return RedirectResponse(url="/personas", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "persona_editar.html",
        {"user": user, "persona": persona, "error": None},
    )


@app.post("/persona/{persona_id}", response_class=HTMLResponse)
async def editar_persona_submit(
    request: Request,
    persona_id: int,
    nombre: str = Form(...),
    celular: str = Form(""),
    estado: str = Form(...),
    db: Session = Depends(get_db),
):
    from .processing import normalizar_celular, normalizar_nombre

    user, redir = require_com_user(request)
    if redir:
        return redir
    persona = db.query(Persona).filter(Persona.id == persona_id).first()
    if not persona:
        return RedirectResponse(url="/personas", status_code=status.HTTP_303_SEE_OTHER)

    nombre_limpio = normalizar_nombre(nombre)
    if not nombre_limpio:
        return templates.TemplateResponse(
            request, "persona_editar.html",
            {"user": user, "persona": persona, "error": "El nombre no puede estar vacío."},
        )

    if estado not in ("Instalada", "No instalada"):
        return templates.TemplateResponse(
            request, "persona_editar.html",
            {"user": user, "persona": persona, "error": "Estado no válido."},
        )

    celular_limpio = normalizar_celular(celular) if celular else None

    if estado == "Instalada" and not celular_limpio:
        return templates.TemplateResponse(
            request, "persona_editar.html",
            {"user": user, "persona": persona,
             "error": "Para marcar Instalada el celular debe tener 10 dígitos válidos."},
        )

    if celular_limpio:
        otro = db.query(Persona).filter(
            Persona.celular == celular_limpio,
            Persona.id != persona.id,
        ).first()
        if otro:
            return templates.TemplateResponse(
                request, "persona_editar.html",
                {"user": user, "persona": persona,
                 "error": (
                     f"Ese celular ya está en otro registro: {otro.nombre} "
                     f"({otro.estado}). Elimina uno de los dos en Personas."
                 )},
            )
        persona.celular = celular_limpio
        persona.pendiente_revision = False
    else:
        persona.pendiente_revision = True

    persona.nombre = nombre_limpio
    persona.estado = estado
    if estado == "Instalada":
        persona.motivo = None
        persona.motivo_detalle = None
    db.commit()

    return RedirectResponse(url="/personas?estado=todos", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/persona/{persona_id}/eliminar")
async def eliminar_persona(request: Request, persona_id: int, db: Session = Depends(get_db)):
    user, redir = require_com_user(request)
    if redir:
        return redir
    persona = db.query(Persona).filter(Persona.id == persona_id).first()
    if not persona:
        return RedirectResponse(url="/personas?estado=todos&error=Esa+persona+ya+no+existe", status_code=status.HTTP_303_SEE_OTHER)
    try:
        for turno in db.query(Turno).filter(Turno.persona_id == persona.id).all():
            db.delete(turno)
        db.delete(persona)
        db.commit()
    except Exception:
        db.rollback()
        return RedirectResponse(url="/personas?estado=todos&error=No+se+pudo+eliminar.+Intenta+de+nuevo", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url="/personas?estado=todos", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/personas", response_class=HTMLResponse)
async def listar_personas(
    request: Request,
    db: Session = Depends(get_db),
    q: str = "",
    fuente: str = "",
    fecha_desde: str = "",
    fecha_hasta: str = "",
    estado: str = "",
):
    from datetime import datetime as dt

    user, redir = require_com_user(request)
    if redir:
        return redir
    query = db.query(Persona)

    estado = (estado or "todos").strip().lower()
    if estado == "instalada":
        query = query.filter(
            Persona.estado == "Instalada",
            Persona.pendiente_revision == False,
        )
    elif estado == "no_instalada":
        query = query.filter(Persona.estado == "No instalada")
    elif estado == "pendientes":
        query = query.filter(Persona.pendiente_revision == True)
    # estado == "todos" → sin filtro de estado

    q = (q or "").strip()
    if q:
        from .processing import normalizar_celular
        import re
        like = f"%{q}%"
        digitos = re.sub(r"\D", "", q)
        if len(digitos) >= 12 and digitos.startswith("57"):
            digitos = digitos[2:]
        celular_exacto = normalizar_celular(q)
        if celular_exacto:
            query = query.filter(
                (Persona.nombre.ilike(like)) | (Persona.celular == celular_exacto)
            )
        elif digitos:
            like_cel = f"%{digitos}%"
            query = query.filter(
                (Persona.nombre.ilike(like)) | (Persona.celular.ilike(like_cel))
            )
        else:
            query = query.filter(Persona.nombre.ilike(like))

    if fuente and fuente in FUENTES_VALIDAS:
        valores = [fuente]
        if fuente == FUENTES_VALIDAS[0]:
            valores.append(FUENTE_PRESENCIAL_ANTERIOR)
        query = query.filter(Persona.fuente_ultima.in_(valores))

    if fecha_desde:
        try:
            d = dt.strptime(fecha_desde, "%Y-%m-%d").date()
            query = query.filter(Persona.fecha_listado >= d)
        except ValueError:
            pass
    if fecha_hasta:
        try:
            d = dt.strptime(fecha_hasta, "%Y-%m-%d").date()
            query = query.filter(Persona.fecha_listado <= d)
        except ValueError:
            pass

    total_filtrado = query.count()
    personas = query.order_by(Persona.nombre.asc()).limit(500).all()

    total_instaladas = db.query(Persona).filter(
        Persona.estado == "Instalada",
        Persona.pendiente_revision == False,
    ).count()
    total_no_instalada = db.query(Persona).filter(
        Persona.estado == "No instalada"
    ).count()

    return templates.TemplateResponse(
        request,
        "personas.html",
        {
            "user": user,
            "personas": personas,
            "total_filtrado": total_filtrado,
            "total_instaladas": total_instaladas,
            "total_no_instalada": total_no_instalada,
            "fuentes": FUENTES_VALIDAS,
            "filtros": {
                "q": q,
                "fuente": fuente,
                "fecha_desde": fecha_desde,
                "fecha_hasta": fecha_hasta,
                "estado": estado,
            },
            "limitado": total_filtrado > 500,
        },
    )



# ========== Historial de cargas ==========

@app.get("/cargas", response_class=HTMLResponse)
async def historial_cargas(request: Request, db: Session = Depends(get_db)):
    user, redir = require_com_user(request)
    if redir:
        return redir
    cargas = db.query(Carga).order_by(Carga.fecha_carga.desc()).limit(100).all()

    return templates.TemplateResponse(
        request,
        "cargas.html",
        {
            "user": user,
            "cargas": cargas,
        },
    )

# ========== ETAPA 7: Respaldo simple ==========

@app.get("/backup")
async def crear_backup(request: Request):
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    # Respaldo solo por Railway. Código histórico debajo no se ejecuta.
    """Descarga una copia de la base de datos (usa DATABASE_PATH en producción)."""
    import shutil
    import tempfile
    from fastapi.responses import FileResponse
    from datetime import datetime
    from .database import DB_PATH

    user, redir = require_com_user(request)
    if redir:
        return redir
    db_path = Path(DB_PATH)
    if not db_path.exists():
        # Fallback a la ruta local por si no hay variable de entorno
        db_path = BASE_DIR / "infomira_censo.db"
    if not db_path.exists():
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"backup_infomira_{fecha}.db"

    # Copia temporal (en producción /app puede no ser escribible de forma fiable)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    shutil.copy2(db_path, tmp.name)

    return FileResponse(
        path=tmp.name,
        filename=backup_name,
        media_type="application/octet-stream",
    )


def _delegacion(request: Request) -> str:
    return request.session.get("delegacion") or "comunicaciones"


@app.get("/programacion", response_class=HTMLResponse)
async def programacion_ver(request: Request, db: Session = Depends(get_db),
                           q: str = "", horario_sel: str = "", fecha_sel: str = "",
                           modo: str = "editar", error: str = "", ok: str = "",
                           confirmar: str = "", hl: str = ""):
    user = require_login(request)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    from datetime import datetime as dt
    from .processing import normalizar_celular
    delg = _delegacion(request)
    if delg == "sonido":
        from .processing import normalizar_celular
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
        busqueda = []
        rol_sel = request.query_params.get("rol", "")
        editando = (modo or "editar") != "foto"
        if not editando:
            fecha_sel = ""
            horario_sel = ""
            rol_sel = ""
            q = ""
        if editando and q.strip():
            like = f"%{q.strip()}%"
            cel = normalizar_celular(q)
            qq = db.query(Persona).filter(Persona.nombre.ilike(like))
            if cel:
                qq = db.query(Persona).filter((Persona.nombre.ilike(like)) | (Persona.celular == cel))
            busqueda = qq.order_by(Persona.nombre).limit(20).all()
        ids_mios = {t.persona_id for t in db.query(Turno).filter(Turno.delegacion == "sonido").all()}
        mis_cols = db.query(Persona).filter(Persona.id.in_(ids_mios)).all() if ids_mios else []
        cerrados = db.query(Turno).filter(Turno.delegacion == "sonido", Turno.semana_lunes < lunes).all()
        anio = lunes.year
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
        mis_cols.sort(key=lambda p: (-score_hora.get(p.id, 0), -score_anio.get(p.id, 0), (p.nombre or "").lower()))
        alertas = db.query(Alerta).filter(Alerta.para_delegacion == delg, Alerta.leida == False).order_by(Alerta.id.desc()).all()
        request.session["n_alertas"] = len(alertas)
        from .models import AusenciaSemana, Postulacion
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
        return templates.TemplateResponse(request, "programacion_sonido.html", {
            "user": user, "alertas": alertas,
            "lunes": lunes, "domingo": dias[-1], "filas": filas,
            "q": q, "busqueda": busqueda, "mis_cols": mis_cols, "puesto": puesto,
            "fecha_sel": fecha_sel, "horario_sel": horario_sel, "rol_sel": rol_sel,
            "modo": "foto" if not editando else "editar",
            "error": error, "ok": ok,
            "busqueda_vacia": bool(q.strip()) and not busqueda,
            "no_pueden": no_pueden, "postulados": postulados,
        })
    generar_alertas_festivo(db)
    generar_alertas_domingo(db)
    lunes = semana_vigente()
    dias = dias_semana(lunes)
    turnos = db.query(Turno).filter(Turno.semana_lunes == lunes, Turno.delegacion != "sonido").all()
    personas_map = {p.id: p for p in db.query(Persona).all()} if turnos else {}
    ids_mios = {t.persona_id for t in db.query(Turno).filter(Turno.delegacion == delg).all()}
    mis_cols = db.query(Persona).filter(Persona.id.in_(ids_mios)).all() if ids_mios else []
    cerrados = db.query(Turno).filter(Turno.delegacion == delg, Turno.semana_lunes < lunes).all()
    anio = lunes.year
    score_hora = {}
    score_anio = {}
    wd_sel = None
    try:
        from datetime import datetime as _dt
        if fecha_sel and horario_sel:
            wd_sel = _dt.strptime(fecha_sel, "%Y-%m-%d").date().weekday()
    except ValueError:
        wd_sel = None
    for ct in cerrados:
        if ct.fecha.year == anio:
            score_anio[ct.persona_id] = score_anio.get(ct.persona_id, 0) + 1
        if wd_sel is not None and ct.fecha.weekday() == wd_sel and ct.horario == horario_sel:
            score_hora[ct.persona_id] = score_hora.get(ct.persona_id, 0) + 1
    mis_cols.sort(key=lambda p: (
        -score_hora.get(p.id, 0),
        -score_anio.get(p.id, 0),
        (p.nombre or "").lower(),
    ))
    busqueda = []
    if q.strip():
        like = f"%{q.strip()}%"
        cel = normalizar_celular(q)
        qq = db.query(Persona).filter(Persona.nombre.ilike(like))
        if cel:
            qq = db.query(Persona).filter((Persona.nombre.ilike(like)) | (Persona.celular == cel))
        busqueda = qq.order_by(Persona.nombre).limit(20).all()
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
    request.session["n_alertas"] = len(alertas)
    _rank, puesto = ranking_anio(db)
    return templates.TemplateResponse(request, "programacion.html", {
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
    })


@app.post("/programacion/tutorial-ok")
async def programacion_tutorial(request: Request):
    if require_login(request):
        request.session["tut_prog"] = True
    return RedirectResponse(url="/programacion", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/programacion/asignar")
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


@app.post("/programacion/quitar")
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


def _nombre_equipo(delegacion: str) -> str:
    return {
        "comunicaciones": "Comunicaciones",
        "politica": "Política",
        "juventudes": "Juventudes",
        "electoral": "Electoral",
        "fimlm": "FIMLM",
        "sonido": "Sonido",
    }.get(delegacion or "", delegacion or "otro equipo")


def _sonido_ok(request: Request):
    user = require_login(request)
    if not user:
        return None, RedirectResponse(url="/login", status_code=303)
    if _delegacion(request) != "sonido":
        return None, RedirectResponse(url="/programacion", status_code=303)
    return user, None


@app.get("/programacion/sonido/enlace", response_class=HTMLResponse)
async def sonido_enlace(request: Request):
    return RedirectResponse(url="/postulaciones?modo=link", status_code=303)


@app.get("/postulaciones", response_class=HTMLResponse)
async def postulaciones_ver(request: Request, db: Session = Depends(get_db), ok: str = "", error: str = "", modo: str = "confirmar"):
    from .models import AusenciaSemana, Postulacion
    from .postulacion import clave_vigente, texto_plantilla
    user, redir = _sonido_ok(request)
    if redir:
        return redir
    lunes = semana_vigente()
    clave = clave_vigente(db, "sonido")
    base = str(request.base_url).rstrip("/")
    enlace = f"{base}/postular/{clave.token}"
    ocupado = {
        (t.fecha, t.horario, t.rol or "sonido")
        for t in db.query(Turno).filter(Turno.delegacion == "sonido", Turno.semana_lunes == lunes).all()
    }
    posts = db.query(Postulacion).filter(Postulacion.labor == "sonido", Postulacion.semana_lunes == lunes).all()
    ids = {p.persona_id for p in posts}
    ausencias = db.query(AusenciaSemana).filter(AusenciaSemana.labor == "sonido", AusenciaSemana.semana_lunes == lunes).all()
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
            "etiqueta": f"{NOMBRES_DIA[fecha.weekday()]} {fecha.day:02d} · {etiqueta_horario(horario)} · {'Cámara' if rol == 'camara' else 'Sonido'}",
            "gente": items,
            "lleno": (fecha, horario, rol) in ocupado,
        })
    postulados.sort(key=lambda x: (x["fecha"], x["horario"], x["rol"]))
    no_pueden = [personas.get(a.persona_id) for a in ausencias]
    no_pueden = [p for p in no_pueden if p]
    return templates.TemplateResponse(request, "postulaciones.html", {
        "user": user, "ok": ok, "error": error, "enlace": enlace, "clave_mes": clave.clave,
        "plantilla": texto_plantilla(clave, enlace), "postulados": postulados, "no_pueden": no_pueden,
        "lunes": lunes, "domingo": lunes + __import__("datetime").timedelta(days=6),
        "modo": "link" if modo == "link" else "confirmar",
    })


@app.post("/programacion/sonido/clave")
async def sonido_regenerar_clave(request: Request, db: Session = Depends(get_db)):
    user, redir = _sonido_ok(request)
    if redir:
        return redir
    from .postulacion import regenerar_clave
    row = regenerar_clave(db, "sonido")
    return RedirectResponse(url=f"/postulaciones?modo=link&ok=Clave+nueva:+{row.clave}", status_code=303)


@app.post("/programacion/sonido/plantilla")
async def sonido_guardar_plantilla(
    request: Request, plantilla: str = Form(""), db: Session = Depends(get_db),
):
    user, redir = _sonido_ok(request)
    if redir:
        return redir
    from .postulacion import clave_vigente, guardar_plantilla
    clave = clave_vigente(db, "sonido")
    base = str(request.base_url).rstrip("/")
    enlace = f"{base}/postular/{clave.token}"
    guardar_plantilla(db, plantilla, enlace, "sonido")
    return RedirectResponse(url="/postulaciones?modo=link&ok=Mensaje+guardado", status_code=303)


def _enlace_sonido(db, token: str):
    from .postulacion import clave_vigente
    clave = clave_vigente(db, "sonido")
    if clave.token != token:
        return None
    return clave


def _vistas_cambio(db, alertas) -> None:
    from .models import CambioPostulacion, Persona
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
        per = db.query(Persona).filter(Persona.id == cambio.persona_id).first()
        antes = _texto_vigente(db, cambio.persona_id, cambio.semana_lunes)
        ahora = ["Esta semana no puede apoyar."] if cambio.no_puede else _lineas_detalle(cambio.detalle)
        alerta.vista = {
            "id": cambio.id,
            "nombre": per.nombre if per else "Una persona",
            "antes": [parte.strip() for parte in antes.split(",") if parte.strip()],
            "ahora": ahora,
            "ahora_no": cambio.no_puede,
        }


def _linea_hueco(fecha, horario, rol):
    mes = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"][fecha.month - 1].capitalize()
    return f"{NOMBRES_DIA[fecha.weekday()]} {fecha.day:02d}/{mes} · {etiqueta_horario(horario)} · {'Cámara' if rol == 'camara' else 'Sonido'}"


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


def _texto_vigente(db, persona_id: int, lunes) -> str:
    from .models import AusenciaSemana
    from .postulacion import postulaciones_persona
    if db.query(AusenciaSemana).filter(
        AusenciaSemana.persona_id == persona_id,
        AusenciaSemana.labor == "sonido",
        AusenciaSemana.semana_lunes == lunes,
    ).first():
        return "no podía apoyar esta semana"
    filas = postulaciones_persona(db, persona_id, lunes)
    if not filas:
        return "sin horarios"
    return ", ".join(_linea_hueco(p.fecha, p.horario, p.rol) for p in sorted(filas, key=lambda p: (p.fecha, p.horario, p.rol)))


def _postular_abierto(request: Request, token: str, clave) -> bool:
    return request.session.get("postular") == token and request.session.get("postular_clave") == clave.clave


@app.get("/postular/{token}", response_class=HTMLResponse)
async def postular_ver(request: Request, token: str, db: Session = Depends(get_db), error: str = "", ok: str = "", cambiar: str = ""):
    clave = _enlace_sonido(db, token)
    if not clave:
        return templates.TemplateResponse(request, "postular.html", {"invalido": True})
    abierto = _postular_abierto(request, token, clave)
    ctx = {"invalido": False, "token": token, "abierto": abierto, "error": error, "ok": ok, "filas": [], "mios": set(), "no_puede": False, "resumen": False, "lineas": [], "cambio_antes": None, "cambio_ahora": [], "cambio_no_puede": False}
    if abierto:
        from .festivos import es_festivo
        from .models import AusenciaSemana, Postulacion
        lunes = semana_vigente()
        dias = dias_semana(lunes)
        ocupados = {
            (p.fecha, p.horario, p.rol)
            for p in db.query(Postulacion).filter(
                Postulacion.labor == "sonido", Postulacion.semana_lunes == lunes,
            ).all()
        }
        pid = request.session.get("postular_persona") if ok and not cambiar else None
        mios = set()
        lineas = []
        no_puede = False
        if pid:
            from .postulacion import postulaciones_persona
            for p in postulaciones_persona(db, pid, lunes):
                mios.add(f"{p.fecha.isoformat()}|{p.horario}|{p.rol}")
                lineas.append(f"{NOMBRES_DIA[p.fecha.weekday()]} {p.fecha.day:02d}/{['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'][p.fecha.month-1].capitalize()} · {etiqueta_horario(p.horario)} · {'Cámara' if p.rol == 'camara' else 'Sonido'}")
            lineas.sort()
            no_puede = db.query(AusenciaSemana).filter(
                AusenciaSemana.persona_id == pid, AusenciaSemana.labor == "sonido", AusenciaSemana.semana_lunes == lunes,
            ).first() is not None
            from .models import CambioPostulacion
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


@app.post("/postular/{token}/revisar")
async def postular_revisar(
    request: Request, token: str, db: Session = Depends(get_db),
    celular: str = Form(""), celular2: str = Form(""),
):
    from .processing import normalizar_celular
    if not _enlace_sonido(db, token) or not _postular_abierto(request, token, _enlace_sonido(db, token)):
        return {"ok": False, "error": "Primero escribe la clave"}
    cel = normalizar_celular(celular)
    cel2 = normalizar_celular(celular2)
    if not cel or cel != cel2:
        return {"ok": False, "error": "Los dos celulares deben coincidir y tener 10 dígitos"}
    per = db.query(Persona).filter(Persona.celular == cel).first()
    return {"ok": True, "existe": bool(per), "nombre": per.nombre if per else ""}


@app.post("/postular/{token}")
async def postular_guardar(
    request: Request, token: str, db: Session = Depends(get_db),
    clave: str = Form(""), celular: str = Form(""), celular2: str = Form(""),
    nombre: str = Form(""), no_puedo: str = Form(""),
):
    from .processing import normalizar_celular, normalizar_nombre
    from .postulacion import clave_valida, aplicar_semana, tiene_semana, postulaciones_persona
    row = _enlace_sonido(db, token)
    if not row:
        return RedirectResponse(url=f"/postular/{token}", status_code=303)
    form = await request.form()
    if form.get("paso") == "clave":
        from datetime import datetime as dt, timedelta as td
        hasta = request.session.get("postular_bloqueo")
        if hasta:
            try:
                if dt.fromisoformat(hasta) > dt.now():
                    return RedirectResponse(url=f"/postular/{token}?error=Demasiados+intentos.+Espera+15+minutos", status_code=303)
            except ValueError:
                pass
        if not clave_valida(db, clave):
            fallos = int(request.session.get("postular_fallos") or 0) + 1
            request.session["postular_fallos"] = fallos
            if fallos >= 5:
                request.session["postular_bloqueo"] = (dt.now() + td(minutes=15)).isoformat(timespec="seconds")
                request.session["postular_fallos"] = 0
                return RedirectResponse(url=f"/postular/{token}?error=Demasiados+intentos.+Espera+15+minutos", status_code=303)
            return RedirectResponse(url=f"/postular/{token}?error=Clave+incorrecta", status_code=303)
        request.session["postular_fallos"] = 0
        request.session.pop("postular_bloqueo", None)
        request.session["postular"] = token
        request.session["postular_clave"] = row.clave
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
    request.session["postular_persona"] = per.id
    lunes = semana_vigente()
    huecos = []
    if not no_puedo:
        from datetime import datetime as dt, timedelta as td
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
        from .models import CambioPostulacion
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
        return RedirectResponse(url=f"/postular/{token}?ok=Quedó+enviado.+El+coordinador+confirma+el+cambio", status_code=303)
    aplicar_semana(db, per.id, lunes, huecos, bool(no_puedo))
    if no_puedo:
        return RedirectResponse(url=f"/postular/{token}?ok=Quedó+registrado+que+esta+semana+no+puedes+apoyar", status_code=303)
    return RedirectResponse(url=f"/postular/{token}?ok=Estos+son+los+horarios+que+marcaste", status_code=303)


@app.post("/programacion/sonido/cambio")
async def sonido_cambio(
    request: Request, db: Session = Depends(get_db),
    cambio_id: int = Form(...), decision: str = Form(...),
):
    from datetime import datetime as dt
    from .models import CambioPostulacion
    from .postulacion import aplicar_semana
    user, redir = _sonido_ok(request)
    if redir:
        return redir
    cambio = db.query(CambioPostulacion).filter(CambioPostulacion.id == cambio_id, CambioPostulacion.estado == "pendiente").first()
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
        aplicar_semana(db, cambio.persona_id, cambio.semana_lunes, huecos, cambio.no_puede)
        cambio.estado = "aceptado"
    else:
        cambio.estado = "rechazado"
    db.query(Alerta).filter(Alerta.tipo == f"cambio-{cambio.id}").update({"leida": True})
    db.commit()
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/programacion/sonido/asignar")
async def sonido_asignar(
    request: Request, db: Session = Depends(get_db),
    persona_id: int = Form(...), fecha: str = Form(...), horario: str = Form(...), rol: str = Form(...),
    modo: str = Form(""), origen: str = Form(""), ajax: str = Form(""),
):
    from datetime import datetime as dt, timedelta as td
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


@app.post("/programacion/sonido/quitar")
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


@app.post("/programacion/sonido/alta")
async def sonido_alta(
    request: Request, db: Session = Depends(get_db),
    nombre: str = Form(...), celular: str = Form(...),
    fecha: str = Form(...), horario: str = Form(...), rol: str = Form(...), modo: str = Form(""),
):
    from .processing import normalizar_celular, normalizar_nombre
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


@app.post("/programacion/alta")
async def programacion_alta(
    request: Request, db: Session = Depends(get_db),
    nombre: str = Form(...), celular: str = Form(...),
    fecha: str = Form(...), horario: str = Form(...),
):
    from .processing import normalizar_celular, normalizar_nombre
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


def _exige_com(request: Request):
    user = require_login(request)
    if not user:
        return None, RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if not es_comunicaciones(request):
        return None, RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return user, None


@app.get("/usuarios", response_class=HTMLResponse)
async def usuarios_lista(request: Request, db: Session = Depends(get_db), ok: str = "", error: str = ""):
    user, redir = _exige_com(request)
    if redir:
        return redir
    cuentas = db.query(Usuario).order_by(Usuario.usuario.asc()).all()
    return templates.TemplateResponse(
        request,
        "usuarios.html",
        {
            "user": user,
            "cuentas": cuentas,
            "delegaciones": DELEGACIONES,
            "ok": ok,
            "error": error,
        },
    )


@app.post("/usuarios", response_class=HTMLResponse)
async def usuarios_crear(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
    delegacion: str = Form(...),
):
    user, redir = _exige_com(request)
    if redir:
        return redir
    username = (username or "").strip().lower()
    delegacion = (delegacion or "").strip().lower()
    if not username or not password:
        return RedirectResponse(url="/usuarios?error=Usuario+y+contraseña+son+obligatorios", status_code=status.HTTP_303_SEE_OTHER)
    if delegacion not in DELEGACIONES:
        return RedirectResponse(url="/usuarios?error=Delegación+no+válida", status_code=status.HTTP_303_SEE_OTHER)
    if delegacion == "comunicaciones":
        return RedirectResponse(url="/usuarios?error=Ya+existe+la+cuenta+de+Comunicaciones", status_code=status.HTTP_303_SEE_OTHER)
    if db.query(Usuario).filter(Usuario.usuario == username).first():
        return RedirectResponse(url="/usuarios?error=Ese+usuario+ya+existe", status_code=status.HTTP_303_SEE_OTHER)
    db.add(Usuario(
        usuario=username,
        password_hash=hash_password(password),
        delegacion=delegacion,
        activo=True,
    ))
    db.commit()
    return RedirectResponse(url="/usuarios?ok=Usuario+creado", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/usuarios/{usuario_id}/eliminar")
async def usuarios_eliminar(request: Request, usuario_id: int, db: Session = Depends(get_db)):
    user, redir = _exige_com(request)
    if redir:
        return redir
    cuenta = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not cuenta:
        return RedirectResponse(url="/usuarios?error=No+existe", status_code=status.HTTP_303_SEE_OTHER)
    if cuenta.usuario == user:
        return RedirectResponse(url="/usuarios?error=No+puedes+eliminar+la+cuenta+con+la+que+entraste", status_code=status.HTTP_303_SEE_OTHER)
    if (cuenta.delegacion or "") == "comunicaciones":
        otras = db.query(Usuario).filter(
            Usuario.delegacion == "comunicaciones", Usuario.id != cuenta.id
        ).count()
        if otras < 1:
            return RedirectResponse(url="/usuarios?error=Debe+quedar+al+menos+una+cuenta+de+Comunicaciones", status_code=status.HTTP_303_SEE_OTHER)
    db.delete(cuenta)
    db.commit()
    return RedirectResponse(url="/usuarios?ok=Usuario+eliminado", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/usuarios/{usuario_id}/desactivar")
async def usuarios_desactivar(request: Request, usuario_id: int, db: Session = Depends(get_db)):
    user, redir = _exige_com(request)
    if redir:
        return redir
    cuenta = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not cuenta:
        return RedirectResponse(url="/usuarios?error=No+existe", status_code=status.HTTP_303_SEE_OTHER)
    if cuenta.usuario == user or (cuenta.delegacion or "") == "comunicaciones":
        return RedirectResponse(url="/usuarios?error=La+cuenta+de+Comunicaciones+no+se+desactiva", status_code=status.HTTP_303_SEE_OTHER)
    cuenta.activo = False
    db.commit()
    return RedirectResponse(url="/usuarios?ok=Usuario+desactivado", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/usuarios/{usuario_id}/activar")
async def usuarios_activar(request: Request, usuario_id: int, db: Session = Depends(get_db)):
    user, redir = _exige_com(request)
    if redir:
        return redir
    cuenta = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if cuenta:
        cuenta.activo = True
        db.commit()
    return RedirectResponse(url="/usuarios?ok=Usuario+activado", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/usuarios/{usuario_id}/clave")
async def usuarios_clave(
    request: Request,
    usuario_id: int,
    db: Session = Depends(get_db),
    password: str = Form(...),
):
    user, redir = _exige_com(request)
    if redir:
        return redir
    if not (password or "").strip():
        return RedirectResponse(url="/usuarios?error=La+clave+no+puede+ir+vacía", status_code=status.HTTP_303_SEE_OTHER)
    cuenta = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not cuenta:
        return RedirectResponse(url="/usuarios?error=No+existe", status_code=status.HTTP_303_SEE_OTHER)
    cuenta.password_hash = hash_password(password.strip())
    db.commit()
    return RedirectResponse(url="/usuarios?ok=Clave+restablecida.+Compártela+por+WhatsApp+al+grupo", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/alertas/leer")
async def alertas_leer(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    delg = request.session.get("delegacion") or "comunicaciones"
    db.query(Alerta).filter(
        Alerta.para_delegacion == delg,
        Alerta.leida == False,
        ~Alerta.tipo.like("cambio-%"),
    ).update({"leida": True}, synchronize_session=False)
    db.commit()
    request.session["n_alertas"] = 0
    return RedirectResponse(url="/dashboard", status_code=303)



@app.get("/config-punto", response_class=HTMLResponse)
async def config_punto_ver(request: Request, db: Session = Depends(get_db), ok: str = "", error: str = ""):
    user, redir = require_com_user(request)
    if redir:
        return redir
    cfg = obtener_config(db)
    doms = db.query(DomingoPunto).order_by(DomingoPunto.fecha).all()
    return templates.TemplateResponse(request, "config_punto.html", {
        "user": user, "cfg": cfg, "domingos": doms, "ok": ok, "error": error,
    })


@app.post("/config-punto")
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


@app.post("/config-punto/domingo")
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
        from .punto import MESES_ES, NOMBRES_DIA
        from .auth import DELEGACIONES
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


@app.post("/config-punto/domingo/{did}/quitar")
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



@app.get("/podio", response_class=HTMLResponse)
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
