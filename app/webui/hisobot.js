/* Veb-ilova: admin paneli — O'quvchilar natijasi (botdagi bilan bir xil raqamlar). Har ekranda orqaga tugmasi bor. */
(function () {
  "use strict";
  const OQ = window.OQ;
  if (!OQ || !OQ.u || OQ.adminReports) return;
  const { screen, empty, wait, stat } = OQ.u;
  const fail = (e, back) => { toast(e.message); if (back) back(); };
  const btn = (cls, label, fn, ic) => el("button", { class: "btn " + cls, onclick: fn }, ic ? icon(ic) : null, label);
  const SORTS = [["faollik", "🕓 Faollik"], ["natija", "📊 Natija"], ["ism", "🔤 Ism"]];
  const R = { q: "", sort: "faollik", page: 0, back: null };
  const bar = (p) => { const n = Math.max(0, Math.min(8, Math.round((p || 0) / 12.5))); return "█".repeat(n) + "░".repeat(8 - n); };
  const toAdmin = () => (R.back ? R.back() : OQ.admin && OQ.admin());

  function qimg(id) {
    const img = el("img", { class: "q", alt: "" });
    blobUrl(`/api/r/qimg/${id}`).then((u) => (img.src = u)).catch(() => img.replaceWith(el("div", { class: "hint" }, "Rasm topilmadi.")));
    return img;
  }
  const pctTxt = (p) => (p == null ? "—" : p + "%");

  /* ---------- 1: ro'yxat ---------- */
  async function list(reset, back) {
    if (typeof back === "function") R.back = back;
    if (reset) Object.assign(R, { q: "", sort: "faollik", page: 0 });
    wait();
    let d;
    try { d = await api(`/api/r/list?page=${R.page}&sort=${R.sort}&q=${encodeURIComponent(R.q)}`); } catch (e) { return fail(e, toAdmin); }
    R.page = d.page;
    const nodes = [];
    nodes.push(el("div", { class: "card" },
      stat("👥 Jami o'quvchi", d.total), stat("🔥 Shu hafta faol", d.week), stat("😴 Hech test ishlamagan", d.never)));
    nodes.push(el("div", { class: "row", style: "gap:6px;margin:8px 0" }, ...SORTS.map(([k, t]) =>
      btn("small grow" + (R.sort === k ? "" : " alt"), t, () => { R.sort = k; R.page = 0; list(); }))));
    const inp = el("input", { class: "ans", type: "text", maxlength: "50", placeholder: "Ism, guruh yoki ID", value: R.q, autocomplete: "off", style: "margin:0" });
    const go = () => { R.q = inp.value.trim(); R.page = 0; list(); };
    inp.addEventListener("keydown", (ev) => { if (ev.key === "Enter") go(); });
    nodes.push(el("div", { class: "row", style: "gap:6px;margin-bottom:8px" }, inp, btn("small sq", "🔍", go)));
    if (R.q) nodes.push(btn("alt small", "✖ Qidiruvni tozalash", () => { R.q = ""; R.page = 0; list(); }));
    nodes.push(el("div", { class: "hint", style: "margin:8px 0" }, `Topildi: ${d.found} · Sahifa ${d.page + 1}/${d.pages}`));
    if (!d.items.length) nodes.push(empty(d.total ? "Hech narsa topilmadi." : "Tasdiqlangan o'quvchilar yo'q."));
    for (const x of d.items) nodes.push(el("div", { class: "card tap", onclick: () => card(x.tg) },
      el("div", { class: "row" }, el("div", { class: "grow" }, el("b", {}, x.name),
        el("div", { class: "hint" }, [x.group, x.attempts ? `${x.attempts} test · ${pctTxt(x.pct)} · ${x.when}` : "test ishlamagan"].filter(Boolean).join(" · "))), icon("chev"))));
    if (d.pages > 1) nodes.push(el("div", { class: "row", style: "margin-top:8px" },
      d.page > 0 ? btn("alt small grow", "⬅️ Oldingi", () => { R.page = d.page - 1; list(); }) : null,
      d.page + 1 < d.pages ? btn("alt small grow", "Keyingi ➡️", () => { R.page = d.page + 1; list(); }) : null));
    screen("O'quvchilar natijasi", "O'quvchini tanlang:", toAdmin, ...nodes);
  }

  /* ---------- 2: o'quvchi kartasi ---------- */
  function trendNode(t) {
    if (!t) return el("div", { class: "hint" }, "📈 O'sish: hali aniqlab bo'lmaydi (test kam ishlangan)");
    const dl = t.delta;
    const [ic, w] = dl > 2 ? ["📈", "O'sish"] : dl < -2 ? ["📉", "Pasayish"] : ["➡️", "O'zgarishsiz"];
    const sign = dl > 2 ? "+" + dl : dl < -2 ? String(dl) : (dl >= 0 ? "+" : "") + dl;
    const det = t.mode === "oxirgi" ? `oxirgi ${t.window} ta test ${t.recent}%, undan oldingi ${t.window} tasi ${t.earlier}%`
      : `oxirgi yarmi ${t.recent}%, birinchi yarmi ${t.earlier}%`;
    return el("div", { class: "hint" }, `${ic} ${w}: ${det} → ${sign}`);
  }
  /* kartaning ichki qismi: admin ham, ustoz paneli ham ishlatadi (opts.attempts: testlarni ochish mumkinmi) */
  function cardBody(c, opts) {
    opts = opts || {};
    const nodes = [el("div", { class: "card" }, el("b", {}, c.name), c.group ? el("div", { class: "hint" }, "👥 Guruh: " + c.group) : null,
      el("div", { class: "hint" }, "📅 Ro'yxatdan o'tgan: " + c.registered))];
    if (!c.count) {
      nodes.push(empty("Bu o'quvchi hali birorta test ishlamagan."));
      return nodes;
    }
    nodes.push(el("div", { class: "card" },
      stat("🕓 Oxirgi test", `${c.last} (${c.when})`), stat("🆕 Birinchi test", c.first),
      stat("📝 Jami", `${c.count} ta test · ${c.total} savol`),
      stat("🎯 Umumiy natija", `${bar(c.pct)} ${c.pct}% (${c.correct}/${c.total})`), trendNode(c.trend)));
    if (c.subjects.length) nodes.push(el("div", { class: "card" }, el("b", {}, "📚 Fanlar bo'yicha"),
      ...c.subjects.map((s) => stat(s.name, `${s.pct}% (${s.attempts} test)`))));
    nodes.push(el("div", { class: "card" }, el("b", {}, "🗓 Oxirgi testlar"),
      ...c.recent.map((a) => {
        const attrs = { class: "card" + (opts.attempts ? " tap" : ""), style: "margin:6px 0" };
        if (opts.attempts) attrs.onclick = () => attempt(a.id, () => card(c.tg));
        return el("div", attrs, el("div", { class: "hint" }, a.at), el("div", {}, a.label), el("b", {}, `${a.correct}/${a.total} (${a.pct}%)`));
      })));
    return nodes;
  }
  async function card(tg) {
    wait();
    let c;
    try { c = await api(`/api/r/student/${tg}`); } catch (e) { return fail(e, () => list()); }
    const nodes = cardBody(c, { attempts: true });
    if (c.count) {
      if (OQ.personalAssign) nodes.push(btn("", "📋 Topshiriq berish", () => OQ.personalAssign(tg, () => card(tg))));
      nodes.push(btn("alt", "📄 To'liq tarix", () => history(tg, 0), "clip"));
      nodes.push(btn("alt", "❗ Eng ko'p xato qilingan savollar", () => wrong(tg)));
    }
    screen("O'quvchi kartasi", c.name, () => list(), ...nodes);
  }

  /* ---------- 3: to'liq tarix ---------- */
  async function history(tg, page) {
    wait();
    let d;
    try { d = await api(`/api/r/history/${tg}?page=${page}`); } catch (e) { return fail(e, () => card(tg)); }
    const nodes = [el("div", { class: "hint" }, `Jami ${d.total} ta test · Sahifa ${d.page + 1}/${d.pages}. Savollarni ko'rish uchun testni bosing.`)];
    for (const a of d.items) nodes.push(el("div", { class: "card tap", onclick: () => attempt(a.id, () => history(tg, d.page)) },
      el("div", { class: "hint" }, `${a.n}. ${a.at}`), el("div", {}, a.source === "quiz" ? `${a.subject} · ${a.label}` : a.label),
      el("b", {}, `${bar(a.pct)} ${a.correct}/${a.total} (${a.pct}%)`)));
    if (d.pages > 1) nodes.push(el("div", { class: "row", style: "margin-top:8px" },
      d.page > 0 ? btn("alt small grow", "⬅️ Oldingi", () => history(tg, d.page - 1)) : null,
      d.page + 1 < d.pages ? btn("alt small grow", "Keyingi ➡️", () => history(tg, d.page + 1)) : null));
    screen("To'liq tarix", d.name, () => card(tg), ...nodes);
  }

  /* ---------- 4: bitta test javoblari ---------- */
  async function attempt(id, back, page) {
    wait();
    let d;
    try { d = await api(`/api/r/attempt/${id}`); } catch (e) { return fail(e, back); }
    const PS = 8, pages = Math.max(1, Math.ceil(d.answers.length / PS));
    page = Math.max(0, Math.min(page || 0, pages - 1));
    const nodes = [el("div", { class: "card" }, el("b", {}, d.student), el("div", { class: "hint" }, `${d.at} · ${d.subject} · ${d.topic}`),
      el("b", {}, `${d.correct}/${d.total} (${d.pct}%)`))];
    const opt = (a, k) => (a.opts[k] ? `${k}) ${a.opts[k]}` : k || "—");
    for (const a of d.answers.slice(page * PS, (page + 1) * PS)) {
      const box = el("div", { class: "card" }, el("b", {}, `${a.n}. ${a.ok ? "✅" : "❌"} ${a.text || (a.img ? "🖼 (rasmli savol)" : "")}`));
      if (a.img) box.append(qimg(a.qid));
      box.append(el("div", { class: "hint" }, "Javob: " + (a.sel === "?" ? "variant noma'lum (guruh testi)" : opt(a, a.sel))));
      if (!a.ok) box.append(el("div", { class: "hint" }, "To'g'risi: " + opt(a, a.right)));
      nodes.push(box);
    }
    if (pages > 1) nodes.push(el("div", { class: "row", style: "margin-top:8px" },
      page > 0 ? btn("alt small grow", "⬅️ Oldingi", () => attempt(id, back, page - 1)) : null,
      page + 1 < pages ? btn("alt small grow", "Keyingi ➡️", () => attempt(id, back, page + 1)) : null,
      el("span", { class: "hint" }, `${page + 1}/${pages}`)));
    nodes.push(btn("alt", "👤 O'quvchi kartasi", () => card(d.tg)));
    screen("Test javoblari", `${d.student}`, back, ...nodes);
  }

  /* ---------- 5: eng ko'p xato qilingan savollar ---------- */
  async function wrong(tg) {
    wait();
    let d;
    try { d = await api(`/api/r/wrong/${tg}`); } catch (e) { return fail(e, () => card(tg)); }
    const nodes = [];
    if (!d.items.length) nodes.push(empty("Xato qilingan savol yo'q."));
    for (const w of d.items) {
      const box = el("div", { class: "card" }, el("b", {}, w.text || (w.img ? "🖼 (rasmli savol)" : `Savol #${w.qid}`)),
        el("div", { class: "hint" }, `📖 ${w.topic}`), el("div", {}, `❌ ${w.wrong}/${w.total} marta xato (${w.pct}%)`));
      if (w.img) box.append(qimg(w.qid));
      nodes.push(box);
    }
    screen("Eng ko'p xatolar", d.name, () => card(tg), ...nodes);
  }

  OQ.reportCard = cardBody;
  OQ.adminReports = (back) => list(true, back);
})();
