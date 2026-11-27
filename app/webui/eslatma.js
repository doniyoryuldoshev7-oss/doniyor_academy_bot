/* Veb-ilova: Eslatmalar (faqat admin). Botdagi «🔔 Eslatmalar» bilan bir xil amallar. Har ekranda orqaga tugmasi bor. */
(function () {
  "use strict";
  const OQ = window.OQ;
  if (!OQ || !OQ.u || OQ.adminReminders) return;
  const { screen, wait, good, stat } = OQ.u;
  const btn = (cls, label, fn) => el("button", { class: "btn " + cls, onclick: fn }, label);
  let back0 = null;
  const toAdmin = () => (back0 ? back0() : OQ.admin && OQ.admin());
  let timer = null;
  const stop = () => { if (timer) { clearInterval(timer); timer = null; } };

  async function home(back) {
    if (typeof back === "function") back0 = back;
    stop();
    wait();
    let d;
    try { d = await api("/api/rm/status"); } catch (e) { toast(e.message); return toAdmin(); }
    const nodes = [
      el("div", { class: "card" },
        stat("⚙️ Smart Takrorlash", d.smart ? "yoqilgan" : "o'chirilgan"),
        stat("🔔 Eslatmalar", d.enabled ? "yoqilgan" : "o'chirilgan"),
        stat("🕓 Yuborish vaqti", `${String(d.hour).padStart(2, "0")}:00 (UTC${d.tz >= 0 ? "+" : ""}${d.tz})`),
        stat("👥 Eslatma kutayotgan o'quvchilar", d.students),
        stat("🔢 Jami muddati kelgan savollar", d.due)),
    ];
    if (d.top.length) {
      nodes.push(el("div", { class: "hint", style: "margin:8px 0 4px" }, "Eng ko'p savoli borlar:"));
      for (const x of d.top) nodes.push(el("div", { class: "hint" }, `• ${x.name} — ${x.count} ta`));
      if (d.more) nodes.push(el("div", { class: "hint" }, `… va yana ${d.more} ta o'quvchi`));
    } else {
      nodes.push(el("div", { class: "hint", style: "margin:8px 0" }, "Hozircha hech kimda muddati kelgan takrorlash yo'q. O'quvchilar test ishlab, xato qilgandan keyin bu ro'yxat to'la boshlaydi."));
    }
    if (!d.smart) nodes.push(el("div", { class: "hint", style: "margin:8px 0" }, "⚠️ SMART_REVIEW_ENABLED=1 qo'yilmagan — eslatma yuborilmaydi."));
    nodes.push(el("div", { class: "hint", style: "margin:8px 0" }, `Oxirgi tekshiruv: ${d.checked}`));
    if (d.job) {
      nodes.push(el("div", { class: "card tap", onclick: () => progress() },
        el("b", {}, d.job.running ? "⏳ Hozir yuborilmoqda" : "So'nggi yuborish"),
        el("div", { class: "hint" }, d.job.running ? "Holatni ko'rish uchun bosing" : (d.job.error || "Natijani ko'rish uchun bosing"))));
    }
    nodes.push(
      btn("", "📨 Hozir yuborish", () => confirmSheet("Eslatmalar yuborilsinmi?",
        `${d.students} ta o'quvchiga takrorlash eslatmasi yuboriladi. Topshiriq eslatmalari ham yuboriladi.`, "Yuborish", start)),
      btn("alt", "🔄 Yangilash", () => home()),
      btn("alt", "🧪 Sinov: muddatni hozirga surish", () => confirmSheet("Sinov",
        "Faqat SIZNING takrorlash savollaringiz muddati hozirga suriladi. Boshqa o'quvchilarga tegmaydi.", "Surish", test)),
      btn("alt", "🔍 Mening navbatim", queue));
    screen("Eslatmalar", "Takrorlash eslatmalarini boshqarish", toAdmin, ...nodes);
  }

  async function start() {
    try { await api("/api/rm/send", { method: "POST", body: "{}" }); } catch (e) { return toast(e.message); }
    progress();
  }

  async function progress() {
    stop();
    const draw = (j) => {
      const r = j.result;
      let body;
      if (j.running) body = good("⏳ Eslatmalar yuborilmoqda…");
      else if (j.error) body = el("div", { class: "card" }, j.error);
      else if (r && r.skipped) body = good("⏸ Eslatmalar o'chirilgan.");
      else if (r) body = good(`✅ Eslatmalar yuborildi\n\n👥 O'quvchilar: ${r.users}\n📨 Yuborildi: ${r.sent}\n❌ Yuborilmadi: ${r.failed}` +
        (r.tasks ? `\n\n📋 Topshiriqlar\n⏰ Muddat eslatmasi: ${r.tasks.sent} ta\n📊 Ustozga hisobot: ${r.tasks.summaries} ta` : ""));
      else body = good("Natija yo'q.");
      screen("Yuborish holati", j.running ? "Yuborilmoqda…" : "Yakunlandi", () => home(), body,
        el("div", { class: "hint", style: "margin-top:8px" }, "«Yuborilmadi» — botni bloklagan yoki chatni o'chirgan o'quvchilar."));
    };
    let r;
    try { r = await api("/api/rm/job"); } catch (e) { toast(e.message); return home(); }
    if (!r.job) return home();
    draw(r.job);
    if (!r.job.running) return;
    timer = setInterval(async () => {
      try {
        const x = await api("/api/rm/job");
        if (!x.job) return stop();
        draw(x.job);
        if (!x.job.running) stop();
      } catch (e) { stop(); }
    }, 2500);
  }

  async function test() {
    wait();
    let r;
    try { r = await api("/api/rm/test", { method: "POST", body: "{}" }); } catch (e) { toast(e.message); return home(); }
    const msg = r.count < 0 ? "⚠️ Sizning o'quvchi yozuvingiz topilmadi. Avval botdan ro'yxatdan o'ting."
      : r.count === 0 ? "🧪 Sinov\n\nSizda takrorlash navbatida savol yo'q.\n\nSinab ko'rish uchun Fanlardan test ishlang va bir-ikki savolga ataylab xato javob bering. Keyin shu tugmani qayta bosing."
        : `🧪 Sinov tayyor\n\n${r.count} ta savolning muddati hozirgi vaqtga surildi.\n\nEndi «Smart Takrorlash» ni oching — savollar darhol chiqadi.`;
    screen("Sinov", "Muddatni hozirga surish", () => home(), good(msg));
  }

  async function queue() {
    wait();
    let r;
    try { r = await api("/api/rm/queue"); } catch (e) { toast(e.message); return home(); }
    screen("Mening navbatim", "Takrorlash navbati va sabablari", () => home(), el("div", { class: "expl pre" }, r.text));
  }

  OQ.adminReminders = (back) => home(back);
})();
