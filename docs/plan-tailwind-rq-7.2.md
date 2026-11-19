# Plan de implementación — RQ-7.2 Tailwind compilado

Referencia de evaluación: plan interno RQ-7.2 (CDN → `static/css/app.css`, artefacto en Git, Railway sin Node).

Enfoque: **un paso → prueba si la fase lo indica → siguiente paso**.

---

## Resumen de pruebas por fase

| Fase | ¿Probar ahora? | Por qué |
|------|----------------|---------|
| **0** Tooling + primera compilación | **Sí, mínima** | Confirmar que el CLI corre y genera un archivo con tamaño razonable. Aún no hay impacto en la UI. |
| **1** Content / safelist | **Sí, técnica** | Revisar que `app.css` contiene clases críticas (p. ej. `bg-green-600` del dashboard). Sin abrir todas las pantallas. |
| **2** Sustituir CDN en plantillas | **Sí, obligatoria** | Es el cambio visible: una pantalla con CDN + una sin `_base` (login o postular). |
| **3** Artefacto en repo | **No** (solo verificar archivo) | `git status` / tamaño del archivo. La prueba visual ya empezó en fase 2. |
| **4** Documentación | **No** | Lectura humana del README. |
| **5** Aceptación pre-producción | **Sí, completa** | Checklist RQ: Red, consola, paridad visual, WhatsApp. |

---

## Fase 0 — Tooling (solo desarrollo)

**Qué hacer**

1. Crear [`package.json`](../package.json) con `tailwindcss` **3.4.x** (devDependency).
2. Scripts:
   - `npm run build:css` → `tailwindcss -i ./static/css/input.css -o ./static/css/app.css --minify`
   - `npm run watch:css` → mismo con `--watch` (opcional, comodidad local).
3. Crear [`static/css/input.css`](../static/css/input.css):

   ```css
   @tailwind base;
   @tailwind components;
   @tailwind utilities;
   ```

4. `npm install` y primera compilación.

**Qué no hacer**

- No tocar plantillas ni Railway.
- No mover `<style>` de programación/postular a Tailwind.

**Pruebas en esta fase**

| Prueba | Obligatoria |
|--------|-------------|
| `npm run build:css` termina sin error | Sí |
| Existe `static/css/app.css` y pesa orden de **decenas–~200 KB** (minificado) | Sí |
| Abrir la app en local **sin** quitar el CDN aún | No (sigue igual) |

---

## Fase 1 — Configuración `content` y clases dinámicas

**Qué hacer**

1. Crear [`tailwind.config.js`](../tailwind.config.js):

   ```js
   module.exports = {
     content: [
       "./templates/**/*.html",
       "./static/js/**/*.js",
       "./app/**/*.py",
     ],
   };
   ```

2. **Sin** `darkMode` en Tailwind: el tema es [`theme.css`](../static/css/theme.css) + `data-theme`.

3. Regenerar CSS y comprobar clases que vienen de Python ([`app/routes/censo.py`](../app/routes/censo.py)):  
   `bg-green-600`, `bg-green-100`, `bg-green-500`, `bg-green-50`, `bg-amber-400`, `bg-amber-50`, `bg-red-500`, `bg-red-50`.

4. Si alguna falta tras incluir `./app/**/*.py`, añadir `safelist` explícita solo para esas utilidades.

**Qué no hacer**

- No ampliar safelist a ciegas (infla `app.css`).

**Pruebas en esta fase**

| Prueba | Obligatoria |
|--------|-------------|
| `grep -o 'bg-green-600' static/css/app.css` (u otra clase de la barra) encuentra coincidencia | Sí |
| Recompilar tras tocar `tailwind.config.js` | Sí |
| Smoke visual del dashboard | **No todavía** (CDN sigue activo hasta fase 2) |

---

## Fase 2 — Sustituir CDN (3 archivos)

**Qué hacer**

1. Crear partial opcional [`templates/_assets_css.html`](../templates/_assets_css.html):

   ```html
   <link rel="stylesheet" href="/static/css/theme.css">
   <link rel="stylesheet" href="/static/css/app.css">
   ```

2. En [`templates/_base.html`](../templates/_base.html), [`login.html`](../templates/login.html), [`postular.html`](../templates/postular.html):
   - Quitar `<script src="https://cdn.tailwindcss.com"></script>`.
   - Cargar `theme.css` + `app.css` en ese orden (partial o dos `<link>`).

3. En `postular.html` y `login.html`, **no** duplicar `theme.css` si ya se incluye vía partial unificado.

4. Regenerar `app.css` (`npm run build:css`).

**Qué no hacer**

- No editar las 16 plantillas que solo `extend` `_base.html`.
- No meter `app.css` dentro de `_head_icons.html` (mantener CSRF/meta separado).

**Pruebas en esta fase — obligatorias**

| Prueba | Detalle |
|--------|---------|
| **Login** | Layout, inputs, error de credenciales, tema claro/oscuro. |
| **Postular** (Sonido o Baños) | Clave → grilla (`grid`, `rounded`, `hidden` en pasos). Crítico para WhatsApp. |
| **Una pantalla con `_base`** | p. ej. `/dashboard` o `/personas`: nav + cards + responsive `sm:`. |
| **Red (DevTools)** | En esas URLs: **0** peticiones a `cdn.tailwindcss.com`. |
| **Consola** | Sin aviso *"should not be used in production"* de Tailwind. |

Si algo se ve “sin estilo”, parar aquí: casi siempre falta clase en `content`/safelist o no se regeneró `app.css`.

---

## Fase 3 — Artefacto versionado

**Qué hacer**

1. Confirmar `static/css/app.css` está en Git (no en `.gitignore`).
2. Commitear junto con `package.json`, `package-lock.json`, config, plantillas y doc.

**Pruebas en esta fase**

| Prueba | Obligatoria |
|--------|-------------|
| `git grep cdn.tailwindcss` en el repo → vacío | Sí |
| Clonar / checkout limpio, `pip` + arrancar app **sin** `npm install` | Sí (simula Railway) |
| UI igual que al final de fase 2 | Sí (rápido: login + dashboard) |

---

## Fase 4 — Documentación

**Qué hacer**

1. [`README.md`](../README.md): Node solo para CSS; `npm ci` + `npm run build:css`; recordatorio si cambias clases en plantillas/JS/Python.
2. [`AGENTS.md`](../AGENTS.md): stack Tailwind compilado, no CDN.
3. (Opcional) Una línea en checklist pre-push en reglas Cursor cuando existan.

**Pruebas en esta fase**

| Prueba | Obligatoria |
|--------|-------------|
| Seguir README en máquina limpia y regenerar CSS | Recomendada, no bloqueante |
| Revisión humana de comandos | Sí |

---

## Fase 5 — Aceptación pre-producción (RQ completo)

**Cuándo:** después de fases 0–4, antes de push a `master`.

**Checklist visual** (modo claro y oscuro; escritorio + un celular o emulador):

- [ ] Login
- [ ] Dashboard (barra de meta: colores verde/ámbar/rojo según avance)
- [ ] Programación punto
- [ ] Programación Sonido / Baños (sesión)
- [ ] Postulaciones (enlace, plantilla, badges)
- [ ] Enlace postular Sonido y Baños
- [ ] Personas, cargas, usuarios
- [ ] Config punto, podio, informe (si se usan en operación)

**Checklist técnico**

- [ ] Ninguna página pide `cdn.tailwindcss.com`
- [ ] Consola limpia de warning Tailwind CDN
- [ ] Enlace postulación en **navegador interno de WhatsApp** (o dispositivo que antes fallaba)
- [ ] `app.css` tamaño acorde (decenas de KB minificado; no MB)

---

## Después del deploy

- Railway **no** ejecuta `npm`: depende del `app.css` commiteado.
- Cualquier PR que cambie clases Tailwind debe incluir `app.css` regenerado o la UI en prod quedará desfasada.

---

## Orden sugerido para esta sesión

1. **Fase 0** → prueba mínima CLI.
2. **Fase 1** → grep clases barra dashboard.
3. **Fase 2** → prueba obligatoria login + postular + una con nav.
4. **Fase 3 + 4** → commit y docs.
5. **Fase 5** → checklist completo → producción.