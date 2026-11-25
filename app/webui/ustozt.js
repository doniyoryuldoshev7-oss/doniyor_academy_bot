/* Veb-ilova: Ustoz topshirig'i (admin va ustoz). Har ekranda orqaga tugmasi bor. */
(function () {
  "use strict";
  const OQ = window.OQ;
  if (!OQ || !OQ.u || OQ.adminAssign) return;
  const { screen, empty, wait, good, stat } = OQ.u;
  const fail = (e, back) => { toast(e.message); if (back) back(); };
  const btn = (cls, label, fn, ic) => el("button", { class: "btn " + cls, onclick: fn }, ic ? icon(ic) : null, label);
  let back0 = null, busy = false, pollTok = 0;
  const toHome = () => (back0 ? back0() : OQ.admin && OQ.admin());
  const W = {}; /* yangi topshiriq: topic_id, topic, count, days, daysLabel */

  /* ---------- ro'yxat ---------- */
  async function list(back) {
    if (typeof back === "function") back0 = back;
    pollTok++;
    wait();
    let d;
    try { d = await api("/api/at/list"); } catch (e) { return fail(e, toHome); }
    const nodes = [btn("", "➕ Yangi topshiriq", subjects)];
    if (!d.items.length) nodes.push(empty("Hali topshiriq berilmagan. Yangi topshiriq bering: o'quvchilarga xabar boradi va kim bajarganini shu yerda kuzatasiz."));
    else nodes.push(el("div", { class: "hint", style: "margin:8px 0" }, "Oxirgi topshiriqlar:"));
    for (const a of d.items) nodes.push(el("div", { class: "card tap", onclick: () => progress(a.id) }, el("div", { class: "row" },
      el("div", { class: "grow" }, el("b", {}, a.topic), el("div", { class: "hint" }, `${a.required} savol · 👥 ${a.target} · 🕓 ${a.deadline}`)), icon("chev"))));
    screen("Ustoz topshiriqlari", "", toHome, ...nodes);
  }

  /* ---------- yangi topshiriq: 1-fan ---------- */
  async function subjects() {
    wait();
    let d;
    try { d = await api("/api/at/subjects"); } catch (e) { return fail(e, list); }
    const nodes = [el("div", { class: "hint" }, "1-qadam: fanni tanlang.")];
    if (!d.items.length) nodes.push(empty("Fanlar yo'q."));
    for (const s of d.items) nodes.push(el("div", { class: "card tap", onclick: () => topics(s.id) }, el("div", { class: "row" }, el("b", { class: "grow" }, "📚 " + s.name), icon("chev"))));
    screen("Yangi topshiriq", "", list, ...nodes);
  }
  async function topics(sid) {
    wait();
    let d;
    try { d = await api(`/api/at/topics/${sid}`); } catch (e) { return fail(e, subjects); }
    const nodes = [el("div", { class: "hint" }, "2-qadam: mavzuni tanlang.")];
    if (!d.items.length) nodes.push(empty("Bu fanda savolli mavzu yo'q."));
    for (const t of d.items) nodes.push(el("div", { class: "card tap", onclick: () => counts(t.id, sid) }, el("div", { class: "row" },
      el("b", { class: "grow" }, `📖 ${t.name} (${t.count})`), icon("chev"))));
    screen("Mavzu", "", subjects, ...nodes);
  }
  async function counts(tid, sid) {
    wait();
    let d;
    try { d = await api(`/api/at/options/${tid}`); } catch (e) { return fail(e, () => topics(sid)); }
    Object.assign(W, { topic_id: d.id, topic: d.name, total: d.total, sid, days: d.days });
    const nodes = [el("div", { class: "card" }, el("b", {}, "📖 " + d.name), stat("🔢 Mavjud savollar", d.total)),
      el("div", { class: "hint" }, "3-qadam: nechta savol ishlashi kerak?")];
    for (const n of d.counts) nodes.push(btn("alt", `${n} ta savol`, () => { W.count = n; deadlines(); }));
    screen("Savollar soni", "", () => topics(sid), ...nodes);
  }
  function deadlines() {
    const nodes = [el("div", { class: "hint" }, "4-qadam: muddat qachon tugaydi?")];
    for (const d of W.days) nodes.push(btn("alt", d.label, () => { W.dayCount = d.days; W.daysLabel = d.label; targets(); }));
    screen("Muddat", "", () => counts(W.topic_id, W.sid), ...nodes);
  }
  async function targets() {
    wait();
    let d;
    try { d = await api("/api/at/groups"); } catch (e) { return fail(e, deadlines); }
    const nodes = [el("div", { class: "hint" }, "5-qadam: kimga beriladi?")];
    if (!d.all_ok && !d.items.length) nodes.push(empty("Sizga hali guruh biriktirilmagan. Admin bilan bog'laning."));
    if (d.all_ok) nodes.push(btn("alt", "👥 Hamma o'quvchilar", () => create(null, "hamma")));
    for (const g of d.items) nodes.push(btn("alt", "🏫 " + g, () => create(g, g)));
    screen("Kimga", "", deadlines, ...nodes);
  }
  async function create(group, label) {
    if (busy) return;
    busy = true; wait();
    let r;
    try { r = await api("/api/at/create", { method: "POST", body: JSON.stringify({ topic_id: W.topic_id, count: W.count, days: W.dayCount, group }) }); }
    catch (e) { busy = false; return fail(e, targets); }
    busy = false;
    const tok = ++pollTok;
    const status = el("div", { class: "hint", style: "margin-top:8px" }, r.students ? `⏳ Xabar yuborilmoqda… 0/${r.students}` : "Bu guruhda o'quvchi yo'q — xabar yuborilmadi.");
    screen("Topshiriq berildi", "", list,
      good(`✅ Topshiriq berildi\n\n📖 ${r.topic}\n🔢 ${r.required} ta savol\n⏰ ${r.deadline}\n👥 ${r.target}`), status,
      btn("", "📊 Bajarilishini ko'rish", () => progress(r.id)), btn("alt", "📋 Topshiriqlar", list));
    const poll = async () => {
      if (tok !== pollTok || !r.students) return;
      let s;
      try { s = await api(`/api/at/send/${r.id}`); } catch (e) { return; }
      if (tok !== pollTok) return;
      status.textContent = s.done ? `📨 Xabar yuborildi: ${s.sent}/${s.total} o'quvchiga` : `⏳ Xabar yuborilmoqda… ${s.sent}/${s.total}`;
      if (!s.done) setTimeout(poll, 2000);
    };
    setTimeout(poll, 1500);
  }

  /* ---------- bajarilish ---------- */
  async function progress(id) {
    pollTok++;
    wait();
    let d;
    try { d = await api(`/api/at/progress/${id}`); } catch (e) { return fail(e, list); }
    const nodes = [el("div", { class: "card" }, el("b", {}, "📖 " + d.topic), el("div", { class: "hint" }, d.subject),
      stat("🔢 Kamida", `${d.required} ta savol`), stat("⏰ Muddat", d.deadline), stat("👥 Kimga", d.target),
      stat("✅ Bajardi", `${d.done.length} / ${d.total}`), stat("❌ Bajarmadi", d.pending.length))];
    if (d.done.length) nodes.push(el("div", { class: "card" }, el("b", {}, "Bajarganlar"),
      ...d.done.map((x) => el("div", { class: "hint" }, `✅ ${x.name} — ${x.correct}/${x.total} (${x.pct}%)`))));
    if (d.pending.length) nodes.push(el("div", { class: "card" }, el("b", {}, "Bajarmaganlar"),
      ...d.pending.map((x) => el("div", { class: "hint" }, `❌ ${x.name}` + (x.started ? ` — ${x.pct}% (${x.answered}/${x.required})` : " — boshlamagan")))));
    nodes.push(btn("alt", "🔄 Yangilash", () => progress(id)));
    nodes.push(btn("alt", "🗑 Topshiriqni olib tashlash", () => confirmSheet("Olib tashlansinmi?",
      `📖 ${d.topic}\n⏰ ${d.deadline}\n👥 ${d.target}\n✅ Bajargan: ${d.done.length} / ${d.total}\n\nTopshiriq o'quvchilar ro'yxatidan va eslatmalardan chiqib ketadi. Test natijalari saqlanib qoladi.`,
      "Ha, olib tashlansin", async () => {
        try { await api("/api/at/remove", { method: "POST", body: JSON.stringify({ id }) }); toast("Olib tashlandi"); } catch (e) { toast(e.message); }
        list();
      })));
    screen("Topshiriq bajarilishi", d.topic, list, ...nodes);
  }

  /* ---------- shaxsiy topshiriq (admin; o'quvchi kartasidan) ---------- */
  async function personal(tg, back) {
    wait();
    let d;
    try { d = await api(`/api/at/weak/${tg}`); } catch (e) { return fail(e, back); }
    const nodes = [];
    if (!d.items.length) nodes.push(empty("Bu o'quvchi hali yetarli test ishlamagan, shuning uchun zaif mavzusini aniqlab bo'lmaydi. Istasangiz, mavzuni o'zingiz tanlab, guruh topshirig'i sifatida bering."));
    else nodes.push(el("div", { class: "hint" }, "Tizim eng zaif mavzularni topdi. Birini tanlang:"));
    for (const w of d.items) nodes.push(el("div", { class: "card tap", onclick: () => personalDays(tg, w, d.name, back) }, el("div", { class: "row" },
      el("div", { class: "grow" }, el("b", {}, `📖 ${w.topic} — ${w.pct}%`), el("div", { class: "hint" }, `${w.subject} · ${w.distinct} savol ishlangan · mavzuda ${w.available} ta savol`)), icon("chev"))));
    screen("Shaxsiy topshiriq", d.name, back, ...nodes);
  }
  async function personalDays(tg, w, name, back) {
    wait();
    let d;
    try { d = await api("/api/at/days"); } catch (e) { return fail(e, () => personal(tg, back)); }
    const count = Math.min(20, w.available) || 1;
    const nodes = [el("div", { class: "card" }, el("b", {}, "📖 " + w.topic), el("div", { class: "hint" }, `🔢 ${count} ta savol (mavzuda ${w.available} ta bor)`)),
      el("div", { class: "hint" }, "Muddat qachon tugaydi?")];
    for (const x of d.days) nodes.push(btn("alt", x.label, () => confirmSheet("Shaxsiy topshiriq berilsinmi?", `${name}\n📖 ${w.topic}\n🔢 ${count} ta savol\n⏰ ${x.label}`, "Ha, berish", async () => {
      if (busy) return;
      busy = true; wait();
      try {
        const r = await api("/api/at/personal", { method: "POST", body: JSON.stringify({ tg, topic_id: w.topic_id, days: x.days }) });
        busy = false;
        screen("Shaxsiy topshiriq berildi", name, back, good(`✅ Shaxsiy topshiriq berildi\n\n👤 ${r.name}\n📖 ${r.topic}\n🔢 ${r.required} ta savol\n⏰ ${r.deadline}\n\n` +
          (r.delivered ? "📨 Xabar yuborildi." : "⚠️ Xabar yuborilmadi (o'quvchi botni bloklagan bo'lishi mumkin).")),
          btn("", "📊 Bajarilishini ko'rish", () => progress(r.id)), btn("alt", "👤 O'quvchi kartasi", back));
      } catch (e) { busy = false; fail(e, back); }
    })));
    screen("Muddat", name, () => personal(tg, back), ...nodes);
  }

  OQ.adminAssign = (back) => list(back);
  OQ.personalAssign = personal;
})();
