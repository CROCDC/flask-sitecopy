# Plan de mejora de calidad

Un relevamiento hecho **usando el producto**, no leyéndolo. Se levantó la demo
(`example.app`) y se la manejó con un navegador real (Chromium vía Playwright): sitio
público, login, editor visual, sheet de texto rico, lista/forms, y viewport de celular.
En paralelo se auditó el código fuente en tres frentes —seguridad, correctitud y
accesibilidad/usabilidad— verificando cada hallazgo contra el código real.

El objetivo es el mismo que `TESTING.md`: **encontrar mejoras concretas**, cada una con
su archivo, su síntoma y su arreglo. No perseguir un número.

## Contexto de la corrida

- **Instalación:** en este entorno el `pip install` global choca con el `blinker` que
  trae Debian (`RECORD file not found`) y aborta. Se resolvió con un venv limpio. Vale la
  pena una línea en el README para quien se cruce con esto.
- **Base sana:** 329 tests pasan; el editor funciona de punta a punta (click-to-edit,
  contador de pendientes, guardar/publicar/descartar, sheet rico con focus trap y Escape);
  0 errores de página JS en toda la exploración.
- **Gating de seguridad verificado en vivo:** `?edit=1` inyecta markers solo con sesión
  (12 vs 0 sin sesión); `?preview=1` autenticado responde con `X-Robots-Tag: noindex` y
  `Cache-Control: no-store`; público es no-op. El sanitizer, el CSRF, el anti-forja de
  markers y la validación de `postMessage` resisten (ver apéndice).

**Lectura de conjunto:** el producto está sólido y las fases de testing previas se nota
que cerraron lo grueso. Lo que sigue son mejoras de borde. El patrón más útil que apareció:
**varias son inconsistencias, no bugs nuevos** — el editor visual ya resuelve bien algo
(foco, `aria-pressed`, target táctil de 44px, `aria-describedby`) que una pantalla vecina
—la lista, el grupo, el preview— todavía no. Cerrar esa brecha es barato y sube la calidad
percibida de todo el panel.

---

## Hallazgos

Severidad: 🔴 alta · 🟠 media · 🟡 baja. Cada uno con archivo:línea y arreglo.

### Correctitud

**C1 · 🟠 El índice `#n` de un campo `lines` se desincroniza cuando un token expande a un
salto de línea → el editor publica la viñeta equivocada.**
`resolver.py:417` (y `:309`). El docstring afirma *"The index addresses the RAW value"*,
pero el `enumerate` corre sobre `str(t(key, **params)).split("\n")` — el valor **ya
interpolado**. Si un token vale `"AA\nBB"` (p. ej. un token que apunta a un campo `text`
multilínea, o un valor restaurado de backup), el render numera más viñetas que las que
tiene el valor crudo que el JS del editor reescribe. Un click en la viñeta renderizada `#1`
edita la línea cruda 1 (la que no era) y deja intacta la clickeada. Es exactamente la
corrupción que el módulo dice prevenir. Sin cobertura de tests.
*Arreglo:* numerar sobre el valor crudo (sin interpolar) que ve el editor, o documentar y
prohibir tokens con `\n` en campos `lines`. Test de regresión primero.

**C2 · 🟡 `publish()` cuenta como "cambiado" un draft que no cambia nada visible.**
`storage.py:228` (SQLAlchemy) y `:377` (Memory). Un draft igual al default de una clave
nunca publicada, o igual al valor publicado actual, devuelve `changed=1` (y reescribe
`previous_value` con el mismo valor). El contador llega al operador como "Se publicó N
texto". `test_publishing_counts_only_what_actually_changed` solo cubre el caso sin draft.
*Arreglo:* incrementar solo cuando el valor efectivo cambia; extender el test.

**C3 · 🟡 Un draft sobre una clave que ya no está en el registry es invisible e
inalcanzable.** `resolver.py:479-483` cuenta iterando `registry.fields`; `publish_all` y
`discard_all` operan sobre `list(registry.fields)`. Pero `store.draft_keys()` devuelve
también los huérfanos (clave renombrada/eliminada). Ese draft nunca se cuenta, ni se
publica, ni se descarta desde la UI: queda para siempre. Inocuo (no renderiza), pero es un
estado que la UI no puede limpiar.
*Arreglo:* una acción de mantenimiento que liste/purgue drafts huérfanos, o incluirlos en
`discard_all`.

**C4 · 🟡 El ABC `TextStore` omite `set_published` y `delete`, que las implementaciones y
los llamadores usan.** `storage.py:45-92`. El docstring dice "cualquier cosa que responda
estos nueve métodos funciona", pero ambos stores implementan `set_published` y solo
`SQLAlchemyStore` tiene `delete`. Un `TextStore` de terceros que implemente exactamente los
nueve abstractos quedaría sin esos dos; y la property-suite cross-store no puede detectar
divergencias de `delete` porque `MemoryStore` no lo tiene.
*Arreglo:* agregar `set_published`/`delete` al ABC (o documentar por qué no), y sumar
`delete` a `MemoryStore` para que la máquina de estados cross-store lo cubra.

### Seguridad

Ninguna vulnerabilidad crítica/alta explotable. Las cuatro son de borde o dependen de
configuración no-default.

**S1 · 🟠 (condicional) `jinja_globals=False` silenciosamente saltea el endurecimiento de
preview y los headers anti-clickjacking.** `extension.py:140-147` + `resolver.py:511-522`.
Con esa opción solo corre `editor_markup.install`; se saltea `resolver.register_jinja` y con
él el `after_request` `_mark_preview`. Resultado: las páginas `?preview=1` (que muestran
copia en borrador) pierden `noindex`/`no-store` —un CDN podría cachear y servir un borrador
al público— y todas las respuestas pierden `X-Frame-Options`/`CSP: frame-ancestors`.
*Arreglo:* registrar `_mark_preview` (y `editor_markup.install`) siempre en `init_app`;
que solo los bindings de globals de Jinja dependan del flag.

**S2 · 🟠 (por diseño) El login de contraseña compartida no tiene rate-limiting ni
lockout.** `auth.py:35-43`, `admin.py:752-767`. El compare es de tiempo constante, pero
`/login` acepta intentos ilimitados: un único secreto compartido sin throttling es
fuerza-bruteable, y detrás está todo el panel.
*Arreglo:* backoff/throttle en `/login`, o dejar documentado que el auth bundled va detrás
de un rate-limiter externo (hoy el docstring no lo dice).

**S3 · 🟡 El canvas de edición reescribe links cross-origin a `target="_blank"` sin
`rel="noopener"`.** `editor-frame.js:646-649`. Reverse tabnabbing vía `window.opener`. Solo
en la vista de edición del admin sobre los links del propio sitio, de ahí la severidad baja.
El sanitizer del server sí agrega `noopener` (`sanitizer.py:125-131`); este es el único path
que no.
*Arreglo:* setear `rel="noopener noreferrer"` junto con `target="_blank"`.

**S4 · 🟡 El login bundled no regenera la sesión al autenticar.** `auth.py:46-47`. Las
cookies firmadas de Flask hacen difícil el fixation (el atacante no puede fijar la cookie de
la víctima), así que es defensa en profundidad.
*Arreglo:* rotar/limpiar la sesión en login exitoso.

### Accesibilidad (WCAG 2.1 AA)

**A1 · 🟠 El switch de formato del preview es un `tablist` falso.** `preview.html:33-49`,
`sitecopy-admin.js:128-138`. WCAG 4.1.2 / 2.1.1. Es `role="tablist"` con `role="tab"` +
`aria-selected`, pero **no hay `role="tabpanel"`** ni `aria-controls`, y `select()` no maneja
roving tabindex ni flechas: un `tablist` promete navegación con flechas que no existe.
Irónico porque el editor **ya lo hace bien** al lado (`editor.html:35` usa `role="group"` +
`aria-pressed` para los mismos botones, con el comentario que explica por qué evitó tabs).
*Arreglo:* copiar el patrón del editor (`role="group"` + `aria-pressed`), o hacerlo un
tablist de verdad (roving tabindex, flechas, `role="tabpanel"` + `aria-controls`).

**A2 · 🟠 El form de grupo no asocia programáticamente los hints ni los errores de
validación.** `group.html:50-73`. WCAG 1.3.1 / 3.3.1 / 3.3.2. El `<p class="hint">` no tiene
`id` y el input no lo referencia con `aria-describedby`; los campos inválidos reciben
`aria-invalid="true"` pero **no hay texto de error** ni `aria-describedby` a una explicación
—la única señal es un borde rojo que un lector de pantalla no percibe. El editor sí cablea
`aria-describedby="edStatus"` (`sitecopy-editor.js:637`); otra brecha con una pantalla que ya
resolvió el problema.
*Arreglo:* `id` en cada hint + `aria-describedby` desde el control (sumando el id del error
cuando es inválido); render de un string de error por campo.

**A3 · 🟠 El flash de éxito y el tag "editado" fallan contraste.** `--adm-ok:#2e7d52`
(`sitecopy-shell.css:20`) usado como texto en `.adm-flash-success` (`:172`) y `.ct-tag-edited`
(`sitecopy-admin.css:59`). WCAG 1.4.3. `#2e7d52` sobre el fondo tintado da **~4.06:1** (texto
normal necesita 4.5:1). El flash de error queda borderline en ~4.44:1.
*Arreglo:* oscurecer `--adm-ok` (p. ej. `#256b45`) o usar un verde específico para
texto-sobre-tinte.

**A4 · 🟠 Los bordes de los controles de formulario quedan por debajo del umbral 3:1.**
`--adm-line: rgba(26,23,20,0.14)` (`sitecopy-shell.css:18`) es lo único que separa un input
de su fondo (input blanco sobre card blanca): `.adm-field input`, `.ct-input`, `.ed-field
input`. WCAG 1.4.11. El borde compuesto da **~1.34:1** contra el blanco, y el relleno del
input iguala al contenedor, así que en reposo no hay otra pista del límite del campo. (El
foco sí está bien: outline de 2px.)
*Arreglo:* borde de reposo más oscuro para inputs (≥3:1), p. ej. `rgba(26,23,20,0.3)`.

**A5 · 🟠 (baja) El sheet rico: `role="textbox"` sobre contenido de bloque + los botones de
formato sin estado presionado.** `editor.html:185-199`. El `contenteditable` con estructura
real (`<h2>`, `<ul>`, `<li>`) lleva `role="textbox"`, que aplana esa estructura para el AT y
es redundante en un contenteditable. Y Negrita/Cursiva/Subtítulo/Párrafo/Lista son toggles
sin `aria-pressed`: nadie refleja el formato del cursor.
*Arreglo:* quitar el `role="textbox"` explícito; en `selectionchange` setear `aria-pressed`
desde `document.queryCommandState`.

**A6 · 🟡 No hay soporte de `prefers-reduced-motion`.** Ninguno de los cuatro CSS lo define y
el JS emite `behavior:"smooth"` incondicional (varios lugares). WCAG 2.3.3.
*Arreglo:* un bloque `@media (prefers-reduced-motion: reduce)` que anule transiciones, y
condicionar el scroll smooth a esa media query.

**A7 · 🟡 Los flashes de resultado y el error de login no se anuncian.** `base.html:61-68`,
`login.html:15-20`. En el flujo de grupo (POST de página completa) el flash es el único
feedback, pero el contenedor no es `role="status"`/`role="alert"`. El error de login tampoco
lo es ni se ata al campo.
*Arreglo:* `role="status"` (éxito) / `role="alert"` (error) en los contenedores; asociar el
error de login vía `aria-describedby` en `#password`.

**A8 · 🟡 Targets táctiles chicos en las pantallas de grupo/preview.** `.ct-restore` es
`min-height:24px` (`sitecopy-admin.css:122`) — y es "Volver al texto original", el control más
usado de esa pantalla. El editor ya subió su equivalente a 44px (`sitecopy-editor.css:172`,
con el comentario explicando por qué). Otra inconsistencia.
*Arreglo:* subir `.ct-restore`, `.ct-filter-clear`, `.ct-preview-reload` a 44px mínimo.

**A9 · 🟡 Las confirmaciones destructivas dependen solo de JS.** `group.html:97-100` (discard
vía `data-ct-confirm`), `index.html:28-38` (publish-all/discard-all vía `onsubmit=confirm`).
Con JS apagado, acciones irreversibles se envían sin confirmar. Inconsistente con el orgullo
no-JS del resto del panel.
*Arreglo:* un paso de confirmación server-side para esas rutas.

**A10 · 🟡 Los contadores de caracteres no se anuncian en vivo.** `.ct-counter`/`.ed-field-count`
actualizan en `input` pero no están en una live region; el rojo "is-over" es la única señal.
*Arreglo:* `aria-live="polite"` en el contador (o anunciar solo al cruzar el límite).

**A11 · 🟡 Falta empty state en el índice.** `index.html:42-63` no renderiza nada si
`groups_by_category` está vacío — solo un header pelado.
*Arreglo:* bloque de estado vacío.

**A12 · 🟡 Nits de semántica.** `.ed-card-kind` ("Resultado en Google"…) son `<p>` que actúan
como títulos → considerar `<h3>`. El `✎` de `index.html:10` sin `aria-hidden` (las tarjetas
sí lo tienen). Los facsímiles de tarjeta social (WhatsApp/Twitter) están bajo 4.5:1 pero es a
propósito (replican el estilo real de esas plataformas).

### Usabilidad / hallazgos de uso en vivo

**U1 · 🟡 La barra de admin muestra "Salir" (logout) y el chrome de admin en la pantalla de
login, sin sesión aún.** `base.html:42` — se muestra con `sitecopy_owns_auth`, no con "está
logueado". Un usuario no autenticado ve un botón de salir.
*Arreglo:* condicionar el chrome de sesión a "está logueado", no a "el panel maneja su auth".

**U2 · 🟡 (demo) `.shipping` verde `#3f7d54` sobre `--paper` `#f7f3ec` da 4.44:1 → falla AA
por poco.** `example/static/site.css:116`. Es la demo, no la librería, pero es la primera
impresión de calidad que da el sitio de ejemplo.
*Arreglo:* un verde de acento apenas más oscuro (o subir el peso a "large text").

**U3 · 🟡 (cosmético) `GET /favicon.ico → 404` en la demo.** Ruido en consola/logs.
*Arreglo:* servir un favicon mínimo en el ejemplo.

---

## Roadmap priorizado (valor / esfuerzo)

Cada casilla es una unidad entregable; el orden es por relación valor/esfuerzo.

### Tanda 1 — Barato y de alto impacto (medio día)
Cerrar las inconsistencias donde una pantalla ya resolvió lo que otra no, más las correcciones
de contraste (todas de una línea de CSS):
- [ ] **A3 + A4 + U2** — subir contrastes: `--adm-ok`, borde de inputs, verde de la demo.
- [ ] **A1** — preview: pasar el `tablist` falso al patrón `role="group"`+`aria-pressed` del editor.
- [ ] **A2** — grupo: `aria-describedby` para hints y errores + texto de error por campo.
- [ ] **A8** — targets táctiles a 44px en grupo/preview.
- [ ] **U1** — no mostrar "Salir"/chrome de sesión en el login.
- [ ] **S3** — `rel="noopener noreferrer"` en el canvas.

### Tanda 2 — Bugs de correctitud (con test de regresión primero)
- [ ] **C1** — desync del índice `#n` en `lines` con tokens multilínea. *(el de mayor riesgo)*
- [ ] **C2** — `publish()` no debe contar drafts que no cambian nada.
- [ ] **S1** — registrar el hardening de preview/clickjacking siempre, no solo con `jinja_globals`.

### Tanda 3 — Robustez y a11y fina
- [ ] **A5** — quitar `role="textbox"`; `aria-pressed` en la toolbar del sheet.
- [ ] **A7 + A10** — live regions para flashes, error de login y contadores.
- [ ] **A6** — `prefers-reduced-motion`.
- [ ] **A9** — confirmación server-side de acciones destructivas (fallback no-JS).
- [ ] **A11 + A12** — empty state del índice y nits de semántica.
- [ ] **C3 + C4** — drafts huérfanos y el contrato del ABC `TextStore`.
- [ ] **S2 + S4** — throttle de login (o documentarlo) y rotación de sesión.

### Tanda 4 — Red de seguridad para todo lo anterior
- [ ] Sumar los tests E2E de las Tandas 1-2 a `tests/e2e/` (el `test_a11y.py` con axe ya existe:
      agregarle las pantallas de preview y grupo, que hoy no cubre).
- [ ] Meter axe en CI como gate (hoy corre pero conviene que falle el build ante violaciones serias).
- [ ] Nota en el README sobre el conflicto de `blinker`/venv en instalaciones tipo Debian.

---

## Apéndice — verificado sólido (no tocar)

Para no gastar esfuerzo en lo que ya está bien, esto se auditó y resiste:

- **Sanitizer server:** la salida solo contiene tags de la allow-list con atributos
  escapados; nunca emite un `<` crudo, así que el XSS por mutación serialize→reparse no es
  alcanzable. `safe_href` colapsa whitespace/control/zero-width antes de leer el esquema y
  rechaza `javascript:`/`data:`/protocol-relative; se re-chequea en render.
- **Forja de markers:** todo write pasa por `_normalize` → `strip_edit_markers`; los markers
  solo se producen en modo edición (requiere admin). Un valor guardado no puede forjar un
  segundo `<ct-t>` ni filtrar codepoints privados al público. Verificado en vivo (0 markers
  sin sesión).
- **CSRF:** `before_request` valida token por sesión en todo método no-safe; forms no-JS y
  login/logout llevan el campo; el editor manda el header. On por default.
- **Gating de preview/edit:** requiere `?edit/preview` **y** sesión de admin. Verificado en
  vivo: `?preview=1` autenticado lleva `noindex`/`no-store`; público es no-op.
- **postMessage:** ambos lados validan `origin` **y** `source`, con tag e `targetOrigin`
  explícito.
- **Tokens:** interpolación de un solo paso, no recursiva, termina; ciclos brand↔tagline no
  cuelgan; token desconocido y llave suelta quedan literales.
- **Máquina de estados draft/publish/preview, `previous_value`/`revert`, scope de publish,
  caché por-request, migración aditiva de `ensure_schema`:** consistentes.
- **Sheet rico:** `role="dialog"` `aria-modal`, focus trap, `inert` en hermanos, foco
  guardado y restaurado, Escape con confirmación de cambios. Sin trampa de teclado.
- **Editor:** device buttons (`role="group"`+`aria-pressed`), panel tablist con roving
  tabindex real, labels asociados, `lang="es"`, skip link, `beforeunload`, live region de
  guardado. Correctos — son el modelo a imitar en las otras pantallas.
