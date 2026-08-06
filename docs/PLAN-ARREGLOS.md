# Plan de arreglos

Cómo cerrar cada hallazgo de [`MEJORAS.md`](MEJORAS.md). Este documento es el de
**ejecución**: por cada ítem, el archivo, el cambio concreto y el test. El orden respeta
las cuatro tandas del roadmap (valor/esfuerzo), con **regresión primero** para los bugs.

Convención de trabajo (igual que `TESTING.md`): un bug = un test que falla, después el fix.
Todo sobre la rama de la tarea; cada tanda es un commit (o un PR) autocontenido.

---

## Estado de ejecución

Ejecutado en esta rama. Todo verde: **340 tests unit/integración + 18 E2E** (con CSRF
encendido, en navegador real), y **axe en 0 violaciones serias** en todas las pantallas.

| ítem | estado | commit / nota |
|------|--------|---------------|
| A3, A4, U2 (contrastes) | ✅ | `--adm-ok`, nueva var `--adm-input-line`, verde de la demo |
| A1 (preview tablist falso) | ✅ | `role=group` + `aria-pressed` |
| A2 (grupo: hints/errores) | ✅ | `aria-describedby` + `.ct-error` por campo |
| A8 (targets 44px) | ✅ | `.ct-restore`, `.ct-filter-clear`, `.ct-preview-reload` |
| U1 (chrome de sesión en login) | ✅ | `sitecopy_logged_in` |
| S3 (`rel=noopener` en canvas) | ✅ | editor-frame.js |
| C1 (índice de `lines` con tokens) | ✅ | `_raw_lines`; regresión nueva |
| C2 (publish cuenta no-ops) | ✅ | ambos stores; regresión cross-store |
| S1 (hardening de preview siempre) | ✅ | `harden_responses`; regresión |
| A5 (role=textbox / toolbar aria-pressed) | ✅ | `syncToolbar` |
| A6 (prefers-reduced-motion) | ✅ | CSS admin+canvas, scrolls JS |
| A7, A10 (live regions) | ✅ | flashes, error de login, contadores |
| A11, A12 (empty state, nits) | ✅ | índice + `✎` decorativo |
| C3 (drafts huérfanos) | ✅ | site-wide sobre `draft_keys()`; regresiones |
| C4 (contrato `TextStore`) | ✅ | `MemoryStore.delete` + doc del ABC |
| S2, S4 (login: doc rate-limit + rotación) | ✅ | `auth.py`; regresión |
| **A9 (confirmación server-side sin JS)** | ⏸️ **diferido** | ver abajo |

**Por qué A9 quedó fuera:** es el único ítem que **cambia el flujo de UX sin JavaScript**
—necesita una página de confirmación intermedia, tocar las tres rutas destructivas, el JS
(para que el usuario con JS no vea doble confirmación) y su propia E2E—, para un valor bajo:
con JS —la práctica totalidad de los usuarios— las confirmaciones ya funcionan
(`onsubmit=confirm` y `data-ct-confirm`). Meterlo apurado al final arriesgaba los flujos de
publish/discard, que están bien cubiertos. Merece ser un cambio propio y deliberado. El
diseño recomendado (patrón del campo `confirmed`) queda descrito en la Tanda 3, ítem A9.

---

## Tanda 1 — Barato y de alto impacto

Sin cambios de comportamiento salvo los visuales/ARIA. Mayormente CSS y plantillas.

### T1.1 · Contrastes (A3, A4, U2)
- **A3** `src/sitecopy/static/css/sitecopy-shell.css:21` — `--adm-ok: #2e7d52` → **`#256b45`**
  (verde que supera 4.5:1 sobre el tinte del flash de éxito y del tag "editado"). Verificar
  también `.adm-flash-error` (`--adm-err:#c0392b`); si queda <4.5:1 sobre su tinte, oscurecer
  a `#a5311f`.
- **A4** `sitecopy-shell.css:18` — `--adm-line: rgba(26,23,20,0.14)` → **`rgba(26,23,20,0.3)`**
  (borde de input ≥3:1 en reposo). Esta var alimenta `.adm-field input`, `.ct-input` y
  `.ed-field input`, así que un solo cambio cubre las tres pantallas. Revisar que el borde de
  separadores decorativos que también use `--adm-line` no quede demasiado marcado; si molesta,
  separar en dos vars (`--adm-line` para inputs, `--adm-hair` para separadores).
- **U2** (demo) `example/static/site.css:7` — `--accent: #3f7d54` → **`#356b48`** para que
  `.shipping` supere 4.5:1 sobre `--paper`.
- **Verificación:** re-correr `a11y.py` (axe) sobre las pantallas; `.shipping` y los flashes
  deben salir sin `color-contrast`. Snapshot de contraste con la fórmula WCAG.

### T1.2 · Preview: tablist falso → patrón del editor (A1)
- `src/sitecopy/templates/sitecopy/preview.html:33-49` — cambiar `role="tablist"`/`role="tab"`/
  `aria-selected` por **`role="group"` + `aria-pressed`**, replicando `editor.html:35-45`
  (los device buttons). Aplica a los dos switches (dispositivo y tarjetas).
- `src/sitecopy/static/js/sitecopy-admin.js:128-138` — en `select()`, togglear `aria-pressed`
  en vez de `aria-selected`; quitar cualquier expectativa de roving tabindex.
- **Verificación:** axe sin `aria-required-children`/`aria-required-parent`; teclado: Tab llega
  a cada botón, Enter/Espacio activa, no se promete navegación con flechas.

### T1.3 · Grupo: asociar hints y errores (A2)
- `src/sitecopy/templates/sitecopy/group.html:50-73` — a cada `<p class="hint">` darle
  `id="hint-{{ f.key }}"`; en el `input`/`textarea` agregar
  `aria-describedby="hint-{{ f.key }}{% if invalid %} err-{{ f.key }}{% endif %}"`. Cuando
  `invalid`, renderizar `<p class="ct-error" id="err-{{ f.key }}">{{ mensaje }}</p>` y mantener
  `aria-invalid="true"`. Reusar el patrón que ya usa el editor (`sitecopy-editor.js:637`,
  `aria-describedby="edStatus"`).
- El texto de error puede venir del mismo lugar que hoy decide `invalid` (el server valida
  requeridos/`max_length`); pasar el string al template.
- **Verificación:** axe; con lector de pantalla el hint y el error se anuncian con el campo.

### T1.4 · Targets táctiles a 44px (A8)
- `src/sitecopy/static/css/sitecopy-admin.css` — `.ct-restore` (`:122`), `.ct-filter-clear`
  (`:76`), `.ct-preview-reload` (`:159`) → `min-height:44px` (y `min-width` donde sea icónico),
  copiando lo que ya hizo `.ed-field-restore` (`sitecopy-editor.css:172`).
- **Verificación:** medir el bounding box en Playwright ≥44px.

### T1.5 · No mostrar chrome de sesión en el login (U1)
- El flag `sitecopy_owns_auth` (`admin.py:389`) dice "el panel maneja su auth", no "hay
  sesión". Agregar al contexto un `sitecopy_logged_in = state.is_logged_in()` (ya existe
  `state.is_logged_in`, `state.py:34`) y en `base.html:42` gatear el form de logout con
  **`{% if sitecopy_owns_auth and sitecopy_logged_in %}`**. Evaluar si "Ver la web" también
  debería ocultarse en el login (probablemente sí: es chrome de panel).
- **Verificación:** `GET /login` sin sesión no trae el form de logout (grep del HTML); con
  sesión sí. Test de admin.

### T1.6 · `rel="noopener noreferrer"` en el canvas (S3)
- `src/sitecopy/static/js/editor-frame.js:646-649` — donde se setea `target="_blank"`, setear
  además `rel="noopener noreferrer"`. Alinea con lo que ya hace el sanitizer del server
  (`sanitizer.py:125-131`).
- **Verificación:** en la E2E, los `<a>` cross-origin del canvas tienen el `rel`.

---

## Tanda 2 — Bugs de correctitud (test primero)

### T2.1 · C1 — índice `#n` de `lines` desincronizado con tokens multilínea  *(el de mayor riesgo)*
**Causa:** `resolver.py:417` numera las viñetas partiendo el valor **interpolado**
(`t(key).split("\n")`), pero el editor JS (`editor-frame.js:106-108`,
`linesOf(CURRENT[key])`) reescribe por índice sobre el valor **crudo**. Si un token expande a
`\n`, los índices divergen. `t_lines` (`:309`) tiene el mismo problema para el render público.

**Fix (una regla en los tres lados: partir el crudo, después interpolar por línea):**
1. Agregar un helper en `resolver.py`, p. ej.:
   ```python
   def _interpolated_lines(key, **params):
       tokens = _tokens_for(key, params)          # globales + per-call, como en t()
       raw = effective(key)                        # crudo, sin interpolar (ya se usa en t())
       return [ _interpolate(line, tokens) for line in str(raw).split("\n") ]
   ```
   (extraer el armado de `tokens` que hoy vive inline en `t()` `:252-262` a `_tokens_for`
   para reusarlo sin duplicar el strip de markers de los params.)
2. `t_lines` (`:308-309`) → `return [s.strip() for s in _interpolated_lines(key, **params) if s.strip()]`.
3. `editable_lines` (`:414-421`) → enumerar `_interpolated_lines(...)`, `strip`, y por cada no
   vacía `_wrap(key, text, line=index)`. El `index` ahora direcciona el crudo, igual que el JS.
4. Corregir el docstring: ahora sí "the index addresses the RAW value".

**Efecto:** un token con `\n` queda contenido en su viñeta (su `\n` colapsa como espacio en
HTML), y editor, render público y JS cuentan lo mismo. El caso normal (sin tokens con salto)
no cambia.

**Test primero** (`tests/test_editor_markup.py` y/o `test_properties.py`): campo `lines`
`"start {brand} end\nsecond"` con `brand="AA\nBB"`; afirmar que `editable_lines` produce 2
ítems con `line=0` y `line=1`, que el `line=1` mapea a `"second"`, y que `t_lines` da 2 ítems.
Propiedad Hypothesis: para todo valor y todo token, `len(editable_lines)` (sin blancos) ==
número de líneas crudas no vacías, y el índice de cada una recupera la línea cruda correcta.

### T2.2 · C2 — `publish()` no debe contar drafts que no cambian nada
- `src/sitecopy/storage.py:228` (SQLAlchemy) y `:377` (Memory) — incrementar `changed` solo
  cuando el valor publicado efectivo cambia (comparar draft contra el `published_value`/default
  vigente antes de promover). No reescribir `previous_value` con un valor idéntico.
- **Test primero:** extender `test_publishing_counts_only_what_actually_changed` con: draft ==
  default en clave nunca publicada → `changed==0`; draft == published actual → `changed==0`.
  Correr sobre ambos stores (ya es el patrón de `test_storage.py`).

### T2.3 · S1 — registrar el hardening de preview/clickjacking siempre
- `src/sitecopy/extension.py:140-147` — mover el registro de `_mark_preview` (el `after_request`
  de `resolver.py:511-522`) y de `editor_markup.install` **fuera** del branch de `jinja_globals`,
  a `init_app` incondicional. Que solo los *bindings de globals de Jinja* dependan del flag.
- **Test primero:** app con `jinja_globals=False`; `?preview=1` autenticado debe traer
  `X-Robots-Tag: noindex` y `Cache-Control: no-store`, y toda respuesta `X-Frame-Options`/CSP.

---

## Tanda 3 — Robustez y a11y fina

- **A5** `editor.html:197-199` — quitar el `role="textbox"` explícito del `contenteditable`;
  en `sitecopy-editor.js`, en `selectionchange` del sheet, setear `aria-pressed` de cada botón
  de la toolbar desde `document.queryCommandState(...)`.
- **A7 + A10** — `base.html:61-68` `.adm-flashes` → `role="status"` (y `role="alert"` para el
  contenedor de error); `login.html:15-20` error con `role="alert"` + `aria-describedby` en
  `#password`; contadores `.ct-counter`/`.ed-field-count` → `aria-live="polite"`.
- **A6** — un bloque `@media (prefers-reduced-motion: reduce)` en `sitecopy-shell.css` que anule
  `transition`/`animation`; en el JS gatear `behavior:"smooth"` con
  `matchMedia("(prefers-reduced-motion: reduce)")`.
- **A9** *(diferido — ver "Estado de ejecución")* — confirmación server-side para
  discard/publish-all/discard-all (hoy solo JS). Diseño recomendado, patrón del campo
  `confirmed`: la ruta destructiva, si el POST no trae `confirmed=1`, renderiza una página de
  confirmación que re-postea la misma acción con `confirmed=1`; y el handler JS
  (`data-ct-confirm` + los `onsubmit=confirm` del índice, unificados a `data-ct-confirm`)
  inyecta ese campo al aceptar, para que el usuario con JS nunca vea doble confirmación. El
  path JSON del editor (`/discard` scoped) queda intacto. Necesita: una plantilla `confirm.html`,
  el chequeo en las tres rutas, el ajuste del JS, y E2E para ambos caminos (con y sin JS).
- **A11 + A12** — empty state en `index.html:42-63`; `.ed-card-kind` `<p>` → `<h3>`; `aria-hidden`
  en el `✎` de `index.html:10`.
- **C3** — acción de mantenimiento que liste/purgue drafts huérfanos (claves fuera del registry),
  o incluirlos en `discard_all`.
- **C4** — sumar `set_published`/`delete` al ABC `TextStore` (`storage.py:45-92`) y `delete` a
  `MemoryStore`, para que la property-suite cross-store cubra `delete`.
- **S2 + S4** — throttle/backoff en `/login` (o documentar el rate-limiter externo en el
  docstring de `auth.py`); rotar la sesión en login exitoso (`auth.py:46-47`).

---

## Tanda 4 — Red de seguridad

- Sumar E2E de las Tandas 1-2 a `tests/e2e/`: extender `test_a11y.py` (axe) a las pantallas de
  **preview** y **grupo**, que hoy no cubre; test de teclado para el nuevo `role="group"` del
  preview; test del `rel` en el canvas.
- Meter axe en CI como **gate** que falle ante violaciones serias (hoy corre en el job e2e pero
  conviene el umbral duro).
- Nota en el README sobre el conflicto `blinker`/venv en entornos tipo Debian.

---

## Orden sugerido de commits

1. `fix(a11y): contrastes AA (flash, tag, borde de input) + verde de la demo`  (T1.1)
2. `fix(a11y): preview con role=group, grupo con aria-describedby, targets 44px`  (T1.2–T1.4)
3. `fix(ux): ocultar chrome de sesión en login; rel=noopener en el canvas`  (T1.5–T1.6)
4. `fix: índice de lines direcciona el valor crudo (tokens multilínea)`  (T2.1, con test)
5. `fix: publish no cuenta drafts sin cambio; hardening de preview siempre`  (T2.2–T2.3)
6. Tanda 3 en commits temáticos; Tanda 4 al final.

Cada commit deja verde `pytest` (incluido `--cov-fail-under=94`) y no baja el ratchet de
cobertura.
