# Testing

The suite is built to **find bugs**, not to chase a percentage. This is what it covers,
how to run it, and the reasoning behind how it is put together.

## Running it

```bash
pip install -e ".[test]"          # library + Flask-SQLAlchemy + pytest + hypothesis
pytest                            # the default run: unit + integration, no browser

pip install -e ".[test,e2e]"      # adds pytest-playwright + axe-core
playwright install chromium
pytest tests/e2e -m e2e           # the browser suite
```

The browser tests are behind a marker, and `addopts = -m "not e2e"` in `pyproject.toml`
keeps them out of the default run — a contributor with no Chromium still gets a green
`pytest`. The trailing `-m e2e` above overrides that default.

Coverage runs with `pytest --cov`. `fail_under` is a **ratchet**: it only ever moves up.

## What is covered

| layer | size | what it exercises |
|---|---|---|
| Python unit + integration | 636 tests, **98.7%** branch coverage | resolver, storage, sanitizer, admin endpoints, registry, collections, sizes, CSRF |
| Browser E2E — editor | 35 tests | click-to-edit, media, the draft → preview → publish → undo flow, validation |
| Browser E2E — UX | 42 tests | what a person perceives: keyboard-only operation, focus, 44px touch targets, no horizontal scroll at 390px, computed font sizes, the no-JavaScript path |
| Browser E2E — a11y | 14 checks | axe-core over login, index, group, editor, preview |
| Property-based | `hypothesis` | `lines` splitting, normalization idempotence, token graphs, a cross-store state machine |
| Security | XSS corpus + fuzzing | sanitizer on save *and* on render, `url`/media scheme guards, CSRF on every mutating route |

CI runs the default suite across Python 3.10–3.13 and the E2E job with Chromium, on every
push.

What remains uncovered is a handful of statements unreachable from the public API: a store
that contradicts itself, a `publish` that would have to be a real change and a no-op at
once, and the version fallback for a source tree that was never installed.

## Principles

1. **A bug found is a regression test first.** The test fails, *then* the fix. Every entry
   in the log below was found this way.
2. **Test the contract, not the implementation.** `test_storage.py` runs every test against
   both stores; that is the model.
3. **Invariants over examples.** An example proves a case, a property proves a rule. Prefer
   properties wherever the input space is large — text, tokens, HTML.
4. **Security is tested adversarially.** A payload corpus and fuzzing, not a couple of
   polite `<script>` tags.
5. **Assert what a person perceives.** The markup being right is not the same as the page
   being right — see the responsive-image bug below, where every assertion passed while the
   screen was wrong.

## Bugs this suite has caught

Kept because each one names a *class* of bug worth keeping a guard against, and because
several were invisible to line coverage.

| found by | the bug |
|---|---|
| property-based | `lines` split with `str.splitlines()` in one place and `"\n"` in another — an exotic Unicode separator broke the render |
| cross-store state machine | `MemoryStore.get()` returned the internal object, `SQLAlchemyStore.get()` a copy: two implementations of one contract, diverging |
| offensive security | no CSRF on the mutating routes; now a per-session token on all of them |
| axe-core | a real WCAG AA contrast failure — the "Editor visual" button rendered dark ink on terracotta, 3.2:1 |
| mutation testing | dead code (`returns_to_default`, computed and never read) plus ~12 real test gaps, including a whitespace bypass in `safe_href` |
| UX suite | `image`/`video` fields rendered as `<input type="url">`, so a site path (`/static/hero.jpg` — the documented value) failed the browser's native validation and **the whole section screen could not be saved from a browser**. Every test passed: they all posted to the endpoint directly |
| E2E, asserting `currentSrc` | a picture with a `srcset` never changed on the canvas — the browser paints a variant and never reads `src`, so setting the attribute did nothing visible while the panel reported the edit. See the [integration guide](INTEGRATION.md#responsive-images-need-a-guard) |

The last two are the same lesson twice: a test that drives the API instead of the browser,
or reads an attribute instead of what was painted, will confirm a page that is broken.

## Risk map

Where to aim a new test, by module.

| module | what can go wrong |
|---|---|
| `resolver` / tokens | interpolation order, tokens referencing each other, cycles, `{year}`, unknown token left literal, per-call tokens, stray braces, escape-before-sanitize |
| `lines` | Unicode separators, CRLF, edge whitespace, empty middle lines, `#n` index mapping |
| `sanitizer` / `rich` | XSS corpus, unclosed tags eating text, idempotence, silent loss of visible text, unsafe `href`, entities, deep nesting |
| `url` / `image` / `video` | schemes, `//`, `\`, control chars, unicode look-alikes, fallback to the default on render |
| draft / publish / preview | the full state machine, session gating, `?preview=0/false/off`, `previous_value` round trip |
| publish scope | a colleague's draft must not ride along, key dedup, keys that no longer exist |
| `storage` | `ensure_schema` idempotence, column migration, `table_name`, unicode keys, length limits, both stores identical |
| admin endpoints | auth on every route, CSRF, content type, huge payloads, malformed JSON, keys outside the registry |
| editor shell (JS) | pending count, undo, the flush race, double-click, `beforeunload`, device switch, panel tabs and search |
| editor frame (JS) | click mapping, passthrough on interactive elements, navigation, token dependents, sanitized paste, focus trap, **parked responsive sources** |

## Known gap

Component-level tests for `postMessage` need a JS runner (jest/vitest), which the project
does not carry. Today the `event.source`/origin checks are covered by code review, and the
security-critical path (`sanitizeRich` in the admin origin) by the E2E suite.
