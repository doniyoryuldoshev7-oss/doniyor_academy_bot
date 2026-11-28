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
    if (!ME.admin && !ME.editor && !ME.teacher) { toast("Bu bo'lim faqat adminlar, savol qo'shuvchilar va ustozlar uchun."); return home(); }
    const t = [];
    if (ME.teacher) t.push(menuTile("users", "o", "Ustoz paneli", "Guruhlar va o'quvchilar", () => (OQ.teacherPanel ? OQ.teacherPanel(admin) : soon("Ustoz paneli"))));
    if (ME.admin) t.push(menuTile("book", "b", "Fanlar", "Fan, mavzu, savollar", fanlar));
    if (ME.admin || ME.editor) t.push(menuTile("list", "o", "Savol qo'shish", "Bitta yoki ko'p savol", savolHub));
    if (ME.admin || ME.teacher) t.push(menuTile("trophy", "p", "Milliy sertifikat imtihoni", "Imtihon yaratish va natijalar", () => (OQ.adminExam ? OQ.adminExam(admin) : soon("Milliy sertifikat imtihoni"))));
    if (ME.admin) t.push(menuTile("upload", "g", "Export / Import", "Excel, CSV, Word", eximHub));
    if (ME.admin) t.push(menuTile("chart", "p", "E'lonlar", "Hammaga xabar yuborish", () => (OQ.adminAds ? OQ.adminAds(admin) : soon("E'lonlar"))));
    if (ME.admin) t.push(menuTile("clock", "o", "Eslatmalar", "Eslatma sozlamalari", () => (OQ.adminReminders ? OQ.adminReminders(admin) : soon("Eslatmalar"))));
    if (ME.admin) t.push(menuTile("users", "g", "Ustozlar", "Ustozlarni boshqarish", () => (OQ.adminTeachers ? OQ.adminTeachers(admin) : soon("Ustozlar"))));
    if (ME.admin || ME.teacher) t.push(menuTile("list", "b", "Ustoz topshirig'i", "Topshiriqlar va natijalari", () => (OQ.adminAssign ? OQ.adminAssign(admin) : soon("Ustoz topshirig'i"))));
    if (ME.admin) t.push(menuTile("users", "b", "Foydalanuvchilar", "Ro'yxat, tasdiqlash, bloklash", () => (OQ.adminUsers ? OQ.adminUsers(true) : soon("Foydalanuvchilar"))));
    if (ME.admin) t.push(menuTile("chart", "o", "O'quvchilar natijasi", "Hisobotlar va tarix", () => (OQ.adminReports ? OQ.adminReports(admin) : soon("O'quvchilar natijasi"))));
    if (ME.admin) t.push(menuTile("chart", "p", "Savollar sifati", "Shubhali va qiyin savollar", () => (OQ.quality ? OQ.quality(admin) : soon("Savollar sifati"))));
    if (ME.admin) t.push(menuTile("chart", "g", "Statistika", "Tizim statistikasi", () => (OQ.adminStats ? OQ.adminStats() : soon("Statistika"))));
    screen(ME.admin || ME.editor ? "Admin panel" : "Ustoz bo'limi", "Kerakli bo'limni tanlang:", home, tiles(...t));
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
    if (q.mode === "closed") {
      nodes.push(el("div", { class: "hint", style: "margin-top:10px" }, "🌐 Tarjimalar (o'quvchi tilini tanlasa, shu tilda ko'radi):"));
      for (const [lang, label] of [["ru", "🇷🇺 Русский"], ["en", "🇬🇧 English"]]) {
        nodes.push(btn("alt", label + (q.tr && q.tr[lang] ? "  ✓" : "  — yo'q"), () => trForm(q, lang, () => questionView(qid, back)), "gear"));
      }
    }
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
      if (!e && imgName) body.image = imgName;
      try {
        if (e) { body.id = e.id; await api("/api/g/question_edit", { method: "POST", body: JSON.stringify(body) }); toast("Savol saqlandi"); (c.back)(); }
        else {
          body.topic_id = c.topic_id; await api("/api/g/question_add", { method: "POST", body: JSON.stringify(body) });
          doneAdd(c);
        }
      } catch (err) { toast(err.message); } finally { busy = false; }
    };
    const nodes = [];
    /* Yangi savolga rasm (botdagi «Bitta savol qo'shish» kabi): savol matni va variantlar rasmda bo'lsa, maydonlarni bo'sh qoldiring */
    let imgName = "";
    const imgBox = el("div", {});
    const pick = el("input", { type: "file", accept: "image/*", style: "display:none" });
    const paintImg = () => imgBox.replaceChildren(imgName
      ? el("div", { class: "card" }, el("div", { class: "hint" }, "🖼 Rasm qo'shildi. Savol va variantlar rasmda bo'lsa, quyidagi maydonlarni bo'sh qoldiring — faqat to'g'ri javobni tanlang."),
          btn("alt", "Rasmni olib tashlash", () => { imgName = ""; paintImg(); }, "alert"))
      : btn("alt", "🖼 Rasm qo'shish (ixtiyoriy)", () => pick.click(), "upload"));
    pick.addEventListener("change", async () => {
      const f = pick.files[0]; if (!f) return;
      try {
        const res = await fetch("/api/import/image", { method: "POST", headers: { "X-Init-Data": (window.Telegram && Telegram.WebApp && Telegram.WebApp.initData) || "" }, body: f });
        const d = await res.json().catch(() => null);
        if (!res.ok) throw new Error((d && d.error) || "Rasm yuklanmadi");
        imgName = d.name; paintImg(); toast("Rasm qo'shildi");
      } catch (err) { toast(err.message); }
      pick.value = "";
    });
    if (!e) { paintImg(); nodes.push(imgBox, pick); }
    if (imageOnly) nodes.push(el("div", { class: "hint" }, "Rasmli savol: faqat to'g'ri javob va izohni o'zgartirish mumkin."));
    else nodes.push(text, ...["a", "b", "c", "d"].map((l) => opt[l]));
    nodes.push(el("div", { class: "hint", style: "margin-top:8px" }, "To'g'ri javob:"), chips);
    nodes.push(expl);
    nodes.push(btn("", e ? "Saqlash" : "Savolni saqlash", save, "check"));
    screen(e ? "Savolni tahrirlash" : "Yangi savol", c.topicName ? `Mavzu: ${c.topicName}` : null, c.back, ...nodes);
  }

  /* ---------- savol tarjimasi (ru / en) ---------- */
  function trForm(q, lang, back) {
    const cur = (q.tr && q.tr[lang]) || {};
    const name = lang === "ru" ? "Русский" : "English";
    const text = taField(cur.text, "Savol matni (" + name + ")", 100);
    const opt = {};
    for (const l of "abcd") opt[l] = inField(cur[l], `${l.toUpperCase()} variant`);
    const expl = taField(cur.explanation, "Izoh (ixtiyoriy)", 70);
    let busy = false;
    const send = async (clear) => {
      if (busy) return; busy = true;
      const body = { id: q.id, lang, text: clear ? "" : text.value, explanation: clear ? "" : expl.value };
      for (const l of "abcd") body[l] = clear ? "" : opt[l].value;
      try {
        await api("/api/g/question_tr", { method: "POST", body: JSON.stringify(body) });
        toast(clear ? "Tarjima o'chirildi" : "Tarjima saqlandi");
        q.tr = q.tr || {};
        if (clear) delete q.tr[lang]; else q.tr[lang] = { text: body.text, a: body.a, b: body.b, c: body.c, d: body.d, explanation: body.explanation };
        back();
      } catch (err) { toast(err.message); } finally { busy = false; }
    };
    const nodes = [el("div", { class: "hint" }, "Asl savolning ma'nosi va variantlar tartibi bir xil bo'lsin: to'g'ri javob harfi o'zgarmaydi (" + q.correct + ")."),
      el("div", { class: "card pre" }, q.text || ""), text, ...["a", "b", "c", "d"].map((l) => opt[l]), expl,
      btn("", "Saqlash", () => send(false), "check")];
    if (cur.text) nodes.push(btn("alt", "Tarjimani o'chirish", () => send(true), "alert"));
    screen("Tarjima: " + name, null, back, ...nodes);
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
      menuTile("list", "g", "Ustunli jadval import", "Fan | Mavzu | Savol rasmi | Savol turi…", tableImport),
      menuTile("book", "o", "Word eksport", "Javobli yoki javobsiz", () => (OQ.wordExport ? OQ.wordExport(eximHub) : soon("Word eksport")))));
  }
  /* Botdagi «📥 Excel/CSV import» bilan bir xil: ustunli jadval (rasm nomi, ochiq savol, to'g'ri javob matni) */
  function tableImport() {
    const pick = el("input", { type: "file", accept: ".xlsx,.csv", style: "display:none" });
    const out = el("div", {});
    let file = null;
    const send = async (dry) => {
      const res = await fetch("/api/g/table_import" + (dry ? "?dry=1" : ""), { method: "POST",
        headers: { "X-Init-Data": (window.Telegram && Telegram.WebApp && Telegram.WebApp.initData) || "", "X-Filename": encodeURIComponent(file.name) }, body: file });
      const d = await res.json().catch(() => null);
      if (!res.ok) throw new Error((d && d.error) || "Import bajarilmadi");
      return d;
    };
    pick.addEventListener("change", async () => {
      file = pick.files[0]; pick.value = ""; if (!file) return;
      let d;
      try { d = await send(true); } catch (e) { return toast(e.message); }
      const info = `Fayl: ${file.name}\nSavollar: ${d.count} ta (ochiq: ${d.open}, rasmli: ${d.images})\nFanlar: ${d.subjects.join(", ") || "—"}`;
      if (!d.ok) return out.replaceChildren(el("div", { class: "card" }, el("b", {}, d.error_count ? `⚠️ Faylda ${d.error_count} ta xato — import bajarilmaydi` : "Import qilinadigan savol topilmadi"),
        el("div", { class: "pre", style: "margin-top:6px" }, info), el("div", { class: "pre", style: "margin-top:6px;color:var(--r)" }, d.errors.map((x) => "• " + x).join("\n"))));
      out.replaceChildren(el("div", { class: "card" }, el("b", {}, "✅ Fayl to'g'ri"), el("div", { class: "pre", style: "margin-top:6px" }, info),
        btn("", `Saqlash (${d.count} ta savol)`, async () => {
          try { const r = await send(false); toast(`Qo'shildi: ${r.added} ta savol`); out.replaceChildren(el("div", { class: "card" }, el("b", {}, `✅ Import muvaffaqiyatli: ${r.added} ta savol qo'shildi`), el("div", { class: "hint" }, "Fan va mavzular avtomatik bog'landi."))); }
          catch (e) { toast(e.message); }
        }, "check")));
    });
    screen("Ustunli jadval import", null, eximHub,
      el("div", { class: "card pre hint" }, "Faylni .xlsx yoki .csv qilib tanlang.\n\nUstunlar:\nFan | Mavzu | Savol | Savol rasmi | A | B | C | D | Savol turi | To'g'ri javob | To'g'ri javob matni | Izoh\n\n• Savol turi: closed (A–D) yoki open (ochiq savol: «To'g'ri javob matni» majburiy).\n• Savol rasmi: serverdagi rasm fayli nomi.\n• Avval tekshiriladi, xato bo'lsa hech narsa saqlanmaydi."),
      btn("", "📎 Fayl tanlash (.xlsx / .csv)", () => pick.click(), "upload"), pick, out);
  }

  OQ.admin = admin;
  OQ.adminQuestion = questionView;
})();
