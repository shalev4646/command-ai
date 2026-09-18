(scope) => {
  const W = innerWidth, H = innerHeight, out = [];

  // ---- visibility, the way a user experiences it --------------------------
  // Three things this must catch, all of which produced noise in v1:
  //   a 1px clipped box (Streamlit's visually-hidden button labels),
  //   an ancestor faded or scaled to nothing,
  //   the drawer, parked off-screen by a transform while "visible".
  const hidden = e => {
    const r0 = e.getBoundingClientRect();
    // the element's OWN box: a 1-2px box is Streamlit's visually-hidden label
    if (r0.width <= 2 || r0.height <= 2) return true;
    // ...but never test an ancestor's box for size. Streamlit wraps the app in
    // `withScreencast`, which measures 390x0, so walking that test up the tree
    // marked every element on every screen hidden — and this file reported a
    // clean sweep of 13 screens while blind to two defects already found by
    // eye. Ancestors are only consulted for things that really do inherit.
    for (let n = e, prev = null; n && n.nodeType === 1; prev = n, n = n.parentElement) {
      const s = getComputedStyle(n), r = n.getBoundingClientRect();
      if (s.display === 'none' || s.visibility === 'hidden') return true;
      // a closed <details> draws only its <summary>, yet Chromium lays the
      // rest out when asked: a collapsed st.expander read as 328x43 buttons,
      // one of them "unnamed" because its text was never drawn (2026-09-17)
      if (n.tagName === 'DETAILS' && !n.open && prev && prev.tagName !== 'SUMMARY') return true;
      if (+s.opacity < .05) return true;
      if (s.clipPath && s.clipPath !== 'none' && /inset\(\s*(100|50)/.test(s.clipPath)) return true;
      // a panel parked wholly outside the viewport (the closed drawer) is not
      // a defect — but only when it HAS a box to be parked
      if (r.width > 2 && (r.right <= 0 || r.left >= W)) return true;
    }
    return false;
  };
  const name = e => (typeof e.className === 'string'
      ? '.' + e.className.split(/\s+/).filter(c => /^cai-|^st-key-/.test(c)).join('.') : '')
      .replace(/^\.$/, '') || e.tagName.toLowerCase();
  const txt = e => (e.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 46);

  // The element that OWNS a run of text: it has text, and no element child
  // has the same text. The first version of this file used "no children at
  // all", which silently skipped every string containing a <b>, <br> or
  // <span> — i.e. most real UI copy, and BOTH of the defects this review had
  // already found by eye. Verified against the pre-fix build.
  const owns = e => {
    const t = (e.innerText || '').trim();
    if (!t) return false;
    for (const c of e.children) if ((c.innerText || '').trim() === t) return false;
    return true;
  };
  const norm = t => t.replace(/\s+/g, ' ').replace(/[‎‏]/g, '').trim();

  const de = document.documentElement;
  if (de.scrollWidth > W + 1)
    out.push({k: 'overflow-x', what: `page scrolls sideways: ${de.scrollWidth}px > ${W}px`});

  // scope: the layer a screen is about (an open drawer or settings sheet).
  // What lies under it is covered on purpose and is audited on its own
  // screen; auditing it again reads every line of the chat as "occluded" —
  // 245 of 262 findings in the first local sweep. A scope that matches
  // nothing, or is not on screen, is reported: an audit of nothing is clean.
  const root = scope ? document.querySelector(scope) : document.body;
  if (!root) return [{k: 'scope-missing', what: `no element matches ${scope}`}];
  if (scope && hidden(root)) return [{k: 'scope-hidden', what: `${scope} is not on screen`}];
  const all = [...root.querySelectorAll('*')].filter(e => !hidden(e));

  // ---- colour, composited the way the screen does it ----------------------
  const px = s => { const m = (s||'').match(/[\d.]+/g); return m ? m.map(Number) : null; };
  const alpha = c => c.length > 3 ? c[3] : 1;
  const over = (fg, bg) => {          // fg may be translucent; bg is opaque
    const a = alpha(fg);
    return [0,1,2].map(i => fg[i]*a + bg[i]*(1-a));
  };
  const stopsOf = bi => (bi.match(/rgba?\([^)]*\)/g) || []).map(px);
  // Every backdrop the text can sit on, composited up from the first opaque
  // colour. A gradient adds each of its colour stops as a separate candidate
  // and the WORST one decides; an image (url()) returns null and is judged by
  // eye. History: v1 took the first non-transparent colour as solid and read
  // a 4.5% white wash as white (60+ bogus hits), so translucent layers are
  // composited. The next version composited THROUGH gradients and read the
  // drawer avatar — near-black on a light olive ramp, ~9:1 — as 1.03:1; the
  // one after skipped any text with a gradient behind it, which also switched
  // contrast off on every settings screen — the sheet is a ramp — and
  // reported "0 contrast findings" there (found by audit_selftest).
  //
  // A fixed, full-viewport ::before is a layer too: the app's page ramp is
  // body::before, and html's flat colour under it is darker than the screen
  // near the bottom. With a negative z-index it paints BELOW its element's own
  // background (an opaque one hides it), otherwise above it. (A negative-z
  // pseudo of an element that is itself a stacking context is misjudged.)
  const underlay = n => {
    const p = getComputedStyle(n, '::before');
    if (p.content === 'none' || p.position !== 'fixed') return null;
    if (p.top !== '0px' || p.left !== '0px') return null;
    if (p.bottom !== '0px' && parseFloat(p.height) < H * .9) return null;
    const got = [];
    if (p.backgroundImage !== 'none' && !/url\(/.test(p.backgroundImage)) {
      const st = stopsOf(p.backgroundImage);
      if (st.length) got.push(st);
    }
    const c = px(p.backgroundColor);
    if (c && alpha(c) > 0) got.push([c]);
    return {got, behind: parseInt(p.zIndex, 10) < 0};
  };
  const backdrops = e => {
    const layers = [];  // topmost first
    for (let n = e; n && n.nodeType === 1; n = n.parentElement) {
      const u = underlay(n);
      if (u && !u.behind) layers.push(...u.got);
      const s = getComputedStyle(n), bi = s.backgroundImage;
      if (bi && bi !== 'none') {
        if (/url\(/.test(bi)) return null;
        const stops = stopsOf(bi);
        if (stops.length) layers.push(stops);
      }
      const c = px(s.backgroundColor);
      if (c && alpha(c) > 0) {
        layers.push([c]);
        if (alpha(c) >= .999) break;
      }
      if (u && u.behind) layers.push(...u.got);
    }
    // composite bottom-up (an element's colour sits under its own gradient,
    // which is why it was pushed after it); every stop is its own candidate
    let bases = [[255,255,255]];
    for (let i = layers.length - 1; i >= 0; i--)
      bases = layers[i].flatMap(c => bases.map(b => over(c, b)));
    return bases;
  };
  const lum = c => {
    const v = c.map(x => { x /= 255; return x <= .03928 ? x/12.92 : Math.pow((x+.055)/1.055, 2.4); });
    return .2126*v[0] + .7152*v[1] + .0722*v[2];
  };

  for (const e of all) {
    const s = getComputedStyle(e), r = e.getBoundingClientRect();

    if (r.width < W && (r.right > W + 1 || r.left < -1) && s.position !== 'fixed')
      out.push({k: 'off-screen', what: `${name(e)} spans x=${Math.round(r.left)}..${Math.round(r.right)} of 0..${W}`, t: txt(e)});

    if (!owns(e)) continue;

    if ((s.overflowX === 'hidden' || s.overflow === 'hidden') && e.scrollWidth > e.clientWidth + 2)
      out.push({k: 'clipped-text', what: `${name(e)}: ${e.scrollWidth}px of text in a ${e.clientWidth}px box`, t: txt(e)});

    const fg0 = px(s.color); if (!fg0) continue;
    const bgs = backdrops(e);
    if (!bgs) continue;
    let ratio = Infinity;
    for (const bg of bgs) {
      const fg = over(fg0, bg), L1 = lum(fg), L2 = lum(bg);
      ratio = Math.min(ratio, (Math.max(L1,L2)+.05) / (Math.min(L1,L2)+.05));
    }
    const size = parseFloat(s.fontSize), bold = +s.fontWeight >= 700;
    const floor = (size >= 24 || (size >= 18.66 && bold)) ? 3 : 4.5;
    if (ratio < floor)
      out.push({k: 'contrast', what: `${name(e)} ${ratio.toFixed(2)}:1 (AA needs ${floor}) at ${size}px`, t: txt(e)});
  }

  // ---- the two classes the first pass of this review actually turned up ----
  // Neither is a "wrong" pixel, which is why nothing mechanical caught them:
  // content that renders perfectly and is covered by something else, and
  // content that is correct but said twice on one screen.

  // how much a box paints over what lies beneath it, 0..1 (a gradient counts
  // as the mean of its stops — the hit test already says the point is inside)
  const paintOf = s => {
    const c = px(s.backgroundColor), bi = s.backgroundImage;
    let a = c ? alpha(c) : 0;
    if (bi && bi !== 'none') {
      const al = stopsOf(bi).map(alpha);
      const g = /url\(/.test(bi) ? 1 : (al.length ? al.reduce((x, y) => x + y, 0) / al.length : 0);
      a = a + g * (1 - a);
    }
    return a;
  };
  const fill = n => /^(IMG|CANVAS|VIDEO)$/.test(n.tagName) ? 1 : paintOf(getComputedStyle(n));
  // An ANCESTOR found above its own text is its ::before/::after overlay —
  // hit-testing reports a pseudo-element as its owner. The settings sheet's
  // sticky top fade is one; the ancestor's own background is painted under
  // the text, so the overlay is what gets measured.
  const pseudoFill = n => Math.max(...['::before', '::after'].map(w => {
    const p = getComputedStyle(n, w);
    return p.content === 'none' ? 0 : paintOf(p) * +p.opacity;
  }));
  const opacity = n => {
    let o = 1;
    for (let m = n; m && m.nodeType === 1; m = m.parentElement) o *= +getComputedStyle(m).opacity;
    return o;
  };

  // Hit-testing skips pointer-events:none — and the layers most likely to
  // hide a whole screen, the boot curtain and the navigation veil, pass taps
  // through by design. Everything is hit-testable while the stacks are read,
  // and put back before this returns. A ::before/::after overlay is hit as its
  // owner element (see pseudoFill).
  const tapThrough = document.createElement('style');
  tapThrough.textContent = '*, *::before, *::after { pointer-events: auto !important; }';
  document.head.appendChild(tapThrough);
  const seen = new Map();
  try {
    for (const e of all) {
      const t = txt(e);
      const leaf = owns(e) && t;

      // OCCLUSION — rendered, laid out, and invisible because something
      // opaque sits on it. This is the wordmark-under-the-card bug, generalised.
      if (leaf && t.length > 3) {
        const r = e.getBoundingClientRect();
        const cx = Math.min(W - 1, Math.max(1, r.left + r.width / 2));
        const cy = Math.min(H - 1, Math.max(1, r.top + r.height / 2));
        if (r.top >= 0 && r.bottom <= H) {
          // the whole stack painted above the text, top-down — not just the
          // topmost node, which is usually a transparent div whose card
          // carries the fill
          const stack = document.elementsFromPoint(cx, cy);
          const at = stack.findIndex(n => n === e || e.contains(n));
          let cover = 0, by = null;
          for (const n of at < 0 ? [] : stack.slice(0, at)) {
            if (hidden(n)) continue;
            const a = (n.contains(e) ? pseudoFill(n) : fill(n)) * opacity(n);
            if (!a) continue;
            by = by || n;
            cover = cover + a * (1 - cover);
          }
          if (cover >= .5)
            out.push({k: 'occluded',
                      what: `${name(e)} is ${Math.round(cover*100)}% covered by ${name(by)}`
                            + (by.contains(e) ? ' (its ::before/::after overlay)' : ''), t});
        }
      }

      // DUPLICATE COPY — the same sentence rendered twice on one screen. The
      // wipe note read identically inside its own confirm and under the button.
      if (leaf) {
        const full = norm(e.innerText || '');
        if (full.length >= 40) {
          for (const [prevText, prevEl] of seen) {
            if (prevEl === e || prevEl.contains(e) || e.contains(prevEl)) continue;
            const [a, b] = full.length >= prevText.length ? [full, prevText] : [prevText, full];
            if (a.includes(b))
              out.push({k: 'duplicate-copy',
                        what: `${name(prevEl)} and ${name(e)} say the same thing`,
                        t: b.slice(0, 60)});
          }
          seen.set(full, e);
        }
      }

      // PLACEHOLDER LEAK — a value that was meant to be filled in. The markup
      // tokens sit outside \b: a word boundary before "{" needs a letter
      // there, so " {{name}}" never matched. {name} is the likeliest leak in
      // this codebase: a Python string that lost its f prefix.
      if (leaf && /\b(undefined|NaN|None|null|TODO|FIXME|lorem)\b|\[object Object\]|\{\{|\{[A-Za-z_][\w.]*\}/i.test(t))
        out.push({k: 'placeholder-leak', what: `${name(e)} shows a raw value`, t});
    }
  } finally {
    tapThrough.remove();
  }

  for (const b of [...root.querySelectorAll('button,a[href],[role=button]')].filter(e => !hidden(e))) {
    const r = b.getBoundingClientRect();
    if (r.height < 44)
      out.push({k: 'small-target', what: `${name(b)} is ${Math.round(r.width)}x${Math.round(r.height)} (44px floor)`, t: txt(b)});
    const n = (b.getAttribute('aria-label') || b.innerText || b.title || '').trim();
    if (!n) out.push({k: 'unnamed-control', what: `${name(b)} has no accessible name`});
  }
  return out;
}
