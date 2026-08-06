# Plan de testing a fondo

Un plan orientado a **encontrar bugs**, no a perseguir un porcentaje. Está pensado para
esta librería en concreto: sus módulos, sus zonas de riesgo reales y los huecos que hoy
tiene la suite. Cada fase termina en una *sesión de caza*: correr lo nuevo, hacer triage,
y por cada bug real dejar un test de regresión **antes** del fix.

## Progreso

- ✅ **Fase 0 — CI + medición.** GitHub Actions (matriz 3.10–3.13) corre pytest con
  cobertura de ramas y umbral (`fail_under`, un ratchet que solo sube), más un job E2E que
  instala Chromium. Extras `test`/`e2e` y config en `pyproject.toml`.
- ✅ **Fase 1 — E2E del editor.** `tests/e2e/`: server demo en subproceso con DB temporal
  y 13 tests de navegador sobre los flujos del editor, con aislamiento por test.
- ✅ **Fase 2 — property-based.** `tests/test_properties.py`: split de `lines`,
  normalización idempotente, round-trip, tokens, **fuzz del sanitizer** (Fase 3 adelantada)
  y una **máquina de estados que exige que `MemoryStore` y `SQLAlchemyStore` no diverjan** —
  la forma general del bug de `get()`. Cobertura de ramas 91.9% → **93.1%**.
- ✅ **Fase 3 — seguridad ofensiva.** **CSRF cerrado**: token por sesión validado en toda
  ruta mutante (header en el fetch del editor, campo oculto en los forms), con `test_csrf.py`
  y validado end-to-end por la E2E (demo con CSRF encendido). Corpus de 19 payloads XSS
  conocidos contra el sanitizer. Doble-sanitización del manifest: un valor rich sucio ya no
  llega crudo al `innerHTML` del editor. `csrf.py` al 100%; cobertura 93.1% → **93.5%**.
- ⏭️ **Siguiente:** Fase 4 (postMessage a nivel componente, a11y con axe, concurrencia
  multi-worker) y Fase 5 (mutation testing).

## Estado actual (línea de base)

Medido con `pytest --cov`:

| capa | estado | nota |
|------|--------|------|
| Python (unit + integración) | **94%**, 182 tests, 8 archivos | sólido |
| JavaScript del editor (~2300 líneas) | **0% automatizado** | solo pruebas manuales con navegador |
| E2E de navegador en el repo | **no hay** | el flujo real nunca se testea en CI |
| CI (GitHub Actions) | **no hay** | los tests no corren solos en cada push |
| Property-based / fuzzing | **no hay** | — |
| `sanitizer.py` (seguridad) | 89% | crítico: merece corpus adversarial |

**Por qué importa el enfoque, no el número:** los dos bugs reales de la última auditoría
no eran líneas sin cubrir, eran *clases* de bug que la cobertura por líneas no ve:

- `lines` se partía con `str.splitlines()` en un lado y `"\n"` en otro → **input exótico**
  (un separador Unicode pegado) rompía el render. → pide *property-based testing*.
- `MemoryStore.get()` devolvía el objeto interno y `SQLAlchemyStore.get()` una copia →
  **divergencia entre dos implementaciones del mismo contrato**. → pide *tests de
  contrato cross-implementación como invariantes*.

El plan prioriza justo esas dos técnicas, más la deuda de E2E del editor.

---

## Principios

1. **Un bug encontrado = un test de regresión primero.** El test falla, después el fix.
2. **Testear el contrato, no la implementación.** Lo que ya hace `test_storage.py`
   (correr cada test sobre ambos stores) es el modelo a extender.
3. **Invariantes por sobre ejemplos.** Un ejemplo prueba un caso; una propiedad prueba una
   regla. Preferir propiedades donde el espacio de entrada es grande (texto, tokens, HTML).
4. **La seguridad se testea siendo adversarial.** Corpus de payloads + fuzzing, no un par
   de `<script>` amables.
5. **El editor es mitad del producto y hoy no tiene red.** El JS necesita su propia suite.

---

## Fases (roadmap priorizado)

Orden por relación valor/esfuerzo. Cada casilla es una unidad de trabajo entregable.

### Fase 0 — Red de seguridad: CI + medición  ·  *rápido, desbloquea todo*

- [ ] `.github/workflows/ci.yml`: matriz Python 3.10–3.13, corre `pytest` en cada push/PR.
- [ ] Job de cobertura con umbral que falla por debajo de la línea de base (`--cov-fail-under=93`).
- [ ] Instalar Playwright + Chromium en el runner para habilitar la Fase 1 en CI.
- [ ] Reporte de cobertura visible (artifact o summary) para ver regresiones de un vistazo.

### Fase 1 — E2E del editor (Playwright)  ·  *la deuda más grande*

Convertir la exploración manual de la auditoría en una suite estable y versionada. Un
`conftest` que levanta la app demo en un puerto efímero con DB temporal, y `pytest-playwright`.

- [ ] **Infra:** fixture que arranca `example.app` (DB temporal) + `page`/`canvas` helpers.
- [ ] Click-to-edit: encabezado, botón, párrafo; el panel y el canvas quedan sincronizados.
- [ ] Campo token (`global.brand`): editar re-renderiza dependientes en vivo (footer, `{brand}`).
- [ ] Campo `lines`: editar una línea deja intactas las demás; vaciar una no borra la siguiente.
- [ ] Campo `rich` de bloque: click abre el *sheet*; negrita/link/listas; contador visible.
- [ ] `Escape` cancela sin dejar pendiente; `Enter` en `line` cierra y avisa.
- [ ] Flujo completo: **guardar → previsualizar → publicar → deshacer → descartar**, verificando
      el sitio público en una pestaña aparte en cada paso.
- [ ] Validación: vaciar un requerido y publicar → bloquea, nombra el campo, marca inválido.
- [ ] `max_length`: contador en rojo y publicación bloqueada.
- [ ] Contenido externo (`external_content`): click en la ficha muestra "sale del catálogo".
- [ ] Copia invisible: click en la imagen abre el alt en el panel; `<title>`/meta en el panel.
- [ ] Navegación dentro del canvas mantiene el editor vivo y los pendientes.
- [ ] Selector de dispositivo (Celular/Tablet/Escritorio/Auto) sin romper layout.
- [ ] Tarjetas de compartir (Google/WhatsApp/Twitter) se arman del `<title>`/meta del documento.
- [ ] Accesibilidad por teclado: Tab a un `ct-t`, Enter edita, foco atrapado en el sheet.
- [ ] `beforeunload` avisa con cambios sin guardar.
- [ ] **Cero errores de consola** durante toda la corrida (aserto global).

### Fase 2 — Property-based (Hypothesis)  ·  *donde se esconden los bugs de datos*

- [ ] **`lines`**: para todo texto, `t_lines(store(x))` coincide con partir por `"\n"` como
      lo hace el editor JS; ningún separador Unicode produce viñetas de más. *(regresión del bug)*
- [ ] **Idempotencia de normalización**: `_normalize(_normalize(x)) == _normalize(x)`.
- [ ] **Round-trip de resolución**: `t(publish(x)) == esperado` para cualquier `x`, incluyendo
      emoji, combinantes, RTL, control chars, valores larguísimos.
- [ ] **Tokens**: para cualquier grafo de tokens declarado, la interpolación termina (no cuelga
      con referencias mutuas), respeta el orden, y un token desconocido queda literal.
- [ ] **Máquina de estados draft/publish** (`hypothesis.stateful`): secuencias arbitrarias de
      `set_draft/publish/revert/discard` mantienen los invariantes: el público nunca ve un
      draft; `previous_value` siempre permite volver un paso; "restaurar original" = borrar fila.
- [ ] **Contrato cross-store como propiedad**: la misma secuencia sobre `MemoryStore` y
      `SQLAlchemyStore` produce estados observables idénticos (`as_map`, `get`, `draft_keys`,
      `previous_map`). *(generaliza la regresión de `get()`)*

### Fase 3 — Seguridad ofensiva

- [ ] **Corpus XSS** para `rich`: payloads conocidos (OWASP, `cure53/DOMPurify` fixtures) —
      cada uno debe salir inerte tras sanitizar en save **y** en render.
- [ ] **Fuzz del sanitizer** (Hypothesis con HTML generado): nunca produce `<script>`,
      manejadores `on*`, `javascript:`, ni pierde texto visible sin avisar; es idempotente.
- [ ] **`url`**: esquemas raros, `//`, `\`, control chars, unicode look-alikes → cae al default.
- [ ] **Doble sanitización**: un valor sucio inyectado directo en la DB (backup/UPDATE manual)
      no llega crudo ni al público ni al `innerHTML` del editor (cerrar el hueco del manifest).
- [ ] **CSRF** *(hallazgo abierto de la auditoría)*: test que un form POST cross-site a
      `/discard`, `/publish` y el POST de grupo **no** muta estado; fijar la defensa elegida
      (token o `SameSite`) y testearla.
- [ ] **Auth**: cada ruta mutante exige sesión; una sesión vencida responde 401/JSON a un fetch.
- [ ] **Markers**: un valor con codepoints privados (``) guardado no puede forjar un
      segundo `<ct-t>` ni filtrar tofu al público.

### Fase 4 — Front-end fino, a11y y concurrencia

- [ ] **postMessage** (component-level, jsdom o Playwright): origen y `source` validados en
      ambos lados; un frame hermano no puede manejar `set`/`openRich`. *(regresión del hallazgo)*
- [ ] **`sanitizeRich`/`safeHref` del cliente**: mismo corpus XSS que el server, en el origen admin.
- [ ] **Race del `flush`**: click en Guardar mientras se tipea no pierde el último caracter ni
      duplica el publish (doble-click).
- [ ] **a11y con axe-core**: login, índice, grupo, editor y preview sin violaciones serias;
      foco atrapado en el sheet; roles de tabs y roving tabindex.
- [ ] **Concurrencia multi-worker**: dos "procesos" (dos app contexts) comparten una DB; un
      publish en uno se ve en el otro en el request siguiente (la caché es por-request, no de proceso).
- [ ] **i18n/encoding**: emoji, RTL, combinantes y valores muy largos renderizan y editan sin romper.

### Fase 5 — Calidad de los tests

- [ ] **Mutation testing** (`mutmut` sobre `sanitizer.py`, `resolver.py`, `storage.py`):
      medir si los tests realmente matan mutantes; subir el score donde sobrevivan.
- [ ] Revisar ramas sin cubrir que quedan: `admin.py` 240-244/471-477, `sanitizer.py` 143-145/
      168/172-174, `editor_markup.py` 257-258 — decidir caso por caso si son test o código muerto.

---

## Matriz de riesgo por módulo

Referencia rápida de *qué puede salir mal* en cada zona, para dirigir el diseño de tests.

| módulo | escenarios adversariales que hay que cubrir |
|--------|---------------------------------------------|
| `resolver` / tokens | orden de interpolación, tokens que se referencian entre sí, ciclos, `{year}`, token desconocido literal, per-call tokens, llaves sueltas, escape-antes-de-sanitizar |
| `lines` | separadores Unicode, CRLF, blancos al borde, líneas vacías intermedias, mapeo de índice `#n`, edición concurrente de dos líneas |
| `sanitizer` / `rich` | corpus XSS, tags sin cerrar que se comen texto, idempotencia, pérdida de texto visible, `href` inseguro, entidades, mojibake, anidamiento profundo |
| `url` | esquemas, `//`/`\`, control chars, unicode, fallback al default en render |
| draft/publish/preview | máquina de estados completa, gating por sesión, `?preview=0/false/off`, `previous_value` ida y vuelta |
| publish scope | draft de un colega no viaja, dedup de keys, `IN()` grande, keys que no existen |
| `storage` | `ensure_schema` idempotente, migración de columna, `table_name`, keys unicode, límites de largo, ambos stores idénticos |
| endpoints admin | auth en cada ruta, **CSRF**, content-type, payload gigante, JSON malformado, más keys que el registry |
| editor shell (JS) | `pending`, undo, race del flush, doble-click, `beforeunload`, ajuste de dispositivo, tabs/búsqueda del panel, tarjetas |
| editor-frame (JS) | mapeo del click, lone-editable en heading centrado, passthrough de elementos interactivos, navegación, dependientes de token, paste sanitizado, focus-trap del sheet |

---

## Definición de "a fondo" (criterios de salida)

- CI verde en cada push, en toda la matriz de Python.
- Cobertura Python **≥ 97%**, y **100%** en las ramas de `sanitizer`, resolución y publish.
- E2E cubre los flujos de la Fase 1 y corre en CI sin errores de consola.
- Corpus de seguridad con **0 escapes** en server y cliente.
- Mutation score objetivo **≥ 85%** en los tres módulos núcleo.
- Cada bug encontrado durante la ejecución del plan queda con su test de regresión.

## Herramientas a incorporar

`pytest-cov` · `pytest-playwright` · `hypothesis` · `axe-core` (vía Playwright) · `mutmut`
· GitHub Actions. Todas como extras de desarrollo, sin tocar las dependencias de runtime.
