/* Veb-ilova: admin paneli, 2-bosqich — Word eksport va Savollar sifati (botdagi bilan bir xil qoidalar).
   oquvchi.js (window.OQ.u) dagi umumiy qismlar ishlatiladi. Har ekranda orqaga tugmasi bor. */
(function () {
  "use strict";
  const OQ = window.OQ;
  if (!OQ || !OQ.u || OQ.wordExport) return;
  const { screen, tiles, empty, wait, good } = OQ.u;
  const fail = (e, back) => { toast(e.message); if (back) back(); };
  const btn = (cls, label, fn, ic) => el("button", { class: "btn " + cls, onclick: fn }, ic ? icon(ic) : null, label);
  const row = (title, hint, fn) => el("div", { class: "card tap", onclick: fn },
    el("div", { class: "row" }, el("div", { class: "grow" }, el("b", {}, title), hint ? el("div", { class: "hint" }, hint) : null), icon("chev")));

  /* ---------- Word eksport ---------- */
  async function wordExport(back) {
    back = back || (() => OQ.admin && OQ.admin());
    wait();
    let d;
    try { d = await api("/api/x/subjects"); } catch (e) { return fail(e, back); }
    const nodes = d.subjects.length ? d.subjects.map((s) => row(s.name, `${s.questions} ta savol`, () => wordSubject(s.id, back)))
      : [empty("Fanlar topilmadi.")];
    screen("Word eksport", "Fanni tanlang:", back, ...nodes);
  }

  async function wordSubject(id, back) {
    wait();
    let d;
    try { d = await api(`/api/x/subject/${id}`); } catch (e) { return fail(e, () => wordExport(back)); }
    const nodes = d.topics.map((t) => row(t.name, `${t.questions} ta savol`, () => wordMode("t", t.id, `${d.name} · ${t.name}`, t.questions, () => wordSubject(id, back))));
    if (!d.topics.length) nodes.push(empty("Bu fanda mavzu yo'q."));
    else nodes.push(row("📦 Butun fan", `${d.total} ta savol`, () => wordMode("s", d.id, `${d.name} (butun fan)`, d.total, () => wordSubject(id, back))));
    screen(d.name, "Mavzuni tanlang:", () => wordExport(back), ...nodes);
  }

  function wordMode(kind, id, title, count, back) {
    if (!count) { toast("Savol yo'q."); return; }
    screen("Word eksport", `${title}\nSavollar: ${count} ta. Qanday fayl kerak?`, back,
      btn("", "Javoblar belgilangan", () => wordGo(kind, id, true, back), "check"),
      btn("alt", "Javoblarsiz (o'quvchi uchun)", () => wordGo(kind, id, false, back), "list"));
  }

  let busyWord = false;
  async function wordGo(kind, id, answers, back) {
    if (busyWord) return;
    busyWord = true; wait();
    try {
      const r = await api("/api/x/word", { method: "POST", body: JSON.stringify({ kind, id, answers }) });
      notify("success");
      screen("Fayl yuborildi ✅", null, back,
        good(`${r.count} ta savol .docx faylga yig'ildi va botdagi chatingizga yuborildi. Telegram'dagi bot chatini oching.`),
        btn("alt", "Orqaga", back));
    } catch (e) { toast(e.message); back(); }
    busyWord = false;
  }

  /* ---------- Savollar sifati ---------- */
  const TITLES = { kalit: "⚠️ Kaliti shubhali", qiyin: "🔴 Juda qiyin", oson: "🟢 Juda oson", olik: "💤 Ishlatilmagan variant" };

  async function quality(back) {
    back = back || (() => OQ.admin && OQ.admin());
    wait();
    let d;
    try { d = await api("/api/x/quality"); } catch (e) { return fail(e, back); }
    const nodes = [];
    if (!d.analysed) {
      nodes.push(empty("Hali yetarli javob yig'ilmagan. O'quvchilar test ishlagan sayin statistika to'ldirib boriladi."));
    } else {
      nodes.push(el("div", { class: "glass" },
        el("b", {}, `Tahlil qilingan: ${d.analysed} ta savol`),
        el("div", { class: "hint" }, `(kamida ${d.threshold} marta javob berilgan savollar)`),
        el("div", { class: "hint", style: "margin-top:6px" }, `🎯 O'rtacha qiyinlik: ${d.average}% to'g'ri javob`)));
      let any = false;
      for (const k of ["kalit", "qiyin", "oson", "olik"]) {
        const n = d.counts[k];
        if (n) { any = true; nodes.push(row(`${TITLES[k]} (${n})`, null, () => qualityList(k, 0, back))); }
      }
      if (!any) nodes.push(good("Muammoli savol topilmadi."));
      else if (d.counts.kalit) nodes.push(el("div", { class: "hint", style: "margin-top:8px" },
        "Eng muhimi — «kaliti shubhali»: ko'pchilik bitta xato variantni tanlaydi, ya'ni to'g'ri javob noto'g'ri belgilangan bo'lishi mumkin."));
    }
    screen("Savollar sifati", "Javoblar tarixidan hisoblangan.", back, ...nodes);
  }

  async function qualityList(kind, page, back) {
    wait();
    let d;
    try { d = await api(`/api/x/quality/${kind}?page=${page}`); } catch (e) { return fail(e, () => quality(back)); }
    const nodes = d.items.length ? d.items.map((q) => row(q.t,
      `${q.topic} · ${q.answers} javob · to'g'ri ${q.pct}%` + (kind === "kalit" && q.wrong ? ` · ko'pchilik ${q.wrong}, kalit ${q.key || "—"}` : ""),
      () => qualityOne(q.id, kind, d.page, back))) : [empty("Bu toifada savol yo'q.")];
    if (d.pages > 1) nodes.push(el("div", { class: "row", style: "margin-top:8px" },
      d.page > 0 ? btn("alt small grow", "◀️ Oldingi", () => qualityList(kind, d.page - 1, back)) : null,
      el("span", { class: "hint", style: "align-self:center" }, `${d.page + 1}/${d.pages}`),
      d.page + 1 < d.pages ? btn("alt small grow", "Keyingi ▶️", () => qualityList(kind, d.page + 1, back)) : null));
    screen(`${TITLES[kind]} — ${d.total} ta`, "Savolni ochish uchun bosing.", () => quality(back), ...nodes);
  }

  async function qualityOne(id, kind, page, back) {
    wait();
    const here = () => qualityList(kind, page, back);
    let d;
    try { d = await api(`/api/x/qstat/${id}`); } catch (e) { return fail(e, here); }
    const nodes = [];
    if (d.text) nodes.push(el("div", { class: "card pre" }, d.text));
    else if (d.img) nodes.push(OQ.u.qimage(id));
    nodes.push(el("div", { class: "glass" }, el("b", {}, `Javoblar: ${d.answers} ta · to'g'ri ${d.correct} (${d.pct}%)`)));
    for (const o of d.options) nodes.push(el("div", { class: "card" }, el("div", { class: "row" },
      el("div", { class: "grow" }, el("b", {}, o.l + ")"), o.t ? " " + o.t : "", o.key ? el("span", { class: "tag done", style: "margin-left:6px" }, "kalit") : null),
      el("b", {}, `${o.n} ta (${o.p}%)`))));
    nodes.push(el("div", { class: "hint", style: "margin:8px 0" }, "💡 " + d.verdict));
    if (OQ.adminQuestion) nodes.push(btn("", "Savolni ochish (tahrirlash)", () => OQ.adminQuestion(id, () => qualityOne(id, kind, page, back)), "gear"));
    screen(`Savol #${d.id}`, `${d.subject} · ${d.topic}`, here, ...nodes);
  }

  OQ.wordExport = wordExport;
  OQ.quality = quality;
})();
