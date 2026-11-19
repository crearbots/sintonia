# Sintonía

Aplicación interna (Python · FastAPI) para coordinar equipos: censo de instalación de la app, programación del punto de información y avance frente a sede nacional.

Documentación de producto y despliegue: `README.md`. Este archivo guía a quien modifica el código.

## Stack

- FastAPI + SQLAlchemy + SQLite
- Jinja2 (templates) + Tailwind (CDN) + Chart.js
- Python 3.12
- Variables de entorno obligatorias: `APP_USER`, `APP_PASSWORD`, `SECRET_KEY`
- Opcional: `DATABASE_PATH` (por defecto `infomira_censo.db` en la raíz del proyecto)
- Deploy en Railway

## Desarrollo local

### Variables de entorno

1. Copia `.env.example` a `.env` en la raíz del proyecto (`.env` no se sube a Git).
2. Ajusta valores solo en tu máquina. En cada arranque, la contraseña de `APP_USER` se sincroniza con `APP_PASSWORD`.
3. La app carga `.env` al importar el paquete `app` (vía `python-dotenv`).

| Variable | Uso |
| --- | --- |
| `APP_USER` | Usuario principal (cuenta Comunicaciones) |
| `APP_PASSWORD` | Contraseña de esa cuenta en local |
| `SECRET_KEY` | Firma de cookies de sesión (valor largo y aleatorio en producción) |
| `DATABASE_PATH` | Ruta del SQLite (opcional) |

### Arranque

**Linux / macOS**

```bash
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
cp .env.example .env   # editar .env
python3 -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

**Windows (PowerShell)**

```powershell
python -m venv env
.\env\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # editar .env
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

También: `python run.py` (mismo servidor con recarga).

Login: http://127.0.0.1:8000/login con `APP_USER` / `APP_PASSWORD`.

No subas `*.db`, `.env` ni Excel reales.

### Buenas prácticas al desarrollar

- Probar cambios en local antes de pushear a `master` (dispara deploy en Railway).
- Cambios pequeños y revisables; no tocar archivos innecesarios.
- No agregar dependencias sin acordarlo.
- Respetar tema oscuro/claro y diseño responsive.
- Reutilizar funciones existentes (`normalizar_celular`, `semana_vigente`, `postulaciones_persona`, etc.).
- Rutas solo Comunicaciones: ver `COM_ONLY_PREFIXES` en `app/auth.py` (cargas, personas, usuarios, config-punto, etc.).

## Estructura

```
app/
  main.py              # FastAPI, middlewares, routers, migraciones SQLite al startup
  models.py            # Persona, Usuario, Turno, Postulacion, Carga, etc.
  auth.py              # login, delegaciones, permisos, usuario por defecto
  deps.py              # templates Jinja, require_login / require_com_user
  database.py          # SQLite y DATABASE_PATH
  processing.py        # normalizar celular y nombre, lectura de Excel
  postulacion.py       # claves de enlace, postulaciones por labor
  punto.py             # semana vigente, horarios del día, festivos, config punto
  festivos.py          # festivos de Colombia
  csrf.py              # middleware CSRF
  services/
    programacion.py        # armado de vistas de grilla (punto, sonido, baños)
    enlace_postulacion.py  # GET/POST del enlace público (Sonido y Baños)
  routes/
    auth.py            # login / logout
    censo.py           # dashboard, personas, cargas, informe
    programacion.py    # punto de información + grilla/asignar Baños en sesión
    postulacion.py     # enlace público /postular-banos/{token}
    sonido.py          # enlace público /postular/{token}, panel Sonido y Baños
    usuarios.py        # gestión de usuarios (solo comunicaciones)
templates/             # pantallas Jinja2
static/                # css, js, logo
```

## Tres ámbitos de «programación»

No son intercambiables. **Si el pedido habla de programación y no nombra el equipo, pregunta a cuál pertenece** antes de modificar código. A futuro pueden sumarse más equipos con el mismo criterio.

| Ámbito | Qué es | Dónde vive |
| --- | --- | --- |
| **Punto de información** | Varias delegaciones (COM, POL, JUV, ELE, FIMLM) en la misma grilla semanal | `/programacion`, `Turno.delegacion` ≠ `sonido`/`banos` en la lógica del punto |
| **Sonido** | Equipo aparte: roles, postulación pública, panel del equipo | `labor` / `delegacion` `sonido`, `routes/sonido.py`, enlace `/postular/{token}` |
| **Baños** | Equipo aparte, flujo distinto al de Sonido | `labor` / `delegacion` `banos`, `programacion.py` + `sonido.py`, `/postular-banos/{token}` |

**Nunca mezcles datos** entre Sonido y Baños ni entre un equipo y el punto de información.

## Identificador

El **celular normalizado** (10 dígitos, sin espacios ni prefijo 57) es la llave de la persona.  
Nunca dupliques una persona. Si ya existe, actualiza.  
Si ya está como `Instalada`, un listado posterior no la puede pasar a `No instalada`.

## Delegaciones y permisos

- `comunicaciones`: acceso completo (censo, cargas, personas, programación del punto, usuarios, config-punto, etc.).
- `banos` y `sonido`: su formulario de postulación y su panel. No ven el censo completo ni las cargas.
- `politica`, `juventudes`, `electoral`, `fimlm`: dashboard y programación del **punto**. No suben listados.
- **FIMLM** en el punto:
  - Por defecto **convivencia desactivada** (`convivencia_fimlm` en `/config-punto`): si FIMLM reserva una franja, el resto de delegaciones **no** entra ahí. Si FIMLM toma un horario ya ocupado, puede desplazar a otros (confirmación + alertas).
  - Con convivencia **activada** (excepción temporal): COM y otras delegaciones pueden compartir la misma hora con FIMLM.
  - FIMLM **no** tiene el límite de 2 turnos por semana (sí aplica al resto del punto, salvo Sonido/Baños con sus propias reglas).

## Labores (Baños y Sonido)

- Cada uno tiene su enlace (`/postular-banos/{token}` y `/postular/{token}`), su clave mensual y su mensaje de WhatsApp.
- Las postulaciones se filtran siempre por `labor` (`banos` o `sonido`).
- El formulario de Baños muestra solo la hora. El de Sonido muestra el rol (Sonido / Cámara).
- Si la persona está en ambos equipos, al entrar a un formulario solo ve sus horarios de esa labor.
- Al terminar el formulario, la persona queda en la pantalla de resumen. No vuelve al paso del celular.

## Reglas de programación (punto y calendario)

- Semana vigente: lunes a domingo. Sábado o domingo se edita la semana siguiente (punto y Sonido). **Baños:** desde el viernes (`semana_para_labor("banos")` en `punto.py`). Enlaces: `enlace_valido`, `url_enlace_postulacion` en `postulacion.py`.
- Horarios **de referencia**: Lun–Vie 7:00 y 18:00, Sáb 17:00, Dom 8:00 y 11:00. La grilla real depende de **config del punto** (vigencia, días habilitados Lun–Sáb, domingos manuales, festivos Colombia).
- Festivo entre semana: 8:00 y 17:00. El día se marca como festivo.
- En el punto, un colaborador máximo **2 veces por semana** (FIMLM exento).
- «Esta semana no puedo apoyar» desmarca y oculta los demás horarios.

## Censo

- Columnas esperadas (insensible a mayúsculas): Nombre, Celular, Estado (`Instalada` / `No instalada`).
- `fecha_listado` en **Persona**: se guarda la primera vez que queda asignada y **no se sobrescribe** en cargas posteriores (trazabilidad de gráfica e informe).
- La gráfica y el informe semanal se basan en el historial de cargas, no solo en el estado actual de la persona.

## Estilo (respuestas del agente)

- Responde y explica en español.
- No hardcodees «Sonido» cuando el contexto es Baños (y viceversa).