"""Milliy sertifikat imtihonini bosqichma-bosqich yig'ish.

1-bosqich: yopiq savollar (1-35)
2-bosqich: ochiq savollar (36-45, har birida a) va b))
3-bosqich: birlashtirib o'quvchilarga taqdim etish (imtihon yaratish ekrani
           imtihon_ustoz.py da: «exm:fx»)

Savollar bazaga yozilmaydi: ular faqat shu imtihonning o'zida saqlanadi.
Qoralama FSM ichida «mly» kaliti ostida turadi (bot qayta ishga tushsa ham saqlanadi).
"""
from __future__ import annotations

import html
import io
import logging
import re
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..admin_roles import is_full_admin, is_teacher
from ..services.savol_import import (
    ANS_RE,
    EXP_RE,
    IMG_DIR,
    IMG_RE,
    MAX_FILE,
    MAX_TEXT,
    ACC_RE,
    SRC_RE,
    extract_text,
    image_ref,
    parse_text,
    save_image_bytes,
)

from ..services.imtihon import milliy_open_ok, open_range_text

log = logging.getLogger(__name__)
router = Router()

CLOSED_N = 35   # 1-35
OPEN_N = 10     # 36-45 (har birida a va b)
OPEN_N55 = 20   # 55 talik fayl: 36-55 (har biri bitta javobli)


_SEP = re.compile(r"^\s*(?:---+|\*\*\*+|===+)\s*$")
_PART = re.compile(r"^\s*[a-dA-D]\s*[\)\.]")


class MilliyYig(StatesGroup):
    closed = State()
    opened = State()
    everything = State()   # 1-45 bitta fayldan


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def B(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


def esc(s):
    return html.escape(str(s))


async def show(c: CallbackQuery, text: str, markup):
    try:
        await c.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "not modified" in str(exc).lower():
            return
        await c.message.answer(text, reply_markup=markup)


async def allowed(uid: int) -> bool:
    return bool(is_full_admin(uid) or await is_teacher(uid))


async def gate(c: CallbackQuery) -> bool:
    if await allowed(c.from_user.id):
        return True
    await c.answer("Ruxsat yo'q.", show_alert=True)
    return False


async def clear_keep(state: FSMContext):
    """Holatni tozalaydi, lekin tugallanmagan Milliy sertifikat qoralamasini saqlab qoladi."""
    keep = (await state.get_data()).get("mly")
    await state.clear()
    if keep:
        await state.update_data(mly=keep)


# ---------------------------------------------------------------
#  Matnlar
# ---------------------------------------------------------------
HELP_CLOSED = (
    f"Yopiq savollarni yuboring (<b>{CLOSED_N} ta</b>, 1–{CLOSED_N}): matn, .txt, .docx yoki .xlsx.\n\n"
    "<code>1. Savol matni\nA) variant\nB) variant\nC) variant\nD) variant\nJavob: B</code>\n\n"
    "• Ro'yxatli savol (1) 2) 3) yoki a) b) c) qatorlari, atamalar, asarlar va mualliflar) — "
    "savol matnida oddiy qator qilib yozing. Variantlar faqat KATTA A) B) C) D).\n"
    "• Rasmli savol (xarita, jadval, Venn diagrammasi): avval rasmni yuboring, keyin savol matnini.\n"
    "• Hammasi rasmda bo'lsa: rasmni yuboring, keyin faqat <code>17. Javob: C</code> deb yozing.\n"
    "• 33–35 kabi A–F li umumiy variantlar eng oxirida:\n"
    "<code>Umumiy variantlar: javobni tanlang\nA) …\nF) …\n33. Savol\nJavob: B</code>\n"
    "• <b>Izoh va manba</b> (ixtiyoriy): to'g'ri javobdan keyin <code>Izoh: nega bu javob to'g'ri</code> va "
    "<code>Manba: 8-sinf O'zbekiston tarixi, 52-bet</code> qatorlari. O'quvchi ularni imtihon yopilgach tahlilda ko'radi. "
    "Izohda harf (A, B…) emas, javobning o'zini yozing — variantlar har o'quvchiga aralashtiriladi.\n"
    "\nHammasini yuborgach «✅ Tayyor» ni bosing."
)

HELP_OPEN = (
    f"Ochiq savollarni yuboring (<b>{OPEN_N} ta</b>, {CLOSED_N + 1}–{CLOSED_N + OPEN_N}). "
    "Har birida a) va b) qismi bor. Savollar <code>---</code> qatori bilan ajratiladi.\n\n"
    "<code>Savol matni\na) birinchi qism =&gt; javob1 | javob2\nb) ikkinchi qism =&gt; javob</code>\n"
    "<code>|</code> — muqobil to'g'ri javoblar.\n"
    "• <b>Izoh va manba</b> (ixtiyoriy): savol blokining oxirida <code>Izoh: …</code> va <code>Manba: …</code> qatorlari "
    "(o'quvchi imtihon yopilgach tahlilda ko'radi).\n\n"
    "Hammasini yuborgach «✅ Tayyor» ni bosing."
)


HELP_ALL = (
    f"Hamma savolni (1–{CLOSED_N + OPEN_N}) <b>bitta fayl</b> qilib yuboring: .docx, .txt yoki .xlsx. "
    "Matnni to'g'ridan-to'g'ri yozsangiz ham bo'ladi. Rasmlar Word ichida turishi mumkin.\n\n"
    f"<b>Tuzilishi:</b> avval {CLOSED_N} ta yopiq savol, keyin <code>OCHIQ SAVOLLAR</code> deb yozilgan qator, "
    f"keyin {OPEN_N} ta ochiq savol.\n\n"
    "<code>1. Savol matni\nA) variant\nB) variant\nC) variant\nD) variant\nJavob: B\n\n"
    "… (35 gacha)\n\n"
    "OCHIQ SAVOLLAR\n\n"
    "36. Savol matni\na) birinchi qism =&gt; javob1 | javob2\nb) ikkinchi qism =&gt; javob\n---\n"
    "37. …</code>\n\n"
    "• Ochiq savollar <code>---</code> qatori bilan ajratiladi, <code>|</code> — muqobil to'g'ri javoblar.\n"
    "• Rasmli savol va 33–35 kabi A–F li umumiy variantlar qoidasi yopiq savollarniki kabi "
    "(rasm savol matnidan keyin; umumiy variantlar yopiq qismning oxirida).\n"
    "• Fayl 15 MB dan oshmasin; katta bo'lsa 2 qismga bo'lib yuboring (ketma-ket qo'shiladi).\n"
    "• <b>Izoh va manba</b> (ixtiyoriy): to'g'ri javobdan keyin <code>Izoh: nega bu javob to'g'ri</code> va "
    "<code>Manba: 8-sinf O'zbekiston tarixi, 52-bet</code> qatorlari. O'quvchi ularni imtihon yopilgach tahlilda ko'radi. "
    "Izohda harf (A, B…) emas, javobning o'zini yozing — variantlar har o'quvchiga aralashtiriladi.\n"
    "\n"
    "🆕 <b>55 talik tayyor test fayli</b> ham bo'ladi: 1–55 hammasi oddiy test (A–D, to'g'ri javob "
    "<code>++</code> yoki <code>Javob: B</code>). Bot 1–35 ni yopiq savol qiladi, 36–55 ni esa ochiq "
    "savolga aylantiradi: o'quvchi javobni o'zi yozadi, to'g'ri variant matni — to'g'ri javob.\n"
    "• To'g'ri javob bir nechta bo'lsa (36–55): <code>Qabul: Samarqand | Qarshi | Termiz</code> qatori — "
    "istalgani to'g'ri hisoblanadi.\n\n"
    "Yuborgach «✅ Tayyor» ni bosing: bot ikkala qismni o'zi ajratadi va tekshiradi."
)


def _draft(data) -> dict:
    return dict(data.get("mly") or {})


def _home_text(m: dict) -> str:
    nc, no = len(m.get("c") or []), len(m.get("o") or [])
    o_ok = milliy_open_ok(m.get("o"))
    ready = nc == CLOSED_N and o_ok
    return (
        "🏅 <b>Milliy sertifikat imtihonini yig'ish</b>\n\n"
        f"1️⃣ Yopiq savollar (1–{CLOSED_N}): "
        + (f"✅ {nc} ta yuklandi" if nc == CLOSED_N else "❌ hali yuklanmagan") + "\n"
        + (f"2️⃣ Ochiq savollar ({open_range_text(no)}, har biri bitta javobli): ✅ {no} ta yuklandi\n"
           if o_ok and no == OPEN_N55 else
           f"2️⃣ Ochiq savollar ({CLOSED_N + 1}–{CLOSED_N + OPEN_N}, har birida a va b): "
           + (f"✅ {no} ta yuklandi" if o_ok else "❌ hali yuklanmagan") + "\n")
        + "3️⃣ Birlashtirib o'quvchilarga taqdim etish: "
        + ("✅ tayyor" if ready else "⏳ avval 1 va 2-bosqichni tugating")
    )


def _home_kb(m: dict):
    nc, no = len(m.get("c") or []), len(m.get("o") or [])
    o_ok = milliy_open_ok(m.get("o"))
    ready = nc == CLOSED_N and o_ok
    rows = [
        [B("📄 Hammasini bitta fayldan yuklash (1–45 yoki 1–55)", "mly:a")],
        [B("🗂 Tayyor variantlar (zip: 26 ta variant va h.k.)", "mlv:l:0")],
        [B(("♻️ " if nc == CLOSED_N else "1️⃣ ") + "Yopiq savollarni yuklash", "mly:c")],
        [B(("♻️ " if o_ok else "2️⃣ ") + "Ochiq savollarni yuklash", "mly:o")],
        [B(("✅ " if ready else "3️⃣ ") + "Birlashtirish va taqdim etish", "exm:fx")],
    ]
    if nc or no:
        rows.append([B("🗑 Qoralamani o'chirish", "mly:del")])
    rows.append([B("⬅️ Orqaga", "exm:t")])
    return kb(rows)


def _collect_kb(has_text: bool):
    rows = []
    if has_text:
        rows.append([B("✅ Tayyor — tekshirish", "mly:done")])
        rows.append([B("🗑 Yuborganlarimni tozalash", "mly:clr")])
    rows.append([B("⬅️ Orqaga", "mly:home")])
    return kb(rows)


# ---------------------------------------------------------------
#  Bosh ekran
# ---------------------------------------------------------------
@router.callback_query(F.data == "mly:home")
async def cb_home(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    await state.set_state(None)
    m = _draft(await state.get_data())
    await show(c, _home_text(m), _home_kb(m))
    await c.answer()


@router.callback_query(F.data == "mly:del")
async def cb_delete_ask(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    await show(
        c,
        "🗑 Yuklangan savollar o'chiriladi. Rostdan o'chiraymi?",
        kb([[B("✅ Ha, o'chir", "mly:delok")], [B("⬅️ Yo'q, orqaga", "mly:home")]]),
    )
    await c.answer()


@router.callback_query(F.data == "mly:delok")
async def cb_delete(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    await state.set_state(None)
    await state.update_data(mly=None)
    await show(c, _home_text({}), _home_kb({}))
    await c.answer("O'chirildi.")


# ---------------------------------------------------------------
#  1- va 2-bosqich: yuborish
# ---------------------------------------------------------------
async def _start(c: CallbackQuery, state: FSMContext, stage: str):
    if not await gate(c):
        return
    m = _draft(await state.get_data())
    await state.set_state({"c": MilliyYig.closed, "o": MilliyYig.opened, "a": MilliyYig.everything}[stage])
    buf = m.get("b" + stage) or ""
    lines = len([x for x in buf.splitlines() if x.strip()])
    text = {
        "c": "1️⃣ <b>Yopiq savollar</b>\n\n" + HELP_CLOSED,
        "o": "2️⃣ <b>Ochiq savollar</b>\n\n" + HELP_OPEN,
        "a": "📄 <b>Hammasi bitta fayldan</b>\n\n" + HELP_ALL,
    }[stage]
    if lines:
        text += f"\n\n📎 Oldin yuborganlaringiz saqlangan ({lines} qator). Davom eting yoki tozalang."
    await show(c, text, _collect_kb(bool(lines)))
    await c.answer()


@router.callback_query(F.data == "mly:c")
async def cb_start_closed(c: CallbackQuery, state: FSMContext):
    await _start(c, state, "c")


@router.callback_query(F.data == "mly:o")
async def cb_start_open(c: CallbackQuery, state: FSMContext):
    await _start(c, state, "o")


@router.callback_query(F.data == "mly:a")
async def cb_start_all(c: CallbackQuery, state: FSMContext):
    await _start(c, state, "a")


async def _stage(state: FSMContext) -> str | None:
    cur = await state.get_state()
    if cur == MilliyYig.closed.state:
        return "c"
    if cur == MilliyYig.opened.state:
        return "o"
    if cur == MilliyYig.everything.state:
        return "a"
    return None


_OPEN_HDR = re.compile(r"^[\W_]*ochiq\s+savol", re.I)
_CLOSED_HDR = re.compile(r"^[\W_]*yopiq\s+savol", re.I)
_FIRST_OPEN = re.compile(r"^\s*(?:#|№\s*)?36\s*(?:\.+|\)|:)\s*\S")
_OPEN_NUM = re.compile(r"(?m)^[ \t]*(?:№\s*)?(3[6-9]|4[0-5])\s*[.)]\s+")


def _is_marker(ln: str) -> bool:
    s = ln.strip()
    return len(s) <= 70 and "=>" not in s and bool(_OPEN_HDR.match(s))


def _has_marker(buf: str) -> bool:
    return any(_is_marker(ln) for ln in buf.split("\n"))


_NUM55 = re.compile(r"(?m)^[ \t]*(?:№\s*)?(4[6-9]|5[0-5])\s*[.)]\s+\S")
_OPT_ANY = re.compile(r"(?m)^[ \t]*[A-D]\s*[\)\.]\s*\S")
_PP_OPT = re.compile(r"^(\s*([A-F])\s*[\)\.].*?)\s*\+\+\s*$")
_PP_ANY = re.compile(r"^(.*?)\s*\+\+\s*$")
_LONG_ANSWER = 60


def is55(buf: str) -> bool:
    """55 talik tayyor test fayli: «OCHIQ SAVOLLAR» ajratgichi yo'q, 46–55 raqamli savollar bor
    va 36-dan keyingi qismda A) B) C) D) variantlar bor (ya'ni ular ham test ko'rinishida)."""
    if _has_marker(buf):
        return False
    lines = buf.split("\n")
    idx = next((i for i, ln in enumerate(lines) if _FIRST_OPEN.match(ln)), None)
    if idx is None:
        return False
    tail = "\n".join(lines[idx:])
    return len(set(_NUM55.findall(tail))) >= 3 and len(_OPT_ANY.findall(tail)) >= 20


def plus_to_javob(text: str) -> str:
    """«C) variant ++» belgisini milliy yopiq savol formatidagi «Javob: C» qatoriga aylantiradi."""
    out, pending, last_letter = [], None, None
    for raw in text.split("\n"):
        if _Q_START.match(raw) and not _OPT_UP.match(raw) and not ANS_RE.match(raw.strip()):
            if pending:
                out.append(f"Javob: {pending}")
            pending, last_letter = None, None
        m = _PP_OPT.match(raw)
        if m:
            last_letter = m.group(2).upper()
            pending = last_letter
            out.append(m.group(1))
            continue
        opt = re.match(r"^\s*([A-F])\s*[\)\.]", raw)
        if opt:
            last_letter = opt.group(1).upper()
        m = _PP_ANY.match(raw)
        if m and last_letter:       # variant davomi qatori oxirida «++»
            pending = last_letter
            out.append(m.group(1))
            continue
        if ANS_RE.match(raw.strip()):
            pending = None          # «Javob: B» o'zi yozilgan
        elif pending and (EXP_RE.match(raw.strip()) or SRC_RE.match(raw.strip())):
            out.append(f"Javob: {pending}")   # izoh/manba variantning davomi bo'lib qolmasin
            pending, last_letter = None, None
        out.append(raw)
    if pending:
        out.append(f"Javob: {pending}")
    return "\n".join(out)


_SUFFIX_RULES = [
    (re.compile(r"(\d)\s*-\s*(yillarda|yillari|yillar|yilda|yili|yilgi)$", re.I), r"\1"),   # «395-yilda» → «395»
    (re.compile(r"\s+asrlarda$|\s+asrda$|\s+asrlar$", re.I), " asr"),                      # «XIII asrda» → «XIII asr»
    (re.compile(r"\s+marta$", re.I), ""),                                                    # «17 marta» → «17»
    (re.compile(r"(\w{3,})(?:ning|da|ga|dan)$", re.I), r"\1"),                               # «Falastinda» → «Falastin»
]


def answer_variants(right: str) -> list[str]:
    """Testning to'g'ri variantidan yozma javob uchun qabul qilinadigan ko'rinishlar:
    asl matn + qo'shimchasiz qisqa shakli («395-yilda» → «395», «XIII asrda» → «XIII asr»)."""
    out = [right]
    cur = right
    for rx, rep in _SUFFIX_RULES:
        nxt = rx.sub(rep, cur).strip()
        if nxt and nxt != cur:
            cur = nxt
            out.append(cur)
    seen, res = set(), []
    for v in out:
        k = v.casefold()
        if k not in seen:
            seen.add(k)
            res.append(v)
    return res


def split55(buf: str):
    """55 talik fayl: (yopiq matn, ochiq matn, xatolar, ogohlantirishlar).
    36–55 test savollari «savol / a) Javobingizni yozing => to'g'ri variant matni» ko'rinishiga keltiriladi."""
    from ..services.imtihon import OPEN_SINGLE_PROMPT

    lines = buf.split("\n")
    idx = next(i for i, ln in enumerate(lines) if _FIRST_OPEN.match(ln))
    closed = plus_to_javob("\n".join(lines[:idx]))
    res = parse_text("\n".join(lines[idx:]))
    errors = [e for e in res.errors]
    warnings = []
    if res.open:
        errors.append(f"36–55 qismida {len(res.open)} ta «=>» li ochiq savol bor: bu qismda faqat test (A–D) bo'lishi kerak.")
    if len(res.closed) != OPEN_N55:
        errors.append(f"36–55 qismida {OPEN_N55} ta test bo'lishi kerak, topildi: {len(res.closed)} ta.")
    blocks = []
    for n, q in enumerate(res.closed, start=CLOSED_N + 1):
        right = " ".join((q["options"].get(q["correct"]) or "").split()).replace("|", "/").replace("=>", "→")
        stem = " ".join((q["text"] or "").split()).replace("=>", "→")
        if not right:
            errors.append(f"{n}-savol: to'g'ri variant matni bo'sh (rasmdagi variantni yozma javobga aylantirib bo'lmaydi).")
            continue
        if not stem and not q.get("image"):
            errors.append(f"{n}-savol: savol matni yo'q.")
            continue
        if len(right) > _LONG_ANSWER:
            warnings.append(f"{n}-savol: to'g'ri javob uzun ({len(right)} belgi) — o'quvchi aynan yozishi qiyin. "
                            "Keyin «✍️ Yozma javoblarni tekshirish» orqali qabul qilish mumkin.")
        block = []
        if q.get("image"):
            block.append(f"[rasm:{q['image']}]")
        block.append(stem or "Rasmdagi savolga javob yozing.")
        accepted = []
        for a in [right] + [" ".join(x.split()).replace("|", "/").replace("=>", "→") for x in q.get("accept") or []]:
            for v in answer_variants(a):
                if v.casefold() not in {x.casefold() for x in accepted}:
                    accepted.append(v)
        block.append(f"a) {OPEN_SINGLE_PROMPT} => " + " | ".join(accepted))
        if q.get("explanation"):
            block.append("Izoh: " + q["explanation"])
        if q.get("source"):
            block.append("Manba: " + q["source"])
        blocks.append("\n".join(block))
    return closed, "\n---\n".join(blocks), errors, warnings


def split_all_ex(buf: str):
    """(yopiq matn, ochiq matn, qo'shimcha xatolar, ogohlantirishlar) yoki None.
    Oddiy fayl (1–45) va 55 talik test fayli (1–55) — ikkalasi ham."""
    if is55(buf):
        return split55(buf)
    sp = split_all(buf)
    return None if sp is None else (sp[0], sp[1], [], [])


def split_all(buf: str):
    """Bitta fayldagi matnni (yopiq qism, ochiq qism) ga ajratadi.
    Ajratgich: «OCHIQ SAVOLLAR» qatori; bo'lmasa — «36.» bilan boshlangan birinchi qator."""
    lines = buf.split("\n")
    idx = next((i for i, ln in enumerate(lines) if _is_marker(ln)), None)
    if idx is not None:
        closed, opened = lines[:idx], lines[idx + 1:]
    else:
        idx = next((i for i, ln in enumerate(lines) if _FIRST_OPEN.match(ln)), None)
        if idx is None:
            return None
        closed, opened = lines[:idx], lines[idx:]
    while closed and (not closed[0].strip() or (len(closed[0].strip()) <= 70 and _CLOSED_HDR.match(closed[0].strip()))):
        if not closed[0].strip() or _CLOSED_HDR.match(closed[0].strip()):
            closed.pop(0)
        else:
            break
    return "\n".join(closed), _OPEN_NUM.sub("", "\n".join(opened))


def _join(buf: str, chunk: str, stage: str) -> str:
    """Yangi xabarni oldingisiga ulaydi: savollar bir-biriga yopishib ketmasin."""
    if not buf:
        return chunk
    if stage == "c" or (stage == "a" and not _has_marker(buf)):
        return buf + "\n\n" + chunk
    first = next((ln for ln in chunk.splitlines() if ln.strip()), "")
    last = next((ln for ln in reversed(buf.splitlines()) if ln.strip()), "")
    if _SEP.match(first) or _SEP.match(last):
        return buf + "\n" + chunk
    if _PART.match(first):  # savolning davomi (a) yoki b) qismi)
        return buf + "\n" + chunk
    return buf + "\n---\n" + chunk


async def _append(msg: Message, state: FSMContext, chunk: str, notes=None):
    stage = await _stage(state)
    if stage is None:
        return
    m = _draft(await state.get_data())
    buf = m.get("b" + stage) or ""
    buf = _join(buf, chunk, stage)
    if len(buf) > MAX_TEXT:
        await msg.answer("Juda ko'p matn. «✅ Tayyor» ni bosing.", reply_markup=_collect_kb(True))
        return
    m["b" + stage] = buf
    await state.update_data(mly=m)
    lines = len([x for x in buf.splitlines() if x.strip()])
    text = f"✅ Qabul qilindi ({lines} qator). Yana yuboring yoki «✅ Tayyor» ni bosing."
    if notes:
        text += "\n\n⚠️ " + "\n⚠️ ".join(esc(n) for n in notes)
    await msg.answer(text, reply_markup=_collect_kb(True))


async def _take_image(msg: Message, state: FSMContext, file_obj, caption):
    try:
        buf = io.BytesIO()
        await msg.bot.download(file_obj, destination=buf)
        name = save_image_bytes(buf.getvalue())
    except ValueError as exc:
        await msg.answer("❌ " + esc(exc), reply_markup=_collect_kb(False))
        return
    except Exception:
        log.exception("milliy: rasm")
        await msg.answer("❌ Rasmni qabul qilib bo'lmadi. Qayta yuboring.", reply_markup=_collect_kb(False))
        return
    chunk = f"[rasm:{name}]" + (("\n" + caption) if (caption or "").strip() else "")
    await _append(msg, state, chunk, None if (caption or "").strip() else
                  ["Rasm savol matnisiz keldi: u keyingi yuborgan savolingizga biriktiriladi."])


COLLECTING = StateFilter(MilliyYig.closed, MilliyYig.opened, MilliyYig.everything)


@router.message(COLLECTING, F.photo)
async def msg_photo(msg: Message, state: FSMContext):
    if not await allowed(msg.from_user.id):
        return
    await _take_image(msg, state, msg.photo[-1], msg.caption)


@router.message(COLLECTING, F.document)
async def msg_file(msg: Message, state: FSMContext):
    if not await allowed(msg.from_user.id):
        return
    doc = msg.document
    if (doc.mime_type or "").startswith("image/"):
        return await _take_image(msg, state, doc, msg.caption)
    if (doc.file_size or 0) > MAX_FILE:
        await msg.answer("Fayl juda katta (15 MB dan oshmasin).", reply_markup=_collect_kb(False))
        return
    try:
        buf = io.BytesIO()
        await msg.bot.download(doc, destination=buf)
        text, notes = extract_text(doc.file_name or "", buf.getvalue())
    except ValueError as exc:
        await msg.answer("❌ " + esc(exc), reply_markup=_collect_kb(False))
        return
    except Exception:
        log.exception("milliy: fayl")
        await msg.answer("❌ Faylni o'qib bo'lmadi. Matnni to'g'ridan-to'g'ri yuboring.",
                         reply_markup=_collect_kb(False))
        return
    await _append(msg, state, text, notes)


@router.message(COLLECTING, F.text)
async def msg_text(msg: Message, state: FSMContext):
    if not await allowed(msg.from_user.id):
        return
    await _append(msg, state, msg.text)


@router.message(COLLECTING)
async def msg_other(msg: Message):
    await msg.answer("Matn yoki .txt / .docx / .xlsx fayl yuboring.", reply_markup=_collect_kb(False))


@router.callback_query(COLLECTING, F.data == "mly:clr")
async def cb_clear(c: CallbackQuery, state: FSMContext):
    stage = await _stage(state)
    m = _draft(await state.get_data())
    m["b" + (stage or "c")] = ""
    await state.update_data(mly=m)
    await show(c, "🗑 Tozalandi. Savollarni qaytadan yuboring.", _collect_kb(False))
    await c.answer()


# ---------------------------------------------------------------
#  Tekshirish
# ---------------------------------------------------------------
_SHARED = re.compile(r"^\s*(?:umumiy\s+variantlar|guruh)\s*:\s*(.*)$", re.I)
_OPT = re.compile(r"^\s*[\(\[]?\s*([A-Fa-f])\s*[\)\]\.:]\s*(.*)$")
_NUM = re.compile(r"^\s*(?:#|№\s*)?(\d{1,3})\s*(?:\.+|\)|:|-|–|—)\s*(.+)$")
_ANS = re.compile(r"^\s*(?:javob|answer)\s*[:\-–—]\s*([A-Fa-f])\b", re.I)


def split_shared(buf: str):
    """Matnni oddiy qism va «Umumiy variantlar:» bloklariga ajratadi."""
    plain, blocks, cur = [], [], None
    for line in buf.splitlines():
        if _SHARED.match(line):
            cur = [line]
            blocks.append(cur)
        elif cur is not None:
            cur.append(line)
        else:
            plain.append(line)
    return "\n".join(plain), ["\n".join(b) for b in blocks]


def parse_shared(block: str, n: int):
    """Bitta «Umumiy variantlar» blokini yopiq savollarga aylantiradi. Qaytaradi: (elementlar, xatolar)."""
    lines = block.splitlines()
    intro = [_SHARED.match(lines[0]).group(1).strip()]
    options, order = {}, []
    questions = []          # [raqam, matn qatorlari, javob, izoh, manba]
    errors = []
    tag = f"{n}-umumiy blok"
    meta = None             # izoh/manba davom etayotgan bo'lsa: 3 yoki 4
    for line in lines[1:]:
        if not line.strip():
            continue
        ans = _ANS.match(line)
        if ans and questions:
            questions[-1][2] = ans.group(1).upper()
            meta = None
            continue
        ex, sr = EXP_RE.match(line.strip()), SRC_RE.match(line.strip())
        if (ex or sr) and questions:
            meta = 3 if ex else 4
            questions[-1][meta].append((ex or sr).group(1).strip())
            continue
        num = _NUM.match(line)
        if num and not _OPT.match(line):
            questions.append([num.group(1), [num.group(2).strip()], None, [], []])
            meta = None
            continue
        if meta is not None and questions:
            questions[-1][meta].append(line.strip())
            continue
        opt = _OPT.match(line)
        if opt and not questions:
            letter = opt.group(1).upper()
            if letter in options:
                errors.append(f"{tag}: {letter}) varianti ikki marta yozilgan.")
            options[letter] = opt.group(2).strip()
            order.append(letter)
            continue
        if questions:
            questions[-1][1].append(line.strip())
        elif order:
            options[order[-1]] = (options[order[-1]] + " " + line.strip()).strip()
        else:
            intro.append(line.strip())

    letters = "ABCDEF"[: len(order)]
    if len(order) < 2:
        errors.append(f"{tag}: kamida 2 ta variant (A, B …) kerak.")
    elif order != list(letters):
        errors.append(f"{tag}: variantlar A dan boshlab ketma-ket bo'lishi kerak (hozir: {', '.join(order)}).")
    if len(order) > 6:
        errors.append(f"{tag}: variantlar F gacha bo'lishi mumkin.")
    for letter, text in options.items():
        if not text:
            errors.append(f"{tag}: {letter}) varianti bo'sh.")
    if not questions:
        errors.append(f"{tag}: birorta ham raqamlangan savol yo'q (33. Savol matni).")
    items = []
    head = " ".join(x for x in intro if x)
    for num, qlines, answer, q_exp, q_src in questions:
        text = " ".join(qlines).strip()
        if not text:
            errors.append(f"{tag}: {num}-savol matni bo'sh.")
        if answer is None:
            errors.append(f"{tag}: {num}-savolda «Javob: …» yo'q.")
        elif answer not in options:
            errors.append(f"{tag}: {num}-savolda javob {answer} variantlar orasida yo'q.")
        items.append({
            "k": "c", "t": (head + "\n\n" + text) if head else text,
            "o": dict(options), "c": answer, "img": None, "qid": None,
            "e": " ".join(" ".join(q_exp).split()) or None, "m": " ".join(" ".join(q_src).split()) or None,
        })
    return ([] if errors else items), errors


_Q_START = re.compile(r"^\s*(?:#|№\s*)?(\d{1,3})\s*(?:\.+|\)|:|-|–|—)\s*(.*)$")
_OPT_UP = re.compile(r"^\s*([A-D])\s*[\)\.]\s*(.*)$")        # faqat KATTA harf
_LETTERS = "ABCD"


def _img_ok(name: str) -> bool:
    return (IMG_DIR / Path(name).name).is_file()


def parse_closed_strict(text: str):
    """Milliy sertifikat yopiq savollari: ro'yxatli (1) 2) a) b)) va rasmli savollarni buzmaydi.

    Savol FAQAT oldingisi «Javob: X» bilan tugagandan keyin yangi raqam bilan boshlanadi.
    Qaytaradi: (elementlar, xatolar).
    """
    questions, errors = [], []
    cur = None
    pending_img = None

    def new_q(number, first):
        q = {"n": number, "body": [first] if first else [], "opts": {}, "ans": None, "exp": [], "src": [], "acc": [], "meta": None,
             "img": pending_img}
        questions.append(q)
        return q

    for raw in (text or "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") and not re.match(r"^#\s*\d", line):
            continue
        if "=>" in line:
            errors.append(f"«{line[:40]}» ochiq savolga o'xshaydi (=>). Ochiq savollar 2-bosqichda yuboriladi.")
            continue

        img = IMG_RE.match(line)
        if img:
            if cur is None or cur["ans"] is not None:
                pending_img = img.group(1)
            else:
                cur["img"] = img.group(1)
            continue

        ans = ANS_RE.match(line)
        if ans:
            if cur is None or cur["ans"] is not None:
                errors.append(f"«{line[:40]}» — bu «Javob» qatoriga mos savol topilmadi.")
            else:
                cur["ans"] = ans.group(1).upper()
                cur["meta"] = None
            continue

        # «Izoh: …» va «Manba: …» (javobdan keyin; bir necha qator bo'lishi mumkin)
        if cur is not None and (cur["ans"] is not None or len(cur["opts"]) == 4):
            exp, src = EXP_RE.match(line), SRC_RE.match(line)
            if exp or src:
                cur["meta"] = "exp" if exp else "src"
                cur[cur["meta"]].append((exp or src).group(1).strip())
                continue
            if ACC_RE.match(line):
                cur["meta"] = "acc"           # yopiq savolda (1–35) kerak emas — e'tiborsiz qoldiriladi
                continue

        start = _Q_START.match(line)
        if (start and cur is not None and cur["meta"] and int(start.group(1)) != cur["n"] + 1
                and not re.match(r"^\s*(?:#|№\s*)?\d{1,3}\s*[.)]", line)):
            start = None        # izoh ichidagi «395-yilda …» kabi qator yangi savol emas
        if start:
            number = int(start.group(1))
            finished = cur is None or cur["ans"] is not None
            # «Javob» unutilgan, lekin to'liq 4 variantdan keyin keyingi raqam kelsa — yangi savol
            forgot = (not finished and len(cur["opts"]) == 4 and number == cur["n"] + 1
                      and start.group(0).lstrip().split(None, 1)[0].rstrip(".").isdigit())
            if finished or forgot:
                rest = start.group(2).strip()
                inline = ANS_RE.match(rest)          # «17. Javob: C» (hammasi rasmda)
                cur = new_q(number, "" if inline else rest)
                if inline:
                    cur["ans"] = inline.group(1).upper()
                pending_img = None
                continue

        if cur is None and not questions:
            continue        # birinchi savoldan oldingi sarlavha/izoh qatorlari (fayl nomi, mavzu) tashlanadi
        if cur is not None and cur["meta"]:
            cur[cur["meta"]].append(line)      # izoh/manbaning davomi
            continue
        if cur is None or cur["ans"] is not None:
            errors.append(f"Raqamsiz matn: «{line[:40]}» (savol «1.» kabi raqamdan boshlanishi kerak).")
            continue

        opt = _OPT_UP.match(line)
        if opt:
            expected = _LETTERS[len(cur["opts"])] if len(cur["opts"]) < 4 else None
            if opt.group(1) == expected:
                cur["opts"][expected] = opt.group(2).strip()
                continue
        if cur["opts"]:
            last = list(cur["opts"])[-1]
            cur["opts"][last] = (cur["opts"][last] + " " + line).strip()   # variant davomi
        else:
            cur["body"].append(line)

    items = []
    for q in questions:
        tag = f"{q['n']}-savol"
        problems = []
        body = "\n".join(" ".join(x.split()) for x in q["body"] if x.strip())
        if q["ans"] is None:
            problems.append("«Javob: A» qatori yo'q")
        img_only = not q["opts"] and q["img"] and q["ans"]
        if not img_only:
            missing = [l for l in _LETTERS if l not in q["opts"]]
            if missing:
                problems.append("variant yetishmaydi: " + ", ".join(missing))
            else:
                empty = [l for l in _LETTERS if not q["opts"][l].strip()]
                if empty:
                    problems.append("bo'sh variant: " + ", ".join(empty))
                elif len({" ".join(v.split()).casefold() for v in q["opts"].values()}) < 4:
                    problems.append("ikki variant bir xil yozilgan")
        if not body and not q["img"]:
            problems.append("savol matni ham, rasmi ham yo'q")
        if q["img"] and not _img_ok(q["img"]):
            problems.append("rasm topilmadi (qayta yuboring)")
        if problems:
            errors.append(f"{tag}: " + "; ".join(problems))
            continue
        items.append({
            "k": "c", "t": body,
            "o": dict(q["opts"]) if q["opts"] else {l: "" for l in _LETTERS},
            "c": q["ans"], "img": image_ref(q["img"]) if q["img"] else None,
            "e": " ".join(" ".join(q["exp"]).split()) or None, "qid": None,
            "m": " ".join(" ".join(q["src"]).split()) or None,
        })
    return items, errors


def build_open(res) -> tuple[list, list, list]:
    errors = list(res.errors)
    warnings = []
    if res.closed:
        errors.append(f"Bu bosqichda faqat ochiq savol bo'lishi kerak, lekin {len(res.closed)} ta yopiq savol topildi.")
    single = len(res.open) == OPEN_N55 and all(len(q["parts"]) == 1 for q in res.open)
    if len(res.open) != OPEN_N and not single:
        errors.append(f"Ochiq savol {OPEN_N} ta (a va b qismli) yoki {OPEN_N55} ta (bitta javobli) bo'lishi kerak, topildi: {len(res.open)} ta.")
    items = []
    for i, q in enumerate(res.open, start=CLOSED_N + 1):
        if not single and len(q["parts"]) != 2:
            warnings.append(f"{i}-savolda {len(q['parts'])} ta qism bor (odatda 2 ta: a va b).")
        items.append({
            "k": "o", "s": q["stem"], "p": [dict(p) for p in q["parts"]], "oid": None,
            "img": image_ref(q["image"]) if q.get("image") else None,
        })
    return items, errors, warnings


def split_open_meta(text: str):
    """Ochiq savollar matnidan «Izoh: …» va «Manba: …» qatorlarini ajratib oladi.
    Qaytaradi: (izohsiz matn, [(izoh, manba)] — har bir savol bloki uchun, matndagi tartibda)."""
    out, metas = [], []
    cur, meta, mode = [], {"exp": [], "src": []}, None

    def flush():
        if any(x.strip() for x in cur):
            metas.append((" ".join(" ".join(meta["exp"]).split()), " ".join(" ".join(meta["src"]).split())))
        elif (meta["exp"] or meta["src"]) and metas:      # izoh savoldan keyin --- bilan ajratilgan bo'lsa
            e, m = metas[-1]
            metas[-1] = ((e + " " + " ".join(meta["exp"])).strip(), (m + " " + " ".join(meta["src"])).strip())

    for line in (text or "").split("\n"):
        st = line.strip()
        if _SEP.match(line):
            flush()
            cur, meta, mode = [], {"exp": [], "src": []}, None
            out.append(line)
            continue
        acc = ACC_RE.match(st)
        if acc:                              # «Qabul: x | y» — oxirgi «=>» qatoriga muqobil javob qo'shiladi
            from ..services.savol_import import split_accept

            k = next((i for i in range(len(out) - 1, -1, -1) if "=>" in out[i] and not _SEP.match(out[i])), None)
            if k is not None and out[k] in cur:
                extra = [x.replace("|", "/").replace("=>", "→") for x in split_accept(acc.group(1))]
                ci = len(cur) - 1 - cur[::-1].index(out[k])
                out[k] = out[k].rstrip() + "".join(" | " + x for x in extra)
                cur[ci] = out[k]
            mode = None
            continue
        ex, sr = EXP_RE.match(st), SRC_RE.match(st)
        if ex or sr:
            mode = "exp" if ex else "src"
            meta[mode].append((ex or sr).group(1).strip())
            continue
        if mode and st and not _PART.match(line) and not IMG_RE.match(st) and "=>" not in st:
            meta[mode].append(st)            # izoh/manbaning davomi
            continue
        if st:
            mode = None
        cur.append(line)
        out.append(line)
    flush()
    return "\n".join(out), metas


def build_open_text(text: str):
    """Ochiq savollar (matn) → (elementlar, xatolar, ogohlantirishlar); izoh va manba ham biriktiriladi."""
    clean, metas = split_open_meta(text)
    items, errors, warnings = build_open(parse_text(clean))
    if any(e or m for e, m in metas):
        if len(metas) == len(items):
            for it, (e, m) in zip(items, metas):
                it["e"], it["m"] = e or None, m or None
        elif not errors:
            warnings.append("Ochiq savollarning «Izoh/Manba» qatorlarini savollarga bog'lab bo'lmadi "
                            "(savollar --- bilan ajratilganini tekshiring). Savollar saqlandi, izohlar qo'shilmadi.")
    return items, errors, warnings


def open_text_as_is(text: str) -> str:
    """check_stage uchun: build_open_text matnni o'zi tahlil qiladi."""
    return text


def _check_closed(buf: str):
    buf = plus_to_javob(buf)        # «C) variant ++» ham qabul qilinadi
    plain, blocks = split_shared(buf)
    items, errors = parse_closed_strict(plain) if plain.strip() else ([], [])
    for number, block in enumerate(blocks, start=1):
        got, errs = parse_shared(block, number)
        items += got
        errors += errs
    if not errors and len(items) != CLOSED_N:
        errors.append(f"Yopiq savol {CLOSED_N} ta bo'lishi kerak, topildi: {len(items)} ta.")
    return items, errors


@router.callback_query(COLLECTING, F.data == "mly:done")
async def cb_done(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    stage = await _stage(state)
    m = _draft(await state.get_data())
    buf = m.get("b" + (stage or "c")) or ""
    if not buf.strip():
        await c.answer("Hali hech narsa yuborilmadi.", show_alert=True)
        return

    if stage == "a":
        sp = split_all_ex(buf)
        if sp is None:
            errors, warnings = [
                "Yopiq va ochiq qismni ajratib bo'lmadi. Ochiq savollardan oldin alohida qatorga "
                "«OCHIQ SAVOLLAR» deb yozing."], []
        else:
            citems, cerr = _check_closed(sp[0])
            oitems, oerr, warnings = build_open_text(sp[1]) if sp[1].strip() else ([], ["Ochiq savollar topilmadi."], [])
            errors = ["Yopiq qism: " + e for e in cerr] + ["Ochiq qism: " + e for e in list(sp[2]) + list(oerr)]
            warnings = list(sp[3]) + list(warnings)
            if not errors:
                m["c"], m["o"], m["ba"] = citems, oitems, ""
                await state.update_data(mly=m)
                await state.set_state(None)
                done = (f"✅ <b>Hammasi saqlandi:</b> yopiq {len(citems)} ta (1–{CLOSED_N}), "
                        f"ochiq {len(oitems)} ta ({open_range_text(len(oitems))}"
                        + (", testdan yozma javobga aylantirildi)." if len(oitems) == OPEN_N55 else ")."))
                if warnings:
                    done += "\n\n<b>Ogohlantirish:</b>\n" + "\n".join("• " + esc(w) for w in warnings[:6])
                done += "\n\n" + _home_text(m)
                await show(c, done[:3900], _home_kb(m))
                await c.answer()
                return
    elif stage == "c":
        items, errors = _check_closed(buf)
        warnings = []
    else:
        items, errors, warnings = build_open_text(buf)

    if stage != "a" and not errors:
        m["c" if stage == "c" else "o"] = items
        m["b" + stage] = ""
        await state.update_data(mly=m)
        await state.set_state(None)
        if stage == "c":
            done = f"✅ <b>Yopiq savollar saqlandi:</b> {len(items)} ta (1–{CLOSED_N})."
        else:
            done = (f"✅ <b>Ochiq savollar saqlandi:</b> {len(items)} ta "
                    f"({CLOSED_N + 1}–{CLOSED_N + OPEN_N}, jami {sum(len(i['p']) for i in items)} ta javob).")
        if warnings:
            done += "\n\n<b>Ogohlantirish:</b>\n" + "\n".join("• " + esc(w) for w in warnings[:6])
        done += "\n\n" + _home_text(m)
        await show(c, done[:3900], _home_kb(m))
        await c.answer()
        return

    parts = [f"⚠️ <b>Saqlanmadi — {len(errors)} ta muammo</b>",
             "\n".join("• " + esc(e) for e in errors[:12])]
    if len(errors) > 12:
        parts.append(f"… va yana {len(errors) - 12} ta.")
    parts.append("\nYetishmaganini yana yuborishingiz mumkin (oldingilari saqlanib turadi) yoki boshidan boshlang.")
    await show(c, "\n".join(parts)[:3900], kb([
        [B("➕ Yana qo'shib yuboraman", "mly:more")],
        [B("🗑 Boshidan boshlayman", "mly:clr")],
        [B("⬅️ Orqaga", "mly:home")],
    ]))
    await c.answer()


@router.callback_query(COLLECTING, F.data == "mly:more")
async def cb_more(c: CallbackQuery, state: FSMContext):
    stage = await _stage(state)
    m = _draft(await state.get_data())
    lines = len([x for x in (m.get("b" + (stage or "c")) or "").splitlines() if x.strip()])
    await show(c, f"Yuboring — qo'shib boraman (hozir {lines} qator saqlangan). Tugagach «✅ Tayyor» ni bosing.",
               _collect_kb(True))
    await c.answer()
