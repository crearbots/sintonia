# Plan: unificar postulación Baños / Sonido

Enfoque: **un paso → pruebas en local → siguiente paso**. Sonido solo se modifica cuando el paso busca paridad explícita.

## Por dónde empezamos (recomendado)

**Fase 0** primero: bajo riesgo, quita ruido heredado de Sonido y valida que la grilla Baños sigue bien.

---

## Fase 0 — Limpieza código muerto

**Qué:** Borrar el bloque inalcanzable tras el `return` en `vista_banos` ([`app/services/programacion.py`](app/services/programacion.py) ~líneas 182–261).

**Pruebas:**
- [ ] Usuario `banos` → `/programacion` (Armar semana y Ver para foto).
- [ ] Asignar / quitar un turno.
- [ ] Viernes: semana mostrada = siguiente (regla Baños).

**Criterio listo:** mismo comportamiento que antes; diff solo elimina código muerto.

**Estado:** implementado (código muerto eliminado en `vista_banos`).

---

## Fase 1 — Paridad de UX (sin refactor grande)

Separar en tres entregas probables.

### 1a — Redirects y flujo «Cambiar» (Baños)

**Qué:** Alinear con Sonido:
- `paso != "guardar"` → `?cambiar=1`
- error sin horarios → `?cambiar=1&error=...`

**Archivos:** [`app/routes/programacion.py`](app/routes/programacion.py) (`postular_banos_guardar`).

**Pruebas:**
- [ ] Enlace Baños: clave → celular → marcar horarios → Enviar.
- [ ] Tras resumen, «Cambiar mi postulación» vuelve al formulario de horarios.
- [ ] Enviar sin marcar nada → error y sigues en flujo de horarios.

**Estado:** implementado (`postular_banos_guardar`: `?cambiar=1` en paso distinto de guardar y error sin horarios).

### 1b — Mensajes y resumen tras cambio

**Qué:**
- Baños: en resumen GET, mostrar **Antes / Ahora** si hay `CambioPostulacion` pendiente (como Sonido en `postular_ver`).
- Sonido: tras pedir cambio, mensaje explícito *«Cambio enviado. El coordinador lo confirma.»* (hoy usa mensaje genérico de guardado).
- Opcional: mismo texto de aviso tarde / coordinador(a) consistente.

**Archivos:** `postular_banos_ver`, `postular_ver`, plantillas.

**Pruebas:**
- [ ] Primera postulación → resumen con lista de horarios.
- [ ] Segundo envío distinto → mensaje de cambio + alerta en dashboard Baños/Sonido.
- [ ] Segundo envío igual → sin alerta duplicada (ya existe).
- [ ] Domingo ≥14:00 → aviso tarde en ambos.

**Estado:** implementado (resumen Antes/Ahora en Baños; Sonido con mensaje «Cambio enviado…» al pedir cambio).

### 1c — Texto de alerta al coordinador

**Qué:** Baños use `_texto_vigente` + detalle como Sonido en el texto de alerta («Antes / Ahora»).

**Archivos:** `postular_banos_guardar`; reutilizar helpers de [`app/routes/sonido.py`](app/routes/sonido.py) (`_texto_vigente`, `_lineas_detalle`).

**Pruebas:**
- [ ] Dashboard cuenta Baños/Sonido → tarjeta de cambio muestra antes y ahora coherentes.

**Estado:** implementado (alerta Baños con `Antes` / `Ahora` vía `_texto_vigente` y `_texto_huecos`).

---

## Fase 2 — Utilidades compartidas (sin mover rutas)

**Qué:**
- `semana_para_labor(labor)` en [`app/punto.py`](app/punto.py).
- `enlace_valido(db, token, labor)` en [`app/postulacion.py`](app/postulacion.py) (reemplaza `_enlace_sonido` / `_enlace_banos`).
- Constante o dict `LABOR_TEMPLO` con: path público, nombre equipo, `asegurar_equipo`, rol por defecto, función semana.

**Pruebas:** repetir checklist corto de Fase 1 en ambos enlaces.

**Estado:** implementado (`semana_para_labor`, `enlace_valido`, `url_enlace_postulacion`, `LABORES_ENLACE`).

---

## Fase 3 — Ubicación de rutas

**Qué:** Mover `GET/POST /postular-banos/*` de [`programacion.py`](app/routes/programacion.py) a [`sonido.py`](app/routes/sonido.py) o nuevo [`app/routes/postulacion.py`](app/routes/postulacion.py); registrar router en `main.py`.

**Pruebas:** mismas URLs; smoke de punto (`/programacion` delegación COM) sin regresiones.

**Estado:** implementado — rutas en [`app/routes/postulacion.py`](app/routes/postulacion.py), router en `main.py`.

---

## Fase 4 — Handler único en Python

**Qué:** `postulacion.py` (o módulo dedicado) con `construir_ctx_postular`, `guardar_postulacion_enlace` parametrizado por `labor`; rutas finas que solo delegan.

**Pruebas:** matriz labor × escenarios (1ª vez, cambio, no puedo, celular nuevo, tarde).

**Estado:** implementado — [`app/services/enlace_postulacion.py`](app/services/enlace_postulacion.py) (`construir_ctx_postular`, `revisar_celular_enlace`, `guardar_postulacion_enlace`, helpers de texto); rutas en [`postulacion.py`](app/routes/postulacion.py) y [`sonido.py`](app/routes/sonido.py) delegan.

---

## Fase 5 — Plantilla única

**Qué:** Un `postular.html` con `labor` y partial para filas (con/sin rol); un solo JS o partial con flags `tiene_rol`, `path_enlace`.

**Pruebas:** visual + mismos escenarios en móvil; CSRF y `/revisar` iguales.

**Estado:** implementado — `templates/postular.html`, partial `_postular_filas.html`, `static/js/postular.js`; `postular_banos.html` eliminado.

---

## Qué no tocar en fases 0–1

- Panel `/postulaciones` (ya unificado).
- `banos_cambio` / `sonido_cambio` en dashboard.
- Grilla del punto / `DELEGACIONES_EQUIPOS`.
- Regla viernes Baños vs sábado Sonido.

---

## Orden sugerido para esta sesión

1. **Fase 0** (empezar aquí).
2. **Fase 1a** (impacto directo en quien usa el enlace Baños).
3. **Fase 1b** → **1c** según prioridad del equipo (coordinadoras vs colaboradores).

Cuando confirmes, implementamos **solo Fase 0** y te dejamos el checklist de prueba marcado.