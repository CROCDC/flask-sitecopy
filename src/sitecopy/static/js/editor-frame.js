// Runs INSIDE the public page when a logged-in admin opens it with ?edit=1.
// (Injected by sitecopy/editor_markup.py — never present on a normal request.)
//
// Its job: turn the <ct-t> wrappers the server emitted into click-to-edit text,
// and report every change up to the editor shell over postMessage. It owns no
// state: the shell holds the pending changes and does the saving.

(function () {
  "use strict";

  const manifestNode = document.getElementById("ctManifest");
  if (!manifestNode) return;

  // Honour the OS reduced-motion setting: jump instead of gliding.
  const reduceMotion = window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : { matches: false };
  const scrollBehavior = () => (reduceMotion.matches ? "auto" : "smooth");

  let MANIFEST;
  try {
    MANIFEST = JSON.parse(manifestNode.textContent || "{}");
  } catch (_) {
    return;
  }

  const FIELDS = MANIFEST.fields || {};
  const TOKENS = MANIFEST.tokens || {};
  // Raw value per key, kept in sync as the user types so several occurrences of the
  // same string (a nav label in the header, the menu and the footer) stay identical.
  const CURRENT = {};
  Object.keys(FIELDS).forEach((key) => {
    CURRENT[key] = FIELDS[key].raw;
  });

  /* ---------------- helpers ---------------- */

  function post(message) {
    if (window.parent === window) return;
    window.parent.postMessage(Object.assign({ source: "ct-frame" }, message), window.origin);
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** The subset of a value we're willing to put in an `<img src>` — the client-side
   *  mirror of sanitizer.safe_image_src, so the canvas preview never tries to load a
   *  `javascript:`/`data:` URL the server will reject anyway (browsers won't run it, but
   *  it flashes a broken image and logs a scheme error). "" means "show no picture". */
  function imageSrcSafe(value) {
    var v = String(value == null ? "" : value).replace(/[\u0000-\u001f]/g, "").trim();
    if (!v || /^(\/\/|\/\\|\\)/.test(v)) return "";
    if (/^[a-z][a-z0-9+.-]*:/i.test(v)) return /^https?:/i.test(v) ? v : "";
    return v; // a site path (root-relative or relative) or an in-page anchor
  }

  /** `escaped` mirrors the server: inside a rich value a token is DATA, so it is
   *  escaped before splicing. Substituting raw and then assigning innerHTML made the
   *  canvas execute what the public page escapes. */
  function interpolate(raw, escaped) {
    return String(raw).replace(/\{([a-z_][a-z0-9_]*)\}/g, (whole, name) => {
      if (!Object.prototype.hasOwnProperty.call(TOKENS, name)) return whole;
      return escaped ? escapeHtml(TOKENS[name]) : TOKENS[name];
    });
  }

  /** Show `key` at `token` right now, on every copy of it on the page.
   *
   *  The rules live in the stylesheet the server injected, so this only swaps which one
   *  applies — nothing here computes a font size, and nothing but a class the server
   *  emitted can end up on the element. `base` means "the site's own size", so it drops
   *  the class rather than adding one. */
  function applySize(key, token) {
    document.querySelectorAll("ct-t").forEach((node) => {
      if (parseTarget(node).key !== key) return;
      Array.from(node.classList).forEach((name) => {
        if (name.indexOf("sc-s-") === 0) node.classList.remove(name);
      });
      if (token && token !== "base") {
        node.classList.add("sc-s-" + token);
        node.setAttribute("data-s", token);
      } else {
        node.removeAttribute("data-s");
      }
    });
    // The block's own control has to agree with the page: the size can also change from
    // the side list, and a readout saying "Normal" over a heading that is clearly not
    // is worse than no readout at all. A step also reflows the page under every bar.
    SIZE_NOW[key] = token || "base";
    const sync = sizeBars.get(key);
    if (sync) sync();
    scheduleBars();
  }

  function parseTarget(node) {
    const label = node.getAttribute("data-k") || "";
    const hash = label.indexOf("#");
    return hash === -1
      ? { key: label, line: null }
      : { key: label.slice(0, hash), line: parseInt(label.slice(hash + 1), 10) };
  }

  /** The text a node should DISPLAY for the current raw value of its field. */
  /** The lines of a `lines` value, WITHOUT dropping blanks.
   *  Filtering here shifted every later item up one index while the DOM nodes kept
   *  their original `key#index`, so emptying one item silently deleted the next. */
  function linesOf(raw) {
    return String(raw == null ? "" : raw).split("\n");
  }

  function displayFor(target) {
    const raw = CURRENT[target.key];
    if (raw == null) return "";
    if (target.line == null) return interpolate(raw);
    const lines = linesOf(raw);
    return interpolate(lines[target.line] != null ? lines[target.line] : "");
  }

  /** The text a node should EDIT: the raw value, tokens and all. */
  /** Same as displayFor, but every token escaped — the value goes to innerHTML. */
  function displayForRich(target) {
    const raw = CURRENT[target.key];
    if (raw == null) return "";
    return interpolate(target.line == null ? raw : linesOf(raw)[target.line] || "", true);
  }

  function editableFor(target) {
    const raw = CURRENT[target.key];
    if (raw == null) return "";
    if (target.line == null) return raw;
    const lines = linesOf(raw);
    return lines[target.line] != null ? lines[target.line] : "";
  }

  /** Write `text` back into the field's raw value (splicing one line if needed).
   *  `whole` replaces the entire raw value instead, for the Escape path. */
  function applyEdit(target, text, whole) {
    if (whole !== undefined) {
      CURRENT[target.key] = whole;
      return whole;
    }
    if (target.line == null) {
      CURRENT[target.key] = text;
      return text;
    }
    const lines = linesOf(CURRENT[target.key]);
    lines[target.line] = text;
    CURRENT[target.key] = lines.join("\n");
    return CURRENT[target.key];
  }

  // A field that IS a token: changing it has to re-render every string that mentions
  // it, or the canvas shows the old brand in the footer while the live site shows the
  // new one — and the natural next move is to type the brand in by hand, which breaks
  // the token for good. Which fields those are is the site's to declare, so it comes
  // down in the manifest rather than being listed here.
  const TOKEN_FIELDS = MANIFEST.tokenFields || {};

  let tokenDependents = null;

  function refreshDependents(key) {
    const token = TOKEN_FIELDS[key];
    if (!token) return;
    TOKENS[token] = String(CURRENT[key] || "");
    if (tokenDependents === null) {
      tokenDependents = Object.keys(CURRENT).filter((other) =>
        /\{[a-z_]+\}/.test(String(FIELDS[other] ? FIELDS[other].raw : CURRENT[other]))
      );
    }
    tokenDependents.forEach((other) => {
      if (other !== key) refresh(other);
    });
  }

  // key -> the nodes that render it. Built once; `refresh` used to sweep every
  // <ct-t> on the page for every key, and editing a token field re-runs that for
  // each of the ~80 strings that mention it — 28 full-document scans per keystroke.
  let nodeIndex = null;

  function indexNodes() {
    nodeIndex = new Map();
    document.querySelectorAll("ct-t").forEach((node) => {
      const key = parseTarget(node).key;
      const bucket = nodeIndex.get(key);
      if (bucket) bucket.push(node);
      else nodeIndex.set(key, [node]);
    });
  }

  function nodesForKey(key) {
    if (nodeIndex === null) indexNodes();
    return nodeIndex.get(key) || [];
  }

  function refresh(key) {
    nodesForKey(key).forEach((node) => {
      if (node.hasAttribute("data-ct-editing")) return;
      const target = parseTarget(node);
      const field = FIELDS[key] || {};
      if (field.type === "rich") node.innerHTML = displayForRich(target);
      else node.textContent = displayFor(target);
      if (CURRENT[key] !== field.raw) node.setAttribute("data-ct-dirty", "");
      else node.removeAttribute("data-ct-dirty");
    });
    updateMedia(key);
    // Longer copy pushes everything below it down; the controls travel with their block.
    scheduleBars();
  }

  /** An image/video field lands in a `src` attribute, so it has no <ct-t> node to
   *  refresh — but a media change IS visual, so mirror the new URL onto the element live.
   *  The key sits in data-ct-keys (recorded by editor_markup for attribute copy), so an
   *  <img>/<video> whose src came from this field is `[data-ct-keys~="key"]`. Setting the
   *  attribute (not .src="") avoids reloading the page itself when the URL is blank. */
  function updateMedia(key) {
    if (["image", "video"].indexOf((FIELDS[key] || {}).type) === -1) return;
    const raw = String(CURRENT[key] == null ? "" : CURRENT[key]);
    const src = imageSrcSafe(interpolate(raw));
    document.querySelectorAll('img[data-ct-keys~="' + key + '"], video[data-ct-keys~="' + key + '"]').forEach((el) => {
      if (src) {
        if (el.getAttribute("src") !== src) {
          el.setAttribute("src", src);
          if (el.tagName === "VIDEO" && el.load) el.load();  // pick up the new source
        }
      } else {
        // Unsafe or empty: show nothing rather than flashing broken media — the same
        // value the server will refuse to publish.
        el.removeAttribute("src");
      }
      if (raw !== ((FIELDS[key] || {}).raw || "")) el.setAttribute("data-ct-dirty", "");
      else el.removeAttribute("data-ct-dirty");
    });
  }

  /* ---------------- floating chrome ---------------- */

  let tip = null;
  let tipTimer = null;
  /** Put the caret at `range`, nudged off the whitespace between block elements.
   *  The raw-value swap changes the text length, so the click point can land in the
   *  gap between two paragraphs, where typing goes nowhere useful. */
  function placeCaret(range) {
    let container = range.startContainer;
    let offset = range.startOffset;
    if (container.nodeType === 3 && !container.textContent.trim()) {
      const walker = document.createTreeWalker(editing || document.body, NodeFilter.SHOW_TEXT);
      let previous = null;
      while (walker.nextNode()) {
        if (walker.currentNode === container) break;
        if (walker.currentNode.textContent.trim()) previous = walker.currentNode;
      }
      const target = previous || walker.nextNode();
      if (target && target.textContent.trim()) {
        container = target;
        offset = previous ? target.textContent.length : 0;
      }
    }
    const selection = window.getSelection();
    const placed = document.createRange();
    try {
      placed.setStart(container, Math.min(offset, (container.textContent || "").length));
      placed.collapse(true);
    } catch (_) {
      return;
    }
    selection.removeAllRanges();
    selection.addRange(placed);
  }

  // Controls the SITE owns — a click on one of these must reach the site so the cart,
  // the menu and the gallery keep working. Deliberately native-only: the editor adds
  // role="button" to elements it makes focusable, and matching those here would swallow
  // the very clicks that open their copy.
  function place(el, anchor, above) {
    const box = anchor.getBoundingClientRect();
    el.style.left = Math.max(6, box.left + window.scrollX) + "px";
    el.style.top =
      (above ? box.top + window.scrollY - el.offsetHeight - 8 : box.bottom + window.scrollY + 6) +
      "px";
  }

  function showTip(node, text) {
    hideTip();
    tip = document.createElement("div");
    tip.className = "ct-tip";
    tip.id = "ctTipLive";
    tip.setAttribute("role", "status");
    tip.setAttribute("aria-live", "polite");
    tip.textContent = text;
    document.body.appendChild(tip);
    place(tip, node, false);
  }

  function hideTip() {
    if (tipTimer) {
      window.clearTimeout(tipTimer);
      tipTimer = null;
    }
    if (tip) tip.remove();
    tip = null;
  }

  /** How long the value would be ONCE STORED, which is what the cap applies to.
   *  A `lines` node holds one line of a value whose limit covers all of them, so
   *  counting the node alone read "580 / 600" for a save the server then refused at
   *  636 — in the one field type where the bubble exists to prevent exactly that. */
  function storedLength(node, field) {
    const target = parseTarget(node);
    if (field.type === "rich") return node.innerHTML.length;
    if (target.line == null) return node.textContent.length;
    const lines = linesOf(CURRENT[target.key]);
    lines[target.line] = currentText(node, field);
    return lines.join("\n").length;
  }

  /** Put a real `<br>` where the caret is, and leave the caret after it.
   *
   *  Built by hand rather than with execCommand: `insertLineBreak` writes a literal "\n"
   *  because the node is `white-space: pre-wrap` while editing (a break in the canvas, a
   *  space on the live page), and `insertHTML` leaves the caret BEFORE a <br> inserted at
   *  the end of a block, so the next keystroke lands on the wrong side of it. */
  function insertBreak(node) {
    const selection = window.getSelection();
    if (!selection || !selection.rangeCount) return;
    const range = selection.getRangeAt(0);
    if (!node.contains(range.startContainer)) return;
    range.deleteContents();
    const br = document.createElement("br");
    range.insertNode(br);
    // A <br> that ENDS a block is a filler, not a position: the caret snaps back in
    // front of it and the next keystroke lands on the line above. A zero-width space
    // gives it somewhere real to stand; `currentText` drops it again on the way out.
    const anchor = document.createTextNode("\u200b");
    br.after(anchor);
    const after = document.createRange();
    after.setStart(anchor, 1);
    after.collapse(true);
    selection.removeAllRanges();
    selection.addRange(after);
    // Nothing here is an `input`, and that is what stages the edit.
    node.dispatchEvent(new InputEvent("input", { bubbles: true }));
  }

  /** True where there is no Escape key to offer. */
  function isTouchOnly() {
    return Boolean(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
  }

  /** The bubble under the text being edited: hint, token warning and live count. */
  function showHint(node, field) {
    const length = storedLength(node, field);
    const raw = String(CURRENT[parseTarget(node).key]);
    const parts = [];
    // A value that is NOTHING BUT a token is the jarring case: the biggest text on the
    // site turns into "{tagline}" the moment it is touched, and the generic pair of
    // hints then contradicted itself — "escribí un texto propio" next to "dejá las
    // llaves tal cual". Say what it is showing and what typing will do to it.
    const only = raw.match(/^\{([a-z_]+)\}$/);
    if (only) {
      parts.push(
        "Acá se completa solo con " +
          (TOKENS[only[1]] ? "«" + TOKENS[only[1]] + "»" : "otro texto tuyo") +
          ". Escribí encima para poner algo fijo, o " +
          // Escape is the only way to cancel, and a phone has no Escape key.
          (isTouchOnly() ? "no escribas nada" : "tocá Escape") +
          " para dejarlo como está."
      );
    } else {
      if (field.hint) parts.push(field.hint);
      if (/\{[a-z_]+\}/.test(raw)) parts.push("Dejá los {textos entre llaves} tal cual.");
    }
    parts.push(length + " / " + field.max + " caracteres");
    showTip(node, parts.join(" · "));
    if (tip) tip.classList.toggle("is-over", length > field.max);
  }

  function currentText(node, field) {
    const text = field.type === "rich" ? node.innerHTML : node.textContent;
    // \u200b is the caret anchor insertBreak leaves after a <br>; it is scaffolding for
    // the editor, never copy.
    return String(text).replace(/\u00a0/g, " ").replace(/\u200b/g, "").trim();
  }

  /** A `rich` node is contenteditable="true", so a paste from a word processor lands its
   *  <span style> soup straight into the staged value — and worse, its <p> tags make
   *  isBlockRich() true on the next load, so a heading silently stops being editable in
   *  place. These four fields are short headings, so take the words and drop the styling
   *  here, where it can still be seen. (`plaintext-only` fields never get here: the
   *  browser already does this.) */
  function onPaste(event) {
    const node = event.currentTarget;
    const field = FIELDS[parseTarget(node).key] || {};
    if (field.type !== "rich") return;
    const data = event.clipboardData;
    if (!data) return;
    event.preventDefault();
    document.execCommand("insertText", false, data.getData("text/plain"));
  }

  function onInput(event) {
    const node = event.currentTarget;
    const target = parseTarget(node);
    const field = FIELDS[target.key] || { max: 0 };
    showHint(node, field);
    // Stage on every keystroke, not on blur: the Save button used to stay dead while
    // you typed, which reads as broken — and reloading to "fix" it threw the edit
    // away. refresh() skips the node being edited, so the caret is safe and the other
    // copies of the same string update live.
    const raw = applyEdit(target, currentText(node, field));
    refresh(target.key);
    refreshDependents(target.key);
    post({ type: "change", key: target.key, value: raw, original: field.raw });
  }

  /** Keep the text being edited out from under a phone's on-screen keyboard. */
  function keepVisible(node) {
    const view = window.visualViewport;
    const height = view ? view.height : window.innerHeight;
    const box = node.getBoundingClientRect();
    if (box.top < 8 || box.bottom > height - 8) {
      window.scrollBy({ top: box.top - height / 3, behavior: "instant" });
    }
  }

  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", () => {
      if (editing) keepVisible(editing);
    });
  }

  const INTERACTIVE = "a[href], button, input, select, textarea, summary";

  function isInteractive(el) {
    return Boolean(el && el.closest && el.closest(INTERACTIVE));
  }

  // Product titles, prices and descriptions come from the catalogue, not from the
  // text registry. Clicking them used to do nothing at all, which reads as broken.
  // An array, not a comma-joined string: `indexOf` on the string matched "B" inside
  // "BUTTON", "I" inside "FIGCAPTION", and so on.
  const TEXT_TAGS = ["H1","H2","H3","H4","H5","H6","P","SPAN","A","LI","STRONG","EM",
                     "BUTTON","BLOCKQUOTE","FIGCAPTION","TD","LABEL"];
  // Text the site renders from somewhere else (a product catalogue, a feed). The site
  // declares the scope: everything else that isn't editable in place is still site
  // copy, and saying "it comes from the catalogue" sent people hunting in a section
  // that had nothing to do with the text they clicked.
  const EXTERNAL = MANIFEST.external || null;
  let lastExplain = 0;

  function explainNotEditable(el) {
    if (!el || !el.tagName || TEXT_TAGS.indexOf(el.tagName) === -1) return;
    if (el.closest("ct-t") || el.querySelector("ct-t")) return;
    const text = (el.textContent || "").trim();
    if (!text || text.length > 200) return;
    const now = Date.now();
    if (now - lastExplain < 1200) return;
    lastExplain = now;
    const external =
      EXTERNAL && EXTERNAL.selector && el.closest(EXTERNAL.selector) ? EXTERNAL.message : "";
    showTip(
      el,
      external ||
        "Este texto no se edita tocándolo. Buscalo en «Ver la lista de textos» o en la lista por sección."
    );
    tipTimer = window.setTimeout(hideTip, 3600);
  }

  /* ---------------- editing ---------------- */

  let editing = null;
  // Where the user actually clicked, so the caret can be put back after the raw-value
  // swap replaces the node's contents.
  let clickPoint = null;
  // The value when the current edit began — what Escape reverts to.
  let editStartValue = null;

  /** A rich value that holds BLOCK elements — a whole editorial page. Editing that
   *  in a floating inline box over the site is where every reviewer got hurt: the
   *  toolbar and the counter render outside the canvas, the caret fights the raw-value
   *  swap, and one select-all could blank the page. Short rich values (a heading with
   *  a <br>) stay in place, which is where in-place editing actually shines. */
  function isBlockRich(field, raw) {
    return field.type === "rich" && /<(p|h2|h3|ul|ol|li)\b/i.test(String(raw || ""));
  }

  /** Swapping the rendered text for the RAW value changes how many lines the copy takes:
   *  the hero title renders as three lines and its raw value is "{tagline}", one line.
   *  Clicking the most important text on the site therefore pulled the hero 98px out from
   *  under the cursor before the caret even landed. Hold the block at the height it had
   *  while the edit lasts; it can still grow. */
  let lockedBlock = null;

  function lockHeight(node) {
    let block = node.parentElement;
    while (block && window.getComputedStyle(block).display === "inline") {
      block = block.parentElement;
    }
    if (!block) return;
    lockedBlock = block;
    block.style.minHeight = block.getBoundingClientRect().height + "px";
  }

  function releaseHeight() {
    if (lockedBlock) lockedBlock.style.minHeight = "";
    lockedBlock = null;
  }

  /** The single `ct-t` inside the nearest text block, when there is exactly one and the
   *  click did not land on something interactive. Anything else stays ambiguous and is
   *  left alone. */
  function loneEditableIn(target) {
    if (!target || !target.closest || isInteractive(target)) return null;
    const block = target.closest("h1, h2, h3, h4, p, li, figcaption, blockquote, dd, dt, td, th");
    if (!block || block.closest("ct-t")) return null;
    const inside = block.querySelectorAll("ct-t");
    if (inside.length !== 1) return null;
    // A block that also holds a link or a button is not "just this text".
    if (block.querySelector("a[href], button, input, select, textarea")) return null;
    return inside[0];
  }

  function startEditing(node) {
    if (editing === node) return;
    if (editing) stopEditing();

    const target = parseTarget(node);
    const field = FIELDS[target.key];
    if (!field) return;

    if (isBlockRich(field, CURRENT[target.key])) {
      post({ type: "openRich", key: target.key, value: CURRENT[target.key] });
      return;
    }

    editing = node;
    editStartValue = CURRENT[target.key];
    lockHeight(node);
    node.setAttribute("data-ct-editing", "");
    node.setAttribute("contenteditable", field.type === "rich" ? "true" : "plaintext-only");
    node.setAttribute("spellcheck", "true");

    // Swap the rendered text for the RAW value: typing over an interpolated
    // "{brand}" would otherwise bake the brand name into that string forever.
    if (field.type === "rich") node.innerHTML = editableFor(target);
    else node.textContent = editableFor(target);

    node.focus();
    if (field.type === "rich" && clickPoint) {
      // Aim for the same spot on screen: landing at character 0 meant a typo fix in
      // paragraph four was typed into paragraph one.
      const range =
        document.caretRangeFromPoint && document.caretRangeFromPoint(clickPoint.x, clickPoint.y);
      if (range && node.contains(range.startContainer)) {
        placeCaret(range);
      }
    }
    clickPoint = null;
    if (field.type !== "rich") {
      // Short copy: select it so typing replaces it. A rich body is a whole page of
      // text — selecting all of it would mean one keystroke wipes the page, so there
      // the caret just lands where the user clicked.
      const range = document.createRange();
      range.selectNodeContents(node);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }

    // On a phone the keyboard covers the bottom ~half of the screen and nothing
    // scrolled the text out from under it.
    keepVisible(node);
    showHint(node, field);
    node.addEventListener("input", onInput);
    node.addEventListener("paste", onPaste);

    post({ type: "focus", key: target.key });
    // The raw value can be longer than what was rendered (a {token} spelled out), and
    // the node is now pre-wrap: the block just changed shape under its own control.
    scheduleBars();
  }

  function stopEditing(discard) {
    if (!editing) return;
    const node = editing;
    const target = parseTarget(node);
    const field = FIELDS[target.key] || {};

    editing = null;
    releaseHeight();
    node.removeEventListener("input", onInput);
    node.removeEventListener("paste", onPaste);
    node.removeAttribute("contenteditable");
    node.removeAttribute("data-ct-editing");
    hideTip();

    // `discard` (Escape) goes back to where THIS edit began — not to the value the
    // page was loaded with, which used to throw away edits Escape never touched.
    const raw = discard
      ? applyEdit(target, null, editStartValue)
      : applyEdit(target, currentText(node, field));
    editStartValue = null;
    refresh(target.key);
    refreshDependents(target.key);
    // Reported even when the edit was cancelled: that is how the shell learns the
    // value went back to its original and drops the pending change.
    post({ type: "change", key: target.key, value: raw, original: field.raw });
  }

  document.addEventListener(
    "click",
    (event) => {
      // The block bar is our own chrome; let its buttons run without the "this text
      // is not editable" tip firing on them first.
      if (event.target.closest && event.target.closest("[data-ct-bar]")) return;
      const node = event.target.closest ? event.target.closest("ct-t") : null;
      if (node) {
        clickPoint = { x: event.clientX, y: event.clientY };
        // preventDefault stops navigation and form submission. NOT stopPropagation:
        // the site's own handlers are delegated on `document`, so stopping the event
        // in the capture phase left the cart button and the checkout button dead —
        // and their copy only exists once those panels are open.
        event.preventDefault();
        startEditing(node);
        return;
      }
      // A <ct-t> is inline, so its target is only as wide as the words. A centred
      // heading in a full-width <h1> therefore did nothing over most of its own row,
      // and "tocá cualquier texto para editarlo" read as a lie. If the block that WAS
      // clicked holds exactly one editable string, that is unambiguously the one meant.
      const lone = loneEditableIn(event.target);
      if (lone) {
        clickPoint = { x: event.clientX, y: event.clientY };
        event.preventDefault();
        startEditing(lone);
        return;
      }
      const owner = event.target.closest ? event.target.closest("[data-ct-keys]") : null;
      // An element that only carries copy in its ATTRIBUTES (an image alt, a button's
      // aria-label) opens that copy in the panel — unless it is interactive, because
      // swallowing that click would leave the cart, the menu and the gallery dead in
      // the editor, and their copy only exists once they are open.
      if (owner && !isInteractive(event.target)) {
        event.preventDefault();
        event.stopPropagation();
        stopEditing();
        // A picture or a clip opens its OWN controls — preview, upload, earlier
        // versions — because the thing that was clicked is the thing to change. The
        // rest of what lives in an attribute (an alt text, an aria-label) is copy with
        // nowhere on the page to edit it, so that still opens in the panel.
        const keys = (owner.getAttribute("data-ct-keys") || "").split(/\s+/).filter(Boolean);
        const media = mediaKeyOf(owner);
        if (media) post({ type: "openMedia", key: media, keys: keys });
        else post({ type: "openKeys", keys: keys });
        return;
      }
      if (editing) stopEditing();
      if (!owner) explainNotEditable(event.target);
    },
    true
  );

  document.addEventListener(
    "keydown",
    (event) => {
    if (!editing) return;
    const field = FIELDS[parseTarget(editing).key] || {};
    if (event.key === "Escape") {
      event.preventDefault();
      // The site closes its cart drawer / menu on Escape too; cancelling an edit
      // must not also close the panel the text lives in.
      event.stopPropagation();
      const target = parseTarget(editing);
      stopEditing(true);
      refresh(target.key);
      return;
    }
    if (event.key === "Enter" && field.type === "rich") {
      // A short `rich` heading is a block contenteditable, so Enter inserts a <div> —
      // which the server's allow-list unwraps on save, gluing the two lines together on
      // the live page while the editor kept showing them apart. <br> is the line break
      // this field type actually keeps (and what its own hint recommends).
      event.preventDefault();
      insertBreak(editing);
      return;
    }
    if (event.key === "Enter" && field.type !== "rich") {
      event.preventDefault();
      const node = editing;
      stopEditing();
      // Closing in silence read as the editor swallowing the keystroke. `text` and
      // `lines` values DO hold newlines, but only the panel can add one, so say where to
      // go instead of just eating the Enter.
      const multiline = field.type === "text" || field.type === "lines";
      showTip(
        node,
        multiline
          ? "Listo. Acá Enter cierra la edición; para separar en renglones, editá este texto en el panel de la derecha."
          : "Listo. Enter cierra la edición: este texto va en una sola línea."
      );
      tipTimer = window.setTimeout(hideTip, 3600);
      return;
    }

    },
    true
  );

  document.addEventListener("focusout", (event) => {
    if (editing && event.target === editing) {
      // Let the browser settle the new focus target before tearing down.
      window.setTimeout(() => {
        if (editing && !editing.contains(document.activeElement)) stopEditing();
      }, 0);
    }
  });

  /* ---------------- navigation ---------------- */

  // Keep the editor alive while browsing the site inside the frame.
  document.addEventListener(
    "click",
    (event) => {
      const link = event.target.closest ? event.target.closest("a[href]") : null;
      if (!link || event.defaultPrevented) return;
      if (event.target.closest("ct-t")) return;  // that click opened an editor
      const url = new URL(link.getAttribute("href"), window.location.href);
      if (url.origin !== window.location.origin) {
        link.setAttribute("target", "_blank");
        // Without noopener the opened tab can reach back through window.opener
        // (reverse tabnabbing). The server sanitizer already does this for rich
        // links; this is the one path that rewrites target on the fly.
        link.setAttribute("rel", "noopener noreferrer");
        return;
      }
      // The QUERY has to match too, or `href="/#shop"` on `/?edit=1` is a full load to
      // `/#shop`: the canvas quietly stops being editable while the toolbar still shows
      // pending changes. The header's COMPRAR button is 60% padding, so it is the easiest
      // thing on the page to hit. Falling through re-adds edit=1 and keeps the hash, which
      // IS a same-document jump.
      if (
        url.pathname === window.location.pathname &&
        url.search === window.location.search &&
        url.hash
      ) {
        return; // in-page anchor
      }
      event.preventDefault();
      url.searchParams.set("edit", "1");
      window.location.href = url.toString();
    },
    false
  );

  /* ---------------- shell messages ---------------- */

  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin) return;
    // The SENDER has to be the shell that framed us. Origin alone let any same-origin
    // script (a sibling frame, an extension, a future widget) drive `set` — which for a
    // rich key lands its markup in innerHTML here in edit mode. The shell already checks
    // both sides this way; this closes the mirror-image gap on the frame side.
    if (window.parent === window || event.source !== window.parent) return;
    const data = event.data || {};
    if (data.source !== "ct-shell") return;
    if (data.type === "flush") {
      // Clicking a toolbar button blurs the node being edited, and that teardown is
      // deferred a tick so the browser can settle focus first — so the shell used to
      // read `pending` BEFORE the edit landed in it, and its answer was then overwritten
      // by the change message arriving late. Committing on demand removes the race
      // instead of racing it back.
      if (editing) stopEditing();
      window.parent.postMessage(
        { source: "ct-frame", type: "flushed", token: data.token },
        window.location.origin
      );
    } else if (data.type === "set") {
      CURRENT[data.key] = data.value;
      refresh(data.key);
      refreshDependents(data.key);
    } else if (data.type === "chrome") {
      showChrome(data.on !== false);
    } else if (data.type === "size") {
      applySize(data.key, data.token);
    } else if (data.type === "highlight") {
      document.querySelectorAll("ct-t[data-ct-invalid]").forEach((node) =>
        node.removeAttribute("data-ct-invalid")
      );
      document.querySelectorAll("ct-t[aria-invalid]").forEach((node) =>
        node.removeAttribute("aria-invalid")
      );
      (data.keys || []).forEach((key) => {
        document.querySelectorAll("ct-t").forEach((node) => {
          if (parseTarget(node).key !== key) return;
          node.setAttribute("data-ct-invalid", "");
          node.setAttribute("aria-invalid", "true");
        });
      });
      const first = (data.keys || [])[0];
      const nodes = first ? document.querySelectorAll("ct-t[data-ct-invalid]") : [];
      if (nodes.length) nodes[0].scrollIntoView({ block: "center", behavior: scrollBehavior() });
    }
  });

  /** Make every editable string reachable without a mouse, and give it a name. */
  function makeReachable() {
    indexNodes();
    document.querySelectorAll("ct-t").forEach((node) => {
      const field = FIELDS[parseTarget(node).key];
      if (!field) return;
      node.setAttribute("tabindex", "0");
      // A control that OPENS an editor — not a textbox. It only becomes one while
      // `contenteditable` is on, and then the browser reports the right role itself.
      node.setAttribute("role", "button");
      // Saved but not published: on the canvas a draft looks exactly like the live copy,
      // so nothing here said "your customers are not seeing this yet". The manifest is
      // rebuilt on every load, so this is right on a cold start AND after navigating to
      // another page inside the frame.
      if (field.hasDraft) node.setAttribute("data-ct-draft", "");
      const block = isBlockRich(field, CURRENT[parseTarget(node).key]);
      node.setAttribute(
        "aria-label",
        (block ? "Editar el texto de esta página: " : "Editar: ") +
          field.label +
          (field.hasDraft ? " (guardado, sin publicar)" : "")
      );
      if (block) node.setAttribute("data-ct-block", "");
      else node.setAttribute("aria-describedby", "ctTipLive");
    });
    // The ✎ badge is an ::after, so its host needs to be a containing block — but only
    // where the site left one to give. As a stylesheet rule it matched the site's own
    // `.cart-close { position: absolute }` at equal specificity and won, dropping the
    // cart's × out of its corner and pushing .hero-scroll clean off the viewport.
    document.querySelectorAll("[data-ct-keys]").forEach((node) => {
      if (node.closest("head")) return;
      if (window.getComputedStyle(node).position === "static") node.style.position = "relative";
    });
    // Elements whose copy lives only in an attribute (an image's alt) had a badge
    // that only hover could raise, so it could never be reached by keyboard or touch.
    document.querySelectorAll("[data-ct-keys]").forEach((node) => {
      if (node.matches(INTERACTIVE) || node.closest("head")) return;
      if (node.hasAttribute("tabindex")) return;
      // A container that already holds links or buttons must NOT become a button
      // itself: nested interactive controls are announced as one thing, and the site's
      // own <nav aria-label> — a whole menu — was being read out as a single button.
      // It gets a control of its own on the canvas instead, which is reachable by
      // keyboard and by touch without touching the site's semantics at all.
      if (node.querySelector(INTERACTIVE)) {
        node.setAttribute("data-ct-nested", "");
        return;
      }
      node.setAttribute("tabindex", "0");
      node.setAttribute("role", "button");
      node.setAttribute("aria-label", "Editar los textos de este elemento");
    });
    setupBars();
  }

  /* ---------------- the controls that stand on the canvas ---------------- */
  //
  // Editing here is BLOCK editing: every block that can be changed carries its own
  // controls, in place, standing. They used to appear on hover (the picture) or to live
  // only in the side list (the size), which quietly made the list the real editor and
  // the canvas a preview — exactly backwards. A control you have to discover is a
  // control most people never find.

  let barLayer = null;
  const bars = [];             // { el, anchor }
  const sizeBars = new Map();  // key -> sync()

  // The scale this install offers, in order. Empty where the host never turned sizes on,
  // and then no size control is ever drawn.
  const SIZE_STEPS = (MANIFEST.sizes || []).map((step) => step.token);
  const SIZE_LABELS = {};
  (MANIFEST.sizes || []).forEach((step) => { SIZE_LABELS[step.token] = step.label; });
  // What each field is showing RIGHT NOW, so a step is taken from the size on screen
  // and not from the one the page happened to load with.
  const SIZE_NOW = {};
  Object.keys(FIELDS).forEach((key) => { SIZE_NOW[key] = FIELDS[key].size || "base"; });

  let liveRegion = null;

  /** Say out loud what a button just did. The canvas shows it (the text really does get
   *  bigger); a screen reader has nothing to look at. */
  function announce(text) {
    if (!liveRegion) {
      liveRegion = document.createElement("p");
      liveRegion.id = "ctLive";
      liveRegion.className = "ct-live";
      liveRegion.setAttribute("role", "status");
      liveRegion.setAttribute("aria-live", "polite");
      document.body.appendChild(liveRegion);
    }
    liveRegion.textContent = text;
  }

  function ensureBarLayer() {
    if (barLayer) return barLayer;
    barLayer = document.createElement("div");
    barLayer.className = "ct-bars";
    document.body.appendChild(barLayer);
    return barLayer;
  }

  function makeBar(anchor, extraClass, key) {
    const el = document.createElement("div");
    el.className = "ct-bar" + (extraClass ? " " + extraClass : "");
    el.setAttribute("data-ct-bar", "");
    if (key) el.setAttribute("data-ct-for", key);
    ensureBarLayer().appendChild(el);
    bars.push({ el: el, anchor: anchor });
    return el;
  }

  function mediaKeyOf(el) {
    const raw = el.getAttribute("data-ct-keys");
    if (!raw) return null;
    const keys = raw.split(/\s+/);
    for (let i = 0; i < keys.length; i++) {
      const type = (FIELDS[keys[i]] || {}).type;
      if (type === "image" || type === "video") return keys[i];
    }
    return null;
  }

  /** A replaced element can host neither a <ct-t> nor a ::after badge, so the picture's
   *  own button lives in the bar. Everything that element carries — the picture AND its
   *  alt text — travels with it: they are one thing to the person looking at it. */
  function addMediaBar(el, key) {
    const bar = makeBar(el, "ct-bar-media", key);
    const keys = (el.getAttribute("data-ct-keys") || "").split(/\s+/).filter(Boolean);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ct-media-chip";
    btn.textContent =
      (FIELDS[key] || {}).type === "video" ? "✎ Cambiar video" : "✎ Cambiar imagen";
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      post({ type: "openMedia", key: key, keys: keys });
    });
    bar.appendChild(btn);
  }

  /** Two steps and a readout, rather than a dropdown: the scale is closed and short, the
   *  answer is on the page right behind the button, and a step is one tap on a phone. */
  function addSizeBar(node, key) {
    const field = FIELDS[key] || {};
    const name = field.label || key;
    const bar = makeBar(node, "ct-bar-size", key);

    const glyph = document.createElement("span");
    glyph.className = "ct-size-of";
    glyph.setAttribute("aria-hidden", "true");  // "A" is decoration; the buttons say it
    glyph.textContent = "A";
    bar.appendChild(glyph);

    const down = document.createElement("button");
    down.type = "button";
    down.className = "ct-size-btn";
    down.textContent = "\u2212";
    bar.appendChild(down);

    const read = document.createElement("span");
    read.className = "ct-size-now";
    bar.appendChild(read);

    const up = document.createElement("button");
    up.type = "button";
    up.className = "ct-size-btn";
    up.textContent = "+";
    bar.appendChild(up);

    function at() {
      const index = SIZE_STEPS.indexOf(SIZE_NOW[key] || "base");
      return index === -1 ? SIZE_STEPS.indexOf("base") : index;
    }

    function sync() {
      const index = at();
      const now = SIZE_LABELS[SIZE_STEPS[index]] || "";
      read.textContent = now;
      // The readout is shown on hover and on focus only — a standing control has to be
      // as small as it can be, and a word of chrome per block is what turns a page into
      // a cockpit. Nobody loses the information: it is in the buttons' own names.
      const where = ' el texto: ' + name + (now ? " (ahora " + now + ")" : "");
      down.setAttribute("aria-label", "Achicar" + where);
      up.setAttribute("aria-label", "Agrandar" + where);
      // aria-disabled, not `disabled`: a real disabled attribute takes focus away from
      // the very button that was just pressed, dropping the keyboard back to <body>.
      down.setAttribute("aria-disabled", index <= 0 ? "true" : "false");
      up.setAttribute("aria-disabled", index >= SIZE_STEPS.length - 1 ? "true" : "false");
    }

    function step(delta) {
      const next = at() + delta;
      if (next < 0 || next >= SIZE_STEPS.length) {
        announce(
          delta < 0 ? "Ya está en el tamaño más chico." : "Ya está en el tamaño más grande."
        );
        return;
      }
      const token = SIZE_STEPS[next];
      // Apply it here first: the point of stepping on the canvas is judging the size
      // against the real page, and waiting for the shell to echo it back would blink.
      applySize(key, token);
      post({ type: "size", key: key, token: token });
      announce(name + ": " + (SIZE_LABELS[token] || token));
    }

    down.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      step(-1);
    });
    up.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      step(1);
    });

    sizeBars.set(key, sync);
    sync();
  }

  /** Copy that reaches the page only through an attribute of an element that is itself
   *  full of links (a menu's aria-label). It cannot be clicked without swallowing the
   *  site's own controls, so its own button lives beside it. */
  function addKeysBar(el, keys) {
    const bar = makeBar(el, "ct-bar-keys", keys[0]);
    const name = (FIELDS[keys[0]] || {}).label || "este elemento";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ct-keys-chip";
    btn.textContent = "✎ " + name;
    btn.setAttribute("aria-label", "Editar los textos de este elemento: " + name);
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      post({ type: "openKeys", keys: keys });
    });
    bar.appendChild(btn);
  }

  function setupBars() {
    document.querySelectorAll("img[data-ct-keys], video[data-ct-keys]").forEach((el) => {
      const key = mediaKeyOf(el);
      // NOT data-ct-bar: that attribute marks the bar itself, and the click handler
      // skips anything inside one. Putting it on the picture made the editor ignore
      // every click on the picture — the exact gesture this feature exists for.
      if (!key || el.dataset.ctWired) return;
      el.dataset.ctWired = "1";  // wire each element once, even across re-scans
      addMediaBar(el, key);
    });
    document.querySelectorAll("[data-ct-nested]").forEach((el) => {
      const keys = (el.getAttribute("data-ct-keys") || "").split(/\s+/).filter(Boolean);
      if (!keys.length || el.dataset.ctWired) return;
      el.dataset.ctWired = "1";
      addKeysBar(el, keys);
    });
    if (SIZE_STEPS.length) {
      document.querySelectorAll("ct-t").forEach((node) => {
        const key = parseTarget(node).key;
        // One control per FIELD, not per node: a `lines` value renders a node per item,
        // and a nav label repeats in the header, the menu and the footer.
        if (sizeBars.has(key)) return;
        const field = FIELDS[key];
        if (!field || field.resizable !== true) return;
        addSizeBar(node, key);
      });
    }
    scheduleBars();
  }

  let chromeOn = true;

  /** Hide or show every block control at once — the way to look at the page as a
   *  customer will, without leaving the editor. */
  function showChrome(on) {
    chromeOn = on;
    if (barLayer) barLayer.hidden = !on;
    if (on) scheduleBars();
  }

  let barFrame = 0;

  /** Placing is measuring, and measuring on every scroll event is how a page stutters.
   *  One placement per frame, however many events asked for it. */
  function scheduleBars() {
    if (barFrame || !bars.length || !chromeOn) return;
    barFrame = window.requestAnimationFrame(() => {
      barFrame = 0;
      placeBars();
    });
  }

  function overlaps(a, b) {
    return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
  }

  function placeBars() {
    const vw = document.documentElement.clientWidth;
    const vh = window.innerHeight;
    // Read everything, then write everything. Interleaving them makes the browser lay
    // the page out again for every single bar — 40 forced reflows per scrolled frame on
    // a long page, which is exactly the stutter this chrome must not introduce.
    const boxes = [];
    const lines = [];
    let remeasure = false;
    bars.forEach((bar) => {
      const anchor = bar.anchor;
      if (!anchor.isConnected) {
        boxes.push(null);
        lines.push(null);
        return;
      }
      const box = anchor.getBoundingClientRect();
      // Laid out to nothing (a closed menu, an emptied string), or scrolled out of
      // sight: no control, and nothing measured for it either.
      if ((!box.width && !box.height) || box.bottom < 8 || box.top > vh - 8) {
        boxes.push(null);
        lines.push(null);
        return;
      }
      // A hidden element measures 0, so a bar coming back into view is placed from the
      // size it had last time and corrected on the next frame.
      if (!bar.el.hidden) {
        bar.w = bar.el.offsetWidth;
        bar.h = bar.el.offsetHeight;
      } else if (!bar.h) {
        remeasure = true;
      }
      boxes.push(box);
      // The LINES, not the block: a wrapped heading's bounding box is the union of its
      // lines, so a two-word second line reserves the whole width and every free spot
      // beside it reads as taken. On a phone that left nowhere to put anything.
      const rects = anchor.getClientRects();
      lines.push(rects.length ? Array.prototype.slice.call(rects) : [box]);
    });

    function cover(a, b) {
      const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      return w > 0 && h > 0 ? w * h : 0;
    }

    // Placed bars are obstacles for the ones after them: two controls stacked on each
    // other hide one of the two, which is worse than either of them being slightly off.
    const placed = [];
    const plan = boxes.map((box, index) => {
      if (!box) return null;
      const bar = bars[index];
      const w = bar.w || 0;
      const h = bar.h || 0;
      const beside = Math.min(Math.max(2, box.top), Math.max(2, vh - h - 2));
      const right = Math.max(4, Math.min(box.right - w, vw - w - 4));
      const left = Math.max(4, Math.min(box.left, vw - w - 4));
      // In order of how little they cost the page: hugging the block above or below,
      // then either side of it, and only as a last resort on top of the block itself.
      // A control parked over someone else's words is the thing this must never do.
      const spots = [];
      if (box.top - h - 4 >= 2) {
        spots.push({ top: box.top - h - 4, left: left });
        spots.push({ top: box.top - h - 4, left: right });
      }
      if (box.right + 8 + w <= vw - 4) spots.push({ top: beside, left: box.right + 8 });
      if (box.left - w - 8 >= 4) spots.push({ top: beside, left: box.left - w - 8 });
      if (box.bottom + 4 + h <= vh - 2) {
        spots.push({ top: box.bottom + 4, left: left });
        spots.push({ top: box.bottom + 4, left: right });
      }
      spots.push({ top: beside, left: left });
      spots.push({ top: beside, left: right });

      let best = null;
      let bestCost = Infinity;
      for (let i = 0; i < spots.length; i++) {
        const rect = {
          left: spots[i].left,
          top: spots[i].top,
          right: spots[i].left + w,
          bottom: spots[i].top + h,
        };
        // Free means free of every OTHER editable block — its own is what it labels.
        let cost = 0;
        for (let j = 0; j < lines.length; j++) {
          if (j === index || !lines[j]) continue;
          for (let k = 0; k < lines[j].length; k++) cost += cover(rect, lines[j][k]);
        }
        for (let j = 0; j < placed.length; j++) cost += cover(rect, placed[j]) * 4;
        if (cost === 0) {
          best = spots[i];
          bestCost = 0;
          break;
        }
        // Nowhere is free — a phone at 390px runs out of page long before it runs out
        // of blocks. Then take the spot that hides the least, rather than the first one.
        if (cost < bestCost) {
          bestCost = cost;
          best = spots[i];
        }
      }
      placed.push({ left: best.left, top: best.top, right: best.left + w, bottom: best.top + h });
      return best;
    });

    bars.forEach((bar, index) => {
      const at = plan[index];
      if (!at) {
        bar.el.hidden = true;
        return;
      }
      bar.el.hidden = false;
      bar.el.style.top = at.top + "px";
      bar.el.style.left = at.left + "px";
    });
    // One correcting pass for the bars that had never been measured.
    if (remeasure) scheduleBars();
  }

  window.addEventListener("scroll", scheduleBars, { passive: true });
  window.addEventListener("resize", scheduleBars);
  // A photo that arrives late reflows everything under it.
  window.addEventListener("load", scheduleBars);
  if (window.ResizeObserver) {
    // The document's own size, not each block's: cheap, and it catches the reflows that
    // matter (an image loading, a size step, a menu opening). The work happens in the
    // next frame, so this can never loop back into itself.
    new window.ResizeObserver(scheduleBars).observe(document.documentElement);
  }

  document.addEventListener("keydown", (event) => {
    if (editing) return;
    // The capture-phase handler above already consumed this key to COMMIT an edit;
    // the element keeps focus (it is focusable now), so without this the same Enter
    // reopened it with everything selected and the next keystroke wiped the field.
    if (event.defaultPrevented) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    const node = document.activeElement;
    if (!node) return;
    if (node.tagName === "CT-T") {
      event.preventDefault();
      startEditing(node);
      return;
    }
    if (node.hasAttribute && node.hasAttribute("data-ct-keys")) {
      event.preventDefault();
      const keys = (node.getAttribute("data-ct-keys") || "").split(/\s+/).filter(Boolean);
      const media = mediaKeyOf(node);
      if (media) post({ type: "openMedia", key: media, keys: keys });
      else post({ type: "openKeys", keys: keys });
    }
  });

  makeReachable();
  post({ type: "ready", path: window.location.pathname, manifest: MANIFEST });
  post({ type: "askChrome" });
})();
