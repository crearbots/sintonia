# Sintonía

Aplicación interna (Python · FastAPI) para coordinar equipos: censo de instalación, programación del punto y avance frente a sede nacional.

Resuelve duplicados (el celular es el identificador), compara el registro interno con el dato oficial de sede nacional y genera un informe semanal listo para pegar en WhatsApp. El acceso es con login: es un **demo operativo**, no un sitio abierto.

Contexto: se usa en campo con listados Excel y metas de instalación. El detalle de la marca del cliente no es el foco de este repo.

**Demo:** [dashboard en Railway](https://infomira-censo-production.up.railway.app/) (pide usuario y contraseña).

Acceso restringido. No es un sitio público.

## Producto

- Carga de Excel sin contar dos veces a la misma persona.
- Dashboard oscuro: avance vs meta, curva interna vs sede nacional, comparación de un periodo (dos puntos) e informe para WhatsApp.
- Personas con búsqueda por nombre o celular (tolera espacios y prefijo 57) y seguimiento de quienes no han instalado.
- Estética: contraste azul / verde, énfasis en el avance y en comparar tramos de la gráfica.

## Instalación local (Linux)

Requisito: Python 3.10+ y Git.

```bash
git clone https://github.com/crearbots/censo.git
cd censo
git checkout master

python3 -m venv env
source env/bin/activate
pip install -r requirements.txt

python3 -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Abrir: http://127.0.0.1:8000/login

Credenciales: variables `APP_USER` y `APP_PASSWORD`. En local, si no las defines, hay valores de desarrollo.

La base SQLite se crea sola. No la subas a GitHub.

## Formato del Excel

Columnas (el orden y las mayúsculas no importan): **Nombre**, **Celular**, **Estado**.

| Estado | Qué ocurre |
|--------|------------|
| Instalada | Entra al censo si el celular es válido (10 dígitos) |
| No instalada | Queda en seguimiento; el motivo se asigna en la app |

- El celular es el identificador. Si ya existe, se actualiza; no se duplica.
- Si la persona ya es Instalada, un listado posterior en “No instalada” no la revierte.
- `fecha_listado` se guarda la primera vez y no se pisa al volver a subir el mismo archivo.

Al cargar: fuente, fecha del listado y equipo.

## Variables de entorno (producción)

| Variable | Uso |
|----------|-----|
| `APP_USER` | Usuario de acceso |
| `APP_PASSWORD` | Contraseña |
| `SECRET_KEY` | Firma de sesiones (fija entre reinicios) |
| `DATABASE_PATH` | Ruta del archivo `.db` |

En Railway el volumen va en `/data` y `DATABASE_PATH=/data/infomira_censo.db`.

## Despliegue

Fuente de verdad: GitHub, rama **`master`**. Un push a `master` dispara el deploy en Railway.

1. Descargar respaldo de la base en producción.
2. Probar en local.
3. `git pull origin master` y push a `master`.
4. Verificar el deploy (login + una revisión del dashboard).

## Versiones

Los cortes estables están en los **tags** del repo (`v1.0.0-mvp` … `v1.6.0` y siguientes). Ver [Releases / tags](https://github.com/crearbots/censo/tags).

## Privacidad

No subir a GitHub bases `.db`, respaldos ni Excel con datos reales.

## UI

- Un botón primario azul por pantalla.
- WhatsApp / copiar informe: verde.
- Eliminar: rojo y siempre con confirmación.

---

Sebastian Romero · [Portafolio](https://crearbots.github.io/portafolio/) · [GitHub](https://github.com/crearbots)
