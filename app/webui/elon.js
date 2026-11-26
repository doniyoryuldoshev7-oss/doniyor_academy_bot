/* Veb-ilova: E'lonlar (faqat admin). Har ekranda orqaga tugmasi bor. */
(function () {
  "use strict";
  const OQ = window.OQ;
  if (!OQ || !OQ.u || OQ.adminAds) return;
  const { screen, wait, good } = OQ.u;
  const btn = (cls, label, fn) => el("button", { class: "btn " + cls, onclick: fn }, label);
  let back0 = null;
  const toAdmin = () => (back0 ? back0() : OQ.admin && OQ.admin());
  let timer = null;
  const stopTimer = () => { if (timer) { clearInterval(timer); timer = null; } };
  const fmt = (ts) => { try { return new Date(ts * 1000).toLocaleString("uz-UZ", { timeZone: "Asia/Tashkent" }); } catch (e) { return ""; } };

  async function home(back) {
    if (typeof back === "function") back0 = back;
    stopTimer();
    wait();
    let d;
    try { d = await api("/api/ad/info"); } catch (e) { toast(e.message); return toAdmin(); }
    const nodes = [el("div", { class: "hint" }, `E'lon botdagi barcha ${d.total} foydalanuvchiga yuboriladi. Veb-ilovadan faqat matn yuboriladi (rasm yoki fayl kerak bo'lsa, botdagi «📢 E'lonlar» dan foydalaning).`)];
    const j = d.job;
    if (j) {
      nodes.push(el("div", { class: "card tap", onclick: () => progress() },
        el("b", {}, j.running ? "⏳ Hozir yuborilmoqda" : "So'nggi e'lon"),
        el("div", { class: "hint" }, `«${j.preview}» · ${j.ok} yetdi, ${j.fail} yetmadi, jami ${j.total}${j.finished ? " · " + fmt(j.finished) : ""}`)));
    }
    const ta = el("textarea", { class: "imp", maxlength: String(d.max), placeholder: `E'lon matni (eng ko'pi ${d.max} belgi)`, style: "height:160px;font-family:inherit;font-size:15px" });
    nodes.push(ta, btn("", "Ko'rib chiqish", async () => {
      const text = ta.value.trim();
      if (!text) return toast("E'lon matnini yozing.");
      let p;
      try { p = await api("/api/ad/preview", { method: "POST", body: JSON.stringify({ text }) }); } catch (e) { return toast(e.message); }
      screen("E'lon ko'rinishi", "Yuborishdan oldin tekshiring.", () => home(),
        el("div", { class: "expl pre" }, p.text),
        el("div", { class: "hint", style: "margin:8px 0" }, `Qabul qiluvchilar: ${p.count} ta foydalanuvchi`),
        btn("", `✅ Yuborish (${p.count} kishi)`, async () => {
          try { await api("/api/ad/send", { method: "POST", body: JSON.stringify({ text }) }); } catch (e) { return toast(e.message); }
          progress();
        }),
        btn("alt", "❌ Bekor qilish", () => home()));
    }));
    screen("E'lonlar", "Barcha foydalanuvchilarga xabar yuborish.", toAdmin, ...nodes);
  }

  async function progress() {
    stopTimer();
    const draw = (j) => {
      const done = j.ok + j.fail;
      screen("Yuborish holati", j.running ? "Yuborilmoqda…" : "Yakunlandi", () => home(),
        good(`${j.running ? "⏳" : "✅"} ${done} / ${j.total}\n✅ Yetdi: ${j.ok}\n❌ Yetmadi: ${j.fail}`),
        el("div", { class: "hint", style: "margin-top:8px" }, "«Yetmadi» — odatda botni bloklagan yoki o'chirib yuborgan foydalanuvchilar."));
    };
    let r;
    try { r = await api("/api/ad/status"); } catch (e) { toast(e.message); return home(); }
    if (!r.job) return home();
    draw(r.job);
    if (!r.job.running) return;
    timer = setInterval(async () => {
      try {
        const x = await api("/api/ad/status");
        if (!x.job) return stopTimer();
        draw(x.job);
        if (!x.job.running) stopTimer();
      } catch (e) { stopTimer(); }
    }, 2000);
  }

  OQ.adminAds = (back) => home(back);
})();
