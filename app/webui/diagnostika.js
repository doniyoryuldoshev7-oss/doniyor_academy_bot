/* Veb-ilova: Diagnostika (botdagidek). Savollar serverda tanlanadi, natija serverda hisoblanadi.
   oquvchi.js (window.OQ.u) dagi umumiy qismlar ishlatiladi. */
(function () {
  "use strict";
  const OQ = window.OQ;
  if (!OQ || !OQ.u || OQ.diagnostika) return;
  const { screen, empty, pc, wait, good, bad, questionView } = OQ.u;

  const D = { subs: [], step: 5, budgets: {}, filter: [], token: null, qs: [], i: 0, ans: [] };
  const back = () => OQ.testHub();
  const total = () => Object.values(D.budgets).reduce((a, b) => a + b, 0);

  /* ---------- 1-ekran: fanlar va savollar soni ---------- */
  async function setup(fresh) {
    if (fresh !== false) { D.budgets = {}; D.filter = []; }
    wait();
    try {
      const d = await api("/api/d/setup");
      D.subs = d.subjects; D.step = d.step || 5;
    } catch (e) { toast(e.message); return back(); }
    if (!D.subs.length) return screen("Diagnostika", null, back, empty("Hozircha diagnostika uchun savol yo'q."));
    paint();
  }

  function adjust(s, delta) {
    const cur = D.budgets[s.id] || 0;
    let v = delta === null ? 0 : cur + delta;
    if (cur === 0 && delta > 0) v = Math.min(D.step, s.count);
    v = Math.max(0, Math.min(v, s.count));
    if (v === cur && delta !== null) toast(v ? "Chegaraga yetdi." : "Bu fanda savol yo'q.");
    if (v) D.budgets[s.id] = v; else delete D.budgets[s.id];
    if (Object.keys(D.budgets).length !== 1) D.filter = [];
    paint();
  }

  function paint() {
    const active = Object.keys(D.budgets);
    const rows = D.subs.map((s) => {
      const n = D.budgets[s.id] || 0;
      return el("div", { class: "card" },
        el("div", { class: "row" }, el("div", { class: "grow" }, el("b", {}, s.name), el("div", { class: "hint" }, `mavjud: ${s.count} ta`)),
          el("button", { class: "btn alt small", onclick: () => adjust(s, -D.step), "aria-label": "Kamaytirish" }, "−"),
          el("b", { style: "min-width:34px;text-align:center", onclick: () => adjust(s, null) }, String(n)),
          el("button", { class: "btn alt small", onclick: () => adjust(s, D.step), "aria-label": "Ko'paytirish" }, "+")));
    });
    const nodes = [...rows];
    if (active.length === 1) nodes.push(el("button", { class: "btn alt", onclick: () => pickTopics(Number(active[0])) },
      icon("list"), "Mavzularni tanlash" + (D.filter.length ? ` (${D.filter.length})` : "")));
    nodes.push(total() ? el("button", { class: "btn gold", onclick: start }, icon("play"), `Boshlash — ${total()} ta savol`)
      : el("div", { class: "hint", style: "margin-top:8px" }, "+ tugmasi bilan savol sonini belgilang."));
    nodes.push(el("div", { class: "hint", style: "margin-top:10px" }, "Savollar mavzularga o'zi taqsimlanadi: zaif va hali ishlanmagan mavzulardan ko'proq, mustahkamlaridan kamroq."));
    screen("Diagnostika", "Qaysi fandan nechta savol olishni belgilang. Bir nechta fanni birga tanlash mumkin.", back, ...nodes);
  }

  /* ---------- mavzularni tanlash (bitta fan bo'lganda) ---------- */
  async function pickTopics(sid) {
    wait();
    let d;
    try { d = await api(`/api/d/topics/${sid}`); } catch (e) { toast(e.message); return paint(); }
    const draw = () => {
      const nodes = d.topics.map((t) => {
        const on = D.filter.includes(t.id);
        return el("div", { class: "card tap", style: on ? "border-color:var(--g)" : "", onclick: () => {
          D.filter = on ? D.filter.filter((x) => x !== t.id) : D.filter.concat([t.id]); draw();
        } }, el("div", { class: "row" }, el("div", { class: "grow" }, el("b", {}, t.name), el("div", { class: "hint" }, `${t.count} ta savol`)),
          el("span", { class: "tag" + (on ? " done" : "") }, on ? "✅ tanlandi" : "tanlanmagan")));
      });
      nodes.push(el("button", { class: "btn", onclick: paint }, icon("check"), "Tayyor"));
      screen("Mavzularni tanlang", "Hech biri belgilanmasa, barcha mavzular hisobga olinadi.", paint, ...nodes);
    };
    draw();
  }

  /* ---------- test ---------- */
  async function start() {
    let d;
    try { d = await api("/api/d/start", { method: "POST", body: JSON.stringify({ budgets: D.budgets, topic_ids: D.filter }) }); }
    catch (e) { return toast(e.message); }
    Object.assign(D, { token: d.token, qs: d.questions, i: 0, ans: [], busy: false });
    closing(true);
    view();
  }

  const stop = () => confirmSheet("Diagnostikani to'xtatasizmi?", "Javoblar saqlanmaydi: diagnostika oxirigacha ishlanishi kerak.", "To'xtatish", () => {
    closing(false); D.token = null; setup(false);
  });

  function view() {
    backTo(stop);
    const q = D.qs[D.i], n = D.qs.length;
    questionView({
      q, a: null, i: D.i, total: n, done: D.i, src: "Diagnostika", last: D.i + 1 >= n,
      onQuit: stop, onFinish: stop, onPick: pick,
    });
  }

  function pick(letter) {
    if (D.busy) return;
    haptic();
    D.ans.push({ qid: D.qs[D.i].id, sel: letter });
    if (D.i + 1 < D.qs.length) { D.i++; view(); } else finish();
  }

  async function finish() {
    D.busy = true; wait(); backTo(null);
    let r;
    try { r = await api("/api/d/finish", { method: "POST", body: JSON.stringify({ token: D.token, answers: D.ans }) }); }
    catch (e) {
      D.busy = false; closing(false);
      toast(e.message);
      if (e.status === 410) return setup(false);
      D.ans.pop(); D.i = Math.max(0, D.qs.length - 1); closing(true); return view();   // tarmoq xatosi: oxirgi savolga qaytish
    }
    D.busy = false; D.token = null; closing(false);
    result(r);
  }

  /* ---------- natija ---------- */
  function group(icon_, title, items) {
    if (!items.length) return null;
    return el("div", { class: "card" }, el("b", {}, `${icon_} ${title}`),
      items.slice(0, 10).map((t) => el("div", { class: "stat" }, el("span", {}, t.name), el("b", { style: `color:${pc(t.pct)}` }, `${t.pct}% (${t.correct}/${t.total})`))),
      items.length > 10 ? el("div", { class: "hint" }, `… va yana ${items.length - 10} ta mavzu`) : null);
  }

  function result(r) {
    const g = r.groups;
    const nodes = [
      el("div", { style: "text-align:center" },
        el("div", { class: "big" }, el("div", { class: "score", style: `--p:${r.pct}` }, el("i", {}, el("div", {}, el("b", {}, `${r.pct}%`), el("small", {}, `${r.correct}/${r.total}`)))))),
      group("🟢", "Kuchli mavzular", g.strong), group("🟡", "O'rtacha", g.medium), group("🔴", "Zaif mavzular", g.weak),
      g.unclear.length ? el("div", { class: "card" }, el("b", {}, "⚪ Aniq baho uchun savol kam edi"),
        g.unclear.slice(0, 10).map((t) => el("div", { class: "stat" }, el("span", {}, t.name), el("b", {}, `${t.correct}/${t.total}`)))) : null,
      r.skipped.length ? el("div", { class: "card" }, el("b", {}, "📋 Bu safar tekshirilmadi"),
        el("div", { class: "hint pre" }, r.skipped.slice(0, 8).join(", ") + (r.skipped.length > 8 ? ` … va yana ${r.skipped.length - 8} ta` : "")),
        el("div", { class: "hint" }, "Savol sonini oshirsangiz, ular ham qamrab olinadi.")) : null,
    ];
    if (r.next) {
      nodes.push(good(r.next.level === "unclear" ? `💡 Tavsiya: ${r.next.name} bo'yicha savol kam bo'ldi. Aniq baho uchun shu mavzuni ishlab ko'ring.` : `💡 Tavsiya: ${r.next.name} dan boshlang.`));
      if (r.next.subject_id) nodes.push(el("button", { class: "btn gold", onclick: () => OQ.openTopic(r.next.subject_id, r.next.id) }, icon("play"), `${r.next.name} testi`));
    } else if (g.strong.length || g.medium.length || g.weak.length) nodes.push(good("🎉 Tekshirilgan mavzular yaxshi o'zlashtirilgan."));
    if (r.smart) nodes.push(el("div", { class: "hint" }, "Xato javoblar Smart Takrorlash navbatiga qo'shildi."));
    nodes.push(el("button", { class: "btn alt", onclick: () => setup(true) }, icon("refresh"), "Yana diagnostika"));
    if (OQ.knowledge) nodes.push(el("button", { class: "btn alt", onclick: () => OQ.knowledge() }, icon("map"), "Bilimlar xaritasi"));
    screen("Diagnostika natijasi", r.title, back, ...nodes);
  }

  OQ.diagnostika = setup;
})();
