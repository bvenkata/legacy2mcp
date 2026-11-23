/* legacy2mcp report app. Plain modern JS, no network access: everything it
   renders comes from the analysis JSON embedded in this file. */
(function () {
  "use strict";

  const A = JSON.parse(document.getElementById("analysis-data").textContent);
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // ---------------------------------------------------------------- helpers
  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else if (k === "dataset") Object.assign(node.dataset, v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");
  const short = (key) => (key || "").split("::").pop();
  const fileOf = (key) => (key || "").split("::")[0];
  const plural = (n, one, many) => `${fmt(n)} ${n === 1 ? one : many || one + "s"}`;
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  const RISK = {
    read_only: { label: "Read-only", icon: "✓", token: "--good" },
    low_risk_write: { label: "Low-risk write", icon: "!", token: "--warning" },
    high_risk: { label: "High-risk", icon: "▲", token: "--critical" },
    info: { label: "Note", icon: "i", token: "--text-muted" },
  };
  const RISK_ORDER = ["high_risk", "low_risk_write", "read_only"];
  function riskBadge(level) {
    const r = RISK[level] || RISK.info;
    return h("span", { class: `badge risk-${level}` }, h("span", { class: "ico", "aria-hidden": "true" }, r.icon), r.label);
  }
  const LANG_TOKEN = { python: "--series-1", typescript: "--series-2", javascript: "--series-3" };
  const KIND = {
    db: { label: "Database", shape: "barrel", token: "--series-1" },
    http_out: { label: "External HTTP", shape: "diamond", token: "--series-2" },
    payment: { label: "Payments", shape: "star", token: "--series-8" },
    email: { label: "Email", shape: "tag", token: "--series-7" },
    queue: { label: "Queue", shape: "round-rectangle", token: "--series-4" },
    file: { label: "Files", shape: "rectangle", token: "--series-6" },
    auth: { label: "Auth", shape: "pentagon", token: "--series-5" },
  };
  const STEP_ICON = { enter: "▶", call: "→", async: "⇢", effect: "●", seen: "↺" };

  const resources = new Map((A.graph.resources || []).map((r) => [r.id, r]));
  const functions = new Map();
  for (const m of A.modules) for (const f of m.functions) functions.set(`${m.path}::${f.qualname}`, { module: m, fn: f });
  const flowsById = new Map(A.flows.map((f) => [f.id, f]));

  const tooltip = $("#tooltip");
  function showTip(evt, html) {
    tooltip.replaceChildren(...(Array.isArray(html) ? html : [html]));
    tooltip.hidden = false;
    const x = Math.min(evt.clientX + 14, window.innerWidth - 360);
    tooltip.style.left = `${x}px`;
    tooltip.style.top = `${evt.clientY + 14}px`;
  }
  const hideTip = () => { tooltip.hidden = true; };

  function storage(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      window.localStorage.setItem(key, value);
    } catch (e) { /* private mode / blocked storage: preferences just don't persist */ }
    return null;
  }

  // ------------------------------------------------------------- sections
  const sections = [];
  function section(id, title, lede, body, opts = {}) {
    sections.push({ id, title });
    const num = sections.length;
    const details = h("details", { open: opts.collapsed ? null : true },
      h("summary", {}, h("h2", { id: `${id}-h` }, h("span", { class: "section-num" }, `${num}. `), title)),
      h("div", { class: "card-body" }, lede ? h("p", { class: "lede" }, lede) : null, body));
    return h("section", { class: "card", id, "aria-labelledby": `${id}-h` }, details);
  }

  // --------------------------------------------------------------- summary
  function recommendedSteps() {
    if (A.roadmap && A.roadmap.phases && A.roadmap.phases.length) {
      return A.roadmap.phases.slice(0, 3).map((p) => h("li", {}, h("strong", {}, `${p.name}: `), p.summary));
    }
    const out = [];
    const safeTools = A.tools.filter((t) => t.safe && t.exposable);
    if (safeTools.length) out.push(h("li", {}, h("strong", {}, "Start read-only: "),
      `expose ${plural(safeTools.length, "read-only tool")} first (`, safeTools.slice(0, 4).map((t, i) => [i ? ", " : "", h("code", {}, t.name)]),
      safeTools.length > 4 ? ", …" : "", ")."));
    const high = A.flows.filter((f) => f.risk === "high_risk");
    if (high.length) out.push(h("li", {}, h("strong", {}, "Gate the risky flows: "),
      `${plural(high.length, "flow")} move money, delete data, send externally or change credentials. Keep them out of phase 1 and add human approval before exposing any.`));
    const unauth = A.flows.filter((f) => f.authenticated === false && f.risk !== "read_only");
    if (unauth.length) out.push(h("li", {}, h("strong", {}, "Check access control: "),
      `${plural(unauth.length, "write flow")} showed no authentication middleware or check. Verify before any agent can reach them.`));
    const untyped = A.tools.filter((t) => JSON.stringify(t.input_schema).includes("not inferred"));
    if (untyped.length) out.push(h("li", {}, h("strong", {}, "Tighten schemas: "),
      `${plural(untyped.length, "proposed tool")} take a structured body whose fields weren't inferred.`));
    if (!out.length) out.push(h("li", {}, "No entry points were found. Check the excluded paths in the appendix."));
    return out;
  }

  function summarySection() {
    const s = A.summary;
    const langs = Object.entries(s.languages).map(([k, v]) => `${k} (${fmt(v.files)})`).join(", ") || "none";
    const kpis = [
      [fmt(s.files), "files analyzed"], [fmt(s.lines), "lines of code"], [fmt(s.functions), "functions"],
      [fmt(s.entry_points), "entry points"], [fmt(s.flows), "business flows"], [fmt(A.tools.length), "proposed MCP tools"],
    ];
    const total = s.flows || 0;
    const bar = h("div", { class: "riskbar", role: "img", "aria-label": RISK_ORDER.map((r) => `${RISK[r].label}: ${s.risk[r] || 0}`).join(", ") });
    for (const r of ["read_only", "low_risk_write", "high_risk"]) {
      const n = s.risk[r] || 0;
      if (!n) continue;
      const seg = h("span", { style: `flex:${n};background:var(${RISK[r].token})` });
      seg.addEventListener("mousemove", (e) => showTip(e, [h("strong", {}, RISK[r].label), h("div", {}, `${plural(n, "flow")} · ${Math.round((n / total) * 100)}%`)]));
      seg.addEventListener("mouseleave", hideTip);
      bar.append(seg);
    }
    const legend = h("ul", { class: "legend" }, ["read_only", "low_risk_write", "high_risk"].map((r) =>
      h("li", {}, h("span", { class: "swatch", style: `background:var(${RISK[r].token})` }), `${RISK[r].label} `, h("strong", {}, fmt(s.risk[r] || 0)))));
    return section("summary", "Executive summary",
      `${A.project}: ${fmt(s.files)} files in ${langs}. Frameworks detected: ${s.frameworks.join(", ") || "none"}.`,
      [
        h("div", { class: "kpis" }, kpis.map(([v, l]) => h("div", { class: "kpi" }, h("div", { class: "value" }, v), h("div", { class: "label" }, l)))),
        h("div", { class: "two-col" },
          h("div", {}, h("h3", {}, "Recommended first steps"), h("ol", { class: "steps-list" }, recommendedSteps())),
          h("div", {}, h("h3", {}, "Flow risk"), total ? [bar, legend] : h("p", { class: "empty" }, "No flows found."),
            h("h3", { style: "margin-top:16px" }, "Side effects found"),
            Object.keys(s.side_effects).length
              ? h("ul", { class: "legend" }, Object.entries(s.side_effects).map(([k, n]) => h("li", {}, h("span", { class: "tag" }, (KIND[k] || {}).label || k), h("strong", {}, fmt(n)))))
              : h("p", { class: "empty" }, "None detected."))),
      ]);
  }

  // ------------------------------------------------------------ graphs
  const graphs = [];
  function cyStyle() {
    const text = css("--text-primary"), muted = css("--text-muted"), border = css("--border");
    const surface = css("--surface-1"), surface0 = css("--surface-0");
    return [
      { selector: "node", style: {
        label: "data(label)", color: text, "font-size": 11, "font-family": css("--font") || "sans-serif",
        "text-valign": "bottom", "text-margin-y": 4, "text-wrap": "ellipsis", "text-max-width": 140,
        "border-width": 2, "border-color": surface,
        "text-background-color": surface0, "text-background-opacity": 0.75, "text-background-padding": 1 } },
      { selector: "node[color]", style: { "background-color": "data(color)" } },
      { selector: "node[size]", style: { width: "data(size)", height: "data(size)" } },
      { selector: "node[shape]", style: { shape: "data(shape)" } },
      { selector: ":parent", style: {
        "background-color": surface, "background-opacity": 0.6, "border-color": border, "border-width": 1,
        shape: "round-rectangle", label: "data(label)", "text-valign": "top", "text-halign": "center",
        color: muted, "font-size": 11, padding: 14 } },
      { selector: "edge", style: {
        width: 1.5, "line-color": border, "target-arrow-color": border, "target-arrow-shape": "triangle",
        "arrow-scale": 0.8, "curve-style": "bezier", opacity: 0.9 } },
      { selector: "edge[kind = 'async']", style: { "line-style": "dashed" } },
      { selector: "edge[op = 'write']", style: { "line-color": css("--critical"), "target-arrow-color": css("--critical"), width: 2 } },
      { selector: "edge[op = 'unknown']", style: { "line-color": css("--warning"), "target-arrow-color": css("--warning") } },
      { selector: "edge[label]", style: { label: "data(label)", "font-size": 9, color: muted, "text-rotation": "autorotate",
        "text-background-color": surface0, "text-background-opacity": 1, "text-background-padding": 1 } },
      { selector: ".dim", style: { opacity: 0.15 } },
      { selector: ".hit", style: { "border-color": css("--accent"), "border-width": 3, "z-index": 10 } },
      { selector: ":selected", style: { "border-color": css("--accent"), "border-width": 4 } },
      { selector: "node.entry", style: { "border-color": text, "border-width": 3 } },
    ];
  }
  function makeGraph(container, elements, layout) {
    if (typeof cytoscape !== "function") {
      container.append(h("div", { class: "graph-empty" }, "Graph library failed to load."));
      return null;
    }
    const cy = cytoscape({
      container, elements, style: cyStyle(), layout: { name: "preset" }, minZoom: 0.1, maxZoom: 3,
      boxSelectionEnabled: false,
    });
    // Run the real layout after mount, then fit -- without capping, a tiny graph
    // would zoom to fill the box and a big one would overflow it.
    const run = cy.layout(layout);
    run.one("layoutstop", () => { cy.fit(undefined, 30); if (cy.zoom() > 1.6) { cy.zoom(1.6); cy.center(); } });
    run.run();
    cy.on("mouseover", "node", (e) => {
      const d = e.target.data();
      if (d.tip) showTip(e.originalEvent, [h("strong", {}, d.tipTitle || d.label), h("div", {}, d.tip)]);
    });
    cy.on("mouseout", "node", hideTip);
    graphs.push(cy);
    return cy;
  }
  function graphControls(getCy) {
    return h("div", { class: "graph-controls no-print" },
      h("button", { class: "btn", type: "button", "aria-label": "Zoom in", onclick: () => { const cy = getCy(); cy && cy.zoom({ level: cy.zoom() * 1.25, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } }); } }, "+"),
      h("button", { class: "btn", type: "button", "aria-label": "Zoom out", onclick: () => { const cy = getCy(); cy && cy.zoom({ level: cy.zoom() / 1.25, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } }); } }, "−"),
      h("button", { class: "btn", type: "button", onclick: () => { const cy = getCy(); cy && cy.fit(undefined, 30); } }, "Fit"));
  }

  // --------------------------------------------------------- codebase map
  const AGGREGATE_THRESHOLD = 300;
  let mapCy = null;
  function dirOf(path, depth) { const parts = path.split("/"); return parts.length > 1 ? parts.slice(0, Math.min(depth, parts.length - 1)).join("/") : "(root)"; }

  function mapElements(langFilter) {
    const mods = A.modules.filter((m) => langFilter.has(m.language));
    const aggregate = mods.length > AGGREGATE_THRESHOLD;
    const els = [];
    const entryFiles = new Set(A.entry_points.map((e) => fileOf(e.handler)).concat(A.entry_points.map((e) => e.file)));
    if (aggregate) {
      const groups = new Map();
      for (const m of mods) {
        const d = dirOf(m.path, 2);
        const g = groups.get(d) || { lines: 0, files: 0, lang: {} };
        g.lines += m.lines; g.files += 1; g.lang[m.language] = (g.lang[m.language] || 0) + 1;
        groups.set(d, g);
      }
      for (const [d, g] of groups) {
        const lang = Object.entries(g.lang).sort((a, b) => b[1] - a[1])[0][0];
        els.push({ data: { id: `dir:${d}`, label: d, color: css(LANG_TOKEN[lang] || "--series-7"), size: 14 + Math.sqrt(g.lines) / 2,
          tip: `${plural(g.files, "file")}, ${fmt(g.lines)} lines`, tipTitle: d, dir: d } });
      }
      const weights = new Map();
      const keep = new Set(mods.map((m) => m.path));
      for (const e of A.graph.module_edges) {
        if (!keep.has(e.source) || !keep.has(e.target)) continue;
        const a = dirOf(e.source, 2), b = dirOf(e.target, 2);
        if (a === b) continue;
        weights.set(`${a}\u0000${b}`, (weights.get(`${a}\u0000${b}`) || 0) + 1);
      }
      for (const [k, w] of weights) {
        const [a, b] = k.split("\u0000");
        els.push({ data: { id: `e:${k}`, source: `dir:${a}`, target: `dir:${b}`, label: w > 1 ? String(w) : undefined } });
      }
      return { els, aggregate };
    }
    const dirs = new Set();
    for (const m of mods) {
      const d = dirOf(m.path, 1);
      if (d !== "(root)") dirs.add(d);
    }
    for (const d of dirs) els.push({ data: { id: `dir:${d}`, label: d } });
    const keep = new Set(mods.map((m) => m.path));
    const baseCount = {};
    for (const m of mods) { const b = m.path.split("/").pop(); baseCount[b] = (baseCount[b] || 0) + 1; }
    const labelOf = (path) => { const parts = path.split("/"); return baseCount[parts[parts.length - 1]] > 1 ? parts.slice(-2).join("/") : parts[parts.length - 1]; };
    for (const m of mods) {
      const d = dirOf(m.path, 1);
      const effects = m.functions.reduce((n, f) => n + f.side_effects.length, 0);
      els.push({
        data: {
          id: m.path, label: labelOf(m.path), parent: d !== "(root)" ? `dir:${d}` : undefined,
          color: css(LANG_TOKEN[m.language] || "--series-7"), size: 12 + Math.min(40, Math.sqrt(m.lines) * 1.6),
          tipTitle: m.path, tip: `${m.language} · ${fmt(m.lines)} lines · ${plural(m.functions.length, "function")} · ${plural(effects, "side effect")}`,
        },
        classes: entryFiles.has(m.path) ? "entry" : "",
      });
    }
    for (const e of A.graph.module_edges) {
      if (keep.has(e.source) && keep.has(e.target)) els.push({ data: { id: `e:${e.source}->${e.target}`, source: e.source, target: e.target } });
    }
    return { els, aggregate };
  }

  function modulePanel(panel, path) {
    const m = A.modules.find((x) => x.path === path);
    if (!m) return;
    const eps = A.entry_points.filter((e) => fileOf(e.handler) === path || e.file === path);
    panel.replaceChildren(
      h("h3", {}, m.path),
      h("p", { class: "secondary" }, `${m.language} · ${fmt(m.lines)} lines`),
      m.parse_error ? h("p", { class: "callout" }, `Parse issue: ${m.parse_error}`) : null,
      eps.length ? [h("strong", {}, "Entry points"), h("ul", {}, eps.map((e) => h("li", {}, h("code", {}, e.name))))] : null,
      h("strong", {}, `Functions (${m.functions.length})`),
      m.functions.length ? h("ul", {}, m.functions.map((f) => h("li", {},
        h("button", { class: "fn-btn", type: "button", onclick: () => showFunctionGraph(`${m.path}::${f.qualname}`) }, f.qualname),
        f.side_effects.length ? [" ", ...uniq(f.side_effects.map((s) => s.kind)).map((k) => h("span", { class: "tag", style: "margin-left:4px" }, (KIND[k] || {}).label || k))] : null)))
        : h("p", { class: "empty" }, "No functions."));
  }
  const uniq = (xs) => Array.from(new Set(xs));

  function mapSection() {
    const langs = Object.keys(A.summary.languages);
    const filter = new Set(langs);
    const container = h("div", { class: "graph" });
    const panel = h("div", { class: "panel" }, h("p", { class: "empty" }, "Click a module to see its functions, entry points and side effects. Click a function to open its call graph below."));
    const note = h("p", { class: "secondary", style: "font-size:13px;margin:8px 0 0" });
    function draw() {
      if (mapCy) { mapCy.destroy(); graphs.splice(graphs.indexOf(mapCy), 1); mapCy = null; }
      const { els, aggregate } = mapElements(filter);
      note.textContent = aggregate
        ? `Large codebase: showing ${A.modules.length} modules grouped by directory. Node size = lines of code; edge label = number of imports.`
        : "Node = module (size by lines, color by language, outlined if it holds an entry point). Box = top-level directory. Arrow = import.";
      if (!els.length) return;
      mapCy = makeGraph(container, els, { name: "cose", animate: false, nodeRepulsion: 9000, idealEdgeLength: 80, padding: 30, randomize: false });
      if (!mapCy) return;
      mapCy.on("tap", "node", (e) => {
        const d = e.target.data();
        if (d.dir) return;
        if (!e.target.isParent()) modulePanel(panel, d.id);
      });
      applySearchToMap();
    }
    const toolbar = h("div", { class: "toolbar no-print" },
      h("span", { class: "secondary" }, "Languages:"),
      langs.map((l) => {
        const chip = h("button", { class: "chip", type: "button", "aria-pressed": "true" },
          h("span", { class: "swatch", style: `background:var(${LANG_TOKEN[l] || "--series-7"})` }), l);
        chip.addEventListener("click", () => {
          const on = chip.getAttribute("aria-pressed") === "true";
          if (on && filter.size === 1) return;
          chip.setAttribute("aria-pressed", String(!on));
          on ? filter.delete(l) : filter.add(l);
          draw();
        });
        return chip;
      }));
    const el = section("map", "Codebase map",
      "Every analyzed module and the imports between them. Drag to pan, scroll to zoom, use the search box to highlight matches.",
      [toolbar, h("div", { class: "map-layout" }, h("div", { class: "graph-wrap" }, container, graphControls(() => mapCy)), panel), note]);
    requestAnimationFrame(draw);
    return el;
  }

  // ------------------------------------------------- call / data-flow view
  let focusCy = null;
  const focusContainer = h("div", { class: "graph small" });
  const focusTitle = h("p", { class: "lede" });
  const focusEmpty = h("div", { class: "graph-empty" }, "Pick a flow (“Show graph”) or a function in the codebase map.");

  function fnNode(key, extra = {}) {
    const entry = functions.get(key);
    const effects = entry ? entry.fn.side_effects.length : 0;
    return { data: { id: key, label: short(key), color: css("--accent"), size: 22, shape: "ellipse",
      tipTitle: key, tip: entry ? `${fmt(entry.fn.end_line - entry.fn.line + 1)} lines · ${plural(effects, "side effect")}` : "outside the analyzed code", ...extra } };
  }
  function resNode(id) {
    const r = resources.get(id) || { kind: "db", label: id };
    const k = KIND[r.kind] || KIND.db;
    return { data: { id: `res:${id}`, label: r.label, color: css(k.token), size: 26, shape: k.shape, tipTitle: k.label, tip: r.label } };
  }
  function renderFocus(els, root, title) {
    focusEmpty.hidden = true;
    focusTitle.replaceChildren(...title);
    if (focusCy) { focusCy.destroy(); graphs.splice(graphs.indexOf(focusCy), 1); }
    focusCy = makeGraph(focusContainer, els, { name: "breadthfirst", directed: true, roots: root ? [root] : undefined, spacingFactor: 1.1, padding: 30, animate: false });
    if (focusCy) focusCy.on("tap", "node", (e) => { const id = e.target.id(); if (functions.has(id)) showFunctionGraph(id); });
    document.getElementById("focus").scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function showFlowGraph(flowId) {
    const flow = flowsById.get(flowId);
    if (!flow) return;
    const fnSet = new Set(flow.functions);
    const els = flow.functions.map((k) => fnNode(k, k === flow.handler ? { size: 30 } : {}));
    if (!fnSet.has(flow.handler)) els.push(fnNode(flow.handler, { size: 30 }));
    for (const e of A.graph.call_edges) if (fnSet.has(e.source) && fnSet.has(e.target)) els.push({ data: { id: `c:${e.source}->${e.target}`, source: e.source, target: e.target, kind: e.kind } });
    const resSet = new Set();
    for (const d of A.graph.data_edges) {
      if (!fnSet.has(d.function)) continue;
      if (!resSet.has(d.resource)) { resSet.add(d.resource); els.push(resNode(d.resource)); }
      els.push({ data: { id: `d:${d.function}->${d.resource}:${d.op}`, source: d.function, target: `res:${d.resource}`, op: d.op, label: d.op } });
    }
    renderFocus(els, flow.handler, [h("strong", {}, flow.name), " — ", h("code", {}, flow.entry_name), " ", riskBadge(flow.risk)]);
  }
  function showFunctionGraph(key) {
    const els = [fnNode(key, { size: 30 })];
    const seen = new Set([key]);
    for (const e of A.graph.call_edges) {
      if (e.source === key || e.target === key) {
        const other = e.source === key ? e.target : e.source;
        if (!seen.has(other)) { seen.add(other); els.push(fnNode(other)); }
        els.push({ data: { id: `c:${e.source}->${e.target}`, source: e.source, target: e.target, kind: e.kind } });
      }
    }
    for (const d of A.graph.data_edges) {
      if (d.function !== key) continue;
      if (!seen.has(`res:${d.resource}`)) { seen.add(`res:${d.resource}`); els.push(resNode(d.resource)); }
      els.push({ data: { id: `d:${d.resource}:${d.op}`, source: key, target: `res:${d.resource}`, op: d.op, label: d.op } });
    }
    const callers = A.graph.call_edges.filter((e) => e.target === key).map((e) => e.source);
    renderFocus(els, callers[0] || key, [h("strong", {}, short(key)), " in ", h("code", {}, fileOf(key)), ` — ${plural(callers.length, "caller")}, direct callees and data touched.`]);
  }
  function focusSection() {
    const legend = h("ul", { class: "legend", style: "margin-top:10px" },
      h("li", {}, h("span", { class: "swatch", style: "background:var(--accent);border-radius:50%" }), "function"),
      Object.entries(KIND).map(([, k]) => h("li", {}, h("span", { class: "swatch", style: `background:var(${k.token})` }), k.label)),
      h("li", {}, h("span", { class: "swatch", style: "background:var(--critical);height:3px" }), "write"),
      h("li", {}, "dashed = background (queued)"));
    return section("focus", "Call graph and data flow",
      "Who calls whom inside the selected flow or function, and which data stores, services and side channels each one touches.",
      [focusTitle, h("div", { class: "graph-wrap" }, focusContainer, focusEmpty, graphControls(() => focusCy)), legend]);
  }

  // ------------------------------------------------------------- flows
  const riskFilter = new Set(RISK_ORDER);
  let query = "";
  function flowCard(flow) {
    const auth = flow.authenticated === true ? h("span", { class: "tag", title: "Authentication middleware or check detected" }, "🔒 auth")
      : flow.authenticated === false ? h("span", { class: "tag", title: "No authentication detected" }, "no auth seen") : null;
    const steps = h("ol", { class: "steps" }, flow.steps.map((s) => h("li", { class: `k-${s.kind} r-${s.risk}`, style: `margin-left:${s.depth * 18}px` },
      h("span", { class: "ico", "aria-hidden": "true" }, STEP_ICON[s.kind] || "·"),
      h("span", {}, s.label),
      h("span", { class: "loc" }, `${fileOf(s.function).split("/").pop()}:${s.line}`))));
    const reasons = flow.reasons.length
      ? h("ul", { class: "reasons" }, flow.reasons.map((r) => h("li", {}, riskBadge(r.level), h("span", {}, r.text, r.where ? [" ", h("span", { class: "loc" }, r.where)] : null))))
      : h("p", { class: "secondary" }, "No writes or external effects detected.");
    const resList = flow.resources.length
      ? h("ul", { class: "legend", style: "flex-direction:column;gap:4px" }, flow.resources.map((r) => {
        const res = resources.get(r.resource) || { kind: "", label: r.resource };
        return h("li", {}, h("span", { class: "tag" }, (KIND[res.kind] || {}).label || res.kind), res.label, h("span", { class: "muted" }, ` (${r.op})`));
      }))
      : h("p", { class: "empty" }, "None.");
    const card = h("details", { class: "flow", dataset: { risk: flow.risk, id: flow.id } },
      h("summary", {},
        h("span", {}, riskBadge(flow.risk)),
        h("span", {}, h("div", { class: "title" }, flow.name), h("div", { class: "sub" }, h("code", {}, flow.entry_name), ` · ${flow.framework} · ${short(flow.handler)}`)),
        h("span", { class: "right" }, auth, h("span", { class: "tag" }, plural(flow.steps.length, "step")))),
      h("div", { class: "flow-body" },
        h("div", {}, h("h3", {}, "Steps"), flow.steps.length ? steps : h("p", { class: "empty" }, "Handler not resolved in this repo."),
          flow.truncated ? h("p", { class: "callout" }, "Flow truncated at the depth/step limit.") : null),
        h("div", {},
          h("h3", {}, "Why this risk level"), reasons,
          h("h3", {}, "Data touched"), resList,
          flow.middleware.length ? [h("h3", { style: "margin-top:12px" }, "Middleware / decorators"), h("p", {}, flow.middleware.map((m, i) => [i ? ", " : "", h("code", {}, m)]))] : null,
          h("p", { style: "margin-top:12px" }, h("button", { class: "btn no-print", type: "button", onclick: () => showFlowGraph(flow.id) }, "Show graph")))));
    card._search = [flow.name, flow.entry_name, flow.handler, flow.framework, flow.description, ...flow.steps.map((s) => s.label)].join(" ").toLowerCase();
    return card;
  }
  function riskChips(onChange) {
    return RISK_ORDER.map((r) => {
      const chip = h("button", { class: "chip", type: "button", "aria-pressed": "true" }, h("span", { class: "swatch", style: `background:var(${RISK[r].token})` }), RISK[r].label);
      chip.addEventListener("click", () => {
        const on = chip.getAttribute("aria-pressed") === "true";
        chip.setAttribute("aria-pressed", String(!on));
        on ? riskFilter.delete(r) : riskFilter.add(r);
        $$(`.chip[data-risk="${r}"]`).forEach((c) => c !== chip && c.setAttribute("aria-pressed", String(!on)));
        onChange();
      });
      chip.dataset.risk = r;
      return chip;
    });
  }
  const flowCount = h("span", { class: "secondary" });
  function filterFlows() {
    let shown = 0;
    for (const card of $$("#flows .flow")) {
      const ok = riskFilter.has(card.dataset.risk) && (!query || card._search.includes(query));
      card.classList.toggle("hidden", !ok);
      if (ok) shown += 1;
    }
    flowCount.textContent = `${shown} of ${A.flows.length} flows`;
  }
  function flowsSection() {
    const sorted = [...A.flows].sort((a, b) => RISK_ORDER.indexOf(a.risk) - RISK_ORDER.indexOf(b.risk) || a.entry_name.localeCompare(b.entry_name));
    const list = h("div", {}, sorted.length ? sorted.map(flowCard) : h("p", { class: "empty" }, "No entry points found, so no flows."));
    const toolbar = h("div", { class: "toolbar no-print" }, h("span", { class: "secondary" }, "Risk:"), riskChips(() => { filterFlows(); filterTools(); }),
      h("span", { class: "spacer" }), flowCount,
      h("button", { class: "btn", type: "button", onclick: () => $$("#flows .flow:not(.hidden)").forEach((d) => { d.open = true; }) }, "Expand all"),
      h("button", { class: "btn", type: "button", onclick: () => $$("#flows .flow").forEach((d) => { d.open = false; }) }, "Collapse all"));
    return section("flows", "Business flows",
      "One flow per entry point: what runs, in order, when it fires, including background work it queues. The risk label comes from the strongest side effect in the flow; every reason links to its source line.",
      [toolbar, list]);
  }

  // ------------------------------------------------------------- tools
  const selection = new Map(A.tools.map((t) => [t.name, !!t.include]));
  const toolCount = h("span", { class: "secondary" });
  function updateToolCount() {
    const n = Array.from(selection.values()).filter(Boolean).length;
    toolCount.textContent = `${n} of ${A.tools.length} selected`;
  }
  function filterTools() {
    for (const row of $$("#tools tbody tr")) {
      const ok = riskFilter.has(row.dataset.risk) && (!query || row._search.includes(query));
      row.classList.toggle("hidden", !ok);
    }
  }
  function exportSelections() {
    const payload = {
      schema: "legacy2mcp-selections/1",
      project: A.project,
      analysis_generated_at: (A.meta || {}).generated_at || null,
      exported_at: new Date().toISOString(),
      tools: A.tools.map((t) => ({ name: t.name, flow_id: t.flow_id, include: !!selection.get(t.name) })),
    };
    const blob = new Blob([JSON.stringify(payload, null, 2) + "\n"], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = h("a", { href: url, download: "selections.json" });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function toolsSection() {
    const rows = A.tools.map((t) => {
      const box = h("input", { type: "checkbox", checked: selection.get(t.name) ? true : null, disabled: t.exposable ? null : true,
        "aria-label": `Include ${t.name}` });
      box.addEventListener("change", () => { selection.set(t.name, box.checked); updateToolCount(); });
      const flow = flowsById.get(t.flow_id);
      const row = h("tr", { dataset: { risk: t.risk } },
        h("td", {}, h("label", { class: "toggle" }, box, h("span", { class: "sr-only" }, "include"))),
        h("td", {}, h("code", {}, t.name), h("div", { class: "secondary", style: "font-size:12px;margin-top:2px" }, `${t.method || ""} ${t.path || ""}`)),
        h("td", {}, t.description,
          t.notes.length ? h("ul", { style: "margin:4px 0 0;padding-left:18px;font-size:13px", class: "secondary" }, t.notes.map((n) => h("li", {}, n))) : null,
          h("details", {}, h("summary", { class: "secondary", style: "cursor:pointer;font-size:13px;margin-top:4px" }, "Input schema"),
            h("pre", {}, JSON.stringify(t.input_schema, null, 2)))),
        h("td", {}, riskBadge(t.risk), h("div", { style: "margin-top:4px" }, h("span", { class: "tag" }, t.safe ? "safe" : "risky"))),
        h("td", {}, flow ? h("button", { class: "fn-btn", type: "button", onclick: () => { const c = $(`#flows .flow[data-id="${CSS.escape(flow.id)}"]`); if (c) { c.open = true; c.scrollIntoView({ behavior: "smooth", block: "center" }); } } }, "view flow") : null));
      row._search = [t.name, t.description, t.path, t.method].join(" ").toLowerCase();
      return row;
    });
    const toolbar = h("div", { class: "toolbar no-print" },
      h("button", { class: "btn", type: "button", onclick: () => setAll((t) => t.safe && t.exposable) }, "Select read-only"),
      h("button", { class: "btn", type: "button", onclick: () => setAll(() => false) }, "Clear"),
      h("span", { class: "spacer" }), toolCount,
      h("button", { class: "btn primary", type: "button", onclick: exportSelections }, "Export selections.json"));
    function setAll(pred) {
      A.tools.forEach((t, i) => { const on = t.exposable && pred(t); selection.set(t.name, on); const box = rows[i].querySelector("input"); box.checked = on; });
      updateToolCount();
    }
    updateToolCount();
    return section("tools", "Proposed MCP tools",
      "One tool per HTTP flow. Read-only tools start selected; writes start excluded and need an explicit opt-in. Export your choices and pass them to `legacy2mcp generate --selections selections.json`.",
      A.tools.length ? [toolbar, h("div", { class: "table-wrap" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Include"), h("th", {}, "Tool"), h("th", {}, "Description"), h("th", {}, "Risk"), h("th", {}, ""))),
        h("tbody", {}, rows)))] : h("p", { class: "empty" }, "No HTTP flows, so no tools to propose."));
  }

  // ------------------------------------------- orchestration sections (M6)
  const extraSections = [];
  window.__legacy2mcpRegisterSection = (fn) => extraSections.push(fn);

  // ------------------------------------------------------------ appendix
  function appendixSection() {
    const scan = A.scan;
    const meta = A.meta || {};
    const ai = meta.ai_usage;
    const reasons = { default_excluded_dir: "Built-in non-code directory (node_modules, venv, build, …)", gitignore: ".gitignore",
      legacy2mcpignore: ".legacy2mcpignore", exclude_flag: "--exclude pattern", tests: "Tests (use --include-tests)",
      migrations: "Migrations", too_large: "Larger than 1 MB", symlink: "Symbolic link", type_declarations: "TypeScript .d.ts declarations" };
    return section("appendix", "Appendix: methodology and scope",
      null,
      [
        h("h3", {}, "How this was produced"),
        h("p", { class: "lede" },
          "legacy2mcp parsed the source statically on the machine that ran it: Python with the standard-library ast module, JavaScript and TypeScript with tree-sitter. ",
          "Nothing in the repo was imported, executed or installed. Entry points come from framework conventions (route decorators, URLconfs, router calls, file-based routes, job and queue registrations). ",
          "Flows follow resolved calls depth-first in source order. Risk labels are rules over the side effects found: payments, deletes, external sends and credential changes are high-risk. ",
          meta.ai ? "AI enrichment added names, descriptions and suggestions on top of these rules; it never lowered a rule-assigned risk level." : "No AI was used for this report."),
        h("p", { class: "callout" }, "Static analysis is a best effort. Calls through dynamic dispatch, reflection or variables whose type isn't visible locally aren't followed, so treat “read-only” as “no write found” and review before exposing anything."),
        h("h3", {}, "Scope"),
        h("dl", { class: "kv" },
          h("dt", {}, "Files analyzed"), h("dd", {}, fmt(scan.included_files)),
          h("dt", {}, "Ignore files honored"), h("dd", {}, scan.ignore_files.length ? scan.ignore_files.map((f, i) => [i ? ", " : "", h("code", {}, f)]) : "none found"),
          h("dt", {}, "--exclude patterns"), h("dd", {}, scan.exclude_patterns.length ? scan.exclude_patterns.map((f, i) => [i ? ", " : "", h("code", {}, f)]) : "none"),
          h("dt", {}, "Internal call edges"), h("dd", {}, `${fmt(A.graph.stats.internal_calls || 0)} resolved, ${fmt(A.graph.stats.unresolved_calls || 0)} calls to code outside the repo or not resolvable`),
          h("dt", {}, "External packages"), h("dd", {}, (A.graph.stats.external_packages || []).join(", ") || "none")),
        Object.keys(scan.excluded).length ? h("div", { class: "table-wrap", style: "margin-top:12px" }, h("table", { class: "data" },
          h("thead", {}, h("tr", {}, h("th", {}, "Excluded because"), h("th", { style: "text-align:right" }, "Files / directories"))),
          h("tbody", {}, Object.entries(scan.excluded).map(([k, v]) => h("tr", {}, h("td", {}, reasons[k] || k), h("td", { class: "num" }, fmt(v))))))) : null,
        scan.notes.length ? h("ul", {}, scan.notes.map((n) => h("li", {}, n))) : null,
        h("h3", { style: "margin-top:16px" }, "AI usage"),
        ai ? h("dl", { class: "kv" },
          h("dt", {}, "Provider / model"), h("dd", {}, `${ai.provider} / ${ai.model}`),
          h("dt", {}, "Requests"), h("dd", {}, fmt(ai.requests)),
          h("dt", {}, "Tokens"), h("dd", {}, `${fmt(ai.input_tokens)} in, ${fmt(ai.output_tokens)} out (budget ${ai.budget ? fmt(ai.budget) : "none"})`),
          h("dt", {}, "Redactions"), h("dd", {}, `${fmt(ai.redactions || 0)} secret-looking values removed before sending`))
          : h("p", { class: "secondary" }, "None: offline analysis only (0 tokens)."),
        h("h3", { style: "margin-top:16px" }, "Run"),
        h("dl", { class: "kv" },
          h("dt", {}, "legacy2mcp"), h("dd", {}, meta.legacy2mcp_version || "unknown"),
          h("dt", {}, "Generated"), h("dd", {}, meta.generated_at || "unknown"),
          h("dt", {}, "Analysis schema"), h("dd", {}, A.schema_version)),
      ], { collapsed: true });
  }

  // ------------------------------------------------------------- search
  function applySearchToMap() {
    if (!mapCy) return;
    mapCy.elements().removeClass("dim hit");
    if (!query) return;
    const hits = mapCy.nodes().filter((n) => !n.isParent() && (n.id().toLowerCase().includes(query) || String(n.data("label")).toLowerCase().includes(query)
      || (A.modules.find((m) => m.path === n.id()) || { functions: [] }).functions.some((f) => f.qualname.toLowerCase().includes(query))));
    mapCy.elements().not(":parent").addClass("dim");
    hits.removeClass("dim").addClass("hit");
  }

  // ------------------------------------------------------------- theme / print
  function initTheme() {
    const btn = $("#theme-toggle");
    const saved = storage("legacy2mcp-theme");
    if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
    const current = () => document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const label = () => { btn.textContent = current() === "dark" ? "Light theme" : "Dark theme"; };
    label();
    btn.addEventListener("click", () => {
      const next = current() === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      storage("legacy2mcp-theme", next);
      label();
      restyle();
    });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { label(); restyle(); });
  }
  function restyle() {
    for (const cy of graphs) {
      cy.style(cyStyle());
      cy.nodes().forEach((n) => {
        const d = n.data();
        if (d.dir || n.isParent()) return;
        if (d.id.startsWith("res:")) { const r = resources.get(d.id.slice(4)); if (r && KIND[r.kind]) n.data("color", css(KIND[r.kind].token)); }
        else if (functions.has(d.id)) n.data("color", css("--accent"));
        else { const m = A.modules.find((x) => x.path === d.id); if (m) n.data("color", css(LANG_TOKEN[m.language] || "--series-7")); }
      });
    }
  }
  function initPrint() {
    let opened = [];
    $("#print").addEventListener("click", () => window.print());
    window.addEventListener("beforeprint", () => {
      opened = $$("details:not([open])");
      opened.forEach((d) => { d.open = true; });
      graphs.forEach((cy) => { cy.resize(); cy.fit(undefined, 20); });
    });
    window.addEventListener("afterprint", () => { opened.forEach((d) => { d.open = false; }); opened = []; });
  }

  // ------------------------------------------------------------- boot
  function boot() {
    document.title = `${A.project} — legacy2mcp report`;
    const app = $("#app");
    const head = h("header", { class: "report-head" },
      h("h1", {}, A.project),
      h("p", { class: "meta" }, `Codebase analysis · ${A.meta && A.meta.generated_at ? new Date(A.meta.generated_at).toLocaleString() : ""} · legacy2mcp ${(A.meta || {}).legacy2mcp_version || ""}${(A.meta || {}).ai ? " · AI-assisted" : " · offline"}`));
    const parts = [summarySection(), mapSection(), focusSection(), flowsSection(), toolsSection()];
    for (const fn of extraSections) { const s = fn({ h, section, makeGraph, graphControls, css, riskBadge, plural, fmt, KIND, RISK }); if (s) parts.push(s); }
    parts.push(appendixSection());
    app.replaceChildren(head, ...parts);
    $("#toc").replaceChildren(...sections.map((s) => h("li", {}, h("a", { href: `#${s.id}` }, s.title))));
    filterFlows();
    filterTools();

    let timer = null;
    $("#search").addEventListener("input", (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => { query = e.target.value.trim().toLowerCase(); filterFlows(); filterTools(); applySearchToMap(); }, 120);
    });
    const links = $$("#toc a");
    const obs = new IntersectionObserver((entries) => {
      for (const en of entries) if (en.isIntersecting) links.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === `#${en.target.id}`));
    }, { rootMargin: "-40% 0px -55% 0px" });
    $$("section.card").forEach((s) => obs.observe(s));
    initTheme();
    initPrint();
    // Deep link: #flow=<id> opens that flow and draws its graph.
    const m = /^#flow=(.+)$/.exec(location.hash);
    if (m) {
      const id = decodeURIComponent(m[1]);
      const card = $(`#flows .flow[data-id="${CSS.escape(id)}"]`);
      if (card) { card.open = true; requestAnimationFrame(() => showFlowGraph(id)); }
    }
    window.__legacy2mcpReady = true;
  }
  boot();
})();
