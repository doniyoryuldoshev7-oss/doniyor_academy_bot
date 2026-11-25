/* Doniyor Academy veb-ilovasi: o'quvchi bo'limi (botdagi funksiyalar).
   index.html dagi umumiy funksiyalar ishlatiladi: el, icon, show, page, tabBar, api, toast, backTo, confirmSheet,
   blobUrl, menuTile, openTab, haptic, closing, stopTimer, fatal, mascot, soon, S, tg.
   Hamma narsa IIFE ichida: index.html dagi nomlar bilan to'qnashmaydi. Tashqariga faqat window.OQ chiqadi. */
(function () {
  "use strict";
  if (window.OQ) return;

  /* ---------- qo'shimcha ikonlar (mavjud <defs> ga qo'shiladi) ---------- */
  (function addIcons() {
    try {
      const defs = document.querySelector("svg defs");
      if (!defs) return;
      const add = {
        brain: '<path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V4a3 3 0 0 0-3 0zM15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1"/>',
        target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
        alert: '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/>',
        map: '<path d="M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2zM9 4v14M15 6v14"/>',
        list: '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
        refresh: '<path d="M20 11a8 8 0 0 0-14-4M4 5v4h4M4 13a8 8 0 0 0 14 4M20 19v-4h-4"/>',
      };
      const doc = new DOMParser().parseFromString(
        '<svg xmlns="http://www.w3.org/2000/svg">' +
          Object.entries(add).map(([k, v]) => `<symbol id="i-${k}" viewBox="0 0 24 24">${v}</symbol>`).join("") + "</svg>",
        "image/svg+xml");
      for (const sym of Array.from(doc.documentElement.children)) {
        if (!document.getElementById(sym.id)) defs.appendChild(document.importNode(sym, true));
      }
    } catch (e) {}
  })();

  /* ---------- umumiy yordamchilar ---------- */
  const pc = (p) => (p >= 70 ? "var(--g)" : p >= 50 ? "var(--o)" : "var(--r)");
  const toOrigin = () => openTab(S.tab || "home");

  function wait() { show({ focus: false }, el("div", { class: "empty" }, "Yuklanmoqda…")); }

  /* oddiy ekran: orqaga (x) tugmasi + sarlavha + tab-bar */
  function screen(title, sub, backFn, ...nodes) {
    backTo(backFn);
    show({ focus: false },
      el("button", { class: "x", style: "margin-bottom:8px", onclick: backFn, "aria-label": "Orqaga" }, icon("left")),
      el("h1", {}, title), sub ? el("div", { class: "hint" }, sub) : null, ...nodes, tabBar());
  }
  const tiles = (...t) => el("div", { class: "mt" }, ...t);
  const stat = (k, v, color) => el("div", { class: "stat" }, el("span", {}, k), el("b", color ? { style: "color:" + color } : {}, String(v)));
  const empty = (text) => el("div", { class: "card empty" }, mascot(), el("div", { style: "margin-top:8px" }, text));
  function qimage(id) {
    const img = el("img", { class: "q", alt: "" });
    blobUrl(`/api/s/qimg/${id}`).then((u) => (img.src = u)).catch(() => img.remove());
    return img;
  }
  const bad = (text) => el("div", { class: "expl pre", style: "background:#ff6b7812;border-color:#ff6b7833;color:#ffd0d4" }, text);
  const good = (text) => el("div", { class: "expl pre" }, text);

  /* savol ko'rinishi (test, Smart Takrorlash uchun umumiy) */
  function questionView(c) {
    const q = c.q, a = c.a;
    const nodes = [
      el("div", { class: "qbar" },
        el("div", { class: "row" },
          el("button", { class: "x", onclick: c.onQuit, "aria-label": "Chiqish" }, icon("x")),
          el("div", { class: "grow" }, el("b", {}, `${c.i + 1}-savol `), el("span", { class: "hint" }, `(${c.i + 1}/${c.total})`)),
          c.chip ? el("span", { class: "chip" }, c.chip) : null),
        el("div", { class: "pbar", style: "margin-top:10px" }, el("i", { style: `width:${c.done / c.total * 100}%` }))),
    ];
    if (c.src) nodes.push(el("div", { class: "src" }, c.src));
    if (q.t) nodes.push(el("div", { class: "q pre" }, q.t));
    if (q.img) nodes.push(qimage(q.id));
    for (const [letter, text] of Object.entries(q.o)) {
      if (!text) continue;
      let cls = "opt";
      if (a) { cls += " static"; if (letter === a.correct) cls += " right"; else if (letter === a.sel) cls += " wrong"; }
      nodes.push(el("button", { class: cls, onclick: () => { if (!a) c.onPick(letter); } },
        el("em", {}, letter), el("span", { class: "pre grow" }, text)));
    }
    if (a) nodes.push(a.ok ? good(c.okText) : bad(c.badText), a.e ? good("💡 " + a.e) : null);
    nodes.push(el("div", { class: "nav" },
      el("button", { class: "btn alt sq", onclick: c.onFinish, "aria-label": "Tugatish" }, icon("flag")),
      a ? el("button", { class: "btn", onclick: c.onNext }, c.nextLabel, icon(c.last ? "flag" : "right"))
        : (c.onSkip ? el("button", { class: "btn alt", onclick: c.onSkip }, "O'tkazib yuborish") : el("div", { class: "grow" }))));
    show({ focus: true }, ...nodes);
  }

  /* ---------- markaziy menyular (botdagi tuzilma) ---------- */
  function testHub() {
    stopTimer(); closing(false);
    screen("Sinov va imtihon", "Bilimingizni sinab ko'ring:", toOrigin,
      tiles(
        menuTile("book", "b", "Fanlar", "Fan va mavzu bo'yicha test", subjects),
        menuTile("clip", "o", "Milliy sertifikat", "Milliy sertifikat imtihoni", () => openTab("exams")),
        menuTile("target", "p", "Diagnostika", "Bilim darajasini aniqlash", () => soon("Diagnostika"), { soon: true })));
  }
  function profileHub() {
    stopTimer(); closing(false);
    screen("Profil", "Ma'lumotlaringiz, natijalaringiz va o'rningiz:", toOrigin,
      tiles(
        menuTile("user", "g", "Mening profilim", "Ma'lumotlar va tarix", profile),
        menuTile("map", "b", "Bilimlar xaritasi", "Kuchli va zaif joylar", () => (window.OQ.knowledge ? window.OQ.knowledge() : soon("Bilimlar xaritasi"))),
        menuTile("trophy", "o", "Reyting", "TOP 10 va o'rningiz", leaderboard),
        menuTile("chart", "p", "Statistikam", "Javoblar va foiz", () => stats(profileHub))));
  }
  function resultTiles() {
    return tiles(
      menuTile("list", "b", "Testlar tarixi", "Ishlagan testlaringiz", () => history()),
      menuTile("chart", "p", "Statistikam", "Javoblar va foiz", () => stats()));
  }

  /* ---------- fan -> mavzu -> test ---------- */
  async function subjects() {
    wait();
    let d;
    try { d = await api("/api/s/subjects"); } catch (e) { toast(e.message); return testHub(); }
    const nodes = d.subjects.length ? d.subjects.map((s) =>
      el("div", { class: "card tap", onclick: () => topics(s.id) },
        el("div", { class: "row" }, el("div", { class: "ic b", style: "width:38px;height:38px;border-radius:12px;display:grid;place-items:center;flex:none" }, icon("book")),
          el("div", { class: "grow" }, el("b", {}, s.name), el("div", { class: "hint" }, `${s.count} ta savol`)), icon("chev"))))
      : [empty("Hozircha fanlar yo'q.")];
    screen("Fanlar", "Fanni tanlang:", testHub, ...nodes);
  }

  async function topics(sid) {
    wait();
    let d;
    try { d = await api(`/api/s/topics/${sid}`); } catch (e) { toast(e.message); return subjects(); }
    const nodes = d.topics.length ? d.topics.map((t) =>
      el("div", { class: "card tap", style: t.count ? "" : "opacity:.55", onclick: () => (t.count ? topicInfo(sid, t) : toast("Bu mavzuda hozircha test yo'q.")) },
        el("div", { class: "row" }, el("div", { class: "grow" }, el("b", {}, t.name)), el("span", { class: "tag" }, `${t.count} ta`), icon("chev"))))
      : [empty("Bu fanda mavzu yo'q.")];
    screen(d.subject.name, "Mavzuni tanlang:", subjects, ...nodes);
  }

  function topicInfo(sid, t) {
    let size = t.count;
    const sizes = [...new Set([10, 20, 30, 50].filter((n) => n < t.count).concat([t.count]))];
    const box = el("div", { class: "row", style: "flex-wrap:wrap" });
    const paint = () => box.replaceChildren(...sizes.map((n) =>
      el("button", { class: "btn small" + (n === size ? "" : " alt"), onclick: () => { size = n; paint(); } }, n === t.count ? `Hammasi (${n})` : String(n))));
    paint();
    screen(t.name, `Jami savollar: ${t.count}`, () => topics(sid),
      el("div", { class: "card" }, el("div", { class: "hint", style: "margin-bottom:8px" }, "Nechta savol:"), box),
      el("div", { class: "card pre" }, "ℹ️ Har javobdan keyin to'g'ri javob va izoh ko'rsatiladi. Vaqt cheklanmagan."),
      el("button", { class: "btn", onclick: () => startQuiz(t.id, size) }, icon("play"), "Testni boshlash"));
  }

  const Q = { topic: null, qs: [], i: 0, ans: {}, busy: false };

  async function startQuiz(topicId, size) {
    let d;
    try { d = await api("/api/s/start", { method: "POST", body: JSON.stringify({ topic_id: topicId, size }) }); }
    catch (e) { return toast(e.message); }
    Object.assign(Q, { topic: d.topic, qs: d.questions, i: 0, ans: {}, busy: false });
    closing(true);
    quizView();
  }

  const askQuit = () => confirmSheet("Testni tugatasizmi?", "Hozirgacha berilgan javoblar bilan natija saqlanadi.", "✅ Tugatish", finishQuiz);

  function quizView() {
    backTo(askQuit);
    const total = Q.qs.length, q = Q.qs[Q.i], a = Q.ans[q.id];
    const vals = Object.values(Q.ans), right = vals.filter((x) => x.ok).length;
    questionView({
      q, a, i: Q.i, total, done: vals.length, src: Q.topic.name, last: Q.i + 1 >= total,
      chip: `✅ ${right}/${vals.length}`,
      okText: "✅ To'g'ri!", badText: a ? `❌ Xato. To'g'ri javob: ${a.correct}` : "",
      onQuit: askQuit, onFinish: askQuit, onPick: quizPick,
      onNext: () => (Q.i + 1 < total ? (Q.i++, quizView()) : finishQuiz()),
      nextLabel: Q.i + 1 < total ? "Keyingi" : "Natijani ko'rish",
      onSkip: Q.i + 1 < total ? () => { Q.i++; quizView(); } : null,
    });
  }

  async function quizPick(letter) {
    const q = Q.qs[Q.i];
    if (Q.busy || Q.ans[q.id]) return;
    Q.busy = true; haptic();
    try {
      const r = await api("/api/s/check", { method: "POST", body: JSON.stringify({ qid: q.id, sel: letter }) });
      Q.ans[q.id] = { sel: letter, ok: r.ok, correct: r.correct, e: r.e };
      notify(r.ok ? "success" : "error");
    } catch (e) { toast(e.message); }
    Q.busy = false;
    quizView();
  }

  async function finishQuiz() {
    if (!Object.keys(Q.ans).length) { toast("Hali birorta savolga javob berilmadi."); closing(false); return testHub(); }
    wait(); backTo(null);
    let r;
    try {
      r = await api("/api/s/finish", { method: "POST", body: JSON.stringify({
        topic_id: Q.topic.id, qids: Q.qs.map((x) => x.id),
        answers: Object.entries(Q.ans).map(([qid, v]) => ({ qid: Number(qid), sel: v.sel })) }) });
    } catch (e) { toast(e.message); return quizView(); }
    closing(false);
    backTo(testHub);
    show({ focus: false },
      el("button", { class: "x", style: "margin-bottom:8px", onclick: testHub, "aria-label": "Orqaga" }, icon("left")),
      el("div", { class: "row" }, el("div", { class: "grow" }, el("h1", {}, "Test natijasi"), el("div", { class: "hint" }, Q.topic.name)), mascot()),
      el("div", { style: "text-align:center" },
        el("div", { class: "big" }, el("div", { class: "score", style: `--p:${r.pct}` }, el("i", {}, el("div", {}, el("b", {}, `${r.pct}%`), el("small", {}, `${r.correct}/${r.total}`))))),
        el("span", { class: "lvl" }, r.grade)),
      el("div", { class: "stats" },
        el("div", {}, String(r.correct), el("small", {}, "to'g'ri")),
        el("div", {}, String(r.total - r.correct), el("small", {}, "xato yoki javobsiz")),
        el("div", {}, `+${r.score}`, el("small", {}, "ball"))),
      el("button", { class: "btn gold", onclick: () => attempt(r.attempt_id, testHub) }, "📖 Test tafsilotlari"),
      el("button", { class: "btn alt", onclick: errors }, icon("alert"), "Mening xatolarim"),
      el("button", { class: "btn alt", onclick: testHub }, "Sinov va imtihon"),
      tabBar());
  }

  /* ---------- tarix va tafsilot ---------- */
  async function history(backFn) {
    backFn = typeof backFn === "function" ? backFn : toOrigin;
    wait();
    let d;
    try { d = await api("/api/s/history"); } catch (e) { toast(e.message); return backFn(); }
    const nodes = d.attempts.length ? d.attempts.map((a, i) =>
      el("div", { class: "card tap", onclick: () => attempt(a.id, () => history(backFn)) },
        el("div", { class: "row" }, el("div", { class: "grow" }, el("b", {}, `${i + 1}. ${a.topic}`),
          el("div", { class: "hint" }, `${a.correct}/${a.total} · +${a.score} ball · ${a.date}`)),
          el("b", { style: `color:${pc(a.pct)}` }, `${a.pct}%`), icon("chev"))))
      : [empty("Hozircha test ishlanmagan.")];
    screen("Testlar tarixi", "Oxirgi ishlagan testlaringiz:", backFn, ...nodes);
  }

  async function attempt(id, backFn) {
    wait();
    let d;
    try { d = await api(`/api/s/attempt/${id}`); } catch (e) { toast(e.message); return backFn(); }
    const nodes = [
      el("div", { class: "card" },
        stat("Mavzu", d.topic), stat("Natija", `${d.correct}/${d.total}`), stat("Foiz", `${d.pct}%`, pc(d.pct)),
        stat("Javobsiz", d.unanswered), stat("Ball", `+${d.score}`), stat("Baho", d.grade), stat("Sana", d.date)),
      el("div", { class: "sec" }, "Javoblar"),
    ];
    d.items.forEach((it, i) => {
      const card = el("div", { class: "card" }, el("b", {}, `${it.ok ? "✅" : "❌"} ${i + 1}-savol`));
      if (it.t) card.append(el("div", { class: "pre", style: "margin:6px 0" }, it.t));
      if (it.img) card.append(qimage(it.id));
      for (const [letter, text] of Object.entries(it.o)) {
        if (!text) continue;
        card.append(el("div", { class: "opt static " + (letter === it.c ? "right" : letter === it.yours ? "wrong" : "") },
          el("em", {}, letter), el("span", { class: "pre grow" }, text)));
      }
      card.append(el("div", { class: "hint" }, `Sizniki: ${it.yours || "yo'q"} · To'g'ri: ${it.c}`));
      if (it.e) card.append(good("💡 " + it.e));
      nodes.push(card);
    });
    if (d.unanswered) nodes.push(el("div", { class: "hint" }, `⚠️ ${d.unanswered} ta savolga javob berilmagan.`));
    screen("Test natijasi", null, backFn, ...nodes);
  }

  /* ---------- profil, statistika, reyting ---------- */
  async function profile() {
    wait();
    let p;
    try { p = await api("/api/s/profile"); } catch (e) { toast(e.message); return profileHub(); }
    screen("Mening profilim", null, profileHub,
      el("div", { class: "glass row" },
        el("div", { class: "logo", style: "width:52px;height:52px;border-radius:50%" }, (p.name || "?")[0].toUpperCase()),
        el("div", { class: "grow" }, el("b", {}, p.name), el("div", { class: "hint" }, p.username ? "@" + p.username : "username yo'q"),
          el("div", { class: "hint" }, p.group ? `Guruh: ${p.group}` : "Guruhsiz"))),
      el("div", { class: "stats3" },
        el("div", {}, el("div", { class: "ic b" }, icon("clip")), el("b", {}, String(p.tests)), el("small", {}, "Testlar")),
        el("div", {}, el("div", { class: "ic g" }, icon("check")), el("b", {}, String(p.correct)), el("small", {}, "To'g'ri javob")),
        el("div", {}, el("div", { class: "ic o" }, icon("trophy")), el("b", {}, String(p.score)), el("small", {}, "Ball"))),
      el("button", { class: "btn", onclick: () => history(profile) }, icon("list"), "Testlar tarixi"));
  }

  async function stats(backFn) {
    backFn = typeof backFn === "function" ? backFn : toOrigin;
    wait();
    let p;
    try { p = await api("/api/s/profile"); } catch (e) { toast(e.message); return backFn(); }
    screen("Statistikam", null, backFn,
      el("div", { class: "glass" },
        el("div", { class: "row" }, el("b", { class: "grow" }, "Umumiy aniqlik"), el("b", { style: `color:${pc(p.pct)}` }, `${p.pct}%`)),
        el("div", { class: "pbar", style: "margin-top:10px" }, el("i", { style: `width:${p.pct}%` }))),
      el("div", { class: "card" },
        stat("Savollar", p.questions), stat("To'g'ri", p.correct), stat("Xato", p.wrong),
        stat("Testlar", p.tests), stat("Ball", p.score)));
  }

  async function leaderboard() {
    wait();
    let d;
    try { d = await api("/api/s/leaderboard"); } catch (e) { toast(e.message); return profileHub(); }
    const medals = ["🥇", "🥈", "🥉"];
    const card = el("div", { class: "card" });
    d.top.forEach((u, i) => card.append(el("div", { class: "stat", style: u.me ? "color:var(--gold);font-weight:800" : "" },
      el("span", {}, `${medals[i] || (i + 1) + "."} ${u.name}${u.me ? " ← siz" : ""}`), el("b", {}, `${u.score} ball`))));
    if (!d.top.length) card.append(el("div", { class: "empty" }, "Hozircha reyting bo'sh."));
    screen("TOP 10 reyting", null, profileHub, card,
      el("div", { class: "hint" }, `Sizning o'rningiz: ${d.me.rank} · ${d.me.score} ball`));
  }

  /* ---------- mening xatolarim ---------- */
  async function errors(page) {
    page = Number.isInteger(page) ? page : 0;
    wait();
    let d;
    try { d = await api("/api/s/errors"); } catch (e) { toast(e.message); return toOrigin(); }
    const per = 5, pages = Math.max(1, Math.ceil(d.errors.length / per));
    page = Math.min(page, pages - 1);
    const nodes = [];
    if (!d.errors.length) nodes.push(el("div", { class: "card empty" }, mascot(), el("div", { style: "margin-top:8px" }, "🎉 Xatolar mavjud emas!")));
    else {
      d.errors.slice(page * per, page * per + per).forEach((q, i) => {
        const txt = (q.t || "Rasmli savol").replace(/\s+/g, " ");
        nodes.push(el("div", { class: "card tap", onclick: () => retry(q, page, []) },
          el("div", { class: "row" }, el("div", { class: "grow" }, `${page * per + i + 1}. ${txt.length > 80 ? txt.slice(0, 80) + "…" : txt}`),
            el("span", { class: "tag active" }, "Qayta"), icon("chev"))));
      });
      if (pages > 1) nodes.push(el("div", { class: "row", style: "margin-top:10px" },
        el("button", { class: "btn alt", disabled: page === 0, onclick: () => errors(page - 1) }, icon("left"), "Oldingi"),
        el("button", { class: "btn alt", disabled: page >= pages - 1, onclick: () => errors(page + 1) }, "Keyingi", icon("right"))));
    }
    screen("Mening xatolarim", d.errors.length ? `Jami: ${d.errors.length} ta xato · sahifa ${page + 1}/${pages}. Qayta ishlash uchun savolni tanlang:` : null,
      toOrigin, ...nodes);
  }

  function retry(q, page, wrong) {
    const nodes = [el("div", { class: "hint" }, "To'g'ri javobni toping.")];
    if (q.t) nodes.push(el("div", { class: "q pre" }, q.t));
    if (q.img) nodes.push(qimage(q.id));
    for (const [letter, text] of Object.entries(q.o)) {
      if (!text) continue;
      const w = wrong.includes(letter);
      nodes.push(el("button", { class: "opt" + (w ? " wrong static" : ""), onclick: () => { if (!w) solve(q, page, letter, wrong); } },
        el("em", {}, letter), el("span", { class: "pre grow" }, text)));
    }
    if (wrong.length) nodes.push(bad("❌ Yana urinib ko'ring! To'g'ri javobni topmaguningizcha davom etamiz."));
    screen("Xatoni qayta ishlash", null, () => errors(page), ...nodes);
  }

  async function solve(q, page, letter, wrong) {
    haptic();
    let r;
    try { r = await api("/api/s/error_solve", { method: "POST", body: JSON.stringify({ qid: q.id, sel: letter }) }); }
    catch (e) { return toast(e.message); }
    if (!r.ok) { notify("error"); return retry(q, page, wrong.concat([letter])); }
    notify("success");
    screen("Barakalla! 🎉", null, () => errors(0),
      el("div", { class: "card" }, el("div", { class: "pre" }, q.t || ""),
        el("div", { class: "opt static right", style: "margin-top:10px" }, el("em", {}, r.correct), el("span", { class: "pre grow" }, q.o[r.correct] || ""))),
      r.e ? good("💡 " + r.e) : null,
      good("🏆 Bu savol o'zlashtirildi! Endi u «Mening xatolarim» bo'limida chiqmaydi."),
      el("button", { class: "btn", style: "margin-top:12px", onclick: () => errors(0) }, "Mening xatolarim"));
  }

  window.OQ = {
    testHub, profileHub, resultTiles, subjects, history, profile, stats, leaderboard, errors,
    knowledge: null, smart: null,
    u: { screen, tiles, stat, empty, qimage, pc, wait, good, bad, questionView, toOrigin },
  };

  /* qo'shimcha bo'limlar (har biri alohida fayl; biri yuklanmasa, qolganlari ishlayveradi) */
  ["takror"].forEach((n) => {
    const sc = document.createElement("script");
    sc.src = `/app/${n}.js`;
    document.head.append(sc);
  });
})();
