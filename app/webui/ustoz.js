/* Veb-ilova: Ustozlar (admin) va Ustoz paneli (ustoz). Har ekranda orqaga tugmasi bor. */
(function () {
  "use strict";
  const OQ = window.OQ;
  if (!OQ || !OQ.u || OQ.adminTeachers) return;
  const { screen, empty, wait, good, stat } = OQ.u;
  const fail = (e, back) => { toast(e.message); if (back) back(); };
  const btn = (cls, label, fn, ic) => el("button", { class: "btn " + cls, onclick: fn }, ic ? icon(ic) : null, label);
  const pager = (d, go) => (d.pages > 1 ? el("div", { class: "row", style: "margin-top:8px" },
    d.page > 0 ? btn("alt small grow", "⬅️ Oldingi", () => go(d.page - 1)) : null,
    d.page + 1 < d.pages ? btn("alt small grow", "Keyingi ➡️", () => go(d.page + 1)) : null) : null);
  const search = (value, ph, go) => {
    const inp = el("input", { class: "ans", type: "text", maxlength: "50", placeholder: ph, value: value || "", autocomplete: "off", style: "margin:0" });
    inp.addEventListener("keydown", (ev) => { if (ev.key === "Enter") go(inp.value.trim()); });
    return el("div", { class: "row", style: "gap:6px;margin-bottom:8px" }, inp, btn("small sq", "🔍", () => go(inp.value.trim())));
  };

  /* =============== ADMIN: USTOZLAR =============== */
  let back0 = null;
  const toAdmin = () => (back0 ? back0() : OQ.admin && OQ.admin());

  async function list(back) {
    if (typeof back === "function") back0 = back;
    wait();
    let d;
    try { d = await api("/api/t/list"); } catch (e) { return fail(e, toAdmin); }
    const nodes = [];
    if (!d.items.length) nodes.push(empty("Hali ustoz qo'shilmagan."));
    else nodes.push(el("div", { class: "hint" }, "Ustoz faqat o'ziga biriktirilgan guruhlarning o'quvchilarini ko'radi va shu guruhlarga xabar yubora oladi."));
    for (const t of d.items) nodes.push(el("div", { class: "card tap", onclick: () => teacher(t.id) },
      el("div", { class: "row" }, el("div", { class: "grow" }, el("b", {}, "🧑‍🏫 " + t.name), el("div", { class: "hint" }, `${t.groups} guruh`)), icon("chev"))));
    nodes.push(btn("", "➕ Ustoz qo'shish", () => pick(0, ""), "users"));
    screen("Ustozlar", "", toAdmin, ...nodes);
  }
  async function teacher(id) {
    wait();
    let d;
    try { d = await api(`/api/t/teacher/${id}`); } catch (e) { return fail(e, list); }
    const nodes = [el("div", { class: "card" }, el("b", {}, d.name), d.username ? el("div", { class: "hint" }, "✉️ @" + d.username) : null)];
    nodes.push(d.groups.length ? el("div", { class: "card" }, el("b", {}, "Biriktirilgan guruhlar"), ...d.groups.map((g) => stat(g.name, `${g.count} o'quvchi`)))
      : el("div", { class: "hint" }, "Hali guruh biriktirilmagan."));
    nodes.push(btn("", "👥 Guruhlarni tanlash", () => groups(id)));
    nodes.push(btn("alt", "🗑 Ustozlikdan olish", () => confirmSheet("Ustozlikdan olasizmi?",
      "Bu odam ustozlikdan olinadi va guruhlari bog'lanishi o'chadi. O'quvchi sifatidagi ma'lumotlari saqlanib qoladi.", "Ha, olib tashlash", async () => {
        try { await api("/api/t/remove", { method: "POST", body: JSON.stringify({ id }) }); toast("Ustozlikdan olindi"); } catch (e) { toast(e.message); }
        list();
      })));
    screen("Ustoz", d.name, list, ...nodes);
  }
  async function pick(page, q) {
    wait();
    let d;
    try { d = await api(`/api/t/pick?page=${page}&q=${encodeURIComponent(q)}`); } catch (e) { return fail(e, list); }
    const nodes = [search(q, "Ism yoki @username", (v) => pick(0, v))];
    if (q) nodes.push(btn("alt small", "✖ Qidiruvni tozalash", () => pick(0, "")));
    nodes.push(el("div", { class: "hint", style: "margin:6px 0" }, `Kimni ustoz qilmoqchisiz? Tanlang. Topildi: ${d.total} · Sahifa ${d.page + 1}/${d.pages}`));
    if (!d.items.length) nodes.push(empty(q ? "Mos foydalanuvchi topilmadi." : "Ustoz qilish mumkin bo'lgan foydalanuvchi yo'q."));
    for (const u of d.items) nodes.push(el("div", { class: "card tap", onclick: () => confirmSheet("Ustoz qilasizmi?", `${u.name} ustoz etib tayinlanadi va unga xabar boradi.`, "Ha, ustoz qilish", () => add(u.tg)) },
      el("div", { class: "row" }, el("div", { class: "grow" }, el("b", {}, u.name), el("div", { class: "hint" }, [u.group, u.username && "@" + u.username].filter(Boolean).join(" · "))), icon("chev"))));
    const pg = pager(d, (p) => pick(p, q)); if (pg) nodes.push(pg);
    screen("Ustoz qo'shish", "", list, ...nodes);
  }
  async function add(tg) {
    wait();
    try {
      const r = await api("/api/t/new", { method: "POST", body: JSON.stringify({ tg }) });
      toast(r.created ? "Ustoz qo'shildi" : "Bu odam allaqachon ustoz");
      teacher(r.id);
    } catch (e) { fail(e, list); }
  }
  async function groups(id) {
    wait();
    let d;
    try { d = await api(`/api/t/groups/${id}`); } catch (e) { return fail(e, () => teacher(id)); }
    const nodes = [el("div", { class: "hint" }, "Guruhni bosing: biriktiriladi (✅) yoki olinadi (▫️). Qavs ichida: o'quvchilar soni.")];
    if (!d.items.length) nodes.push(empty("Hali birorta o'quvchi guruh nomini kiritmagan."));
    for (const g of d.items) nodes.push(el("div", { class: "card tap", onclick: async () => {
      try { await api("/api/t/toggle", { method: "POST", body: JSON.stringify({ id, name: g.name }) }); } catch (e) { toast(e.message); }
      groups(id);
    } }, el("div", { class: "row" }, el("span", {}, g.chosen ? "✅" : "▫️"), el("div", { class: "grow" }, el("b", {}, g.name), el("div", { class: "hint" }, `${g.count} o'quvchi`)))));
    nodes.push(btn("", "✔️ Tayyor", () => teacher(id)));
    screen("Guruhlar", d.name, () => teacher(id), ...nodes);
  }

  /* =============== USTOZ PANELI =============== */
  let back1 = null;
  const toHome = () => (back1 ? back1() : openTab("home"));
  async function panel(back) {
    if (typeof back === "function") back1 = back;
    wait();
    let d;
    try { d = await api("/api/t/home"); } catch (e) { return fail(e, toHome); }
    const nodes = [];
    if (!d.items.length) nodes.push(empty("Sizga hali guruh biriktirilmagan. Guruh biriktirish uchun admin bilan bog'laning."));
    else nodes.push(el("div", { class: "hint" }, "Guruhlaringiz:"));
    for (const g of d.items) nodes.push(el("div", { class: "card tap", onclick: () => group(g.id) },
      el("div", { class: "row" }, el("div", { class: "grow" }, el("b", {}, "👥 " + g.name),
        el("div", { class: "hint" }, `${g.students} o'quvchi` + (g.idle ? ` · 💤 ${g.idle}` : ""))), icon("chev"))));
    screen("Guruhlarim", "", toHome, ...nodes);
  }
  async function group(gid) {
    wait();
    let d;
    try { d = await api(`/api/t/group/${gid}`); } catch (e) { return fail(e, panel); }
    const nodes = [el("div", { class: "card" },
      stat("O'quvchilar", d.students), stat(`✅ Oxirgi ${d.inactive_days} kunda test ishlagan`, d.active), stat("💤 Faol emas", d.idle),
      d.pct == null ? null : stat("🎯 Guruh bo'yicha o'rtacha natija", `${d.pct}% (${d.correct}/${d.total})`),
      d.blocked ? stat("🚫 Botni bloklaganlar", d.blocked) : null)];
    if (d.idle_list.length) nodes.push(el("div", { class: "card" }, el("b", {}, "💤 Test ishlamayotganlar"),
      ...d.idle_list.map((x) => el("div", { class: "hint" }, `• ${x.name} — ${x.days == null ? "hali test ishlamagan" : x.days + " kun oldin"}`)),
      d.idle_more ? el("div", { class: "hint" }, `… va yana ${d.idle_more} ta`) : null));
    nodes.push(el("div", { class: "row", style: "gap:6px" }, btn("grow", "👤 O'quvchilar", () => students(gid, 0)), btn("alt grow", "🏆 Reyting", () => rating(gid))));
    nodes.push(btn("alt", "📣 Guruhga xabar", () => message(gid, d.name)));
    nodes.push(btn("alt", "📋 Topshiriqlar", () => (OQ.adminAssign ? OQ.adminAssign(() => group(gid)) : soon("Topshiriqlar"))));
    nodes.push(btn("alt", "🎓 Milliy sertifikat imtihoni", () => (OQ.adminExam ? OQ.adminExam(() => group(gid)) : soon("Milliy sertifikat imtihoni"))));
    screen("Guruh: " + d.name, "", panel, ...nodes);
  }
  async function students(gid, page) {
    wait();
    let d;
    try { d = await api(`/api/t/students/${gid}?page=${page}`); } catch (e) { return fail(e, () => group(gid)); }
    const nodes = [el("div", { class: "hint" }, `Jami ${d.total} · Sahifa ${d.page + 1}/${d.pages}`)];
    if (!d.items.length) nodes.push(empty("Guruhda o'quvchi yo'q."));
    for (const s of d.items) {
      const mark = s.state === "blocked" ? "🚫" : s.state === "idle" ? "💤" : "✅";
      nodes.push(el("div", { class: "card tap", onclick: () => student(gid, s.tg, d.page) }, el("div", { class: "row" },
        el("div", { class: "grow" }, el("b", {}, `${s.n}. ${mark} ${s.name}`),
          el("div", { class: "hint" }, s.attempts ? `${s.attempts} test · ${s.pct}% · ${s.last}` : "test ishlamagan")), icon("chev"))));
    }
    const pg = pager(d, (p) => students(gid, p)); if (pg) nodes.push(pg);
    screen("O'quvchilar", d.name, () => group(gid), ...nodes);
  }
  async function rating(gid) {
    wait();
    let d;
    try { d = await api(`/api/t/rating/${gid}`); } catch (e) { return fail(e, () => group(gid)); }
    const medals = ["🥇", "🥈", "🥉"];
    const nodes = [];
    if (!d.items.length) nodes.push(empty("Hali hech kim test ishlamagan."));
    for (const x of d.items) nodes.push(el("div", { class: "card" }, el("b", {}, `${medals[x.rank - 1] || x.rank + "."} ${x.name}`),
      el("div", { class: "hint" }, `${x.pct}% (${x.correct}/${x.total}, ${x.attempts} test)`)));
    if (d.items.length) nodes.push(el("div", { class: "hint" }, "Reyting umumiy to'g'ri javoblar foiziga ko'ra tuzilgan."));
    screen("Reyting", d.name, () => group(gid), ...nodes);
  }
  async function student(gid, tg, page) {
    wait();
    let c;
    try { c = await api(`/api/t/student/${gid}/${tg}`); } catch (e) { return fail(e, () => students(gid, page || 0)); }
    const nodes = OQ.reportCard ? OQ.reportCard(c, { attempts: false }) : [el("div", { class: "card" }, el("b", {}, c.name))];
    if (c.username) nodes.push(el("div", { class: "hint" }, "✉️ Telegram: @" + c.username));
    if (c.blocked) nodes.push(el("div", { class: "hint" }, "🚫 Bu o'quvchi botni bloklagan."));
    screen("O'quvchi", c.name, () => students(gid, page || 0), ...nodes);
  }
  function message(gid, gname) {
    const ta = el("textarea", { class: "imp", maxlength: "1000", placeholder: "Xabar matni (eng ko'pi 1000 belgi)", style: "height:140px;font-family:inherit;font-size:15px" });
    const hint = el("div", { class: "hint" }, "Matn guruhdagi barcha o'quvchilarga sizning ismingiz bilan boradi.");
    screen("Guruhga xabar", gname, () => group(gid), hint, ta, btn("", "Ko'rib chiqish", async () => {
      const text = ta.value.trim();
      if (!text) return toast("Matn yozing.");
      let p;
      try { p = await api("/api/t/msg_preview", { method: "POST", body: JSON.stringify({ gid, text }) }); } catch (e) { return toast(e.message); }
      screen("Xabar ko'rinishi", gname, () => message(gid, gname),
        el("div", { class: "expl pre" }, p.text), el("div", { class: "hint", style: "margin:8px 0" }, `Guruh: ${p.group} · qabul qiluvchilar: ${p.count}`),
        btn("", `✅ Yuborish (${p.count} kishi)`, async () => {
          try {
            const r = await api("/api/t/msg_send", { method: "POST", body: JSON.stringify({ gid, text }) });
            screen("Yuborilmoqda", gname, () => group(gid), good(`⏳ Xabar ${r.count} kishiga yuborilmoqda…\nNatija haqida hisobot sizga bot orqali keladi.`));
          } catch (e) { toast(e.message); }
        }),
        btn("alt", "❌ Bekor qilish", () => group(gid)));
    }));
  }

  OQ.adminTeachers = (back) => list(back);
  OQ.teacherPanel = (back) => panel(back);
})();
