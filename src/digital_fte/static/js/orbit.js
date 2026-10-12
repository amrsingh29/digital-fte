/* ASCII agent orbit.
   A shaded core sized by the overall total (DFTE, or activity before DFTE exists), with one sphere per coding agent orbiting it,
   sized by that agent's share of the work. Same method as personal-clone-site:
   work out ink density per character cell, then map it through a character ramp. */
(function () {
  "use strict";
  const RAMP = " .:-=+*#%@";
  const LIGHT = norm([-0.55, -0.62, 0.56]);
  const ORBITS = [ // incl, roll, speed (rad/ms), start phase
    [0.40, 0.10, 0.00021, 0.6],
    [-0.32, -0.16, 0.00015, 2.9],
    [0.48, 0.04, 0.00012, 4.4],
    [-0.24, -0.07, 0.00009, 1.5],
  ];

  function norm(v) { const m = Math.hypot(v[0], v[1], v[2]); return [v[0] / m, v[1] / m, v[2] / m]; }
  function mulberry(a) { return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }

  class Orbit {
    constructor(stage, opts = {}) {
      this.stage = stage;
      this.cv = stage.querySelector("canvas");
      this.ctx = this.cv.getContext("2d");
      this.pillsEl = stage.querySelector(".pills");
      this.onSelect = opts.onSelect || (() => {});
      this.reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
      this.mode = "mono";
      this.agents = [];       // [{id,name,share,active,color}]
      this.state = {};        // per-agent animation state, keyed by id
      this.level = 0;        // 0..1, how big the core is
      this.coreK = 0;         // eased 0..1 core size factor
      this.sel = null; this.hover = null;
      this.visible = true; this.last = 0; this.prev = performance.now(); this.clock = 0;

      const rnd = mulberry(7);
      this.dust = Array.from({ length: 64 }, () => ({
        r: 0.5 + rnd() * 0.62, th: rnd() * 6.283, incl: (rnd() - 0.5) * 1.1,
        sp: (0.25 + rnd()) * 0.00008 * (rnd() < 0.5 ? -1 : 1), ink: 0.08 + rnd() * 0.22,
      }));

      this.readColors();
      new ResizeObserver(() => this.resize()).observe(stage);
      new IntersectionObserver(([e]) => { this.visible = e.isIntersecting; }).observe(stage);
      document.addEventListener("visibilitychange", () => { this.prev = performance.now(); });
      this.cv.addEventListener("pointermove", (e) => this.pointer(e));
      this.cv.addEventListener("pointerleave", () => this.setHover(null));
      this.cv.addEventListener("click", () => this.select(this.hover && this.hover !== this.sel ? this.hover : null, true));
      this.resize();
      if (!this.reduce) requestAnimationFrame((t) => this.loop(t));
    }

    setData(level, agents) {
      this.level = Math.max(0, Math.min(1, level));
      this.agents = agents;
      agents.forEach((a, i) => {
        if (!this.state[a.id]) {
          const o = ORBITS[i % ORBITS.length];
          // first data: start at full size; agents switched on later grow in
          this.state[a.id] = { th: o[3], size: this.started ? 0 : a.share, z: 0, x: 0, y: 0, rb: 0 };
        }
      });
      if (this.sel && !agents.some((a) => a.id === this.sel)) this.sel = null;
      if (!this.started) { this.coreK = this.coreTarget(); this.started = true; }
      this.buildPills();
      if (this.reduce) { this.coreK = this.coreTarget(); agents.forEach((a) => (this.state[a.id].size = a.share)); }
      this.draw();
    }

    coreTarget() { return this.level; }
    setMode(m) { this.mode = m; this.draw(); }
    readColors() {
      const cs = getComputedStyle(document.documentElement);
      const v = (n) => cs.getPropertyValue(n).trim();
      this.colors = { fg: v("--fg"), faint: v("--faint"), core: v("--a-core") };
      this.draw && this.cols && this.draw();
    }

    select(id, fromUser) {
      this.sel = id;
      this.syncPills();
      if (fromUser) this.onSelect(id);
      this.draw();
    }
    setHover(id) {
      if (id === this.hover) return;
      this.hover = id;
      this.cv.style.cursor = id ? "pointer" : "default";
      this.syncPills();
      if (this.reduce) this.draw();
    }

    buildPills() {
      this.pillsEl.textContent = "";
      this.pills = {};
      this.agents.forEach((a) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "pill" + (a.active ? " live" : "");
        b.innerHTML = `<span class="dot" style="background:var(${a.color})"></span>`;
        b.append(a.name);
        b.title = a.active ? `${a.name} · active in the last hour` : a.name;
        b.addEventListener("mouseenter", () => this.setHover(a.id));
        b.addEventListener("mouseleave", () => this.setHover(null));
        b.addEventListener("click", () => this.select(this.sel === a.id ? null : a.id, true));
        this.pillsEl.append(b);
        this.pills[a.id] = b;
      });
      this.syncPills();
    }
    syncPills() {
      for (const id in this.pills) {
        this.pills[id].classList.toggle("on", id === this.sel);
        this.pills[id].classList.toggle("hot", id === this.hover && id !== this.sel);
        this.pills[id].setAttribute("aria-pressed", id === this.sel ? "true" : "false");
      }
    }

    resize() {
      const W = this.stage.clientWidth, H = this.stage.clientHeight;
      if (!W || !H) return;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      this.W = W; this.H = H;
      this.cv.width = W * dpr; this.cv.height = H * dpr;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.cols = Math.max(64, Math.min(170, Math.floor(W / 4.6)));
      this.cw = W / this.cols; this.fs = this.cw / 0.6; this.ch = this.fs * 1.1;
      this.rows = Math.max(24, Math.floor(H / this.ch));
      const n = this.cols * this.rows;
      this.ink = new Float32Array(n); this.zb = new Float32Array(n); this.own = new Int8Array(n);
      this.draw();
    }

    pointer(e) {
      const b = this.cv.getBoundingClientRect();
      const q = Math.floor((e.clientX - b.left) / this.cw), r = Math.floor((e.clientY - b.top) / this.ch);
      let id = null;
      if (q >= 0 && r >= 0 && q < this.cols && r < this.rows) {
        const o = this.own[r * this.cols + q];
        if (o > 0 && o <= this.agents.length) id = this.agents[o - 1].id;
      }
      this.setHover(id);
    }

    loop(now) {
      requestAnimationFrame((t) => this.loop(t));
      if (!this.visible || document.hidden || now - this.last < 33) return;
      const dt = Math.min(80, now - this.prev);
      this.prev = now; this.last = now;
      this.step(dt);
    }

    step(dt) {
      this.clock += dt;
      this.coreK += (this.coreTarget() - this.coreK) * 0.06;
      this.agents.forEach((a, i) => {
        const s = this.state[a.id], o = ORBITS[i % ORBITS.length];
        s.th += dt * o[2] * (a.active ? 2.2 : 1);
        s.size += (a.share - s.size) * 0.07;
      });
      this.dust.forEach((d) => (d.th += dt * d.sp));
      this.draw();
    }

    /* ---------- rendering ---------- */
    geometry() {
      const W = this.W, H = this.H;
      const aMax = Math.max(40, Math.min(W / 2 - (W < 500 ? 12 : 30), (H / 2 - 30) / 0.5));
      const R = Math.min(H * 0.34, aMax * (0.24 + 0.16 * this.coreK));
      return { cx: W / 2, cy: H / 2, aMax, R };
    }

    orbitPoint(i, a, th) {
      const o = ORBITS[i % ORBITS.length];
      const x0 = a * Math.cos(th), z0 = a * Math.sin(th);
      const y = -z0 * Math.sin(o[0]), z = z0 * Math.cos(o[0]);
      return [x0 * Math.cos(o[1]) - y * Math.sin(o[1]), x0 * Math.sin(o[1]) + y * Math.cos(o[1]), z];
    }

    plot(px, py, z, ink, own) {
      const q = Math.floor(px / this.cw), r = Math.floor(py / this.ch);
      if (q < 0 || r < 0 || q >= this.cols || r >= this.rows) return;
      const i = r * this.cols + q;
      if (z > this.zb[i]) { this.zb[i] = z; this.ink[i] = ink; this.own[i] = own; }
    }

    sphere(cx, cy, cz, rad, own, shade) {
      const q0 = Math.max(0, Math.floor((cx - rad) / this.cw)), q1 = Math.min(this.cols - 1, Math.ceil((cx + rad) / this.cw));
      const r0 = Math.max(0, Math.floor((cy - rad) / this.ch)), r1 = Math.min(this.rows - 1, Math.ceil((cy + rad) / this.ch));
      for (let r = r0; r <= r1; r++) for (let q = q0; q <= q1; q++) {
        const nx = ((q + 0.5) * this.cw - cx) / rad, ny = ((r + 0.5) * this.ch - cy) / rad;
        const d2 = nx * nx + ny * ny;
        if (d2 > 1) continue;
        const nz = Math.sqrt(1 - d2), i = r * this.cols + q, z = cz + nz * rad;
        if (z <= this.zb[i]) continue;
        const lam = Math.max(0, nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]);
        this.zb[i] = z; this.ink[i] = Math.min(1, shade(nx, ny, nz, lam)); this.own[i] = own;
      }
    }

    draw() {
      if (!this.cols) return;
      const { cx, cy, aMax, R } = this.geometry();
      const n = this.agents.length, t = this.reduce ? 0 : this.clock;
      this.ink.fill(0); this.zb.fill(-1e9); this.own.fill(-1);

      // dust: loose particles drifting around the system
      this.dust.forEach((d) => {
        const a = d.r * aMax, x0 = a * Math.cos(d.th), z0 = a * Math.sin(d.th);
        this.plot(cx + x0, cy - z0 * Math.sin(d.incl), z0 * Math.cos(d.incl), d.ink, 0);
      });

      // orbit paths, and a short comet trail behind active agents
      const radii = this.agents.map((_, i) => aMax * (n > 1 ? 0.62 + 0.38 * i / (n - 1) : 0.8));
      this.agents.forEach((a, i) => {
        const focus = this.hover === a.id || this.sel === a.id;
        for (let k = 0; k < 200; k++) {
          const p = this.orbitPoint(i, radii[i], (k / 200) * 6.2832);
          this.plot(cx + p[0], cy + p[1], p[2], focus ? 0.3 : 0.18, i + 1);   // ":" reads clearly on light backgrounds; "." was too faint
        }
        if (a.active) {
          const th = this.state[a.id].th;
          for (let k = 1; k <= 16; k++) {
            const p = this.orbitPoint(i, radii[i], th - k * 0.022);
            this.plot(cx + p[0], cy + p[1], p[2] + 0.5, 0.55 * (1 - k / 17), i + 1);
          }
        }
      });

      // core: a slowly turning globe with a lat/long grid
      const rot = t * 0.00022, ct = Math.cos(0.42), st = Math.sin(0.42), cr = Math.cos(rot), sr = Math.sin(rot);
      this.sphere(cx, cy, 0, R, 0, (nx, ny, nz, lam) => {
        const y1 = ny * ct - nz * st, z1 = ny * st + nz * ct;
        const x2 = nx * cr + z1 * sr, z2 = -nx * sr + z1 * cr;
        const lon = Math.atan2(x2, z2), lat = Math.asin(Math.max(-1, Math.min(1, y1)));
        const grid = Math.abs(Math.sin(lon * 5)) < 0.15 || Math.abs(Math.sin(lat * 6)) < 0.13;
        return 0.18 + 0.72 * lam + (grid ? 0.32 : 0);
      });

      // agents
      this.agents.forEach((a, i) => {
        const s = this.state[a.id];
        const p = this.orbitPoint(i, radii[i], s.th);
        const pulse = a.active && !this.reduce ? 1 + 0.07 * Math.sin(t / 260) : 1;
        s.rb = Math.max(6, aMax * (0.045 + 0.1 * Math.sqrt(Math.max(0, s.size)))) * pulse;
        s.x = cx + p[0]; s.y = cy + p[1]; s.z = p[2];
        const boost = this.hover === a.id || this.sel === a.id ? 0.12 : 0;
        this.sphere(s.x, s.y, s.z, s.rb, i + 1, (nx, ny, nz, lam) => 0.3 + 0.72 * lam + boost);
      });

      this.paint(R, cx);
    }

    paint(R, cx) {
      const ctx = this.ctx, cols = this.cols, focusId = this.hover || this.sel;
      const focus = focusId ? this.agents.findIndex((a) => a.id === focusId) + 1 : 0;
      ctx.clearRect(0, 0, this.W, this.H);
      ctx.font = `${this.fs}px "Geist Mono", ui-monospace, Menlo, monospace`;
      ctx.textBaseline = "top";
      let lastStyle = "", lastAlpha = -1;
      for (let i = 0; i < this.ink.length; i++) {
        const v = this.ink[i];
        if (v < 0.04) continue;
        const o = this.own[i];
        const k = Math.min(RAMP.length - 1, 1 + Math.floor(v * (RAMP.length - 1.01)));
        const style = this.mode !== "color" ? this.colors.fg : o > 0 ? this.agentColor(o - 1) : this.colors.core;
        const alpha = !focus ? 0.92 : o === focus ? 1 : o === 0 ? 0.32 : 0.16;
        if (style !== lastStyle) { ctx.fillStyle = style; lastStyle = style; }
        if (alpha !== lastAlpha) { ctx.globalAlpha = alpha; lastAlpha = alpha; }
        ctx.fillText(RAMP[k], (i % cols) * this.cw, Math.floor(i / cols) * this.ch);
      }
      ctx.globalAlpha = 1;

      // pills ride above each agent; dimmed while passing behind the core
      this.agents.forEach((a) => {
        const s = this.state[a.id], b = this.pills && this.pills[a.id];
        if (!b) return;
        const w = b.offsetWidth, h = b.offsetHeight;
        const x = Math.max(0, Math.min(this.W - w, s.x - w / 2));
        const y = Math.max(0, s.y - s.rb - h - 6);
        b.style.transform = `translate(${x.toFixed(1)}px,${y.toFixed(1)}px)`;
        b.classList.toggle("behind", s.z < 0 && Math.abs(s.x - cx) < R);
      });
    }

    agentColor(idx) {
      const a = this.agents[idx];
      if (!a) return this.colors.fg;
      if (!a._c) a._c = getComputedStyle(document.documentElement).getPropertyValue(a.color).trim();
      return a._c;
    }
    clearColorCache() { this.agents.forEach((a) => delete a._c); this.readColors(); }
  }

  window.Orbit = Orbit;
})();
