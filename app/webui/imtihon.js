/* Veb-ilova: Milliy sertifikat / oddiy imtihon (admin va ustoz). Har ekranda orqaga tugmasi bor. */
(function () {
  "use strict";
  const OQ = window.OQ;
  if (!OQ || !OQ.u || OQ.adminExam) return;
  const { screen, empty, wait, good, stat } = OQ.u;
  const fail = (e, back) => { toast(e.message); if (typeof back === "function") back(); };
  const btn = (cls, label, fn) => el("button", { class: "btn " + cls, onclick: fn }, label);
  const post = (url, body) => api(url, { method: "POST", body: JSON.stringify(body || {}) });
  const PAGE = 15;
  let back0 = null, busy = false, pollTok = 0, OPT = null;
  let W = {};
  const toHome = () => (back0 ? back0() : OQ.admin && OQ.admin());

  /* ---------- bosh sahifa ---------- */
  function home(back) {
    if (typeof back === "function") back0 = back;
    pollTok++;
    screen("Milliy sertifikat imtihoni", "", toHome,
      el("div", { class: "hint", style: "margin-bottom:8px" }, "Imtihon — vaqtli test: o'quvchi javobning to'g'riligini oxirigacha ko'rmaydi, faqat bir marta topshiradi. «Milliy sertifikat» shabloni: 35 yopiq + 10 ochiq savol, natija 75 ballik shkala va daraja (A+ … C) bilan."),
      btn("", "🏅 Milliy sertifikatni yig'ish (1–45 yoki 1–55)", () => mlyHome()),
      btn("alt", "🗂 Tayyor variantlar (zip)", () => variants()),
      btn("alt", "➕ Yangi imtihon (bankdan)", template),
      btn("alt", "📋 Imtihonlarim", list),
      btn("alt", "✍️ Ochiq savollar qo'shish", openSubjects));
  }

  /* ---------- yangi imtihon ---------- */
  function template() {
    W = {};
    screen("Yangi imtihon", "Shablonni tanlang:", home,
      btn("", "🏅 Milliy sertifikat (35 yopiq + 10 ochiq)", () => { W.mode = "milliy"; W.c = 35; W.o = 10; W.pick = []; subjects(); }),
      btn("alt", "📝 Oddiy imtihon (o'zim belgilayman)", () => { W.mode = "oddiy"; W.pick = []; subjects(); }));
  }
  async function subjects() {
    wait();
    let d;
    try { d = await api("/api/ex/subjects"); } catch (e) { return fail(e, template); }
    W.subs = d.items;
    drawSubjects();
  }
  function drawSubjects() {
    const nodes = [el("div", { class: "hint" }, "1-qadam: qaysi fanlardan? Bir yoki bir nechta fanni belgilang.")];
    if (!W.subs.length) nodes.push(empty("Hali birorta fan yo'q."));
    for (const s of W.subs) {
      const on = W.pick.includes(s.id);
      nodes.push(el("div", { class: "card tap", onclick: () => { W.pick = on ? W.pick.filter((x) => x !== s.id) : [...W.pick, s.id]; drawSubjects(); } },
        el("div", { class: "row" }, el("b", { class: "grow" }, (on ? "✅ " : "☐ ") + s.name))));
    }
    if (W.pick.length) nodes.push(btn("", `▶️ Davom etish (${W.pick.length} ta fan)`, subjectsDone));
    screen("Yangi imtihon", "", template, ...nodes);
  }
  async function subjectsDone() {
    if (!W.pick.length) return toast("Kamida bitta fanni belgilang.");
    if (W.pick.length === 1) return topics();
    wait();
    let a;
    try { a = await post("/api/ex/avail", { sids: W.pick }); } catch (e) { return fail(e, drawSubjects); }
    Object.assign(W, { tid: null, tname: null, sname: a.name, ac: a.closed, ao: a.open });
    afterSource();
  }
  async function topics() {
    wait();
    let d;
    try { d = await api(`/api/ex/topics/${W.pick[0]}`); } catch (e) { return fail(e, drawSubjects); }
    W.sname = d.subject;
    const nodes = [el("div", { class: "hint" }, `Fan: ${d.subject}. 2-qadam: savollar qaysi mavzulardan olinsin? «Butun fan» — hamma mavzulardan tasodifiy.`),
      el("div", { class: "card tap", onclick: () => topicPick(null) }, el("div", { class: "row" }, el("b", { class: "grow" }, `📚 Butun fan (${d.closed} yopiq, ${d.open} ochiq savol)`), icon("chev")))];
    for (const t of d.items) nodes.push(el("div", { class: "card tap", onclick: () => topicPick(t.id) }, el("div", { class: "row" }, el("b", { class: "grow" }, "📖 " + t.name), icon("chev"))));
    screen("Mavzu", "", drawSubjects, ...nodes);
  }
  async function topicPick(tid) {
    wait();
    let a;
    try { a = await post("/api/ex/avail", { sids: W.pick, tid }); } catch (e) { return fail(e, topics); }
    Object.assign(W, { tid, tname: a.topic, ac: a.closed, ao: a.open });
    afterSource();
  }
  const bankLine = () => `Bazada: ${W.ac} yopiq, ${W.ao} ochiq savol` + (W.tname ? ` (${W.tname})` : "");
  async function opts() {
    if (!OPT) OPT = await api("/api/ex/options");
    return OPT;
  }
  async function afterSource() {
    try { await opts(); } catch (e) { return fail(e, drawSubjects); }
    if (W.mode === "milliy") return durations();
    closedSizes();
  }
  const srcBack = () => (W.vid ? variantView(W.vid) : W.fixed ? mlyHome() : W.pick.length === 1 ? topics() : drawSubjects());
  function closedSizes() {
    const nodes = [el("div", { class: "card" }, el("b", {}, W.sname), el("div", { class: "hint" }, bankLine())), el("div", { class: "hint" }, "3-qadam: nechta yopiq (A/B/C/D) savol?")];
    for (const n of OPT.closed) nodes.push(btn("alt", String(n), () => { W.c = n; openSizes(); }));
    screen("Yopiq savollar", "", srcBack, ...nodes);
  }
  function openSizes() {
    const nodes = [el("div", { class: "card" }, el("div", { class: "hint" }, bankLine())), el("div", { class: "hint" }, "4-qadam: nechta ochiq savol? (o'quvchi javobni o'zi yozadi; 0 — ochiq savolsiz)")];
    for (const n of OPT.open) nodes.push(btn("alt", String(n), () => { W.o = n; durations(); }));
    screen("Ochiq savollar", "", closedSizes, ...nodes);
  }
  function durations() {
    const nodes = [el("div", { class: "card" }, el("div", { class: "hint" }, bankLine()), el("div", { class: "hint" }, `Savollar: ${W.c} yopiq + ${W.o} ochiq`)), el("div", { class: "hint" }, "Imtihon davomiyligi qancha bo'lsin?")];
    for (const m of OPT.durations) nodes.push(btn("alt", `${m} daq.`, () => { W.dur = m; groups(); }));
    screen("Davomiylik", "", W.mode === "milliy" ? srcBack : openSizes, ...nodes);
  }
  async function groups() {
    wait();
    let d;
    try { d = await api("/api/ex/groups"); } catch (e) { return fail(e, durations); }
    const nodes = [el("div", { class: "hint" }, "Kimlar topshiradi?")];
    if (!d.all_ok && !d.items.length) nodes.push(empty("Sizga hali guruh biriktirilmagan. Admin bilan bog'laning."));
    if (d.all_ok) nodes.push(btn("alt", "👥 Hamma o'quvchilar", () => { W.group = null; W.glabel = "hamma o'quvchilar"; days(); }));
    for (const g of d.items) nodes.push(btn("alt", "🏫 " + g, () => { W.group = g; W.glabel = g; days(); }));
    screen("Kimlar", "", durations, ...nodes);
  }
  function days() {
    const nodes = [el("div", { class: "hint" }, "O'quvchilar imtihonni necha kun ichida boshlay olishadi? (Boshlagandan keyin esa faqat imtihon vaqti beriladi.)")];
    for (const n of OPT.days) nodes.push(btn("alt", `${n} kun`, () => { W.days = n; title(); }));
    screen("Boshlash muddati", "", groups, ...nodes);
  }
  function title() {
    const inp = el("input", { class: "input", type: "text", maxlength: "200", placeholder: "Bo'sh qoldirsangiz: fan nomi va sana qo'yiladi", style: "width:100%;margin:8px 0" });
    if (W.vdef) inp.value = W.title || W.vdef;
    screen("Imtihon nomi", "", days, el("div", { class: "hint" }, "Imtihon nomini yozing (ixtiyoriy):"), inp, btn("", "Davom etish", () => { W.title = inp.value.trim(); preview(); }));
  }
  function preview() {
    const rc = Math.min(W.c, W.ac), ro = Math.min(W.o, W.ao);
    const nodes = [el("div", { class: "card" }, el("b", {}, W.title || `${W.sname} (nom avtomatik)`),
      stat("📚 Fan", W.fixed ? "Milliy sertifikat (tayyor savollar)" : W.sname + (W.tname ? " · " + W.tname : "") + (W.pick.length > 1 ? " (fanlar orasida teng olinadi)" : "")),
      stat("🏅 Shablon", W.mode === "milliy" ? "Milliy sertifikat" : "Oddiy"), W.vtitle ? stat("🗂 Variant", W.vtitle) : null, stat("👥 Kimlar", W.glabel),
      stat("⏱ Vaqt", `${W.dur} daqiqa`), stat("📅 Boshlash muddati", `${W.days} kun`),
      stat("Yopiq savollar", rc + (rc < W.c ? ` (so'raldi ${W.c}, bazada yetarli emas)` : "")),
      stat("Ochiq savollar", ro + (ro < W.o ? ` (so'raldi ${W.o}, bazada yetarli emas)` : "")))];
    if (rc + ro === 0) nodes.push(empty("Tanlangan fanda savol yo'q. Avval savol qo'shing."));
    else nodes.push(btn("", "✅ Yaratish va xabar yuborish", create));
    screen("Imtihonni tasdiqlang", "", title, ...nodes);
  }
  async function create() {
    if (busy) return;
    busy = true; wait();
    let r;
    try { r = await post("/api/ex/create", { mode: W.mode, sids: W.pick, tid: W.tid, c: W.c, o: W.o, dur: W.dur, group: W.group, days: W.days, title: W.title, fixed: W.fixed || null }); }
    catch (e) { busy = false; return fail(e, preview); }
    busy = false;
    if (W.fixed && !W.vid) { D = { a: null, c: null, o: null, buf: { c: [], o: [], a: [] } }; saveDraft(); }   /* botdagi kabi: yaratilgach qoralama tozalanadi */
    const tok = ++pollTok;
    const status = el("div", { class: "hint", style: "margin-top:8px" }, r.students ? `⏳ Xabar yuborilmoqda… 0/${r.students}` : "Bu guruhda o'quvchi yo'q — xabar yuborilmadi.");
    screen("Imtihon yaratildi", "", home,
      good(`✅ Imtihon yaratildi\n\n📝 ${r.title}\nYopiq: ${r.closed} · Ochiq: ${r.open} · ${r.duration} daqiqa\n📅 ${r.deadline} gacha`), status,
      btn("", "📊 Boshqarish", () => manage(r.id)), btn("alt", "📋 Imtihonlarim", list));
    const poll = async () => {
      if (tok !== pollTok || !r.students) return;
      let s;
      try { s = await api(`/api/ex/send/${r.id}`); } catch (e) { return; }
      if (tok !== pollTok) return;
      status.textContent = s.done ? `📨 Xabar yuborildi: ${s.sent} ta` + (s.failed ? ` (yetmadi: ${s.failed})` : "") : `⏳ Xabar yuborilmoqda… ${s.sent}/${s.total}`;
      if (!s.done) setTimeout(poll, 2000);
    };
    setTimeout(poll, 1500);
  }

  /* ---------- imtihonlarim ---------- */
  async function list() {
    pollTok++;
    wait();
    let d;
    try { d = await api("/api/ex/list"); } catch (e) { return fail(e, home); }
    const nodes = [];
    if (!d.items.length) nodes.push(empty("Hali imtihon yaratilmagan."));
    else nodes.push(el("div", { class: "hint" }, "🟢 ochiq · 🔒 yopiq"));
    for (const e of d.items) nodes.push(el("div", { class: "card tap", onclick: () => manage(e.id) }, el("div", { class: "row" },
      el("div", { class: "grow" }, el("b", {}, `${e.open ? "🟢" : "🔒"} ${e.title}`), el("div", { class: "hint" }, `${e.subject}${e.group ? " · " + e.group : ""}`)), icon("chev"))));
    screen("Imtihonlarim", "", home, ...nodes);
  }
  async function manage(id, note) {
    pollTok++;
    wait();
    let d;
    try { d = await api(`/api/ex/exam/${id}`); } catch (e) { return fail(e, list); }
    if (note) toast(note);
    const nodes = [el("div", { class: "card" }, el("b", {}, "📝 " + d.title), el("div", { class: "hint" }, `${d.subject} · ${d.mode === "milliy" ? "Milliy sertifikat" : "Oddiy"}`),
      stat("👥 Kimlar", d.group || "hamma o'quvchilar"), stat("⏱ Vaqt", `${d.duration} daqiqa`),
      stat(d.open ? "🟢 Holat" : "🔒 Holat", (d.open ? "Ochiq" : "Yopiq") + (d.closes ? ` · muddat: ${d.closes}` : "")),
      stat("✅ Topshirgan", d.finished), stat("⏳ Yechayotgan", d.active), stat("🕳 Boshlamagan", d.missing_count),
      d.avg !== null ? stat("📊 O'rtacha", d.mode === "milliy" ? `${d.avg} / ${d.scale}` : `${d.avg}%`) : null)];
    if (d.missing_count) nodes.push(el("div", { class: "card" }, el("b", {}, "Boshlamaganlar"),
      ...d.missing.map((n) => el("div", { class: "hint" }, "• " + n)), d.missing_count > d.missing.length ? el("div", { class: "hint" }, `… yana ${d.missing_count - d.missing.length} ta`) : null));
    nodes.push(btn("", "📊 Natijalar", () => results(id, 0)), btn("alt", "📉 Qiyin savollar", () => hard(id)),
      btn("alt", "✍️ Yozma javoblarni tekshirish", () => openReview(id)));
    nodes.push(btn("alt", d.open ? "🔒 Yopish (tahlil ochiladi)" : "🔓 Qayta ochish", async () => {
      try { const r = await post(`/api/ex/toggle/${id}`); manage(id, r.note); } catch (e) { toast(e.message); }
    }));
    nodes.push(btn("alt", "🗑 O'chirish", () => confirmSheet("Imtihon o'chirilsinmi?",
      `📝 ${d.title}\n\nO'quvchilar uni ko'rmaydi, natijalar ham yo'qoladi.`, "Ha, o'chirish", async () => {
        try { await post(`/api/ex/delete/${id}`); toast("Imtihon o'chirildi"); } catch (e) { toast(e.message); }
        list();
      })));
    screen("Imtihonni boshqarish", "", list, ...nodes);
  }
  async function results(id, page) {
    wait();
    let d;
    try { d = await api(`/api/ex/results/${id}`); } catch (e) { return fail(e, () => manage(id)); }
    if (!d.items.length) { toast("Hali hech kim topshirmagan."); return manage(id); }
    const last = Math.floor((d.items.length - 1) / PAGE);
    const p = Math.min(Math.max(0, page), last), start = p * PAGE;
    const nodes = d.items.slice(start, start + PAGE).map((r, i) => el("div", { class: "card" },
      el("b", {}, `${start + i + 1}. ${r.name}`),
      el("div", { class: "hint" }, `${r.value} · ${r.c_ok}/${r.c_total}` + (r.o_total ? ` + ${r.o_ok}/${r.o_total}` : "") + ` · ${r.minutes} daq.`)));
    const nav = [];
    if (p > 0) nav.push(btn("alt", "⬅️ Oldingi", () => results(id, p - 1)));
    if (p < last) nav.push(btn("alt", "Keyingi ➡️", () => results(id, p + 1)));
    nodes.push(...nav);
    screen("Natijalar", d.title, () => manage(id), ...nodes);
  }
  /* ---------- yozma javoblarni ustoz tekshiradi ---------- */
  async function openReview(id, note, keep) {
    if (!keep) wait();
    let d;
    try { d = await api(`/api/ex/openreview/${id}`); } catch (e) { return fail(e, () => manage(id)); }
    if (note) toast(note);
    if (!d.parts.length) { toast("Bu imtihonda yozma savol yo'q."); return manage(id); }
    if (!d.count) { toast("Hali hech kim topshirmagan."); return manage(id); }
    const y = window.scrollY;
    const act = async (key, text, accept) => {
      if (busy) return;
      busy = true;
      try { const r = await post(`/api/ex/accept/${id}`, { key, text, accept }); await openReview(id, r.note, true); window.scrollTo(0, y); }
      catch (e) { toast(e.message); }
      finally { busy = false; }
    };
    const pending = d.parts.reduce((n, p) => n + p.wrong.length, 0);
    const nodes = [el("div", { class: "card" },
      el("div", { class: "pre hint" }, "Bu yerda avtomatik tekshiruvda to'g'ri deb topilmagan yozma javoblar. Bir xil yozilgan javoblar birga ko'rsatiladi.\n\n«✅ Qabul qilish» — shu yozilish SHU imtihonda to'g'ri hisoblanadi: shunday yozgan hamma o'quvchining bali darhol qayta hisoblanadi. Savollar bazasi o'zgarmaydi."),
      stat("👥 Topshirganlar", d.count), stat("🔎 Ko'rib chiqiladigan yozilishlar", pending))];
    for (const p of d.parts) {
      const card = el("div", { class: "card" },
        el("b", {}, `${p.pos}-savol, ${"abcd"[p.part]})`), el("div", { class: "pre", style: "margin:4px 0" }, p.q),
        el("div", { class: "hint" }, `✅ To'g'ri javob: ${p.a.join(" / ")}`),
        el("div", { class: "hint" }, `To'g'ri yechganlar: ${p.ok} ta`));
      for (const x of p.ax) card.append(el("div", { class: "row", style: "margin-top:8px" },
        el("div", { class: "grow pre" }, `➕ Qo'shimcha qabul qilingan: «${x}»`),
        el("button", { class: "btn alt small", onclick: () => confirmSheet("Qabul bekor qilinsinmi?", `«${x}»\n\nShunday yozganlarning bali kamayadi.`, "Ha, bekor qilish", () => act(p.key, x, false)) }, "↩️ Bekor")));
      if (!p.wrong.length) card.append(el("div", { class: "hint", style: "margin-top:8px" }, "Ko'rib chiqiladigan javob yo'q."));
      for (const w of p.wrong) card.append(el("div", { class: "row", style: "margin-top:8px" },
        el("div", { class: "grow pre" }, `«${w.text}» — ${w.count} kishi`),
        el("button", { class: "btn small", onclick: () => confirmSheet("Javob qabul qilinsinmi?", `${p.pos}-savol, ${"abcd"[p.part]})\n«${w.text}»\n\nShunday yozgan ${w.count} kishining bali qayta hisoblanadi.`, "✅ Ha, qabul qilish", () => act(p.key, w.text, true)) }, "✅ Qabul qilish")));
      nodes.push(card);
    }
    screen("Yozma javoblar", d.title, () => manage(id), ...nodes);
  }
  async function hard(id) {
    wait();
    let d;
    try { d = await api(`/api/ex/hard/${id}`); } catch (e) { return fail(e, () => manage(id)); }
    if (!d.count) { toast("Hali hech kim topshirmagan."); return manage(id); }
    const nodes = d.items.map((x) => el("div", { class: "card" }, el("b", {}, `${x.tag} — ${x.pct}% to'g'ri`), el("div", { class: "hint" }, x.label)));
    nodes.push(el("div", { class: "hint" }, "Shu mavzularni darsda takrorlash foydali."));
    screen("Eng qiyin savollar", d.title, () => manage(id), ...nodes);
  }

  /* ---------- ochiq savollar qo'shish ---------- */
  async function openSubjects() {
    wait();
    let d;
    try { d = await api("/api/ex/subjects"); } catch (e) { return fail(e, home); }
    const nodes = [el("div", { class: "hint" }, "Qaysi fanga qo'shamiz?")];
    if (!d.items.length) nodes.push(empty("Fanlar yo'q."));
    for (const s of d.items) nodes.push(el("div", { class: "card tap", onclick: () => openTopics(s.id) }, el("div", { class: "row" }, el("b", { class: "grow" }, "📚 " + s.name), icon("chev"))));
    screen("Ochiq savollar", "", home, ...nodes);
  }
  async function openTopics(sid) {
    wait();
    let d;
    try { d = await api(`/api/ex/topics/${sid}`); } catch (e) { return fail(e, openSubjects); }
    const nodes = [el("div", { class: "hint" }, "Qaysi mavzuga qo'shamiz?")];
    if (!d.items.length) nodes.push(empty("Bu fanda mavzu yo'q."));
    for (const t of d.items) nodes.push(el("div", { class: "card tap", onclick: () => openPaste(t, sid) }, el("div", { class: "row" }, el("b", { class: "grow" }, "📖 " + t.name), icon("chev"))));
    screen(d.subject, "", openSubjects, ...nodes);
  }
  async function openPaste(t, sid) {
    wait();
    let h;
    try { h = await api("/api/ex/open_help"); } catch (e) { return fail(e, () => openTopics(sid)); }
    const ta = el("textarea", { class: "input", rows: "12", style: "width:100%;margin:8px 0", placeholder: "Savollarni shu yerga yozing yoki joylang" });
    const out = el("div", { class: "hint" });
    const send = async (dry) => {
      if (busy) return;
      busy = true;
      try {
        const r = await post("/api/ex/open_add", { topic_id: t.id, text: ta.value, dry });
        const bad = r.errors.length ? "\n⚠️ O'qilmadi:\n" + r.errors.map((x) => "• " + x).join("\n") : "";
        if (dry) out.textContent = `Tekshiruv: ${r.count} ta savol o'qildi.` + bad;
        else { toast(`${r.count} ta ochiq savol saqlandi`); busy = false; return openTopics(sid); }
      } catch (e) { out.textContent = "❌ " + e.message; }
      busy = false;
    };
    screen("Ochiq savollar", t.name, () => openTopics(sid),
      el("div", { class: "card" }, el("div", { class: "hint", style: "white-space:pre-wrap" }, h.help)), ta, out,
      btn("alt", "🔍 Tekshirish", () => send(true)), btn("", "💾 Saqlash", () => send(false)));
  }


  /* ---------- Milliy sertifikatni fayldan yig'ish (1-35 yopiq, 36-45 ochiq) ---------- */
  const NC = 35, NO = 10, KEY = "mly_draft_v1";
  const HELP = {
    c: `Yopiq savollarni yuboring (${NC} ta, 1–${NC}): matn, .txt, .docx yoki .xlsx.\n\n1. Savol matni\nA) variant\nB) variant\nC) variant\nD) variant\nJavob: B\n\n• Ro'yxatli savol (1) 2) 3) yoki a) b) c) qatorlari, atamalar, asarlar va mualliflar) — savol matnida oddiy qator qilib yozing. Variantlar faqat KATTA A) B) C) D).\n• Rasmli savol (xarita, jadval, Venn diagrammasi): avval rasmni qo'shing, keyin savol matnini.\n• Hammasi rasmda bo'lsa: rasmni qo'shing, keyin faqat «17. Javob: C» deb yozing.\n• 33–35 kabi A–F li umumiy variantlar eng oxirida:\nUmumiy variantlar: javobni tanlang\nA) …\nF) …\n33. Savol\nJavob: B\n• Izoh va manba (ixtiyoriy): to'g'ri javobdan keyin «Izoh: nega bu javob to'g'ri» va «Manba: 8-sinf O'zbekiston tarixi, 52-bet» qatorlari. O'quvchi ularni imtihon yopilgach tahlilda ko'radi. Izohda harf (A, B…) emas, javobning o'zini yozing — variantlar aralashtiriladi.`,
    o: `Ochiq savollarni yuboring (${NO} ta, ${NC + 1}–${NC + NO}). Har birida a) va b) qismi bor. Savollar --- qatori bilan ajratiladi.\n\nSavol matni\na) birinchi qism => javob1 | javob2\nb) ikkinchi qism => javob\n\n| — muqobil to'g'ri javoblar.\n• Izoh va manba (ixtiyoriy): savol blokining oxirida «Izoh: …» va «Manba: …» qatorlari.`,
    a: `Hamma savolni (1–${NC + NO}) bitta fayl qilib yuboring: .docx, .txt yoki .xlsx. Matnni to'g'ridan-to'g'ri yozsangiz ham bo'ladi. Rasmlar Word ichida turishi mumkin.\n\nTuzilishi: avval ${NC} ta yopiq savol, keyin «OCHIQ SAVOLLAR» deb yozilgan qator, keyin ${NO} ta ochiq savol.\n\n1. Savol matni\nA) variant\nB) variant\nC) variant\nD) variant\nJavob: B\n\n… (${NC} gacha)\n\nOCHIQ SAVOLLAR\n\n36. Savol matni\na) birinchi qism => javob1 | javob2\nb) ikkinchi qism => javob\n---\n37. …\n\n• Ochiq savollar --- qatori bilan ajratiladi, | — muqobil to'g'ri javoblar.\n• Rasmli savol va 33–35 kabi A–F li umumiy variantlar qoidasi yopiq savollarniki kabi.\n• Fayl 15 MB dan oshmasin; katta bo'lsa 2 qismga bo'lib qo'shing (ketma-ket qo'shiladi).\n• Izoh va manba (ixtiyoriy): to'g'ri javobdan keyin «Izoh: nega bu javob to'g'ri» va «Manba: 8-sinf O'zbekiston tarixi, 52-bet» qatorlari. O'quvchi ularni imtihon yopilgach tahlilda ko'radi. Izohda harf (A, B…) emas, javobning o'zini yozing — variantlar aralashtiriladi.\n\n🆕 55 talik tayyor test fayli ham bo'ladi: 1–55 hammasi oddiy test (A–D, to'g'ri javob ++ yoki «Javob: B»). Tizim 1–35 ni yopiq savol qiladi, 36–55 ni ochiq savolga aylantiradi: o'quvchi javobni o'zi yozadi, to'g'ri variant matni — to'g'ri javob.\n• To'g'ri javob bir nechta bo'lsa (36–55): «Qabul: Samarqand | Qarshi | Termiz» qatori — istalgani to'g'ri hisoblanadi.`,
  };
  const STAGE_TITLE = { c: "Yopiq savollar", o: "Ochiq savollar", a: "Hammasi bitta fayldan" };
  let D = { a: null, c: null, o: null, buf: { c: [], o: [], a: [] } };   /* c/o: tekshirilgan qismlar (yoki null) */
  function loadDraft() {
    try { const x = JSON.parse(localStorage.getItem(KEY) || "null"); if (x && x.buf) D = { a: x.a || null, c: x.c || null, o: x.o || null, no: x.no || null, buf: Object.assign({ c: [], o: [], a: [] }, x.buf) }; } catch (e) {}
  }
  function saveDraft() { try { localStorage.setItem(KEY, JSON.stringify(D)); } catch (e) {} }
  const upload = async (url, file, headers) => {
    const res = await fetch(url, { method: "POST", headers: Object.assign({ "X-Init-Data": (window.Telegram && Telegram.WebApp && Telegram.WebApp.initData) || "" }, headers || {}), body: file });
    let d = null;
    try { d = await res.json(); } catch (e) {}
    if (!res.ok) throw new Error((d && d.error) || "Yuklanmadi");
    return d;
  };
  const lines = (arr) => arr.join("\n").split("\n").filter((x) => x.trim()).length;
  function mlyHome(note, warnings) {
    if (typeof note !== "string") { note = null; warnings = null; }   /* orqaga tugmasi hodisa (event) uzatadi */
    loadDraft();
    pollTok++;
    const cOk = !!D.c || !!D.a, oOk = !!D.o || !!D.a;
    const ready = cOk && oOk;
    const stepLine = (n, label, ok, wait_) => el("div", { class: "hint", style: "margin:4px 0" }, `${n} ${label}: ` + (ok ? "✅ yuklandi" : wait_));
    const nodes = [el("div", { class: "card" }, el("b", {}, "🏅 Milliy sertifikat imtihonini yig'ish"),
      stepLine("1️⃣", `Yopiq savollar (1–${NC})`, cOk, "❌ hali yuklanmagan"),
      stepLine("2️⃣", D.no === 20 ? `Ochiq savollar (${NC + 1}–${NC + 20}, har biri bitta javobli)` : `Ochiq savollar (${NC + 1}–${NC + NO}, har birida a va b)`, oOk, "❌ hali yuklanmagan"),
      stepLine("3️⃣", "Birlashtirib o'quvchilarga taqdim etish", ready, "⏳ avval 1 va 2-bosqichni tugating")),
      btn("", "📄 Hammasini bitta fayldan yuklash (1–45 yoki 1–55)", () => collect("a")),
      btn("alt", "🗂 Tayyor variantlar (zip: 26 ta variant va h.k.)", () => variants()),
      btn("alt", (D.c ? "♻️ " : "1️⃣ ") + "Yopiq savollarni yuklash", () => collect("c")),
      btn("alt", (D.o ? "♻️ " : "2️⃣ ") + "Ochiq savollarni yuklash", () => collect("o")),
      btn(ready ? "" : "alt", (ready ? "✅ " : "3️⃣ ") + "Birlashtirish va taqdim etish", () => {
        if (!ready) return toast(`Avval 1-bosqich (yopiq ${NC} ta) va 2-bosqich (ochiq ${NO} ta) tugashi kerak.`);
        const no = D.no || NO;
        W = { mode: "milliy", c: NC, o: no, pick: [], sname: "Milliy sertifikat", ac: NC, ao: no, tid: null, tname: null, fixed: D.a ? { a: D.a } : { c: D.c, o: D.o } };
        afterSource();
      })];
    if (note) nodes.unshift(good(note + ((warnings || []).length ? "\n\nOgohlantirish:\n" + warnings.slice(0, 6).map((x) => "• " + x).join("\n") : "")));
    if (D.c || D.o || D.a || D.buf.c.length || D.buf.o.length || D.buf.a.length) nodes.push(btn("alt", "🗑 Qoralamani o'chirish", () => confirmSheet("Qoralama o'chirilsinmi?",
      "Yuklangan savollar o'chiriladi (bu qurilmadagi qoralama).", "Ha, o'chirish", () => { D = { a: null, c: null, o: null, buf: { c: [], o: [], a: [] } }; saveDraft(); toast("O'chirildi"); mlyHome(); })));
    screen("Milliy sertifikatni yig'ish", "", home, ...nodes);
  }
  function collect(stage, msg) {
    loadDraft();
    const buf = D.buf[stage];
    const ta = el("textarea", { class: "input", rows: "8", style: "width:100%;margin:8px 0", placeholder: "Savollarni shu yerga yozing yoki joylang, so'ng «Qo'shish» ni bosing" });
    const info = el("div", { class: "hint", style: "margin:6px 0" }, buf.length ? `📎 Saqlangan: ${buf.length} qism, ${lines(buf)} qator.` : "Hali hech narsa qo'shilmagan.");
    const out = el("div", {});
    const refresh = () => { saveDraft(); collect(stage); };
    const add = (chunk, note) => { buf.push(chunk); saveDraft(); toast(note || "Qabul qilindi"); collect(stage); };
    const file = el("input", { type: "file", accept: ".txt,.docx,.xlsx", style: "display:none" });
    const pic = el("input", { type: "file", accept: "image/*", style: "display:none" });
    file.addEventListener("change", async () => {
      const f = file.files[0]; if (!f) return;
      try {
        const d = await upload("/api/import/extract", f, { "X-Filename": encodeURIComponent(f.name) });
        add(d.text, (d.notes && d.notes[0]) || "Fayl qabul qilindi");
      } catch (e) { toast(e.message); }
      file.value = "";
    });
    pic.addEventListener("change", async () => {
      const f = pic.files[0]; if (!f) return;
      try {
        const d = await upload("/api/import/image", f);
        const cap = ta.value.trim();
        add(`[rasm:${d.name}]` + (cap ? "\n" + cap : ""), cap ? "Rasm va matn qo'shildi" : "Rasm qo'shildi: u keyingi yozgan savolingizga biriktiriladi");
      } catch (e) { toast(e.message); }
      pic.value = "";
    });
    const nodes = [];
    if (msg) nodes.push(msg);
    nodes.push(el("div", { class: "card" }, el("div", { class: "hint", style: "white-space:pre-wrap" }, HELP[stage])), info, ta,
      btn("alt", "➕ Matnni qo'shish", () => { const v = ta.value; if (!v.trim()) return toast("Avval matn yozing."); add(v); }),
      btn("alt", "📎 Fayl tanlash (.txt / .docx / .xlsx)", () => file.click()),
      btn("alt", "🖼 Rasm qo'shish (matn yozgan bo'lsangiz, rasmning ostiga yoziladi)", () => pic.click()), file, pic, out);
    {
      nodes.push(btn("", "✅ Tayyor — tekshirish", async () => {
        if (busy) return;
        const pending = ta.value.trim();
        if (pending) { buf.push(pending); saveDraft(); }
        if (!buf.length) return toast("Avval savollarni kiriting.");
        busy = true;
        let r;
        try { r = await post("/api/mly/check", { stage, chunks: buf }); } catch (e) { busy = false; return toast(e.message); }
        busy = false;
        if (r.ok) {
          const keep = buf.slice();
          if (stage === "a") { D.a = keep; D.c = D.o = null; D.buf.a = []; D.no = r.open; }
          else { D[stage] = keep; D.a = null; D.buf[stage] = []; if (stage === "o") D.no = r.open; }
          saveDraft();
          const oRange = `${NC + 1}–${NC + (r.open || NO)}`;
          const done = stage === "c" ? `✅ Yopiq savollar saqlandi: ${r.closed} ta (1–${NC}).` : stage === "o" ? `✅ Ochiq savollar saqlandi: ${r.open} ta (${oRange}).` : `✅ Hammasi saqlandi: yopiq ${r.closed} ta (1–${NC}), ochiq ${r.open} ta (${oRange})` + (r.open === 20 ? ", testdan yozma javobga aylantirildi." : ".");
          toast(done);
          return mlyHome(done, r.warnings);
        }
        out.replaceChildren(el("div", { class: "card" }, el("b", {}, `⚠️ Saqlanmadi — ${r.errors.length} ta muammo`),
          el("div", { class: "pre", style: "margin-top:6px;color:var(--r)" }, r.errors.slice(0, 12).map((x) => "• " + x).join("\n")),
          r.errors.length > 12 ? el("div", { class: "hint" }, `… va yana ${r.errors.length - 12} ta.`) : null,
          el("div", { class: "hint", style: "margin-top:6px" }, "Yetishmaganini yana qo'shishingiz mumkin (oldingilari saqlanib turadi) yoki tozalab boshidan boshlang.")));
      }));
      if (buf.length) nodes.push(btn("alt", "🗑 Yuborganlarimni tozalash", () => { D.buf[stage] = []; saveDraft(); toast("Tozalandi"); collect(stage); }));
    }
    screen(STAGE_TITLE[stage], "", mlyHome, ...nodes);
  }

  /* ---------- Tayyor variantlar (zip yoki fayl) — botdagi «🗂 Tayyor variantlar» bilan bir xil servis ---------- */
  const MAX_ZIP = 60 * 1024 * 1024;
  async function variants(report) {
    pollTok++;
    wait();
    let d;
    try { d = await api("/api/mlv/list"); } catch (e) { return fail(e, mlyHome); }
    const pick = el("input", { type: "file", accept: ".zip,.docx,.txt,.xlsx", style: "display:none" });
    pick.addEventListener("change", async () => {
      const f = pick.files[0]; pick.value = ""; if (!f) return;
      if (f.size > MAX_ZIP) return toast("Fayl 60 MB dan katta. 2 ta zipga bo'lib yuklang.");
      if (busy) return;
      busy = true;
      show({ focus: false }, el("div", { class: "empty pre" }, `⏳ «${f.name}» tekshirilmoqda…
Ko'p variantli zip 10–60 soniya olishi mumkin.`));
      let r;
      try { r = await upload("/api/mlv/upload", f, { "X-Filename": encodeURIComponent(f.name) }); }
      catch (e) { busy = false; toast(e.message); return variants(); }
      busy = false;
      variants(r);
    });
    const nodes = [];
    if (report) {
      const bad = report.items.filter((x) => !x.ok);
      const warn = [];
      for (const x of report.items) if (x.ok) for (const w of x.warnings.slice(0, 1)) warn.push(`${x.title}: ${w}`);
      nodes.push(el("div", { class: "card" }, el("b", {}, bad.length ? "⚠️ Yuklash natijasi" : "✅ Variantlar yuklandi"),
        el("div", { class: "pre", style: "margin-top:6px" }, report.lines.join("\n")),
        warn.length ? el("div", { class: "pre hint", style: "margin-top:6px" }, "Ogohlantirishlar (saqlandi, lekin e'tibor bering):\n" + warn.slice(0, 6).map((x) => "• " + x).join("\n")) : null));
    }
    nodes.push(el("div", { class: "card hint pre" }, "Zip yuklang: ichida har bir variant alohida fayl (.docx, .txt yoki .xlsx). Bitta fayl ham bo'ladi.\n\n• Har bir fayl — bitta variant: 55 ta test (1–35 yopiq, 36–55 yozma javobga aylanadi) yoki 1–45 (OCHIQ SAVOLLAR bilan).\n• To'g'ri javob variant oxirida ++ yoki «Javob: B».\n• Fayl nomi — variant nomi. Shu nomli variant qayta yuklansa, yangilanadi.\n• Xato fayl saqlanmaydi, qolganlari saqlanadi. Zip 60 MB gacha.\n• Izoh va manba (ixtiyoriy): har savolning to'g'ri javobidan keyin «Izoh: …» va «Manba: …» qatorlari — o'quvchi imtihon yopilgach tahlilda ko'radi.\n• 36–55 da to'g'ri javob bir nechta bo'lsa: «Qabul: Samarqand | Qarshi | Termiz» — istalgani qabul qilinadi."),
      btn("", "📦 Zip yoki fayl yuklash", () => pick.click()), pick);
    if (!d.items.length) nodes.push(empty("Hali variant yo'q."));
    else nodes.push(el("div", { class: "hint", style: "margin:8px 0" }, `Jami: ${d.items.length} ta variant. Variantni tanlang — undan imtihon ochasiz. ✅ — shu variantdan imtihon ochilgan.`));
    for (const v of d.items) nodes.push(el("div", { class: "card tap", onclick: () => variantView(v.id) }, el("div", { class: "row" },
      el("div", { class: "grow" }, el("b", {}, `${v.used ? "✅" : "📄"} ${v.title}`), el("div", { class: "hint" }, `${v.closed} yopiq · ${v.open} yozma${v.img ? ` · rasmli: ${v.img}` : ""}${v.used ? ` · ochilgan: ${v.used}` : ""}`)), icon("chev"))));
    if (d.items.length && d.admin) nodes.push(btn("alt", "🗑 Hamma variantlarni o'chirish", () => confirmSheet("Hamma variantlar o'chirilsinmi?",
      "Ulardan ochilgan imtihonlar va natijalar o'chmaydi.", "Ha, hammasini o'chirish", async () => {
        try { await post("/api/mlv/delete_all"); toast("O'chirildi"); } catch (e) { toast(e.message); }
        variants();
      })));
    screen("Tayyor variantlar", "Milliy sertifikat", mlyHome, ...nodes);
  }
  async function variantView(id) {
    wait();
    let v;
    try { v = await api(`/api/mlv/${id}`); } catch (e) { return fail(e, () => variants()); }
    const nodes = [el("div", { class: "card" }, el("b", {}, "📄 " + v.title),
      stat("Yopiq savollar", v.closed), stat("Yozma savollar", v.open), v.img ? stat("Rasmli", v.img) : null,
      stat("Yuklangan", v.updated), stat("Ochilgan imtihonlar", v.used),
      v.first ? el("div", { class: "hint", style: "margin-top:6px" }, "1-savol: " + (v.first.length > 160 ? v.first.slice(0, 159) + "…" : v.first)) : null),
      btn("", "🚀 Imtihon qilib ochish", () => {
        W = { mode: "milliy", c: v.closed, o: v.open, pick: [], sname: "Milliy sertifikat", ac: v.closed, ao: v.open, tid: null, tname: null,
          fixed: { v: v.id }, vid: v.id, vtitle: v.title, vdef: /milliy/i.test(v.title) ? v.title : `Milliy sertifikat — ${v.title}` };
        afterSource();
      })];
    if (v.can_del) nodes.push(btn("alt", "🗑 O'chirish", () => confirmSheet("Variant o'chirilsinmi?", `📄 ${v.title}\n\nUndan ochilgan imtihonlar va natijalar o'chmaydi.`, "Ha, o'chirish", async () => {
      try { await post(`/api/mlv/delete/${id}`); toast("O'chirildi"); } catch (e) { toast(e.message); }
      variants();
    })));
    screen("Variant", "", () => variants(), ...nodes);
  }

  OQ.adminExam = home;
})();
