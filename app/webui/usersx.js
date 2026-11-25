/* Veb-ilova: Foydalanuvchilar — qolgan bo'limlar: Profili to'liq emas, Botni bloklaganlar, Tanlab o'chirish,
   Cheklangan admin. Har ekranda orqaga tugmasi bor. */
(function () {
  "use strict";
  const OQ = window.OQ;
  if (!OQ || !OQ.u || OQ.usersX) return;
  const { screen, empty, wait, good, stat } = OQ.u;
  const fail = (e, back) => { toast(e.message); if (back) back(); };
  const btn = (cls, label, fn, ic) => el("button", { class: "btn " + cls, onclick: fn }, ic ? icon(ic) : null, label);
  const toUsers = () => (OQ.adminUsers ? OQ.adminUsers() : OQ.admin && OQ.admin());
  const FILTERS = [["all", "Hammasi"], ["inc", "To'liq emas"], ["idle", "Testsiz"], ["blk", "Bloklagan"]];
  const D = { filter: "all", page: 0, q: "", sel: new Set(), back: null };
  const contact = (x) => [x.phone && "📱 " + x.phone, x.group && "👥 " + x.group, x.username && "@" + x.username].filter(Boolean).join(" · ");
  let busy = false;

  /* ---------- Profili to'liq emas ---------- */
  async function incomplete() {
    wait();
    let d;
    try { d = await api("/api/ux/incomplete"); } catch (e) { return fail(e, toUsers); }
    const nodes = [el("div", { class: "card" }, stat("Jami", d.total + " ta o'quvchi"))];
    if (!d.total) nodes.push(empty("Profili to'liq bo'lmagan o'quvchi yo'q."));
    for (const x of d.items.slice(0, 15)) nodes.push(el("div", { class: "card" }, el("b", {}, x.name),
      el("div", { class: "hint" }, "Yetishmaydi: " + x.missing.join(", "))));
    if (d.total > 15) nodes.push(el("div", { class: "hint" }, `… va yana ${d.total - 15} ta`));
    if (d.excluded_total) {
      nodes.push(el("div", { class: "hint", style: "margin-top:8px" }, "Eslatma yuborilmaydi (hisobga olinmadi):"));
      for (const x of d.excluded.slice(0, 10)) nodes.push(el("div", { class: "hint" }, `• ${x.name} — ${x.reason}`));
      if (d.excluded_total > 10) nodes.push(el("div", { class: "hint" }, `… va yana ${d.excluded_total - 10} ta`));
    }
    nodes.push(el("div", { class: "hint", style: "margin:8px 0" }, d.auto.on
      ? `🔔 Avtomatik eslatma: har ${d.auto.days} kunda soat ${String(d.auto.hour).padStart(2, "0")}:00 da, bir o'quvchiga ko'pi bilan ${d.auto.max} marta.`
      : "🔕 Avtomatik eslatma o'chirilgan."));
    if (d.total) {
      nodes.push(btn("", `📨 Hozir eslatma yuborish (${d.total})`, () => confirmSheet("Eslatma yuborilsinmi?",
        `${d.total} ta o'quvchiga profilni to'ldirish eslatmasi yuboriladi.`, "Yuborish", sendInc)));
      nodes.push(btn("alt", "🗑 Shulardan tanlab o'chirish", () => delOpen("inc", incomplete)));
    }
    nodes.push(btn("alt", `🚫 Botni bloklaganlar (${d.blocked})`, () => blocked(0)));
    screen("Profili to'liq emas", "To'ldirmagan o'quvchilar", toUsers, ...nodes);
  }
  async function sendInc() {
    if (busy) return;
    busy = true; wait();
    try {
      const r = await api("/api/ux/inc_send", { method: "POST", body: "{}" });
      busy = false;
      screen("Eslatma yuborildi", "", incomplete, good(`To'liq emas: ${r.total}\nYuborildi: ${r.sent}\nBotni bloklagan: ${r.blocked}\nYuborilmadi: ${r.failed}`));
    } catch (e) { busy = false; fail(e, incomplete); }
  }

  /* ---------- Botni bloklaganlar ---------- */
  async function blocked(page) {
    wait();
    let d;
    try { d = await api(`/api/ux/blocked?page=${page || 0}`); } catch (e) { return fail(e, toUsers); }
    const nodes = [el("div", { class: "card" }, stat("Jami", d.total + " ta"))];
    if (!d.total) nodes.push(el("div", { class: "hint" }, "Hozircha bloklagan o'quvchi aniqlanmagan. Tekshirish tugmasi hammani yashirin tekshiradi."));
    for (const x of d.items) nodes.push(el("div", { class: "card" }, el("b", {}, x.name), el("div", { class: "hint" }, contact(x) || "—")));
    if (d.total) nodes.push(el("div", { class: "hint" }, "Ular botga xabar yoza olmaydi, shuning uchun telefon yoki guruh orqali bog'laning. Qaytib kelsa, tekshirishda blok belgisi o'zi olib tashlanadi."));
    if (d.pages > 1) nodes.push(el("div", { class: "row", style: "margin-top:8px" },
      d.page > 0 ? btn("alt small grow", "⬅️ Oldingi", () => blocked(d.page - 1)) : null,
      d.page + 1 < d.pages ? btn("alt small grow", "Keyingi ➡️", () => blocked(d.page + 1)) : null));
    nodes.push(btn("", "🔍 Hammasini tekshirib yangilash", () => confirmSheet("Tekshirilsinmi?",
      "O'quvchilarga hech qanday xabar bormaydi. O'quvchilar ko'p bo'lsa, bir necha daqiqa ketishi mumkin.", "Tekshirish", scan)));
    if (d.total) {
      nodes.push(btn("alt", "📄 To'liq ro'yxat (fayl)", async () => {
        try { const r = await api("/api/ux/blocked_csv", { method: "POST", body: "{}" }); toast(`Fayl botga yuborildi (${r.count} ta).`); } catch (e) { toast(e.message); }
      }));
      nodes.push(btn("alt", "🗑 Shulardan tanlab o'chirish", () => delOpen("blk", () => blocked(0))));
    }
    screen("Botni bloklaganlar", "", toUsers, ...nodes);
  }
  let scanTok = 0;
  async function scan() {
    const tok = ++scanTok;
    screen("Tekshirilmoqda…", "", () => { scanTok++; blocked(0); },
      good("⏳ Tekshirilmoqda… O'quvchilarga hech qanday xabar bormaydi. Bu sahifada kuting."));
    try { await api("/api/ux/scan", { method: "POST", body: "{}" }); } catch (e) { return fail(e, () => blocked(0)); }
    const poll = async () => {
      if (tok !== scanTok) return;
      let s;
      try { s = await api("/api/ux/scan"); } catch (e) { return fail(e, () => blocked(0)); }
      if (tok !== scanTok) return;
      if (s.running) return setTimeout(poll, 3000);
      if (s.error || !s.result) return screen("Tekshiruv", "", () => blocked(0), good("Tekshiruv to'xtadi. Birozdan keyin qayta urinib ko'ring."));
      const r = s.result;
      screen("Tekshiruv tugadi", "", () => blocked(0), good(`Tekshirildi: ${r.total} ta\nBotni bloklagan: ${r.blocked} (yangi aniqlangan: ${r.new_blocked})\nQaytib kelgan: ${r.restored}\nTekshirib bo'lmadi: ${r.failed}`),
        btn("", "🚫 Ro'yxatni ko'rish", () => blocked(0)));
    };
    setTimeout(poll, 2500);
  }

  /* ---------- Tanlab o'chirish ---------- */
  function delOpen(filter, back, keep) {
    if (!keep) D.sel = new Set();
    Object.assign(D, { filter: filter || "all", page: 0, q: "", back: back || toUsers });
    delList();
  }
  async function delList() {
    wait();
    let d;
    try { d = await api(`/api/ux/del/list?filter=${D.filter}&page=${D.page}&q=${encodeURIComponent(D.q)}`); } catch (e) { return fail(e, D.back); }
    D.page = d.page;
    const nodes = [];
    nodes.push(el("div", { class: "row", style: "gap:6px;margin-bottom:8px;flex-wrap:wrap" }, ...FILTERS.map(([k, t]) =>
      btn("small grow" + (D.filter === k ? "" : " alt"), t, () => { D.filter = k; D.page = 0; delList(); }))));
    const inp = el("input", { class: "ans", type: "text", maxlength: "50", placeholder: "Ism, guruh, telefon yoki ID", value: D.q, autocomplete: "off", style: "margin:0" });
    const go = () => { D.q = inp.value.trim(); D.page = 0; delList(); };
    inp.addEventListener("keydown", (ev) => { if (ev.key === "Enter") go(); });
    nodes.push(el("div", { class: "row", style: "gap:6px;margin-bottom:8px" }, inp, btn("small sq", "🔍", go)));
    nodes.push(el("div", { class: "hint", style: "margin:6px 0" },
      `Filtr: ${FILTERS.find((f) => f[0] === D.filter)[1]} — ${d.found} ta · Belgilangan: ${D.sel.size} · Sahifa ${d.page + 1}/${d.pages}. Belgilar sahifa va filtr almashganda saqlanadi.`));
    if (!d.items.length) nodes.push(empty("Bu filtrda o'quvchi yo'q."));
    for (const x of d.items) {
      const mark = el("span", {}, D.sel.has(x.tg) ? "☑" : "☐");
      const row = el("div", { class: "card tap", onclick: () => { D.sel.has(x.tg) ? D.sel.delete(x.tg) : D.sel.add(x.tg); delList(); } },
        el("div", { class: "row" }, mark, el("div", { class: "grow" }, el("b", {}, x.name), el("div", { class: "hint" }, [x.group, x.tests + " test"].filter(Boolean).join(" · ")))));
      nodes.push(row);
    }
    if (d.pages > 1) nodes.push(el("div", { class: "row", style: "margin-top:8px" },
      d.page > 0 ? btn("alt small grow", "⬅️ Oldingi", () => { D.page = d.page - 1; delList(); }) : null,
      d.page + 1 < d.pages ? btn("alt small grow", "Keyingi ➡️", () => { D.page = d.page + 1; delList(); }) : null));
    nodes.push(el("div", { class: "row", style: "gap:6px;margin-top:8px" },
      btn("alt small grow", "☑ Sahifani belgilash", () => { d.items.forEach((x) => D.sel.add(x.tg)); delList(); }),
      btn("alt small grow", "☐ Belgini olib tashlash", () => { D.sel.clear(); delList(); })));
    nodes.push(btn(D.sel.size ? "" : "alt", `🗑 O'chirish (${D.sel.size})`, delConfirm));
    screen("O'quvchilarni o'chirish", "Belgilab o'chiring", D.back, ...nodes);
  }
  async function delConfirm() {
    if (!D.sel.size) return toast("Avval o'chiriladigan o'quvchilarni belgilang.");
    wait();
    let p;
    try { p = await api("/api/ux/del/preview", { method: "POST", body: JSON.stringify({ tgs: [...D.sel] }) }); } catch (e) { return fail(e, delList); }
    const list = p.names.map((x) => `• ${x.name}${x.group ? " (" + x.group + ")" : ""}`).join("\n") + (p.count > p.names.length ? `\n… va yana ${p.count - p.names.length} ta` : "");
    screen(`${p.count} ta o'quvchi o'chiriladi`, "", delList,
      el("div", { class: "expl pre" }, list),
      el("div", { class: "hint", style: "margin:8px 0" }, "Bularning barcha test natijalari, xatolari va tarixi o'chadi. Qaytarib bo'lmaydi. Xavfsizlik uchun avval sizga baza zaxirasi yuboriladi; zaxira yuborilmasa, hech kim o'chirilmaydi."),
      btn("", "✅ Ha, o'chirish", delGo), btn("alt", "❌ Yo'q", delList));
  }
  async function delGo() {
    if (busy) return;
    busy = true; wait();
    try {
      const r = await api("/api/ux/del/do", { method: "POST", body: JSON.stringify({ tgs: [...D.sel] }) });
      busy = false; D.sel = new Set();
      screen("Tayyor", "", toUsers, good(`✅ ${r.deleted} ta o'quvchi o'chirildi.\nBaza zaxirasi sizga yuborilgan (kerak bo'lsa tiklash mumkin).`));
    } catch (e) { busy = false; fail(e, delList); }
  }

  /* ---------- Cheklangan admin (foydalanuvchi kartasi uchun) ---------- */
  async function roleBox(c, refresh) {
    let r;
    try { r = await api(`/api/ux/role/${c.tg}`); } catch (e) { return []; }
    const nodes = [];
    if (r.editor) nodes.push(el("div", { class: "hint" }, "🛡 Cheklangan admin: faol"));
    const act = async (enable) => {
      try { await api("/api/ux/role", { method: "POST", body: JSON.stringify({ tg: c.tg, enable }) });
        toast(enable ? "Cheklangan admin huquqi berildi." : "Cheklangan admin huquqi bekor qilindi."); } catch (e) { toast(e.message); }
      refresh();
    };
    if (r.can_revoke) nodes.push(btn("alt", "🚫 Admin huquqini bekor qilish", () => confirmSheet("Cheklangan admin huquqini bekor qilasizmi?", c.name, "Ha, bekor qilish", () => act(false))));
    else if (r.can_grant) nodes.push(btn("alt", "🛡 Cheklangan admin qilish", () => confirmSheet("Cheklangan admin huquqini berasizmi?", `${c.name} savol qo'sha oladi.`, "Ha, berish", () => act(true))));
    return nodes;
  }

  OQ.usersX = { incomplete, blocked: () => blocked(0), del: () => delOpen("all", toUsers), roleBox };
})();
