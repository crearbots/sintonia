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

@router.get("/dashboard", response_class=HTMLResponse)
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
    if delg == "sonido":
        from ..postulacion import generar_alertas_silencio
        generar_alertas_silencio(db)
    alertas = db.query(Alerta).filter(Alerta.para_delegacion == delg, Alerta.leida == False).order_by(Alerta.id.desc()).all()
    from .sonido import _vistas_cambio, _vistas_silencio
    _vistas_cambio(db, alertas)
    _vistas_silencio(db, alertas)
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

@router.get("/upload", response_class=HTMLResponse)
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


@router.post("/upload", response_class=HTMLResponse)
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

@router.get("/pendiente/{persona_id}", response_class=HTMLResponse)
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


@router.post("/pendiente/{persona_id}", response_class=HTMLResponse)
async def editar_pendiente_submit(
    request: Request,
    persona_id: int,
    nombre: str = Form(...),
    celular: str = Form(...),
    db: Session = Depends(get_db)
):
    from ..processing import normalizar_celular, normalizar_nombre

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

@router.get("/informe", response_class=HTMLResponse)
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








@router.post("/pendiente/{persona_id}/eliminar")
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



@router.get("/seguimiento/{persona_id}", response_class=HTMLResponse)
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


@router.post("/seguimiento/{persona_id}", response_class=HTMLResponse)
async def seguimiento_submit(
    request: Request,
    persona_id: int,
    motivo: str = Form(...),
    motivo_detalle: str = Form(""),
    celular: str = Form(""),
    db: Session = Depends(get_db),
):
    from ..processing import normalizar_celular, normalizar_nombre

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

@router.get("/dato-nacional", response_class=HTMLResponse)
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


@router.post("/dato-nacional", response_class=HTMLResponse)
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


@router.get("/persona/{persona_id}", response_class=HTMLResponse)
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


@router.post("/persona/{persona_id}", response_class=HTMLResponse)
async def editar_persona_submit(
    request: Request,
    persona_id: int,
    nombre: str = Form(...),
    celular: str = Form(""),
    estado: str = Form(...),
    db: Session = Depends(get_db),
):
    from ..processing import normalizar_celular, normalizar_nombre

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


@router.post("/persona/{persona_id}/eliminar")
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


@router.get("/personas", response_class=HTMLResponse)
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
        from ..processing import normalizar_celular
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

@router.get("/cargas", response_class=HTMLResponse)
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

@router.get("/backup")
async def crear_backup(request: Request):
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    # Respaldo solo por Railway. Código histórico debajo no se ejecuta.
    """Descarga una copia de la base de datos (usa DATABASE_PATH en producción)."""
    import shutil
    import tempfile
    from fastapi.responses import FileResponse
    from datetime import datetime
    from ..database import DB_PATH

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

