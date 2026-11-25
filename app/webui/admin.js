/* Veb-ilova: admin paneli, 1-bosqich — Fanlar va Savol qo'shish (botdagi admin amallari bilan bir xil).
   oquvchi.js (window.OQ.u) dagi umumiy qismlar ishlatiladi. Har ekranda orqaga tugmasi bor. */
(function () {
  "use strict";
  const OQ = window.OQ;
  if (!OQ || !OQ.u || OQ.admin) return;
  const { screen, tiles, stat, empty, wait, good, bad } = OQ.u;

  let ME = { admin: false, editor: false };
  const home = () => openTab("home");
  const fail = (e, back) => { toast(e.message); if (back) back(); };
  const btn = (cls, label, fn, ic) => el("button", { class: "btn " + cls, onclick: fn }, ic ? icon(ic) : null, label);
  const taField = (value, ph, h) => { const t = el("textarea", { class: "imp", placeholder: ph, style: `height:${h || 90}px;font-family:inherit;font-size:15px` }); t.value = value || ""; return t; };
  const inField = (value, ph) => el("input", { class: "ans", type: "text", maxlength: "255", placeholder: ph, value: value || "", autocomplete: "off", style: "margin:6px 0" });

  /* ---------- bosh admin menyu ---------- */
  async function admin() {
    wait();
    try { ME = await api("/api/g/me"); } catch (e) { return fail(e, home); }
    if (!ME.admin && !ME.editor) { toast("Admin paneli faqat adminlar va savol qo'shuvchilar uchun."); return home(); }
    const t = [];
    if (ME.admin) t.push(menuTile("book", "b", "Fanlar", "Fan, mavzu, savollar", fanlar));
    t.push(menuTile("list", "o", "Savol qo'shish", "Bitta yoki ko'p savol", savolHub));
    if (ME.admin) t.push(menuTile("upload", "g", "Export / Import", "Excel, CSV, Word", eximHub));
    if (ME.admin) t.push(menuTile("chart", "p", "Savollar sifati", "Shubhali va qiyin savollar", () => (OQ.quality ? OQ.quality(admin) : soon("Savollar sifati"))));
    screen("Admin panel", "Kerakli bo'limni tanlang:", home, tiles(...t));
  }

  /* ---------- Fanlar ---------- */
  function fanlar() {
    if (!ME.admin) return admin();
    screen("Fanlar", "Kerakli amalni tanlang:", admin, tiles(
      menuTile("book", "b", "Fan qo'shish", "Yangi fan yaratish", fanForm),
      menuTile("list", "o", "Mavzu qo'shish", "Fanga mavzu qo'shish", () => pickSubject("addtopic")),
      menuTile("alert", "p", "Fanni o'chirish", "Mavzu va savollari bilan", () => pickSubject("delete")),
      menuTile("map", "g", "Fanlar ro'yxati", "Fanlar va mavzular", () => pickSubject("catalog"))));
  }

  function fanForm() {
    const inp = inField("", "Masalan: O'zbekiston tarixi");
    let busy = false;
    const save = async () => {
      if (busy) return; busy = true;
      try { const r = await api("/api/g/subject_add", { method: "POST", body: JSON.stringify({ name: inp.value }) }); toast(`Fan qo'shildi: ${r.name}`); fanlar(); }
      catch (e) { toast(e.message); } finally { busy = false; }
    };
    screen("Yangi fan", "Fan nomini kiriting:", fanlar, inp, btn("", "Qo'shish", save, "check"));
  }

  async function pickSubject(mode) {
    wait();
    let d;
    try { d = await api("/api/g/subjects"); } catch (e) { return fail(e, fanlar); }
    const sub = { addtopic: "Qaysi fanga mavzu qo'shamiz?", delete: "O'chiriladigan fanni tanlang:", catalog: "Fanni tanlang:" }[mode];
    const nodes = d.subjects.length ? d.subjects.map((s) => el("div", { class: "card tap", onclick: () => {
      if (mode === "addtopic") topicForm(s.id, s.name, () => pickSubject("addtopic"));
      else if (mode === "delete") askDeleteSubject(s);
      else subjectView(s.id);
    } }, el("div", { class: "row" }, el("div", { class: "grow" }, el("b", {}, s.name), el("div", { class: "hint" }, `${s.topics} mavzu · ${s.questions} savol`)), icon("chev"))))
      : [empty("Hozircha fanlar yo'q.")];
    screen(mode === "delete" ? "Fanni o'chirish" : mode === "addtopic" ? "Mavzu qo'shish" : "Fanlar ro'yxati", sub, fanlar, ...nodes);
  }

  function askDeleteSubject(s) {
    confirmSheet("Fanni o'chirasizmi?",
      `⚠️ ${s.name}\n\nMavzular: ${s.topics} ta\nSavollar: ${s.questions} ta\n\nDIQQAT! Fanga tegishli barcha mavzular va savollar ham o'chiriladi. Bu amalni qaytarib bo'lmaydi.`,
      "Ha, o'chirish", async () => {
        try { const r = await api("/api/g/subject_delete", { method: "POST", body: JSON.stringify({ id: s.id }) }); toast(`Fan o'chirildi: ${r.name}`); fanlar(); }
        catch (e) { toast(e.message); }
      });
  }

  /* ---------- fan -> mavzular ---------- */
  async function subjectView(sid) {
    wait();
    let d;
    try { d = await api(`/api/g/subject/${sid}`); } catch (e) { return fail(e, () => pickSubject("catalog")); }
    const nodes = d.topics.length ? d.topics.map((t) => el("div", { class: "card tap", onclick: () => topicView(t.id) },
      el("div", { class: "row" }, el("div", { class: "grow" }, el("b", {}, t.name)), el("span", { class: "tag" }, `${t.questions} ta`), icon("chev"))))
      : [empty("Bu fanda hali mavzu yo'q.")];
    nodes.push(btn("", "Mavzu qo'shish", () => topicForm(sid, d.name, () => subjectView(sid)), "list"));
    nodes.push(btn("alt", "Fanni o'chirish", () => askDeleteSubject({ id: sid, name: d.name, topics: d.topics.length, questions: d.topics.reduce((a, t) => a + t.questions, 0) }), "alert"));
    screen(d.name, `Mavzular: ${d.topics.length} ta`, () => pickSubject("catalog"), ...nodes);
  }

  function topicForm(sid, subjectName, back) {
    const inp = inField("", "Mavzu nomi");
    let busy = false;
    const save = async () => {
      if (busy) return; busy = true;
      try { const r = await api("/api/g/topic_add", { method: "POST", body: JSON.stringify({ subject_id: sid, name: inp.value }) }); toast(`${r.subject}: «${r.name}» mavzusi qo'shildi`); back(); }
      catch (e) { toast(e.message); } finally { busy = false; }
    };
    screen("Yangi mavzu", `Fan: ${subjectName}`, back, inp, btn("", "Qo'shish", save, "check"));
  }

  /* ---------- mavzu ---------- */
  async function topicView(tid) {
    wait();
    let d;
    try { d = await api(`/api/g/questions/${tid}`); } catch (e) { return fail(e, fanlar); }
    const back = () => subjectView(d.subject_id);
    screen(d.name, `Savollar: ${d.total} ta`, back,
      tiles(
        menuTile("list", "b", "Savollar", "Ro'yxat va tahrirlash", () => questionsView(tid)),
        menuTile("check", "g", "Savol qo'shish", "Shu mavzuga", () => questionForm({ topic_id: tid, topicName: d.name, back: () => topicView(tid) })),
        menuTile("gear", "o", "Nomini o'zgartirish", "Mavzu nomi", () => renameTopic(tid, d.name, () => topicView(tid))),
        menuTile("alert", "p", "Mavzuni o'chirish", "Savollari bilan", () => askDeleteTopic(tid, d))));
  }

  function renameTopic(tid, old, back) {
    const inp = inField(old, "Yangi nom");
    let busy = false;
    const save = async () => {
      if (busy) return; busy = true;
      try { await api("/api/g/topic_rename", { method: "POST", body: JSON.stringify({ id: tid, name: inp.value }) }); toast("Mavzu nomi o'zgartirildi"); topicView(tid); }
      catch (e) { toast(e.message); } finally { busy = false; }
    };
    screen("Mavzuni tahrirlash", `Eski nom: ${old}`, back, inp, btn("", "Saqlash", save, "check"));
  }

  function askDeleteTopic(tid, d) {
    confirmSheet("Mavzuni o'chirasizmi?", `⚠️ ${d.name}\n\nSavollar: ${d.total} ta. Mavzu va uning barcha savollari o'chiriladi. Bu amalni qaytarib bo'lmaydi.`, "Ha, o'chirish", async () => {
      try { const r = await api("/api/g/topic_delete", { method: "POST", body: JSON.stringify({ id: tid }) }); toast("Mavzu o'chirildi"); subjectView(r.subject_id); }
      catch (e) { toast(e.message); }
    });
  }

  /* ---------- savollar ro'yxati va bitta savol ---------- */
  async function questionsView(tid, page) {
    page = Number.isInteger(page) ? page : 0;
    wait();
    let d;
    try { d = await api(`/api/g/questions/${tid}`); } catch (e) { return fail(e, () => topicView(tid)); }
    const per = 10, pages = Math.max(1, Math.ceil(d.questions.length / per));
    page = Math.min(page, pages - 1);
    const nodes = d.questions.length ? d.questions.slice(page * per, page * per + per).map((q, i) =>
      el("div", { class: "card tap", onclick: () => questionView(q.id, () => questionsView(tid, page)) },
        el("div", { class: "row" }, el("div", { class: "grow pre" }, `${page * per + i + 1}. ${q.t}`), icon("chev"))))
      : [empty("Bu mavzuda hozircha savol yo'q.")];
    if (pages > 1) nodes.push(el("div", { class: "row", style: "margin-top:10px" },
      el("button", { class: "btn alt", disabled: page === 0, onclick: () => questionsView(tid, page - 1) }, icon("left"), "Oldingi"),
      el("button", { class: "btn alt", disabled: page >= pages - 1, onclick: () => questionsView(tid, page + 1) }, "Keyingi", icon("right"))));
    screen(d.name, `Savollar: ${d.total} ta · sahifa ${page + 1}/${pages}`, () => topicView(tid), ...nodes);
  }

  async function questionView(qid, back) {
    wait();
    let q;
    try { q = await api(`/api/g/question/${qid}`); } catch (e) { return fail(e, back); }
    const nodes = [];
    if (q.text) nodes.push(el("div", { class: "card pre" }, q.text));
    if (q.img) nodes.push(OQ.u.qimage(qid));
    if (q.mode === "closed") for (const l of "abcd") nodes.push(el("div", { class: "opt static" + (q.correct === l.toUpperCase() ? " right" : "") }, el("em", {}, l.toUpperCase()), el("span", { class: "pre grow" }, q[l])));
    else nodes.push(el("div", { class: "hint" }, `To'g'ri javob: ${q.correct}`));
    if (q.explanation) nodes.push(good("💡 " + q.explanation));
    nodes.push(btn("", "Tahrirlash", () => questionForm({ edit: q, topic_id: q.topic_id, back: () => questionView(qid, back) }), "gear"));
    nodes.push(btn("alt", "O'chirish", () => confirmSheet("Savolni o'chirasizmi?", "Savol ro'yxatdan olib tashlanadi.", "Ha, o'chirish", async () => {
      try { await api("/api/g/question_delete", { method: "POST", body: JSON.stringify({ id: qid }) }); toast("Savol o'chirildi"); back(); }
      catch (e) { toast(e.message); }
    }), "alert"));
    screen("Savol", null, back, ...nodes);
  }

  /* ---------- savol qo'shish / tahrirlash formasi ---------- */
  function questionForm(c) {
    const e = c.edit, imageOnly = !!e && e.mode !== "closed";
    let correct = e ? e.correct : "";
    const text = taField(e && e.text, "Savol matni", 100);
    const opt = {};
    for (const l of "abcd") opt[l] = inField(e && e[l], `${l.toUpperCase()} variant`);
    const expl = taField(e && e.explanation, "Izoh (ixtiyoriy)", 70);
    const chips = el("div", { class: "row", style: "margin:8px 0" });
    const paint = () => chips.replaceChildren(...["A", "B", "C", "D"].map((l) =>
      el("button", { class: "btn small grow" + (correct === l ? "" : " alt"), onclick: () => { correct = l; paint(); } }, l)));
    paint();
    let busy = false;
    const save = async () => {
      if (busy) return; busy = true;
      const body = { text: text.value, correct, explanation: expl.value };
      for (const l of "abcd") body[l] = opt[l].value;
      try {
        if (e) { body.id = e.id; await api("/api/g/question_edit", { method: "POST", body: JSON.stringify(body) }); toast("Savol saqlandi"); (c.back)(); }
        else {
          body.topic_id = c.topic_id; await api("/api/g/question_add", { method: "POST", body: JSON.stringify(body) });
          doneAdd(c);
        }
      } catch (err) { toast(err.message); } finally { busy = false; }
    };
    const nodes = [];
    if (imageOnly) nodes.push(el("div", { class: "hint" }, "Rasmli savol: faqat to'g'ri javob va izohni o'zgartirish mumkin."));
    else nodes.push(text, ...["a", "b", "c", "d"].map((l) => opt[l]));
    nodes.push(el("div", { class: "hint", style: "margin-top:8px" }, "To'g'ri javob:"), chips);
    nodes.push(expl);
    nodes.push(btn("", e ? "Saqlash" : "Savolni saqlash", save, "check"));
    screen(e ? "Savolni tahrirlash" : "Yangi savol", c.topicName ? `Mavzu: ${c.topicName}` : null, c.back, ...nodes);
  }

  function doneAdd(c) {
    screen("Savol saqlandi ✅", c.topicName ? `Mavzu: ${c.topicName}` : null, c.back,
      good("Savol muvaffaqiyatli qo'shildi."),
      btn("", "Yana savol qo'shish", () => questionForm({ topic_id: c.topic_id, topicName: c.topicName, back: c.back }), "check"),
      btn("alt", "Tayyor", c.back));
  }

  /* ---------- Savol qo'shish bo'limi ---------- */
  function savolHub() {
    const t = [menuTile("check", "b", "Bitta savol qo'shish", "Qadamma-qadam", () => pickTree(null))];
    t.push(menuTile("list", "o", "Bir nechta savol", "Matn, Word, Excel, rasm", () => { S.importBack = savolHub; importScreen(); }));
    screen("Savol qo'shish", "Bitta savolni formadan, ko'p savolni fayl yoki matndan qo'shing.", admin, tiles(...t));
  }

  async function pickTree(sid) {
    wait();
    let d;
    try { d = await api("/api/g/tree"); } catch (e) { return fail(e, savolHub); }
    if (sid === null) {
      const nodes = d.subjects.length ? d.subjects.map((s) => el("div", { class: "card tap", onclick: () => pickTree(s.id) },
        el("div", { class: "row" }, el("div", { class: "grow" }, el("b", {}, s.name), el("div", { class: "hint" }, `${s.topics.length} mavzu`)), icon("chev"))))
        : [empty("Avval fan qo'shing.")];
      return screen("Yangi test savoli", "Fanni tanlang:", savolHub, ...nodes);
    }
    const s = d.subjects.find((x) => x.id === sid);
    if (!s) return pickTree(null);
    const nodes = s.topics.length ? s.topics.map((t) => el("div", { class: "card tap", onclick: () =>
      questionForm({ topic_id: t.id, topicName: `${s.name} · ${t.name}`, back: () => pickTree(sid) }) },
      el("div", { class: "row" }, el("div", { class: "grow" }, el("b", {}, t.name)), icon("chev"))))
      : [empty("Bu fanda hali mavzu yo'q. Avval mavzu qo'shing.")];
    screen(s.name, "Mavzuni tanlang:", () => pickTree(null), ...nodes);
  }

  /* ---------- Export / Import ---------- */
  function eximHub() {
    if (!ME.admin) return admin();
    screen("Export / Import", "Kerakli amalni tanlang:", admin, tiles(
      menuTile("upload", "b", "Excel/CSV import", "Fayldan savol yuklash", () => { S.importBack = eximHub; importScreen(); }),
      menuTile("book", "o", "Word eksport", "Javobli yoki javobsiz", () => (OQ.wordExport ? OQ.wordExport(eximHub) : soon("Word eksport")))));
  }

  OQ.admin = admin;
  OQ.adminQuestion = questionView;
})();
