() => {
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
    for (let n = e; n && n.nodeType === 1; n = n.parentElement) {
      const s = getComputedStyle(n), r = n.getBoundingClientRect();
      if (s.display === 'none' || s.visibility === 'hidden') return true;
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
  const norm = t => t.replace(/\s+/g, ' ').replace(/[\u200e\u200f]/g, '').trim();

  const de = document.documentElement;
  if (de.scrollWidth > W + 1)
    out.push({k: 'overflow-x', what: `page scrolls sideways: ${de.scrollWidth}px > ${W}px`});

  const all = [...document.querySelectorAll('body *')].filter(e => !hidden(e));

  // ---- colour, composited the way the screen does it ----------------------
  const px = s => { const m = (s||'').match(/[\d.]+/g); return m ? m.map(Number) : null; };
  const over = (fg, bg) => {          // fg may be translucent; bg is opaque
    const a = fg.length > 3 ? fg[3] : 1;
    return [0,1,2].map(i => fg[i]*a + bg[i]*(1-a));
  };
  // A gradient or image behind the text makes "the background colour" a
  // fiction. Compositing through it reported the drawer avatar — near-black on
  // a light olive gradient, about 9:1 in reality — as 1.03:1, and that bogus
  // hit led every screen's list. Contrast is skipped there and judged by eye.
  const paintedBg = e => {
    for (let n = e; n && n.nodeType === 1; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.backgroundImage && s.backgroundImage !== 'none') return true;
      const c = px(s.backgroundColor);
      if (c && (c.length < 4 || c[3] >= .999)) return false;
    }
    return false;
  };
  const solidBg = e => {
    // walk up compositing every translucent layer onto the one behind it —
    // v1 returned the first non-transparent colour and read a 4.5%-alpha white
    // overlay as solid white, which is where 60+ bogus contrast hits came from
    const layers = [];
    for (let n = e; n && n.nodeType === 1; n = n.parentElement) {
      const c = px(getComputedStyle(n).backgroundColor);
      if (!c) continue;
      const a = c.length > 3 ? c[3] : 1;
      if (a === 0) continue;
      layers.push(c);
      if (a >= .999) break;
    }
    let base = [255,255,255];
    for (let i = layers.length - 1; i >= 0; i--) base = over(layers[i], base);
    return base;
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
    if (paintedBg(e)) continue;
    const bg = solidBg(e), fg = over(fg0, bg);
    const L1 = lum(fg), L2 = lum(bg);
    const ratio = (Math.max(L1,L2)+.05) / (Math.min(L1,L2)+.05);
    const size = parseFloat(s.fontSize), bold = +s.fontWeight >= 700;
    const floor = (size >= 24 || (size >= 18.66 && bold)) ? 3 : 4.5;
    if (ratio < floor)
      out.push({k: 'contrast', what: `${name(e)} ${ratio.toFixed(2)}:1 (AA needs ${floor}) at ${size}px`, t: txt(e)});
  }

  // ---- the two classes the first pass of this review actually turned up ----
  // Neither is a "wrong" pixel, which is why nothing mechanical caught them:
  // content that renders perfectly and is covered by something else, and
  // content that is correct but said twice on one screen.
  const seen = new Map();
  for (const e of all) {
    const t = txt(e);
    const leaf = owns(e) && t;

    // OCCLUSION — rendered, laid out, and invisible because something opaque
    // sits on it. This is the wordmark-under-the-card bug, generalised.
    if (leaf && t.length > 3) {
      const r = e.getBoundingClientRect();
      const cx = Math.min(W - 1, Math.max(1, r.left + r.width / 2));
      const cy = Math.min(H - 1, Math.max(1, r.top + r.height / 2));
      if (r.top >= 0 && r.bottom <= H) {
        const hit = document.elementFromPoint(cx, cy);
        if (hit && hit !== e && !e.contains(hit) && !hit.contains(e)) {
          // How opaque is the STACK sitting on top, not just the topmost node?
          // elementFromPoint usually lands on a transparent text div whose
          // card carries the fill, so testing that node's own background said
          // "see-through" for a wordmark buried under a solid card.
          let cover = 0;
          for (let n = hit; n && n.nodeType === 1 && !n.contains(e); n = n.parentElement) {
            const c = px(getComputedStyle(n).backgroundColor);
            if (!c) continue;
            const a = c.length > 3 ? c[3] : 1;
            cover = cover + a * (1 - cover);
          }
          if (cover >= .5)
            out.push({k: 'occluded',
                      what: `${name(e)} is ${Math.round(cover*100)}% covered by ${name(hit)}`, t});
        }
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

    // PLACEHOLDER LEAK — a value that was meant to be filled in
    if (leaf && /\b(undefined|NaN|None|null|\[object Object\]|\{\{|TODO|FIXME|lorem)\b/i.test(t))
      out.push({k: 'placeholder-leak', what: `${name(e)} shows a raw value`, t});
  }

  for (const b of [...document.querySelectorAll('button,a[href],[role=button]')].filter(e => !hidden(e))) {
    const r = b.getBoundingClientRect();
    if (r.height < 44)
      out.push({k: 'small-target', what: `${name(b)} is ${Math.round(r.width)}x${Math.round(r.height)} (44px floor)`, t: txt(b)});
    const n = (b.getAttribute('aria-label') || b.innerText || b.title || '').trim();
    if (!n) out.push({k: 'unnamed-control', what: `${name(b)} has no accessible name`});
  }
  return out;
}
