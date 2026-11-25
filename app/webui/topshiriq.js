/* Veb-ilova: Topshiriqlarim (botdagidek). oquvchi.js (window.OQ.u) dagi umumiy qismlar ishlatiladi. */
(function () {
  "use strict";
  const OQ = window.OQ;
  if (!OQ || !OQ.u || OQ.assignments) return;
  const { screen, empty, pc, wait, toOrigin } = OQ.u;

  async function assignments() {
    wait();
    let d;
    try { d = await api("/api/a/list"); } catch (e) { toast(e.message); return toOrigin(); }
    const nodes = [el("div", { class: "hint" }, "Topshiriq bitta testda to'liq bajarilishi kerak: barcha savollarga javob berib, testni yakunlang.")];
    if (!d.assignments.length) nodes.push(empty("Hozircha faol topshiriq yo'q. Ustoz topshiriq berganda, shu yerda ko'rinadi."));
    for (const a of d.assignments) {
      const card = el("div", { class: "card" },
        el("div", { class: "row" }, el("div", { class: "grow" }, el("b", {}, a.topic), el("div", { class: "hint" }, `${a.subject} · ${a.required} ta savol · ⏰ ${a.deadline}`)),
          el("span", { class: "tag " + (a.done ? "done" : a.answered ? "active" : "") }, a.done ? "bajarildi" : a.personal ? "shaxsiy" : "faol")),
        el("div", { class: "pbar", style: "margin:10px 0 6px" }, el("i", { style: `width:${a.pct}%;background:${a.done ? "var(--g)" : pc(a.pct)}` })),
        el("div", { class: "hint" }, a.done ? "✅ Bajarildi" : a.answered ? `Eng yaxshi urinish: ${a.answered}/${a.required} (${a.pct}%) — testni to'liq ishlab chiqing` : "0% — hali boshlanmagan"));
      if (a.note) card.append(el("div", { class: "hint pre", style: "margin-top:6px" }, a.note));
      if (!a.done) card.append(el("button", { class: "btn small", style: "margin-top:10px", onclick: () => OQ.startQuiz(a.topic_id, a.required, "asg") }, icon("play"), a.answered ? "Qayta urinish" : "Boshlash"));
      nodes.push(card);
    }
    screen("Topshiriqlarim", null, toOrigin, ...nodes);
  }

  OQ.assignments = assignments;
})();
