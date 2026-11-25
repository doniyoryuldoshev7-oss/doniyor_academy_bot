/* Veb-ilova: admin paneli, 3-bosqich — Foydalanuvchilar va Statistika (botdagi bilan bir xil qoidalar).
   oquvchi.js (window.OQ.u) dagi umumiy qismlar ishlatiladi. Har ekranda orqaga tugmasi bor. */
(function () {
  "use strict";
  const OQ = window.OQ;
  if (!OQ || !OQ.u || OQ.adminUsers) return;
  const { screen, empty, wait, good, stat } = OQ.u;
  const fail = (e, back) => { toast(e.message); if (back) back(); };
  const btn = (cls, label, fn, ic) => el("button", { class: "btn " + cls, onclick: fn }, ic ? icon(ic) : null, label);
  const SORTS = [["new", "Yangi"], ["old", "Eski"], ["az", "A-Z"]];
  const U = { q: "", sort: "new", page: 0 };
  const toAdmin = () => OQ.admin && OQ.admin();

  /* ---------- ro'yxat ---------- */
  async function users(reset) {
    if (reset) Object.assign(U, { q: "", sort: "new", page: 0 });
    wait();
    let d;
    try { d = await api(`/api/u/list?page=${U.page}&sort=${U.sort}&q=${encodeURIComponent(U.q)}`); } catch (e) { return fail(e, toAdmin); }
    U.page = d.page;
    const nodes = [];
    nodes.push(el("div", { class: "row", style: "gap:6px;margin-bottom:8px" }, ...SORTS.map(([k, t]) =>
      btn("small grow" + (U.sort === k ? "" : " alt"), t, () => { U.sort = k; U.page = 0; users(); }))));
    const inp = el("input", { class: "ans", type: "text", maxlength: "50", placeholder: "Ism, username, guruh, telefon yoki ID", value: U.q, autocomplete: "off", style: "margin:0" });
    const go = () => { U.q = inp.value.trim(); U.page = 0; users(); };
    inp.addEventListener("keydown", (ev) => { if (ev.key === "Enter") go(); });
    nodes.push(el("div", { class: "row", style: "gap:6px;margin-bottom:8px" }, inp, btn("small sq", "🔍", go)));
    if (U.q) nodes.push(btn("alt small", "✖ Qidiruvni tozalash", () => { U.q = ""; U.page = 0; users(); }));
    if (OQ.usersX) nodes.push(el("div", { class: "row", style: "gap:6px;margin-bottom:8px;flex-wrap:wrap" },
      btn("alt small grow", "⚠️ Profili to'liq emas", () => OQ.usersX.incomplete()),
      btn("alt small grow", "🗑 Tanlab o'chirish", () => OQ.usersX.del()),
      btn("alt small grow", "🚫 Botni bloklaganlar", () => OQ.usersX.blocked())));
    nodes.push(el("div", { class: "hint", style: "margin:8px 0" }, `Jami: ${d.total} · Sahifa ${d.page + 1}/${d.pages}`));
    if (!d.items.length) nodes.push(empty("Hech narsa topilmadi."));
    for (const x of d.items) nodes.push(el("div", { class: "card tap", onclick: () => userCard(x.tg) },
      el("div", { class: "row" }, el("div", { class: "grow" }, el("b", {}, `${x.icon} ${x.name}`),
        x.group || x.blocked ? el("div", { class: "hint" }, [x.group, x.blocked ? "🚫 bloklangan" : ""].filter(Boolean).join(" · ")) : null), icon("chev"))));
    if (d.pages > 1) nodes.push(el("div", { class: "row", style: "margin-top:8px" },
      d.page > 0 ? btn("alt small grow", "⬅️ Oldingi", () => { U.page = d.page - 1; users(); }) : null,
      d.page + 1 < d.pages ? btn("alt small grow", "Keyingi ➡️", () => { U.page = d.page + 1; users(); }) : null));
    screen("Foydalanuvchilar", "Kerakli foydalanuvchini tanlang:", toAdmin, ...nodes);
  }

  /* ---------- foydalanuvchi kartasi ---------- */
  async function userCard(tg) {
    wait();
    let c;
    try { c = await api(`/api/u/user/${tg}`); } catch (e) { return fail(e, () => users()); }
    const line = (k, v) => el("div", { class: "row", style: "margin:4px 0" }, el("span", { class: "hint grow" }, k), el("b", {}, v || "—"));
    const nodes = [el("div", { class: "card" },
      line("Ism-familiya", c.name), line("Username", c.username ? "@" + c.username : ""), line("Telegram ID", String(c.tg)),
      line("Telefon", c.phone), line("Guruh", c.group), line("Holat", c.status),
      line("Hisob", c.blocked ? "🚫 Bloklangan" : "✅ Faol"), line("Testlar / ball", `${c.tests} / ${c.score}`))];
    if (!c.complete) nodes.push(el("div", { class: "hint" }, "⚠️ Ariza to'liq emas. Foydalanuvchi botda /start orqali ro'yxatdan o'tishi kerak."));
    if (c.is_admin) nodes.push(el("div", { class: "hint" }, "🛡 Bu foydalanuvchi admin."));
    const here = () => userCard(tg);
    if (c.can_approve) nodes.push(btn("", "✅ Tasdiqlash", () => act("approve", tg, "Foydalanuvchi tasdiqlandi."), "check"));
    if (c.can_reject) nodes.push(btn("alt", "❌ Rad etish", () => confirmSheet("Arizani rad etasizmi?", `${c.name}ga rad etilgani haqida xabar boradi.`, "Ha, rad etish", () => act("reject", tg, "Foydalanuvchi rad etildi."))));
    if (c.blocked) nodes.push(btn("alt", "🔓 Blokdan chiqarish", () => act("unblock", tg, "Foydalanuvchi blokdan chiqarildi.")));
    else if (c.can_block) nodes.push(btn("alt", "🚫 Bloklash", () => confirmSheet("Bloklaysizmi?", `${c.name} botdan foydalana olmaydi.`, "Ha, bloklash", () => act("block", tg, "Foydalanuvchi bloklandi."))));
    if (OQ.usersX) { try { nodes.push(...(await OQ.usersX.roleBox(c, here))); } catch (e) { /* huquq bloki ixtiyoriy */ } }
    screen("Foydalanuvchi", c.name, () => users(), ...nodes);
  }

  let busy = false;
  async function act(kind, tg, okText) {
    if (busy) return; busy = true;
    try {
      const r = await api(`/api/u/${kind}`, { method: "POST", body: JSON.stringify({ tg }) });
      notify("success");
      toast(okText + (r.notified === false ? " (xabar yuborilmadi)" : ""));
    } catch (e) { toast(e.message); }
    busy = false;
    userCard(tg);
  }

  /* ---------- statistika ---------- */
  async function stats() {
    wait();
    let d;
    try { d = await api("/api/u/stats"); } catch (e) { return fail(e, toAdmin); }
    const u = d.users;
    const nodes = [
      el("div", { class: "sec" }, "👥 Foydalanuvchilar"),
      el("div", { class: "card" },
        stat("Jami ro'yxatda", `${u.total} ta`), stat("✅ Tasdiqlangan", `${u.approved} ta`), stat("⏳ Kutilmoqda", `${u.pending} ta`),
        stat("❌ Rad etilgan", `${u.rejected} ta`), stat("🚫 Bloklangan", `${u.blocked} ta`), stat("⚠️ Profili to'liq emas", `${u.incomplete} ta`),
        stat("Bugun qo'shilganlar", `+${d.new_today} ta`), stat("Haftalik faol (7 kun)", `${d.active7} ta`)),
      el("div", { class: "sec" }, "📋 Yakunlangan testlar"),
      el("div", { class: "card" }, stat("Jami", `${d.attempts} ta`),
        ...d.sources.map((x) => stat(x.title, `${x.attempts} ta · ${x.correct}/${x.questions} (${x.pct.toFixed(1)}%)`))),
      el("div", { class: "sec" }, "🎯 Javoblar"),
      el("div", { class: "card" }, stat("Jami javoblar", `${d.answers} ta`), stat("✅ To'g'ri", `${d.correct} ta`),
        stat("❌ Xato", `${d.wrong} ta`), stat("To'g'ri javoblar foizi", `${d.pct.toFixed(1)}%`)),
      el("div", { class: "sec" }, "🏆 Reyting"),
      el("div", { class: "card" }, stat("Eng yuqori balli talaba", d.top ? `${d.top.name} (${d.top.score} ball)` : "Hali mavjud emas"),
        d.top && d.top.tied > 1 ? el("div", { class: "hint" }, `Yana ${d.top.tied - 1} kishi aynan shu ball bilan.`) : null),
      el("div", { class: "hint", style: "margin:8px 0" }, "Foizlar javoblar jurnalidan (har bir javob bo'yicha) hisoblanadi. «Bugun» Toshkent vaqti bo'yicha."),
      btn("alt", "🔎 Aniqlikni tekshirish", audit, "check")];
    screen("Statistika", "Doniyor Academy — tizim statistikasi", toAdmin, ...nodes);
  }

  /* ---------- aniqlikni tekshirish (faqat o'qiydi) ---------- */
  async function audit() {
    wait();
    let d;
    try { d = await api("/api/u/audit"); } catch (e) { return fail(e, stats); }
    const nodes = [d.ok
      ? good(`✅ Hammasi mos. ${d.users} ta foydalanuvchining hisoblagichlari test tarixi bilan bir xil.`)
      : OQ.u.bad(`⚠️ Farqlar topildi. Quyida ko'rsatilgan.`)];
    nodes.push(el("div", { class: "card" },
      stat("Tekshirilgan foydalanuvchilar", `${d.users} ta`),
      stat("Hisoblagichi tarixdan farq qiladi", `${d.mismatch} ta`),
      stat("Noto'g'ri yozilgan testlar", `${d.bad_attempts} ta`),
      stat("Javoblar soni natijaga mos emas", `${d.log_mismatch} ta`),
      stat("Javoblar savollar sonidan ko'p", `${d.log_overflow} ta`)));
    if (d.samples.length) {
      nodes.push(el("div", { class: "sec" }, `Farq qiluvchilar (eng kattasi ${d.samples.length} ta)`));
      for (const x of d.samples) nodes.push(el("div", { class: "card tap", onclick: () => userCard(x.tg) },
        el("b", {}, x.name),
        el("div", { class: "hint" }, `Profilda: ${x.counter[1]} savol, ${x.counter[2]} to'g'ri`),
        el("div", { class: "hint" }, `Test tarixida: ${x.real[1]} savol, ${x.real[2]} to'g'ri`)));
    }
    if (!d.ok) nodes.push(el("div", { class: "hint", style: "margin-top:8px" },
      "Eng ko'p uchraydigan sabab: fan yoki mavzu o'chirilganda uning test tarixi ham o'chadi, lekin o'quvchi profilidagi umumiy sonlar (testlar, savollar, ball) o'zgarmay qoladi. Bunday farq xato emas, o'chirish natijasi."));
    nodes.push(el("div", { class: "hint", style: "margin-top:8px" }, "Bu tekshiruv hech narsani o'zgartirmaydi, faqat solishtiradi."));
    screen("Aniqlikni tekshirish", "Profil hisoblagichlari va test tarixi solishtiriladi.", stats, ...nodes);
  }

  OQ.adminUsers = users;
  OQ.adminStats = stats;
})();
