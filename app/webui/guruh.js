/* Veb-ilova: Guruhlar va ro'yxatdan o'tish arizalari (botdagi /guruhlar va ariza tugmalari bilan bir xil).
   oquvchi.js (window.OQ.u) dagi umumiy qismlar ishlatiladi. Har ekranda orqaga tugmasi bor. */
(function () {
  "use strict";
  const OQ = window.OQ;
  if (!OQ || !OQ.u || OQ.guruh) return;
  const { screen, empty, wait, stat } = OQ.u;
  const fail = (e, back) => { toast(e.message); if (back) back(); };
  const btn = (cls, label, fn, ic) => el("button", { class: "btn " + cls, onclick: fn }, ic ? icon(ic) : null, label);
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });
  const toAdmin = () => OQ.admin && OQ.admin();
  const STATUS = { approved: "✅", pending: "⏳", rejected: "❌" };
  const PAGE = 10;

  /* ---------- bosh ekran ---------- */
  async function home() {
    wait();
    let d;
    try { d = await api("/api/g/home"); } catch (e) { return fail(e, toAdmin); }
    const nodes = [];
    nodes.push(el("div", { class: "hint", style: "margin-bottom:8px" }, d.groups.length
      ? "Guruhni bosing: o'quvchilarini ko'rasiz, yangi o'quvchi qo'shasiz, nomini o'zgartirasiz."
      : "Ro'yxat hali bo'sh. Guruh qo'shsangiz, o'quvchilar ro'yxatdan o'tishda uni tugma bilan tanlaydi."));
    for (const g of d.groups) nodes.push(el("div", { class: "card tap", onclick: () => group(g.id) },
      el("div", { class: "row" }, el("b", { class: "grow" }, "👥 " + g.name), el("span", { class: "hint" }, `${g.count} ta`), icon("chev"))));
    nodes.push(btn("", "➕ Guruh qo'shish", addForm));
    if (d.groups.length && d.nogroup) nodes.push(btn("alt", `👥 Guruhga ajratilmaganlar: ${d.nogroup} ta`, pickTarget));
    if (d.custom.length) {
      nodes.push(el("div", { class: "sec" }, "✍️ O'quvchilar o'zi yozgan, ro'yxatda yo'q guruhlar"));
      for (const c of d.custom) nodes.push(el("div", { class: "card" }, el("div", { class: "row" },
        el("b", { class: "grow" }, `${c.name} — ${c.count} ta`),
        btn("small", "➕ Ro'yxatga", () => addNames(c.name)))));
      nodes.push(el("div", { class: "hint" }, "O'xshash yozilganlar (175 guruh, 175-guruh) bitta guruhga birlashadi."));
    }
    if (d.goals.length) {
      nodes.push(el("div", { class: "sec" }, "🎯 Tarix qaysi imtihon uchun kerak"));
      nodes.push(el("div", { class: "card" }, ...d.goals.map((x) => stat(x.goal, `${x.count} ta`))));
    }
    screen("Guruhlar", "Guruhlar va o'quvchilarni ajratish", toAdmin, ...nodes);
  }

  function addForm() {
    const inp = el("input", { class: "ans", type: "text", maxlength: "200", placeholder: "175-guruh, 176-guruh", autocomplete: "off" });
    screen("Guruh qo'shish", "Bir nechta bo'lsa, vergul bilan yozing.", home,
      el("div", { class: "card" }, inp), btn("", "✅ Qo'shish", () => addNames(inp.value), "check"));
    setTimeout(() => inp.focus(), 50);
  }

  async function addNames(names) {
    if (!String(names || "").trim()) return toast("Guruh nomini yozing.");
    try {
      const r = await post("/api/g/add", { names });
      const msg = [r.added.length ? "✅ Qo'shildi: " + r.added.join(", ") : "", r.existed.length ? "ℹ️ Allaqachon bor: " + r.existed.join(", ") : "",
        r.moved ? `🔀 O'xshash yozilgan ${r.moved} ta o'quvchi shu guruhga o'tkazildi.` : ""].filter(Boolean).join("\n");
      notify("success"); toast(msg || "Hech narsa qo'shilmadi.");
    } catch (e) { toast(e.message); }
    home();
  }

  /* ---------- bitta guruh ---------- */
  async function group(id) {
    wait();
    let d;
    try { d = await api(`/api/g/group/${id}`); } catch (e) { return fail(e, home); }
    const nodes = [el("div", { class: "card" }, d.users.length
      ? d.users.map((u) => el("div", { class: "stat" }, el("span", {}, `${STATUS[u.status] || "•"} ${u.name}`)))
      : el("div", { class: "empty" }, "Hozircha o'quvchi yo'q."))];
    nodes.push(btn("", "➕ O'quvchi qo'shish / ko'chirish", () => pick(id), "users"));
    nodes.push(el("div", { class: "row", style: "gap:6px" },
      btn("alt grow", "✏️ Nomini o'zgartirish", () => renameForm(d)),
      btn("alt grow", "🗑 O'chirish", () => confirmSheet(`«${d.name}» ro'yxatdan o'chirilsinmi?`,
        "O'quvchilar o'chirilmaydi va guruh yozuvi ularda qoladi; faqat ro'yxatdan tanlab bo'lmaydi.", "Ha, o'chirish",
        async () => { try { await post("/api/g/delete", { id }); notify("success"); toast("O'chirildi"); } catch (e) { toast(e.message); } home(); }))));
    screen(d.name, `${d.users.length} ta o'quvchi`, home, ...nodes);
  }

  function renameForm(d) {
    const inp = el("input", { class: "ans", type: "text", maxlength: "40", value: d.name, autocomplete: "off" });
    screen("Nomini o'zgartirish", "Shu guruhdagi barcha o'quvchilarning guruhi ham o'zgaradi.", () => group(d.id),
      el("div", { class: "card" }, inp),
      btn("", "✅ Saqlash", async () => {
        try { const r = await post("/api/g/rename", { id: d.id, name: inp.value }); notify("success"); toast(`Saqlandi. O'quvchilar: ${r.moved} ta yangilandi.`); }
        catch (e) { return toast(e.message); }
        group(d.id);
      }, "check"));
  }

  /* ---------- o'quvchilarni belgilab qo'shish ---------- */
  const P = { id: 0, sel: new Set(), page: 0, items: [], name: "" };

  async function pick(id) {
    wait();
    let d;
    try { d = await api(`/api/g/cands/${id}`); } catch (e) { return fail(e, () => group(id)); }
    Object.assign(P, { id, sel: new Set(), page: 0, items: d.items, name: d.name });
    drawPick();
  }

  function drawPick() {
    const pages = Math.max(1, Math.ceil(P.items.length / PAGE));
    P.page = Math.min(Math.max(P.page, 0), pages - 1);
    const nodes = [el("div", { class: "hint", style: "margin-bottom:8px" },
      "Tepada guruhga ajratilmaganlar turadi (✍️ — o'zi yozgan guruh). Boshqa guruhdagini belgilasangiz, shu guruhga ko'chadi.")];
    if (!P.items.length) nodes.push(empty("Qo'shish uchun boshqa o'quvchi yo'q."));
    for (const u of P.items.slice(P.page * PAGE, (P.page + 1) * PAGE)) {
      const on = P.sel.has(u.tg);
      nodes.push(el("div", { class: "card tap", style: on ? "outline:2px solid var(--gold)" : "",
        onclick: () => { on ? P.sel.delete(u.tg) : P.sel.add(u.tg); drawPick(); } },
        el("div", { class: "row" }, el("b", { class: "grow" }, `${on ? "☑️" : "⬜"} ${u.name}`),
          el("span", { class: "hint" }, (u.group || "guruhsiz") + (u.listed ? "" : " ✍️")))));
    }
    if (pages > 1) nodes.push(el("div", { class: "row", style: "margin-top:8px" },
      P.page > 0 ? btn("alt small grow", "⬅️ Oldingi", () => { P.page--; drawPick(); }) : null,
      el("span", { class: "hint", style: "padding:0 8px" }, `${P.page + 1}/${pages}`),
      P.page + 1 < pages ? btn("alt small grow", "Keyingi ➡️", () => { P.page++; drawPick(); }) : null));
    nodes.push(btn("", `✅ Saqlash (${P.sel.size} ta)`, async () => {
      if (!P.sel.size) return toast("Hech kim belgilanmadi.");
      try { const r = await post("/api/g/assign", { id: P.id, tgs: [...P.sel] }); notify("success"); toast(`${r.count} ta o'quvchi «${r.name}» guruhiga qo'shildi`); }
      catch (e) { return toast(e.message); }
      group(P.id);
    }, "check"));
    screen(`➕ ${P.name}`, "Qo'shiladigan o'quvchilarni belgilang", () => group(P.id), ...nodes);
  }

  async function pickTarget() {
    wait();
    let d;
    try { d = await api("/api/g/home"); } catch (e) { return fail(e, home); }
    const nodes = d.groups.map((g) => el("div", { class: "card tap", onclick: () => pick(g.id) },
      el("div", { class: "row" }, el("b", { class: "grow" }, "👥 " + g.name), icon("chev"))));
    if (!nodes.length) nodes.push(empty("Avval guruh qo'shing."));
    screen("Qaysi guruhga?", "Guruhni tanlang, keyin o'quvchilarni belgilaysiz.", home, ...nodes);
  }

  /* ---------- foydalanuvchi kartasi uchun qo'shimchalar (users.js chaqiradi) ---------- */
  let last = { tg: null, p: null };
  const info = (tg, fresh) => { if (fresh || last.tg !== tg || !last.p) last = { tg, p: api(`/api/g/info/${tg}`) }; return last.p; };

  /* tasdiqlash tugmasidan oldin: maqsad, tekshiruv eslatmasi, shubhalar */
  async function infoBox(c) {
    const d = await info(c.tg, true);
    const out = [el("div", { class: "card" }, stat("🎯 Maqsad", d.goal || "—"))];
    if (d.pending) out.push(el("div", { class: "hint" }, "⚠️ Tasdiqlashdan oldin tekshiring: ro'yxatdan o'tishda xatolik bormi? Ism va familiya haqiqiy va to'liqmi, o'rni almashmaganmi, guruh to'g'rimi."));
    if (d.warnings.length) out.push(el("div", { class: "card" }, el("b", {}, "🔎 Bot sezgan shubhalar"),
      ...d.warnings.map((w) => el("div", { class: "hint" }, "• " + w))));
    return out;
  }

  /* tasdiqlash tugmasidan keyin: guruhga biriktirish va rad etish shablonlari */
  async function actionBox(c, here) {
    const d = await info(c.tg, false);
    const out = [];
    if (d.groups.length && !c.is_admin) out.push(btn("alt", "👥 Guruhga biriktirish", () => groupSheet(c, d.groups, here)));
    if (c.can_reject) {
      out.push(el("div", { class: "sec" }, "❌ Rad etish"));
      out.push(el("div", { class: "row", style: "gap:6px;flex-wrap:wrap" }, ...d.templates.map((t) =>
        btn("alt small grow", t.label, () => confirmSheet("Rad etasizmi?", `O'quvchiga yuboriladi:\n\n${t.text}`, "Ha, rad etish", () => reject(c.tg, { code: t.code }, here))))));
      out.push(btn("alt", "✍️ O'z so'zim bilan rad etish", () => ownReason(c, here)));
    }
    return out;
  }

  function groupSheet(c, groups, here) {
    screen("Guruhga biriktirish", c.name, here, ...groups.map((g) => el("div", { class: "card tap", onclick: async () => {
      try { await post("/api/g/setgroup", { tg: c.tg, id: g.id }); notify("success"); toast(`Guruh: ${g.name}`); } catch (e) { toast(e.message); }
      here();
    } }, el("div", { class: "row" }, el("b", { class: "grow" }, (c.group === g.name ? "✅ " : "👥 ") + g.name), icon("chev")))));
  }

  function ownReason(c, here) {
    const inp = el("textarea", { class: "ans", maxlength: "500", rows: "4", placeholder: "Masalan: Familiyangizni to'liq yozing" });
    screen("O'z so'zim bilan rad etish", "O'quvchiga xabar oxirida qayta ro'yxatdan o'tish tugmasi qo'shiladi.", here,
      el("div", { class: "card" }, inp), btn("", "❌ Rad etish", () => reject(c.tg, { text: inp.value }, here)));
  }

  let busy = false;
  async function reject(tg, body, here) {
    if (busy) return; busy = true;
    try { const r = await post("/api/g/reject", Object.assign({ tg }, body)); notify("success"); toast(r.message); } catch (e) { toast(e.message); }
    busy = false;
    here();
  }

  OQ.guruh = { home, infoBox, actionBox };
})();
