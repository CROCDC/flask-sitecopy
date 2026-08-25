// sitecopy — the visual editor shell (the chrome around the live site).
//
// Owns the pending changes and the saving; the in-frame script (editor-frame.js)
// owns the clicking and typing. They talk over postMessage, same-origin only.

(function () {
  "use strict";

  const root = document.querySelector("[data-ed]");
  if (!root) return;

  // A user who asked the OS for less motion gets an instant jump, not a smooth glide.
  const reduceMotion = window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : { matches: false };
  const scrollBehavior = () => (reduceMotion.matches ? "auto" : "smooth");

  const iframe = root.querySelector("[data-ed-iframe]");
  const stage = root.querySelector("[data-ed-stage]");
  const frame = root.querySelector("[data-ed-frame]");
  const panel = root.querySelector("[data-ed-panel]");
  const statusEl = root.querySelector("[data-ed-status]");
  const saveBtn = root.querySelector("[data-ed-save]");
  const publishBtn = root.querySelector("[data-ed-publish]");
  const pendingBadge = root.querySelector("[data-ed-pending]");
  const hiddenBadge = root.querySelector("[data-ed-hidden-count]");
  const discardBtn = root.querySelector("[data-ed-discard]");
  const undoBtn = root.querySelector("[data-ed-undo]");
  const findDraftsBtn = root.querySelector("[data-ed-find-drafts]");
  const SAVE_URL = root.dataset.saveUrl;
  const UPLOAD_URL = root.dataset.uploadUrl;
  const MEDIA_VERSIONS_URL = root.dataset.mediaVersionsUrl;
  // The CSRF token for this session, rendered into the shell. Sent on every mutating
  // fetch; the server rejects a POST without it. A cross-site page can force a request
  // but cannot read this to include it.
  const CSRF = root.dataset.csrf || "";
  const jsonHeaders = { "Content-Type": "application/json", "X-Sitecopy-CSRF": CSRF };

  // key -> the value the user has typed but not saved yet. A text size lives in here
  // too, under the same sibling key the store uses (`size:<key>`), so it rides every
  // path a text already takes: staged, counted, saved, published, discarded.
  const pending = Object.create(null);
  const SIZE_PREFIX = "size:";

  /** The field a pending key belongs to — itself, or the field whose size it is. */
  function ownerOf(key) {
    return key.indexOf(SIZE_PREFIX) === 0 ? key.slice(SIZE_PREFIX.length) : key;
  }
  let manifest = { fields: {}, inlineKeys: [], hiddenKeys: [] };
  // key -> the field's own render(), so a change made anywhere (canvas, undo)
  // refreshes its counter. It used to only run on the textarea's own `input`.
  const renderers = Object.create(null);
  // Keys with a draft on the server that this page never rendered (staged on another
  // page, or in an earlier session). They still count and they still publish.
  let serverDrafts = [];
  // What the last Publicar put live, so it can be taken back from the same bar.
  let undoKeys = [];
  let inFlight = false;
  // Metadata for pending keys the current page does not render, so the panel can
  // still show (and fix) them.
  let extraFields = {};
  // Keys this page renders only inside an attribute or a <title> — see renderFields.
  let invisibleKeys = new Set();
  (function seedPending() {
    const node = root.querySelector("[data-ed-pending-state]");
    if (!node) return;
    try {
      const state = JSON.parse(node.textContent || "{}");
      serverDrafts = state.pendingKeys || [];
      extraFields = state.pendingFields || {};
    } catch (_) {}
  })();

  /* ---------------- status ---------------- */

  function setStatus(text, isError) {
    statusEl.textContent = text || "";
    statusEl.classList.toggle("is-error", Boolean(isError));
    // Focus moves out before hiding: taking away the element the keyboard is standing
    // on drops focus to <body>, the same way a disabled Publicar used to.
    if (undoBtn && !undoBtn.hidden) {
      if (document.activeElement === undoBtn) {
        statusEl.setAttribute("tabindex", "-1");
        statusEl.focus();
      }
      undoBtn.hidden = true;
    }
    if (findDraftsBtn && !serverDrafts.length) findDraftsBtn.hidden = true;
    // An error now names the field AND the screen it lives on, which wraps the toolbar
    // onto a second line. The panel is positioned off the toolbar's height.
    syncBarHeight();
  }

  /** Fields with something unsaved. A text and its size are ONE change to the person
   *  who made them — counting them as two is how "te quedaron 2 cambios" ends up over
   *  a list with one row in it. The server folds them the same way. */
  function dirtyCount() {
    const owners = new Set();
    Object.keys(pending).forEach((key) => owners.add(ownerOf(key)));
    return owners.size;
  }

  /** Everything that would go live on Publicar: what is typed here plus what is
   *  already drafted on the server. A union, because the same key can be in both —
   *  adding the two counts instead counted one pending text as two. */
  function pendingKeys() {
    const keys = new Set();
    // Field keys only: the server publishes a text together with its size, so sending
    // the size key as well would just name the same change twice in the confirm.
    Object.keys(pending).forEach((key) => keys.add(ownerOf(key)));
    serverDrafts.forEach((key) => keys.add(key));
    return Array.from(keys);
  }

  function syncButtons() {
    const dirty = dirtyCount();
    const total = pendingKeys().length;
    saveBtn.disabled = inFlight || dirty === 0;
    publishBtn.disabled = inFlight || total === 0;
    pendingBadge.textContent = String(total);
    pendingBadge.hidden = total === 0;
    if (discardBtn) {
      // Its space is RESERVED (see .ed-btn.is-off), because a button appearing here
      // re-wrapped the whole toolbar: Guardar and Publicar jumped ~630px sideways and
      // 59px down, out from under the cursor that was going to click them. `hidden`
      // stays in the markup for the no-JS case and is dropped here.
      discardBtn.hidden = false;
      discardBtn.classList.toggle("is-off", total === 0);
    }
    // Showing Descartar adds a whole row to the fixed mobile bar, and the page reserves
    // exactly its height at the bottom.
    syncBarHeight();
  }

  /** A draft saved in an earlier session renders on the canvas exactly like the live
   *  copy, so without this the only clue that customers are not seeing it yet was a
   *  number on the Publicar button. */
  function draftNotice() {
    if (!serverDrafts.length) return "";
    if (findDraftsBtn) findDraftsBtn.hidden = false;
    return serverDrafts.length === 1
      ? "Tenés 1 texto guardado sin publicar: lo ves acá, pero tus clientes todavía no."
      : "Tenés " + serverDrafts.length +
        " textos guardados sin publicar: los ves acá, pero tus clientes todavía no.";
  }

  if (findDraftsBtn) {
    // Knowing a draft exists without knowing WHICH one left only two ways to find out,
    // and both of them were buttons you would not press just to look around.
    findDraftsBtn.addEventListener("click", () => {
      setPanel(true, false);
      if (filter) filter.value = "";
      // selectTab ends in applyFilter(), which un-hides everything the tab allows —
      // so the draft-only pass below has to come after it, not before.
      selectTab("all");
      root.querySelectorAll("[data-ed-fields] .ed-field").forEach((item) => {
        if (!hasDraft(item.dataset.edField)) item.hidden = true;
      });
      if (noResults) noResults.hidden = true;
      const first = root.querySelector("[data-ed-fields] .ed-field:not([hidden])");
      if (first) first.scrollIntoView({ block: "center", behavior: scrollBehavior() });
    });
  }

  window.addEventListener("beforeunload", (event) => {
    if (dirtyCount() === 0) return;
    event.preventDefault();
    event.returnValue = "";
  });

  /* ---------------- device frame ---------------- */

  let barSyncQueued = false;

  /** Publish the toolbar's real height so the panel can sit below it, and the height of
   *  the mobile action bar so the page can reserve exactly that much. Hard-coding either
   *  is what put the panel's close button under the toolbar, and the field being edited
   *  under the action bar.
   *
   *  Coalesced to one measurement per frame: the callers sit on the per-keystroke path
   *  and each read below forces a synchronous reflow. */
  function syncBarHeight() {
    if (barSyncQueued) return;
    barSyncQueued = true;
    window.requestAnimationFrame(() => {
      barSyncQueued = false;
      const bar = root.querySelector(".ed-bar");
      if (bar) root.style.setProperty("--ed-bar-h", Math.round(bar.offsetHeight) + "px");
      const actions = root.querySelector(".ed-actions");
      if (!actions) return;
      // Only below the breakpoint do the actions detach into a fixed bottom bar, and
      // its height changes with how many buttons are showing.
      const floating = window.getComputedStyle(actions).position === "fixed";
      root.style.setProperty(
        "--ed-actions-h",
        floating ? Math.round(actions.offsetHeight) + "px" : "0px"
      );
    });
  }

  /** The tallest the canvas may be without pushing the admin page into scrolling
   *  (which would slide the sticky toolbar over the panel's header). */
  /** The tallest the canvas may be without pushing the admin page into scrolling
   *  (which slides the sticky toolbar over the panel's header — its close button
   *  included; the panel cannot dodge it, because it is as tall as its own containing
   *  block and `sticky` has nowhere to move it to).
   *  Measured, not the old constant 210: that was the chrome of a ONE-ROW toolbar, and
   *  the bar grows a second row as soon as there is something to publish. */
  function viewportHeight() {
    // Where the toolbar does NOT float over the content (a phone: static bar, panel
    // under the canvas) the page is meant to scroll, and a taller canvas is the point.
    const bar = root.querySelector(".ed-bar");
    if (!bar || window.getComputedStyle(bar).position !== "sticky") {
      return Math.max(420, window.innerHeight - 210);
    }
    // Only what sits ABOVE the canvas is measured, and only that: it is the part that
    // grew (the toolbar now holds a second row open). Deriving the space BELOW from the
    // page height is self-referential — it moves with the canvas, so each call shrank it
    // a little further and the canvas collapsed to its 420px floor.
    const above = stage.getBoundingClientRect().top + window.scrollY;
    const footnote = root.querySelector(".ed-footnote");
    const below = footnote ? Math.ceil(footnote.getBoundingClientRect().height) + 24 : 56;
    return Math.max(420, window.innerHeight - above - below);
  }

  function fitDevice(button) {
    const mode = button.dataset.edDevice;
    root.querySelectorAll("[data-ed-device]").forEach((other) => {
      const on = other === button;
      other.classList.toggle("is-current", on);
      other.setAttribute("aria-pressed", on ? "true" : "false");
    });
    syncBarHeight();
    const available = viewportHeight();
    if (panel) panel.style.maxHeight = available + "px";

    if (mode === "fluid") {
      stage.classList.add("is-fluid");
      frame.style.width = "";
      frame.style.transform = "";
      frame.style.height = available + "px";
      stage.style.height = "";
      return;
    }
    stage.classList.remove("is-fluid");
    const width = parseInt(button.dataset.width, 10);
    const height = parseInt(button.dataset.height, 10);
    const scale = Math.min(1, (stage.clientWidth - 26) / width, available / height);
    frame.style.width = width + "px";
    frame.style.height = height + "px";
    frame.style.transform = "scale(" + scale + ")";
    // transform doesn't affect layout, so the stage needs the scaled height.
    stage.style.height = height * scale + 26 + "px";
  }

  root.querySelectorAll("[data-ed-device]").forEach((button) => {
    button.addEventListener("click", () => fitDevice(button));
  });
  window.addEventListener("resize", () => {
    const current = root.querySelector("[data-ed-device].is-current");
    if (current) fitDevice(current);
  });

  /* ---------------- page picker ---------------- */

  const pagePicker = root.querySelector("[data-ed-page]");
  if (pagePicker) {
    pagePicker.addEventListener("change", () => {
      // Pending edits live in the shell, and the frame re-applies them on load, so
      // switching pages never loses what was typed.
      iframe.src = pagePicker.value + "?edit=1";
    });
  }

  /* ---------------- panel ---------------- */

  const panelToggle = root.querySelector("[data-ed-panel-toggle]");
  const panelClose = root.querySelector("[data-ed-panel-close]");

  function setPanel(open, takeFocus) {
    panel.hidden = !open;
    panelToggle.setAttribute("aria-expanded", open ? "true" : "false");
    const current = root.querySelector("[data-ed-device].is-current");
    if (current) window.setTimeout(() => fitDevice(current), 0);
    if (!open) {
      panelToggle.focus();
      return;
    }
    // Below the stacking breakpoint the panel opens hundreds of pixels down the
    // page: the toggle looked dead because nothing on screen changed. And leaving
    // focus on the toggle meant tabbing past the whole site to reach the panel.
    window.setTimeout(() => {
      panel.scrollIntoView({ block: "nearest", behavior: scrollBehavior() });
      if (takeFocus === false) return;
      const first = panel.querySelector("[data-ed-tab], input, textarea, button");
      if (first) first.focus();
    }, 30);
  }

  panelToggle.addEventListener("click", () => setPanel(panel.hidden));
  panelClose.addEventListener("click", () => setPanel(false));

  // "Todos", not "No visibles": applyFilter() filters by TAB before it filters by query,
  // so opening on the narrow tab made the search answer "no results" for a text that is
  // right there on the page. The badge on the "No visibles" tab still sells it.
  let currentTab = "all";

  function selectTab(name) {
    currentTab = name;
    root.querySelectorAll("[data-ed-tab]").forEach((tab) => {
      const on = tab.dataset.edTab === name;
      tab.classList.toggle("is-current", on);
      tab.setAttribute("aria-selected", on ? "true" : "false");
      tab.tabIndex = on ? 0 : -1;
    });
    const showCards = name === "cards";
    root.querySelector('[data-ed-tabpanel="fields"]').hidden = showCards;
    root.querySelector('[data-ed-tabpanel="cards"]').hidden = !showCards;
    root.querySelector("[data-ed-note]").hidden = name !== "hidden";
    if (showCards) fillCards();
    else applyFilter();
  }

  const tabButtons = Array.from(root.querySelectorAll("[data-ed-tab]"));
  tabButtons.forEach((tab, index) => {
    tab.addEventListener("click", () => selectTab(tab.dataset.edTab));
    // Roving tabindex + arrow keys: the role promised this and nothing implemented it,
    // so all three were separate tab stops and the arrows did nothing.
    tab.addEventListener("keydown", (event) => {
      const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      if (!step) return;
      event.preventDefault();
      const next = tabButtons[(index + step + tabButtons.length) % tabButtons.length];
      selectTab(next.dataset.edTab);
      next.focus();
    });
  });

  // Metadata for every key this session has touched. `manifest.fields` only knows the
  // page on screen and `extraFields` only knows keys with a draft on the SERVER, so an
  // edit typed here and not saved vanished from the panel the moment you changed page:
  // it still counted, it still published, and the confirm printed its raw key.
  const seenFields = Object.create(null);

  function fieldFor(key) {
    return manifest.fields[key] || extraFields[key] || seenFields[key];
  }

  /** How to name a text to someone who has to go and find it. Twelve fields are called
   *  "Antetítulo", so the label alone made the confirm read "· Antetítulo ·
   *  Antetítulo · Antetítulo". The panel already prints exactly this under every label. */
  function fieldName(key) {
    const field = fieldFor(key);
    if (!field) return key;
    const where =
      field.section && field.section !== field.groupTitle
        ? field.groupTitle + " · " + field.section
        : field.groupTitle;
    return where ? field.label + " (" + where + ")" : field.label;
  }

  /** Saved, but the shop still shows the old wording. */
  function hasDraft(key) {
    return serverDrafts.indexOf(key) !== -1;
  }

  function valueOf(key) {
    return key in pending ? pending[key] : (fieldFor(key) || {}).raw || "";
  }

  /** The size the panel should show as chosen: what was picked here, else what loaded. */
  function sizeOf(key) {
    const staged = pending[SIZE_PREFIX + key];
    return staged != null ? staged : (fieldFor(key) || {}).size || "base";
  }

  /* ---------------- media (image/video) controls ---------------- */

  /** Preview + upload + version gallery for a media field. Returns the preview element,
   *  which the field's render() keeps in sync with the URL input. */
  function buildMediaControls(wrap, field, key, input) {
    const isVideo = field.type === "video";

    const preview = document.createElement(isVideo ? "video" : "img");
    preview.className = "ed-media-preview";
    preview.hidden = true;
    if (isVideo) {
      preview.controls = true;
      preview.muted = true;
      preview.playsInline = true;
      preview.preload = "metadata";
      // A video announces readiness with loadeddata, not load.
      preview.addEventListener("loadeddata", () => { preview.hidden = false; });
    } else {
      preview.alt = "";
      preview.addEventListener("load", () => { preview.hidden = false; });
    }
    preview.addEventListener("error", () => { preview.hidden = true; });
    wrap.appendChild(preview);

    const bar = document.createElement("div");
    bar.className = "ed-media-tools";

    // Upload is only offered when the host wired a FileStore (the URL is rendered then).
    if (UPLOAD_URL) {
      const fileInput = document.createElement("input");
      fileInput.type = "file";
      fileInput.accept = isVideo ? "video/*" : "image/*";
      fileInput.hidden = true;
      const uploadBtn = document.createElement("button");
      uploadBtn.type = "button";
      uploadBtn.className = "ed-media-btn";
      uploadBtn.textContent = isVideo ? "Subir un video" : "Subir una imagen";
      uploadBtn.addEventListener("click", () => fileInput.click());
      fileInput.addEventListener("change", () => {
        const file = fileInput.files && fileInput.files[0];
        if (file) uploadMedia(key, file, uploadBtn);
        fileInput.value = ""; // let the same file be re-picked after an error
      });
      bar.appendChild(uploadBtn);
      bar.appendChild(fileInput);
    }

    const gallery = document.createElement("div");
    gallery.className = "ed-media-gallery";
    gallery.hidden = true;

    const versionsBtn = document.createElement("button");
    versionsBtn.type = "button";
    versionsBtn.className = "ed-media-btn";
    versionsBtn.textContent = "Ver versiones anteriores";
    versionsBtn.setAttribute("aria-expanded", "false");
    let loaded = false;
    versionsBtn.addEventListener("click", () => {
      const open = gallery.hidden;
      gallery.hidden = !open;
      versionsBtn.setAttribute("aria-expanded", open ? "true" : "false");
      if (open && !loaded) {
        loaded = true;
        loadVersions(key, gallery);
      }
    });
    bar.appendChild(versionsBtn);

    wrap.appendChild(bar);
    wrap.appendChild(gallery);
    return preview;
  }

  function uploadMedia(key, file, btn) {
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Subiendo…";
    const form = new FormData();
    form.append("key", key);
    form.append("file", file);
    // No Content-Type header: the browser sets the multipart boundary. Only the CSRF
    // token rides along, same as every other mutating request.
    fetch(UPLOAD_URL, { method: "POST", headers: { "X-Sitecopy-CSRF": CSRF }, body: form })
      .then((r) => r.json().catch(() => null))
      .then((payload) => {
        btn.disabled = false;
        btn.textContent = label;
        if (!payload || !payload.ok) {
          setStatus(
            (payload && payload.errors && payload.errors.join(" ")) ||
              "No pudimos subir el archivo.",
            true
          );
          return;
        }
        // Point the field at the uploaded file — stageChange syncs the input, the canvas
        // and the counter, and marks it pending.
        stageChange(key, payload.url);
        setStatus("Archivo subido. Guardá o publicá cuando quieras.");
      })
      .catch(() => {
        btn.disabled = false;
        btn.textContent = label;
        setStatus("No pudimos subir el archivo: revisá tu conexión.", true);
      });
  }

  function loadVersions(key, gallery) {
    gallery.innerHTML = '<p class="ed-media-empty">Cargando…</p>';
    fetch(MEDIA_VERSIONS_URL + "?key=" + encodeURIComponent(key), {
      headers: { "X-Sitecopy-CSRF": CSRF },
    })
      .then((r) => r.json().catch(() => null))
      .then((payload) => {
        if (!payload || !payload.ok) {
          gallery.innerHTML = '<p class="ed-media-empty">No pudimos cargar las versiones.</p>';
          return;
        }
        const isVideo = payload.type === "video";
        const items = [];
        // The code default is always offered as "Original", so there is a way back to
        // what the site shipped with even before anything was published.
        if (payload.default) items.push({ url: payload.default, label: "Original" });
        (payload.versions || []).forEach((v) => items.push({ url: v.url, label: "" }));
        const seen = Object.create(null);
        const uniq = items.filter((it) => (seen[it.url] ? false : (seen[it.url] = true)));
        if (!uniq.length) {
          gallery.innerHTML =
            '<p class="ed-media-empty">Todavía no hay versiones anteriores.</p>';
          return;
        }
        gallery.innerHTML = "";
        uniq.forEach((it) => {
          const thumb = document.createElement("button");
          thumb.type = "button";
          thumb.className = "ed-media-thumb";
          thumb.title = it.url;
          const media = document.createElement(isVideo ? "video" : "img");
          media.src = it.url;
          if (isVideo) {
            media.muted = true;
            media.preload = "metadata";
          } else {
            media.alt = "";
          }
          thumb.appendChild(media);
          if (it.label) {
            const tag = document.createElement("span");
            tag.className = "ed-media-thumb-tag";
            tag.textContent = it.label;
            thumb.appendChild(tag);
          }
          thumb.addEventListener("click", () => {
            stageChange(key, it.url);
            setStatus("Listo. Guardá o publicá para que se vea en la web.");
          });
          gallery.appendChild(thumb);
        });
      })
      .catch(() => {
        gallery.innerHTML = '<p class="ed-media-empty">No pudimos cargar las versiones.</p>';
      });
  }

  /** The size picker for one field, appended to `wrap`. Returns the <select>, or null
   *  where this install (or this field) has no size to offer. */
  function buildSizeControl(wrap, field, key) {
    const steps = manifest.sizes || [];
    if (!steps.length || !field.resizable) return null;

    const box = document.createElement("div");
    box.className = "ed-field-size";

    const label = document.createElement("label");
    label.className = "ed-field-size-label";
    label.htmlFor = "ed-size-" + key;
    label.textContent = "Tamaño";
    box.appendChild(label);

    const select = document.createElement("select");
    select.id = "ed-size-" + key;
    select.className = "ed-size-select";
    steps.forEach((step) => {
      const option = document.createElement("option");
      option.value = step.token;
      option.textContent = step.label;
      select.appendChild(option);
    });
    select.value = sizeOf(key);
    select.addEventListener("change", () => stageSize(key, select.value));
    box.appendChild(select);

    // Saying "no" and saying why, rather than offering a control that would be accepted,
    // stored, and then quietly ignored by every render.
    if (invisibleKeys.has(key)) {
      select.disabled = true;
      const why = document.createElement("p");
      why.className = "ed-field-size-why";
      why.id = "ed-size-why-" + key;
      why.textContent =
        "Este texto no se ve en la página (está en el título de la pestaña, en una " +
        "descripción o en un dato para buscadores), así que no cambia de tamaño.";
      select.setAttribute("aria-describedby", why.id);
      box.appendChild(why);
    }

    wrap.appendChild(box);
    return select;
  }

  function buildField(key) {
    const field = fieldFor(key);
    const wrap = document.createElement("div");
    wrap.className = "ed-field";
    wrap.dataset.edField = key;
    wrap.dataset.edSearch = searchable(field.label + " " + key + " " + valueOf(key));

    const label = document.createElement("label");
    label.className = "ed-field-label";
    label.textContent = field.label;
    label.htmlFor = "ed-" + key;
    wrap.appendChild(label);

    const where = document.createElement("span");
    where.className = "ed-field-where";
    where.textContent = field.groupTitle + (field.section ? " · " + field.section : "");
    wrap.appendChild(where);

    const isMedia = field.type === "image" || field.type === "video";
    const multiline = field.type === "text" || field.type === "lines" || field.type === "rich";
    const input = document.createElement(multiline ? "textarea" : "input");
    input.id = "ed-" + key;
    if (!multiline) input.type = field.type === "url" || isMedia ? "url" : "text";
    else input.rows = field.type === "rich" ? 6 : 3;
    input.value = valueOf(key);
    input.maxLength = field.max;
    wrap.appendChild(input);

    // A media field is a URL, so it edits like one — but you can also upload a file and
    // roll back to an earlier one. The value is still just the URL under the hood.
    let preview = null;
    if (isMedia) {
      preview = buildMediaControls(wrap, field, key, input);
    }

    // How big the text renders. Only where the host turned sizes on, and only on a
    // field that can carry one — a picture's URL has no size, and a text that reaches
    // the page inside an attribute has nowhere to put the wrapper that would apply it.
    const sizeSelect = buildSizeControl(wrap, field, key);

    if (field.hint) {
      const hint = document.createElement("p");
      hint.className = "ed-field-hint";
      hint.textContent = field.hint;
      wrap.appendChild(hint);
    }

    const foot = document.createElement("div");
    foot.className = "ed-field-foot";
    const count = document.createElement("span");
    count.className = "ed-field-count";
    count.setAttribute("aria-live", "polite");
    // Undoing one bad edit used to mean Descartar, which throws away every other change
    // too. It targets what the SHOP currently shows — not what the editor loaded, which
    // IS the bad draft once it has been saved, and not the factory text, which is a
    // different place entirely.
    const undo = document.createElement("button");
    undo.type = "button";
    undo.className = "ed-field-restore ed-field-undo";
    undo.textContent = "Volver a lo que se ve en la web";
    undo.title = "El texto que tus clientes están viendo ahora";
    undo.addEventListener("click", () => {
      const live = field.live != null ? field.live : field.raw;
      input.value = live;
      stageChange(key, live);
      setStatus(
        hasDraft(key)
          ? "Listo. Tocá «Guardar borrador» o «Publicar cambios» para confirmarlo."
          : "Listo, volvió a lo que se ve en la web."
      );
    });
    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "ed-field-restore";
    restore.textContent = "Volver al texto original";
    restore.title = "El texto con el que vino la web";
    foot.appendChild(count);
    foot.appendChild(undo);
    // Both links only DRAFT now, so looking alike stopped being a trap: what tells
    // them apart is where they land, which is why the destination is in the title.
    // Still gated on `previous`: with none, the wording this replaced WAS the factory
    // text and the link beside it already goes there.
    if (field.previous) {
      const revert = document.createElement("button");
      revert.type = "button";
      revert.className = "ed-field-restore";
      revert.textContent = "Volver a lo que decía antes";
      // Tag-free and trimmed: a `rich` field's previous value is a page of HTML.
      revert.title = field.previous
        .replace(/<[^>]*>/g, " ")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 120);
      revert.addEventListener("click", () => revertKey([key]));
      foot.appendChild(revert);
    }
    foot.appendChild(restore);
    wrap.appendChild(foot);

    function render() {
      count.textContent = input.value.length + " / " + field.max;
      count.classList.toggle("is-over", input.value.length > field.max);
      if (preview) {
        const url = input.value.trim();
        // Only preview what could actually be saved: safeHref is false for
        // javascript:/data:/protocol-relative URLs, exactly what the server rejects, so
        // a bad value shows no thumbnail instead of a broken image. Dropping the
        // attribute (not src="") avoids reloading the admin page itself as the image.
        if (url && safeHref(url)) preview.src = url;
        else { preview.removeAttribute("src"); preview.hidden = true; }
      }
      if (sizeSelect && sizeSelect.value !== sizeOf(key)) sizeSelect.value = sizeOf(key);
      // A pending size makes its field dirty: it is the same change to whoever made it.
      const dirty = key in pending || SIZE_PREFIX + key in pending;
      wrap.classList.toggle("is-dirty", dirty);
      // The older form screen has flagged this since day one; with 91 fields in the
      // panel, "which ones did I leave saved?" was a memory game.
      wrap.classList.toggle("is-draft", !dirty && (hasDraft(key) || Boolean(field.sizeHasDraft)));
      // Not `!dirty`: a draft that was already saved is not "dirty", and that was the
      // one state with no way back to the live wording at all.
      const live = field.live != null ? field.live : field.raw;
      undo.hidden = input.value === live;
      restore.hidden = input.value === field.default;
    }

    if (field.type === "rich" && /<(p|h2|h3|ul|ol|li)\b/i.test(String(field.raw || ""))) {
      // A whole editorial page in a 6-row textarea of raw HTML is how a stray </p>
      // breaks the page. Send it to the same editor the canvas uses.
      input.readOnly = true;
      input.title = "Abrir el editor de esta página";
      // Deliberately NOT on `focus`: closing the sheet returns focus here, which
      // would immediately reopen it. Click, or Enter/Space for the keyboard.
      input.addEventListener("click", () => openSheet(key, valueOf(key)));
      input.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        openSheet(key, valueOf(key));
      });
    }
    input.addEventListener("input", () => {
      stageChange(key, input.value);
      render();
    });
    restore.addEventListener("click", () => {
      input.value = field.default;
      stageChange(key, field.default);
      render();
    });

    render();
    renderers[key] = render;
    return wrap;
  }

  function renderFields() {
    const hidden = new Set(manifest.hiddenKeys || []);
    // buildField needs it too: a text that only reaches the page inside an attribute or
    // the <title> has nowhere to put a wrapper, so it cannot be resized.
    invisibleKeys = hidden;
    hiddenBadge.textContent = String(hidden.size);

    const box = root.querySelector("[data-ed-fields]");
    box.textContent = "";
    Object.keys(renderers).forEach((key) => delete renderers[key]);
    const keys = Object.keys(manifest.fields);
    // Pending edits typed on ANOTHER page belong here too: they count towards
    // Publicar, and if one of them is invalid it blocks every save with nothing to
    // click. `pending` as well as `extraFields`, because an edit that was never
    // saved has no server-side draft to be listed from — `seenFields` is what makes
    // it renderable once the manifest of the page it was typed on is gone.
    Object.keys(extraFields)
      .concat(Object.keys(pending).map(ownerOf))
      .forEach((key) => {
        if (keys.indexOf(key) === -1) keys.push(key);
      });
    keys.forEach((key) => {
      if (!fieldFor(key)) return;
      const item = buildField(key);
      // Not visible on the page = only reachable from here, so the "No visibles"
      // tab is really a filter over this same list.
      const elsewhere = !manifest.fields[key];
      item.dataset.edHidden = hidden.has(key) || elsewhere ? "1" : "0";
      if (elsewhere) item.dataset.edElsewhere = "1";
      box.appendChild(item);
    });
    applyFilter();
  }

  /** Bring back the wording that was live before the last publish — as a DRAFT.
   *
   *  It used to publish on the spot: one underlined link in the panel was the only
   *  control in the whole editor that changed the public site without going through
   *  "Publicar cambios", and it sat beside an identical-looking link that did not. A
   *  second click swapped straight back, so a double click published and unpublished
   *  the live site. The server decides WHAT the previous wording is, because the
   *  manifest this page loaded goes stale the moment a colleague publishes.
   */
  async function revertKey(keys) {
    const response = await fetch(root.dataset.revertUrl, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ keys: keys }),
    }).catch(() => null);
    const payload = response ? await response.json().catch(() => null) : null;
    if (!payload || !payload.ok) {
      setStatus(
        payload && payload.errors && payload.errors.length
          ? payload.errors.join(" ")
          : "No pudimos recuperar el texto anterior.",
        true
      );
      return false;
    }
    // Before staging: a key drafted from another page only becomes renderable once
    // its metadata is here, and stageChange looks it up.
    serverDrafts = payload.pendingKeys || serverDrafts;
    extraFields = payload.pendingFields || extraFields;
    const values = payload.values || {};
    const names = Object.keys(values);
    // stageChange, so the canvas, the panel input, the dirty badge and the counter
    // all end up saying the same thing.
    names.forEach((key) => stageChange(key, values[key]));
    if (names.some((key) => !root.querySelector('[data-ed-field="' + CSS.escape(key) + '"]'))) {
      renderFields();
    }
    setStatus("Listo. Tocá «Publicar cambios» para que se vea en la web.");
    syncButtons();
    return true;
  }

  if (undoBtn) {
    undoBtn.addEventListener("click", async () => {
      const keys = undoKeys.slice();
      // The confirm IS the deliberate act that lets this reach the public site in one
      // step: the rule is that nothing goes live unattended, not that undoing has to
      // take two visits. Naming it "Deshacer" and then leaving a draft was a promise
      // the button did not keep.
      if (!window.confirm("¿Volvemos a como estaba antes de publicar?\n\nSe ve en la web enseguida.")) {
        return;
      }
      if (!(await revertKey(keys))) return;
      // Only claim it when the publish that undoes it actually went through: the old
      // message painted over send()'s own error, and the site was still showing the
      // wording the owner had just tried to take back.
      if ((await send("publish", { undone: true })) && !dirtyCount()) {
        setStatus("Listo, la web volvió a como estaba.");
      }
    });
  }

  const filter = root.querySelector("[data-ed-filter]");
  const noResults = root.querySelector("[data-ed-no-results]");

  /** Lower-cased and stripped of accents. "titulo" used to find nothing at all, which
   *  reads as "that text does not exist" rather than "you missed a tilde". */
  function searchable(text) {
    return String(text)
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "");
  }

  function applyFilter() {
    const query = searchable((filter.value || "").trim());
    let shown = 0;
    root.querySelectorAll("[data-ed-fields] .ed-field").forEach((item) => {
      const matchesTab = currentTab !== "hidden" || item.dataset.edHidden === "1";
      const matchesQuery = !query || item.dataset.edSearch.indexOf(query) !== -1;
      const show = matchesTab && matchesQuery;
      item.hidden = !show;
      if (show) shown++;
    });
    if (noResults) noResults.hidden = shown !== 0;
  }

  if (filter) filter.addEventListener("input", applyFilter);

  /* ---------------- change tracking ---------------- */

  function stageChange(key, value) {
    const known = manifest.fields[key] || extraFields[key];
    if (known) seenFields[key] = known;
    const original = (fieldFor(key) || {}).raw;
    if (value === original) delete pending[key];
    else pending[key] = value;
    syncButtons();
    setStatus(dirtyCount() ? "Cambios sin guardar" : draftNotice());
    // Keep the page and the panel showing the same thing.
    tellFrame({ type: "set", key: key, value: value });
    const box = root.querySelector('[data-ed-field="' + CSS.escape(key) + '"] input, [data-ed-field="' + CSS.escape(key) + '"] textarea');
    if (box && box.value !== value) box.value = value;
    root.querySelectorAll('[data-ed-field="' + CSS.escape(key) + '"]').forEach((item) => {
      item.classList.toggle("is-dirty", key in pending);
      const field = fieldFor(key) || {};
      item.dataset.edSearch = searchable((field.label || key) + " " + key + " " + value);
    });
    if (renderers[key]) renderers[key]();
  }

  /** Pick a size for `key`. Staged like any other change, and shown on the canvas at
   *  once so the choice is judged against the real page rather than a dropdown. */
  function stageSize(key, token) {
    const field = fieldFor(key) || {};
    const row = SIZE_PREFIX + key;
    // Against what LOADED, like stageChange: putting the select back where it was is
    // not a pending change, and must not sit in the counter forever.
    if (token === (field.size || "base")) delete pending[row];
    else pending[row] = token;
    syncButtons();
    setStatus(dirtyCount() ? "Cambios sin guardar" : draftNotice());
    tellFrame({ type: "size", key: key, token: token });
    if (renderers[key]) renderers[key]();
  }

  function markInvalid(keys) {
    root.querySelectorAll(".ed-field.is-invalid").forEach((item) => {
      item.classList.remove("is-invalid");
      const box = item.querySelector("input, textarea");
      if (box) {
        box.removeAttribute("aria-invalid");
        box.removeAttribute("aria-describedby");
      }
    });
    // A rejected size names its own row key; the thing to point at is the field.
    keys = Array.from(new Set(keys.map(ownerOf)));
    tellFrame({ type: "highlight", keys: keys });
    if (!keys.length) return;
    setPanel(true, false);
    filter.value = "";
    selectTab("all");
    keys.forEach((key) => {
      const item = root.querySelector('[data-ed-field="' + CSS.escape(key) + '"]');
      if (!item) return;
      item.classList.add("is-invalid");
      const box = item.querySelector("input, textarea");
      if (box) {
        box.setAttribute("aria-invalid", "true");
        box.setAttribute("aria-describedby", "edStatus");
      }
    });
    const first = root.querySelector(".ed-field.is-invalid");
    if (first) {
      window.setTimeout(() => {
        first.scrollIntoView({ block: "center" });
        const box = first.querySelector("input, textarea");
        if (box) box.focus();
      }, 60);
    }
  }

  function reloadCanvas() {
    try {
      iframe.contentWindow.location.reload();
    } catch (_) {
      iframe.src = iframe.src;
    }
  }

  let flushToken = 0;
  const flushWaiters = Object.create(null);

  /** Commit whatever is being typed on the canvas, and only then act.
   *
   *  A toolbar click blurs the node being edited, and the frame defers that teardown a
   *  tick so the browser can settle focus first. Every button that reads `pending` was
   *  therefore reading it one edit behind — and worse, the late `change` message then
   *  overwrote whatever the button had just put in the status line, so a refused publish
   *  looked like a button that did nothing at all.
   */
  function flushFrame() {
    if (!iframe.contentWindow) return Promise.resolve();
    const token = ++flushToken;
    return new Promise((resolve) => {
      // Never hang on it: the canvas may be mid-navigation, and a toolbar that stops
      // responding is worse than acting on a value one keystroke old.
      const timer = window.setTimeout(() => {
        delete flushWaiters[token];
        resolve();
      }, 300);
      flushWaiters[token] = () => {
        window.clearTimeout(timer);
        delete flushWaiters[token];
        resolve();
      };
      tellFrame({ type: "flush", token: token });
    });
  }

  function tellFrame(message) {
    if (!iframe.contentWindow) return;
    iframe.contentWindow.postMessage(
      Object.assign({ source: "ct-shell" }, message),
      window.location.origin
    );
  }

  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin) return;
    // The SENDER has to be our own canvas. Origin alone let any same-origin script
    // (a nested frame, an extension, a future widget) drive openRich and land its
    // markup in `innerHTML` inside the admin document, next to the session cookie.
    if (!iframe.contentWindow || event.source !== iframe.contentWindow) return;
    const data = event.data || {};
    if (data.source !== "ct-frame" || typeof data.type !== "string") return;
    if (data.key !== undefined && typeof data.key !== "string") return;

    if (data.type === "flushed") {
      const done = flushWaiters[data.token];
      if (done) done();
    } else if (data.type === "ready") {
      manifest = data.manifest || manifest;
      // Follow the canvas: a link clicked inside the site used to strand the picker
      // on the old page, and re-selecting the same option fires no change event.
      if (pagePicker && data.path) {
        const match = Array.from(pagePicker.options).find((o) => o.value === data.path);
        pagePicker.value = match ? data.path : "";
      }
      // Re-apply anything edited before navigating to this page.
      Object.keys(pending).forEach((key) => {
        if (manifest.fields[key]) tellFrame({ type: "set", key: key, value: pending[key] });
      });
      renderFields();
      syncButtons();
      const current = root.querySelector("[data-ed-device].is-current");
      if (current) fitDevice(current);
    } else if (data.type === "change") {
      stageChange(data.key, data.value);
    } else if (data.type === "openRich") {
      openSheet(data.key, data.value);
    } else if (data.type === "openKeys") {
      // The caller focuses a specific field below; don't let the panel's own
      // deferred focus take it back.
      setPanel(true, false);
      const first = (data.keys || [])[0];
      if (!first) return;
      // Show everything, so a key that is ALSO visible on the page still resolves.
      filter.value = "";
      selectTab("all");
      const target = root.querySelector('[data-ed-field="' + CSS.escape(first) + '"]');
      if (target) {
        window.setTimeout(() => {
          target.scrollIntoView({ block: "center" });
          const box = target.querySelector("input, textarea");
          if (box) box.focus();
        }, 50);
      }
    }
  });

  /* ---------------- page-body editor ---------------- */

  const sheet = root.querySelector("[data-ed-sheet]");
  const sheetDoc = root.querySelector("[data-ed-sheet-doc]");
  const sheetCount = root.querySelector("[data-ed-sheet-count]");
  let sheetKey = null;
  let sheetReturnFocus = null;
  // What the sheet opened with. Nothing is staged while typing in there — only "Listo"
  // stages — so an Escape reflex threw away a whole rewritten page with no confirm and
  // no badge that had ever shown there was something to lose.
  let sheetOpenedWith = null;

  /** What a reader actually sees. The old counter measured the HTML, so an emptied
   *  body reported "7 / 12000" and a paste of markup burned budget invisibly. */
  // The server sanitizes on save and on render; the editor document needs its own
  // pass, because what lands here comes over postMessage and goes into innerHTML.
  const RICH_TAGS = ["P", "BR", "STRONG", "B", "EM", "I", "H2", "H3", "UL", "OL", "LI", "A"];
  // Dropped WITH their contents, exactly like the server does: unwrapping a <style>
  // keeps its CSS as visible copy, so a paste from a word processor poured a stylesheet
  // into the page as text.
  const DROP_WITH_CONTENT = [
    "SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "TEMPLATE", "SVG", "MATH", "NOSCRIPT",
  ];

  function sanitizeRich(html) {
    const doc = document.implementation.createHTMLDocument("");
    doc.body.innerHTML = String(html == null ? "" : html);
    // Walked with a live cursor, never a snapshot. Unwrapping a tag promotes whatever
    // was inside it, and a snapshot of `children` was taken before that happened — so
    // `<div><img src=x onerror=…></div>` handed the <img> straight to insertHTML and the
    // handler RAN, in the admin's own document, with the shop owner's session.
    const walk = (node) => {
      let child = node.firstChild;
      while (child) {
        let next = child.nextSibling;
        // Word pastes <!--[if gte mso 9]><xml>…<![endif]-->: invisible, counted against
        // the field's cap, and dropped by the server on save anyway.
        if (child.nodeType === Node.COMMENT_NODE) {
          child.remove();
        } else if (child.nodeType === Node.ELEMENT_NODE) {
          // SVG and MathML elements report a lower-case tagName; normalize before matching.
          const tag = child.tagName.toUpperCase();
          const unwrap = () => {
            const promoted = child.firstChild;
            child.replaceWith(...child.childNodes);
            if (promoted) next = promoted;
          };
          if (DROP_WITH_CONTENT.indexOf(tag) !== -1) {
            child.remove();
          } else if (RICH_TAGS.indexOf(tag) === -1) {
            unwrap();
          } else {
            Array.from(child.attributes).forEach((attr) => {
              const keep = tag === "A" && ["href", "target", "rel"].indexOf(attr.name) !== -1;
              if (!keep) child.removeAttribute(attr.name);
            });
            if (tag === "A" && !safeHref(child.getAttribute("href"))) unwrap();
            else walk(child);
          }
        }
        child = next;
      }
    };
    walk(doc.body);
    return doc.body.innerHTML;
  }

  function safeHref(href) {
    const value = String(href || "").replace(/[\s\u0000-\u001f]/g, "");
    if (!value || /^(\/\/|\/\\|\\)/.test(value)) return false;
    if (/^[a-z][a-z0-9+.-]*:/i.test(value)) {
      return /^(https?|mailto|tel):/i.test(value);
    }
    return true;
  }

  function visibleLength(html) {
    const probe = document.createElement("div");
    probe.innerHTML = html;
    return (probe.textContent || "").replace(/\s+/g, " ").trim().length;
  }

  /** What the braces in this value stand for, in words.
   *  The sheet shows the RAW text ("el uso del sitio de {brand}"), so without this the
   *  only reasonable reading is that the shop's name is missing — and the fix anyone
   *  would try is to type it in, which kills the placeholder for good. */
  function tokenNote(value) {
    const known = manifest.tokens || {};
    const names = [];
    String(value == null ? "" : value).replace(/\{([a-z_][a-z0-9_]*)\}/g, (whole, name) => {
      if (names.indexOf(name) === -1 && Object.prototype.hasOwnProperty.call(known, name)) {
        names.push(name);
      }
      return whole;
    });
    if (!names.length) return "";
    return (
      "Dejá tal cual los textos entre llaves: se completan solos al publicar. " +
      names.map((name) => "{" + name + "} = " + known[name]).join(" · ")
    );
  }

  // Reflect the caret's current formatting on the toolbar, so a keyboard or
  // screen-reader user can tell what is already applied (bold, a list, a heading).
  function syncToolbar() {
    root.querySelectorAll("[data-ed-cmd]").forEach((btn) => {
      const cmd = btn.dataset.edCmd;
      let pressed = null;
      try {
        if (cmd === "bold" || cmd === "italic" || cmd === "insertUnorderedList") {
          pressed = document.queryCommandState(cmd);
        } else if (cmd === "formatBlock") {
          const arg = (btn.dataset.edArg || "").replace(/[<>]/g, "").toLowerCase();
          pressed = (document.queryCommandValue("formatBlock") || "").toLowerCase() === arg;
        }
      } catch (_) {
        pressed = null;
      }
      if (pressed === null) btn.removeAttribute("aria-pressed");
      else btn.setAttribute("aria-pressed", pressed ? "true" : "false");
    });
  }

  function openSheet(key, value) {
    const field = fieldFor(key);
    if (!field) return;
    sheetKey = key;
    sheetReturnFocus = document.activeElement;
    root.querySelector("[data-ed-sheet-title]").textContent = field.label;
    root.querySelector("[data-ed-sheet-where]").textContent =
      field.section && field.section !== field.groupTitle
        ? field.groupTitle + " · " + field.section
        : field.groupTitle;
    // textContent, never innerHTML: a token's value comes from site_texts, so it is
    // user-controlled content being printed inside the admin origin.
    const tokens = root.querySelector("[data-ed-sheet-tokens]");
    tokens.textContent = tokenNote(value != null ? value : field.raw);
    tokens.hidden = !tokens.textContent;
    sheetDoc.innerHTML = sanitizeRich(value != null ? value : field.raw);
    sheetOpenedWith = sheetDoc.innerHTML;
    sheet.hidden = false;
    document.body.classList.add("ed-sheet-open");
    // Belt and braces: `inert` removes the background from the tab order and from
    // the accessibility tree in browsers that support it.
    if ("inert" in HTMLElement.prototype) {
      Array.from(root.children).forEach((child) => {
        if (child !== sheet) child.inert = true;
      });
    }
    renderSheetCount();
    sheetDoc.focus();
    syncToolbar();
  }

  function closeSheet(applied) {
    if (!applied && sheetKey && sheetDoc.innerHTML !== sheetOpenedWith) {
      const message =
        "Vas a cerrar «" + ((fieldFor(sheetKey) || {}).label || sheetKey) + "» sin aplicar.\n\n" +
        "Se pierde lo que escribiste acá.";
      if (!window.confirm(message)) return;
    }
    sheetOpenedWith = null;
    sheet.hidden = true;
    showSheetNote("");
    document.body.classList.remove("ed-sheet-open");
    if ("inert" in HTMLElement.prototype) {
      Array.from(root.children).forEach((child) => {
        child.inert = false;
      });
    }
    sheetKey = null;
    if (sheetReturnFocus && sheetReturnFocus.focus) sheetReturnFocus.focus();
  }

  function renderSheetCount() {
    const field = sheetKey ? fieldFor(sheetKey) : null;
    if (!field) return;
    // One quantity, shown and enforced: the counter used to display the visible
    // length and turn red on the HTML length, so it went red while reading "758".
    const visible = visibleLength(sheetDoc.innerHTML);
    const stored = sheetDoc.innerHTML.length;
    // Two raw numbers in different units, and the one that decides whether the save goes
    // through was the unexplained one. Only say something about the cap when the cap is
    // close enough to matter — a percentage nobody can act on is more noise than help.
    const room = field.max - stored;
    sheetCount.textContent =
      stored > field.max
        ? "Te pasaste del máximo: sacá texto o formato para poder guardar."
        : room < field.max * 0.15
        ? visible + " caracteres escritos · te queda poco lugar en esta página"
        : visible + " caracteres escritos";
    sheetCount.title =
      stored + " de " + field.max + " caracteres guardados (el formato también ocupa lugar).";
    sheetCount.classList.toggle("is-over", stored > field.max);
  }

  let sheetNoteTimer = null;

  /** A line under the toolbar saying what the editor just did to a paste. */
  function showSheetNote(text) {
    const note = root.querySelector("[data-ed-sheet-note]");
    if (!note) return;
    note.textContent = text || "";
    note.hidden = !text;
    window.clearTimeout(sheetNoteTimer);
    if (text) sheetNoteTimer = window.setTimeout(() => { note.hidden = true; }, 9000);
  }

  if (sheet) {
    sheetDoc.addEventListener("input", renderSheetCount);
    // What you see has to be what you keep: the server strips the colours, fonts and
    // <o:p> a word processor pastes in, so showing them until the next save is a promise
    // the save quietly breaks. Same allow-list the server uses, reused.
    sheetDoc.addEventListener("paste", (event) => {
      const data = event.clipboardData;
      if (!data) return;
      const html = data.getData("text/html");
      if (!html) return; // plain text: the browser inserts it clean already
      event.preventDefault();
      document.execCommand("insertHTML", false, sanitizeRich(html));
      renderSheetCount();
      if (/\sstyle=|\sclass=|<font\b|mso-/i.test(html)) {
        showSheetNote(
          "Pegamos el texto sin los colores ni las tipografías que traía. " +
            "Se mantienen la negrita, la cursiva, los subtítulos, las listas y los links."
        );
      }
    });
    // Wrapped: passing closeSheet straight in hands it the click Event as `applied`,
    // and an Event is truthy — which would skip the very confirm this adds.
    root.querySelectorAll("[data-ed-sheet-cancel]").forEach((btn) =>
      btn.addEventListener("click", () => closeSheet())
    );
    root.querySelector("[data-ed-sheet-apply]").addEventListener("click", () => {
      const key = sheetKey;
      const html = sheetDoc.innerHTML;
      closeSheet(true);
      if (key) {
        stageChange(key, html);
        setStatus("Texto actualizado. Guardá o publicá cuando quieras.");
      }
    });
    root.querySelectorAll("[data-ed-cmd]").forEach((btn) => {
      btn.addEventListener("mousedown", (event) => event.preventDefault());
      btn.addEventListener("click", () => {
        sheetDoc.focus();
        const cmd = btn.dataset.edCmd;
        if (cmd === "createLink") {
          const href = window.prompt("¿A qué link lleva? (https://…)");
          if (href && !safeHref(href)) {
            // The server drops these silently on save, so the editor showed a link
            // that simply vanished later with no explanation.
            window.alert("Ese link no se puede usar. Tiene que empezar con https://");
          } else if (href) {
            document.execCommand("createLink", false, href);
          }
        } else {
          document.execCommand(cmd, false, btn.dataset.edArg || null);
        }
        renderSheetCount();
        syncToolbar();
      });
    });
    // Keep the toolbar in step as the caret moves through already-formatted text.
    document.addEventListener("selectionchange", () => {
      if (!sheet.hidden) syncToolbar();
    });
    // A dialog that declares aria-modal="true" has to behave like one: focus stays
    // inside until it closes, or a keyboard user tabs straight out into a page that
    // is visually behind an overlay and cannot be seen.
    const FOCUSABLE =
      'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"]), [contenteditable="true"]';

    function sheetFocusables() {
      return Array.from(sheet.querySelectorAll(FOCUSABLE)).filter(
        (el) => el.offsetWidth || el.offsetHeight || el === document.activeElement
      );
    }

    sheet.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeSheet();
        return;
      }
      if (event.key !== "Tab") return;
      const items = sheetFocusables();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    // Focus that escapes some other way (a click on the page behind, a programmatic
    // focus) gets pulled back in.
    document.addEventListener("focusin", (event) => {
      if (sheet.hidden || sheet.contains(event.target)) return;
      const items = sheetFocusables();
      if (items.length) items[0].focus();
    });
  }

  /* ---------------- saving ---------------- */

  /** `options.undone` marks the publish that a Deshacer is finishing: it must not arm
   *  another Deshacer for itself, and it says so in its own words. */
  async function send(action, options) {
    const undone = Boolean(options && options.undone);
    const changes = Object.assign({}, pending);
    // Captured once: it is what goes live, and what a Deshacer would have to take back.
    const scope = pendingKeys();
    // Capture this BEFORE syncButtons() disables the button under the user's fingers:
    // a disabled element drops focus to <body>, which is where a keyboard user lost
    // their place at the most important moment of the flow.
    const hadFocus = document.activeElement === saveBtn || document.activeElement === publishBtn;
    inFlight = true;
    syncButtons();
    if (hadFocus) {
      statusEl.setAttribute("tabindex", "-1");
      statusEl.focus();
    }
    setStatus(action === "publish" ? "Publicando…" : "Guardando…");

    let payload = null;
    let failure = null;
    // A hung request used to leave the buttons dead and "Guardando…" on screen
    // forever, with no way out.
    const abort = new AbortController();
    const timeout = window.setTimeout(() => abort.abort(), 20000);
    try {
      const response = await fetch(SAVE_URL, {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify({ changes: changes, action: action, keys: scope }),
        signal: abort.signal,
      });
      payload = await response.json().catch(() => null);
      if (response.status === 401 || (payload && payload.reason === "auth")) {
        failure = "Se cerró tu sesión. Abrí el admin en otra pestaña, entrá de nuevo y volvé a guardar — no perdiste nada.";
      } else if (!response.ok || !payload || !payload.ok) {
        failure = payload && payload.errors && payload.errors.length
          ? payload.errors.join(" ")
          : "No pudimos guardar. Probá de nuevo en un momento.";
      }
    } catch (error) {
      failure =
        error.name === "AbortError"
          ? "El guardado tardó demasiado. Fijate tu conexión y probá de nuevo."
          : "No pudimos guardar: revisá tu conexión. Tus cambios siguen acá.";
    } finally {
      window.clearTimeout(timeout);
      inFlight = false;
    }

    if (failure) {
      setStatus(failure, true);
      markInvalid((payload && payload.errorKeys) || []);
      syncButtons();
      return false;
    }

    markInvalid([]);
    // Only drop what was actually sent AND is still what we sent: anything typed
    // while the request was in flight was never included, and used to be deleted
    // under a success message.
    Object.keys(changes).forEach((key) => {
      if (pending[key] === changes[key]) delete pending[key];
    });
    serverDrafts = payload.pendingKeys || [];
    extraFields = payload.pendingFields || {};
    const leftover = dirtyCount();
    // Typing while a publish is in flight used to turn the confirmation into "Guardado.
    // Te quedaron 1 cambios…": the wrong verb (the site DID change — "Guardado" means
    // draft in this editor's own vocabulary) and the wrong agreement.
    const done =
      action === "publish" ? "Publicado. Ya se ve en la web." : "Borrador guardado.";
    setStatus(
      leftover
        ? done +
            (leftover === 1
              ? " Te quedó 1 cambio más sin guardar."
              : " Te quedaron " + leftover + " cambios más sin guardar.")
        : action === "publish"
        ? done
        : "Borrador guardado. Publicá cuando quieras."
    );
    // The way back belongs here, at the moment it is needed, instead of behind a panel
    // called "Textos ocultos", two tabs and a search away. Only on a clean publish:
    // with leftovers the status is about something else, and on a phone .ed-actions is
    // a fixed bar whose reserved height fits exactly one row.
    if (undoBtn && action === "publish" && scope.length && !leftover && !undone) {
      undoKeys = scope;
      undoBtn.hidden = false;
    }
    syncButtons();
    // `.src` reflects the ATTRIBUTE, which in-frame navigation never updates, so
    // re-assigning it threw the editor back to the page it started on.
    reloadCanvas();
    return true;
  }

  // `inFlight` only goes up inside send(), and every one of these awaits flushFrame()
  // first — up to 300ms with the button still live, which is exactly a double click.
  // It showed the confirm twice and posted the publish twice.
  let busy = false;

  function guarded(run) {
    return async () => {
      if (busy || inFlight) return;
      busy = true;
      try {
        await run();
      } finally {
        busy = false;
      }
    };
  }

  saveBtn.addEventListener(
    "click",
    guarded(async () => {
      await flushFrame();
      await send("save");
    })
  );

  /** The two rules the panel already draws on screen — empty, and past the cap. The
   *  server is still the real gate; this only stops us from asking "¿publicamos?" about
   *  something that was already condemned. */
  function localErrors(keys) {
    return keys.filter((key) => {
      const field = fieldFor(key);
      if (!field) return false;
      const value = String(valueOf(key));
      // Rich values are markup: what counts as empty is the text a reader would see, and
      // the server measures the SANITIZED string, which is never shorter.
      const written = field.type === "rich" ? visibleLength(value) : value.trim().length;
      return written === 0 || value.length > field.max;
    });
  }

  publishBtn.addEventListener("click", guarded(async () => {
    await flushFrame();
    const keys = pendingKeys();
    const bad = localErrors(keys);
    if (bad.length) {
      const names = bad.map((key) => "«" + fieldName(key) + "»").join(", ");
      setStatus(
        "Todavía no se puede publicar. Revisá " + names +
          ": un texto no puede quedar vacío ni pasarse del largo máximo.",
        true
      );
      markInvalid(bad);
      return;
    }
    // Name what is about to go live. "¿Publicar los cambios?" gave no way to notice
    // that something parked days ago was riding along.
    const names = keys.map((key) => "· " + fieldName(key));
    const shown = names.slice(0, 8).join("\n");
    const rest = names.length > 8 ? "\n… y " + (names.length - 8) + " más" : "";
    const one = keys.length === 1;
    const message =
      (one ? "Se va a publicar 1 texto:" : "Se van a publicar " + keys.length + " textos:") +
      "\n\n" + shown + rest +
      "\n\n" + (one ? "Se ve en la web enseguida." : "Se ven en la web enseguida.");
    if (window.confirm(message)) await send("publish");
  }));

  if (discardBtn) {
    discardBtn.addEventListener("click", guarded(async () => {
      const keys = pendingKeys();
      // The most destructive button in the editor was the only one that didn't say what
      // it was about to take with it.
      const names = keys.map((key) => "· " + fieldName(key));
      const shown = names.slice(0, 8).join("\n");
      const rest = names.length > 8 ? "\n… y " + (names.length - 8) + " más" : "";
      const one = keys.length === 1;
      const message =
        (one
          ? "Vas a descartar este cambio sin publicar:"
          : "Vas a descartar los " + keys.length + " cambios sin publicar:") +
        "\n\n" + shown + rest +
        "\n\n" + (one ? "No se puede recuperar." : "No se pueden recuperar.");
      if (!window.confirm(message)) return;
      // Scoped, and the answer is read. Without a body this endpoint dropped every draft
      // in the database under a confirm that named one; without a JSON content type an
      // expired session answered 302 instead of 401, and the editor reported "Cambios
      // descartados." over a request that had discarded nothing.
      const response = await fetch(root.dataset.discardUrl, {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify({ keys: keys }),
      }).catch(() => null);
      const payload = response ? await response.json().catch(() => null) : null;
      if (!payload || !payload.ok) {
        setStatus(
          response && response.status === 401
            ? "Se cerró tu sesión. Abrí el admin en otra pestaña, entrá de nuevo y volvé a probar."
            : "No pudimos descartar los cambios. Probá de nuevo en un momento.",
          true
        );
        return;
      }
      Object.keys(pending).forEach((key) => delete pending[key]);
      serverDrafts = payload.pendingKeys || [];
      extraFields = payload.pendingFields || {};
      setStatus("Cambios descartados.");
      syncButtons();
      reloadCanvas();
    }));
  }

  /* ---------------- share/search cards ---------------- */

  function fillCards() {
    let doc = null;
    try {
      doc = iframe.contentDocument;
    } catch (_) {
      return;
    }
    if (!doc || !doc.head) return;
    const pick = (sel, attr) => {
      const el = doc.head.querySelector(sel);
      return el ? (el.getAttribute(attr || "content") || "").trim() : "";
    };
    const canonical = pick('link[rel="canonical"]', "href") || pick('meta[property="og:url"]');
    let host = canonical;
    try {
      host = new URL(canonical).host;
    } catch (_) {}

    const set = (sel, text) => {
      const el = root.querySelector(sel);
      if (el) el.textContent = text || "";
    };
    const image = (sel, url) => {
      const el = root.querySelector(sel);
      if (!el) return;
      if (url) {
        el.src = url;
        el.hidden = false;
      } else {
        el.removeAttribute("src");
        el.hidden = true;
      }
    };

    set("[data-ct-serp-url]", canonical);
    set("[data-ct-serp-title]", (doc.title || "").trim());
    set("[data-ct-serp-desc]", pick('meta[name="description"]'));

    set("[data-ct-og-title]", pick('meta[property="og:title"]') || doc.title);
    set("[data-ct-og-desc]", pick('meta[property="og:description"]'));
    set("[data-ct-og-host]", host);
    set("[data-ct-og-url]", canonical);
    image("[data-ct-og-image]", pick('meta[property="og:image"]'));

    set("[data-ct-tw-host]", host);
    set("[data-ct-tw-title]", pick('meta[name="twitter:title"]') || doc.title);
    set("[data-ct-tw-desc]", pick('meta[name="twitter:description"]'));
    image("[data-ct-tw-image]", pick('meta[name="twitter:image"]'));
  }

  iframe.addEventListener("load", () => {
    const cardsTab = root.querySelector('[data-ed-tab="cards"]');
    if (cardsTab && cardsTab.classList.contains("is-current")) fillCards();
  });

  const skipLink = root.querySelector("[data-ed-skip]");
  if (skipLink) {
    skipLink.addEventListener("click", (event) => {
      event.preventDefault();
      setPanel(true);
    });
  }

  const initial = root.querySelector("[data-ed-device].is-current");
  if (initial) fitDevice(initial);
  syncButtons();
  setStatus(draftNotice());
})();
