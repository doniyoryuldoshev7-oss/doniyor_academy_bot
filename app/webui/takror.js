/* Veb-ilova: Bilimlar xaritasi va Smart Takrorlash (botdagi bilan bir xil qoidalar).
   oquvchi.js (window.OQ.u) dagi umumiy qismlar ishlatiladi. */
(function () {
  "use strict";
  const OQ = window.OQ;
  if (!OQ || !OQ.u || OQ.smart) return;
  const { screen, empty, pc, wait, good, bad, questionView, toOrigin, stat } = OQ.u;

  /* ---------- bilimlar xaritasi ---------- */
  async function knowledge() {
    wait();
    let d;
    try { d = await api("/api/k/subjects"); } catch (e) { toast(e.message); return OQ.profileHub(); }
    const nodes = d.subjects.length ? d.subjects.map((s) =>
      el("div", { class: "card tap", onclick: () => knowledgeSubject(s.id) },
        el("div", { class: "row" }, el("div", { class: "grow" }, el("b", {}, s.name),
          el("div", { class: "hint" }, s.answered ? `${s.answered} ta javob` : "Hali test ishlanmagan")),
          s.pct === null ? el("span", { class: "tag" }, "—") : el("b", { style: `color:${pc(s.pct)}` }, `${s.pct}%`), icon("chev"))))
      : [empty("Fanlar topilmadi.")];
    screen("Bilimlar xaritasi", "Qayerda kuchli, qayerda zaif ekaningizni ko'rsatadi. Fanni tanlang:", OQ.profileHub, ...nodes);
  }

  async function knowledgeSubject(id) {
    wait();
    let d;
    try { d = await api(`/api/k/subject/${id}`); } catch (e) { toast(e.message); return knowledge(); }
    const nodes = [el("div", { class: "glass" },
      el("div", { class: "row" }, el("b", { class: "grow" }, d.answered ? `${d.correct}/${d.answered} to'g'ri javob` : "Bu fandan hali test ishlamagansiz"),
        d.answered ? el("b", { style: `color:${pc(d.pct)}` }, `${d.pct}%`) : null),
      d.answered ? el("div", { class: "pbar", style: "margin-top:10px" }, el("i", { style: `width:${d.pct}%` })) : null)];
    if (!d.topics.length) nodes.push(empty("Bu fanda mavzu yo'q."));
    for (const t of d.topics) {
      const card = el("div", { class: "card" },
        el("div", { class: "row" }, el("div", { class: "grow" }, el("b", {}, `${t.icon} ${t.name}`)),
          el("span", { class: "tag" + (t.level === "strong" ? " done" : t.level === "medium" ? " active" : "") }, t.label)));
      if (t.pct !== null) {
        card.append(el("div", { class: "pbar", style: "margin:8px 0 4px" }, el("i", { style: `width:${t.pct}%;background:${pc(t.pct)}` })));
        card.append(el("div", { class: "hint" }, `${t.pct}% · ${t.done}/${t.total} savol` + (t.review ? ` · 🧠 takrorda: ${t.review}` : "")));
      }
      nodes.push(card);
    }
    if (d.tip) nodes.push(good(`💡 Tavsiya: ${d.tip.name} — ${d.tip.text}`));
    nodes.push(el("div", { class: "hint", style: "margin-top:8px" }, "🟢 mustahkam · 🟡 o'rtacha · 🔴 zaif · ⚪ ma'lumot kam"));
    screen(d.name, null, knowledge, ...nodes);
  }

  /* ---------- Smart Takrorlash ---------- */
  const R = { qs: [], i: 0, ans: null, busy: false, right: 0 };

  async function smart() {
    wait();
    let d;
    try { d = await api("/api/r/dashboard"); } catch (e) { toast(e.message); return toOrigin(); }
    if (!d.enabled) return screen("Smart Takrorlash", null, toOrigin, empty("Funksiya hozircha sinov rejimida."));
    screen("Smart Takrorlash", "Xato qilgan savollaringiz o'z vaqtida qayta beriladi.", toOrigin,
      el("div", { class: "stats3" },
        el("div", {}, el("div", { class: "ic o" }, icon("clock")), el("b", {}, String(d.due)), el("small", {}, "Bugun takrorlanadi")),
        el("div", {}, el("div", { class: "ic b" }, icon("list")), el("b", {}, String(d.upcoming)), el("small", {}, "Keyinroq")),
        el("div", {}, el("div", { class: "ic g" }, icon("check")), el("b", {}, String(d.mastered)), el("small", {}, "Mustahkamlangan"))),
      d.due ? el("button", { class: "btn", onclick: smartStart }, icon("play"), "Takrorlashni boshlash")
        : el("div", { class: "card empty" }, mascot(), el("div", { style: "margin-top:8px" }, "Hozircha takrorlash uchun savol yo'q.")));
  }

  async function smartStart() {
    let d;
    try { d = await api("/api/r/due"); } catch (e) { return toast(e.message); }
    if (!d.questions.length) return smart();
    Object.assign(R, { qs: d.questions, i: 0, ans: null, busy: false, right: 0 });
    closing(true);
    smartView();
  }

  const quit = () => confirmSheet("Takrorlashni tugatasizmi?", "Javob bergan savollaringiz saqlangan.", "Tugatish", smartDone);

  function smartView() {
    backTo(quit);
    const total = R.qs.length, a = R.ans;
    questionView({
      q: R.qs[R.i], a, i: R.i, total, done: R.i + (a ? 1 : 0), src: "🧠 Smart Takrorlash", last: R.i + 1 >= total,
      chip: `✅ ${R.right}`,
      okText: a && a.mastered ? "✅ To'g'ri javob!\n🏆 Savol mustahkamlandi!" : "✅ To'g'ri javob!\n🕒 Keyingi takrorlash rejalashtirildi.",
      badText: a ? `❌ Noto'g'ri javob.\n💡 To'g'ri variant: ${a.correct}\n🕒 Savol 1 kundan keyin qayta beriladi.` : "",
      onQuit: quit, onFinish: quit, onPick: smartPick,
      onNext: () => (R.i + 1 < total ? (R.i++, R.ans = null, smartView()) : smartDone()),
      nextLabel: R.i + 1 < total ? "Keyingi savol" : "Yakunlash",
    });
  }

  async function smartPick(letter) {
    const q = R.qs[R.i];
    if (R.busy || R.ans) return;
    R.busy = true; haptic();
    try {
      const r = await api("/api/r/answer", { method: "POST", body: JSON.stringify({ qid: q.id, sel: letter }) });
      R.ans = { sel: letter, ok: r.ok, correct: r.correct, mastered: r.mastered, e: r.e };
      if (r.ok) R.right++;
      notify(r.ok ? "success" : "error");
    } catch (e) { toast(e.message); }
    R.busy = false;
    smartView();
  }

  function smartDone() {
    closing(false);
    screen("Takrorlash yakunlandi 🎉", null, smart,
      el("div", { class: "card" }, stat("To'g'ri javoblar", `${R.right} / ${R.qs.length}`)),
      el("button", { class: "btn", onclick: smart }, "Smart Takrorlash"));
  }

  OQ.knowledge = knowledge;
  OQ.smart = smart;
})();
