"""Savol yuklash: bitta oddiy format, bot va veb-ilova uchun umumiy.

Joylashuvi: app/services/savol_import.py

YOPIQ savol:
    1. Savol matni
    A) variant
    B) variant
    C) variant
    D) variant
    Javob: B
    Izoh: ixtiyoriy tushuntirish (nega bu javob to'g'ri)
    Manba: ixtiyoriy (masalan: 8-sinf O'zbekiston tarixi, 52-bet)

OCHIQ savol (``=>`` belgisi bor, savollar ``---`` bilan ajratiladi):
    Savol matni
    a) qism savoli => javob1 | javob2
    b) qism savoli => javob1 + javob2

Eski usul ham ishlaydi: to'g'ri variant oxiriga ++ yoki ... qo'yish.
Hech narsa jimgina o'tkazib yuborilmaydi: har bir xato qator raqami bilan.
"""
from __future__ import annotations

import io
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

MAX_TEXT = 3_000_000
MAX_QUESTIONS = 3000  # butun darslik bo'yicha FULL test bitta faylda
MAX_FILE = 15 * 1024 * 1024

IMG_DIR = Path("app/question_images")
IMG_RE = re.compile(r"^\[\s*rasm\s*:\s*([A-Za-z0-9_.\-]{3,80})\s*\]$", re.I)
MAX_IMG = 12 * 1024 * 1024

_HOMO = str.maketrans({"А": "A", "В": "B", "С": "C", "а": "a", "с": "c"})

ANS_RE = re.compile(
    r"^(?:to['’ʻ`]?g['’ʻ`]?ri\s+javob|javob|answer|correct)\s*[:=\-–—]\s*[\(\[]?\s*([A-Da-d])\s*[\)\]]?\s*\.?\s*$",
    re.I,
)
EXP_RE = re.compile(r"^(?:izoh|tushuntirish|sharh|explanation)\s*[:\-–—]\s*(.*)$", re.I)
# «Manba: 8-sinf O'zbekiston tarixi, 52-bet» — izohning manbasi (o'quvchiga izoh bilan birga ko'rsatiladi)
SRC_RE = re.compile(r"^(?:📚\s*)?(?:manba|manbaa|manbalar|source|источник)\s*[:\-–—]\s*(.*)$", re.I)
SRC_MARK = "\n📚 Manba: "

# «Qabul: Samarqand | Qarshi | Termiz» — yozma javobga aylanadigan savolda boshqa to'g'ri javoblar
ACC_RE = re.compile(
    r"^(?:qabul(?:\s+qilinadi)?|boshqa\s+javoblar|muqobil\s+javoblar?|to['’ʻ`]?g['’ʻ`]?ri\s+javoblar)\s*[:\-–—]\s*(.*)$", re.I)


def split_accept(text: str) -> list[str]:
    """«Samarqand | Qarshi; Termiz, Toshkent» → ['Samarqand', 'Qarshi', 'Termiz', 'Toshkent']."""
    out = []
    for x in re.split(r"[|;,]", text or ""):
        x = " ".join(x.split()).strip(" .")
        if x and x.casefold() not in {y.casefold() for y in out}:
            out.append(x)
    return out


def with_source(explanation, source):
    """Savollar bankida manba alohida ustun emas: izoh oxiriga «📚 Manba: …» qilib qo'shiladi."""
    exp = (explanation or "").strip()
    src = (source or "").strip()
    if not src:
        return exp or None
    return (exp + "\n" if exp else "") + "📚 Manba: " + src


def split_source(text):
    """with_source ning teskarisi: «izoh\n📚 Manba: …» → (izoh, manba)."""
    t = (text or "").strip()
    if t.startswith("📚 Manba: "):
        return None, t[len("📚 Manba: "):].strip() or None
    exp, sep, src = t.partition(SRC_MARK)
    return (exp.strip() or None), (src.strip() or None) if sep else None


def keep_source(translated, original):
    """Tarjima izohida manba bo'lmasa, asl (o'zbekcha) izohdagi manba saqlanib qoladi."""
    _e, src = split_source(original)
    if not src or not (translated or "").strip() or "📚" in translated:
        return translated
    return translated.rstrip() + SRC_MARK + src


# Ochiq savol bloklarida ham «Izoh:», «Manba:» va «Qabul:» qatorlari (Milliy sertifikat bilan bir xil)
_OPEN_SEP = re.compile(r"^\s*(?:---+|\*\*\*+|===+)\s*$")
_OPEN_PART = re.compile(r"^\s*[a-dA-D]\s*[\)\.]")


def split_open_meta(text: str):
    """Ochiq savollar matnidan «Izoh: …», «Manba: …» qatorlarini ajratadi, «Qabul: x | y» ni oxirgi «=>» qatoriga
    muqobil javob qilib qo'shadi. Qaytaradi: (toza matn, [(izoh, manba)] — har bir savol bloki uchun, tartibda)."""
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
        if _OPEN_SEP.match(line):
            flush()
            cur, meta, mode = [], {"exp": [], "src": []}, None
            out.append(line)
            continue
        acc = ACC_RE.match(st)
        if acc:                              # «Qabul: x | y» — oxirgi «=>» qatoriga muqobil javob qo'shiladi
            k = next((i for i in range(len(out) - 1, -1, -1) if "=>" in out[i] and not _OPEN_SEP.match(out[i])), None)
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
        if mode and st and not _OPEN_PART.match(line) and not IMG_RE.match(st) and "=>" not in st:
            meta[mode].append(st)            # izoh/manbaning davomi
            continue
        if st:
            mode = None
        cur.append(line)
        out.append(line)
    flush()
    return "\n".join(out), metas


def parse_open_meta(text: str):
    """parse_open_text + izoh/manba/qabul. Qaytaradi: (savollar — har birida "e", "m" bo'lishi mumkin, xatolar)."""
    from .imtihon import parse_open_text

    clean, metas = split_open_meta(text)
    qs, errs = parse_open_text(clean)
    if len(metas) == len(qs):
        for q, (e, m) in zip(qs, metas):
            if e:
                q["e"] = e
            if m:
                q["m"] = m
    return qs, errs


def open_parts_json(q, image=None) -> str:
    """Ochiq savol qismlari (bazaga). Rasm, izoh va manba 1-qism ichida saqlanadi (_img, _e, _m)."""
    parts = [dict(p) for p in q["parts"]]
    if parts:
        if image:
            parts[0]["_img"] = image
        if q.get("e"):
            parts[0]["_e"] = q["e"]
        if q.get("m"):
            parts[0]["_m"] = q["m"]
    return json.dumps(parts, ensure_ascii=False)
OPT_RE = re.compile(r"^[\(\[]?\s*([A-Da-d])\s*[\)\]\.:]\s*(.*)$")
STRONG_NUM_RE = re.compile(r"^\s*(?:#|№\s*)?\d{1,4}\s*[.)]")
NUM_RE = re.compile(r"^(?:#|№\s*)?(\d{1,4})\s*(?:\.+|\)|:|;|-|–|—)\s*(.+)$")
ROW_RE = re.compile(r"^#@(\d{1,6})$")
SEP_RE = re.compile(r"^\s*(?:---+|\*\*\*+|===+)\s*$")
HDR_RE = re.compile(r"^\s*(fan|mavzu)\s*[:=]\s*(.+?)\s*$", re.I)
# Ochiq savol qismi: «a) savol → javob». Faqat KICHIK harf: «A) X → Y → Z» kabi yopiq savol varianti
# (voqealar ketma-ketligi) ochiq savol deb o'qilmasin.
PART_ARROW_RE = re.compile(r"^(\s*[a-d]\s*[\)\.].*?)(?:→|⇒|->|=>)(.*)$")


@dataclass
class Parsed:
    closed: list = field(default_factory=list)
    open: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.closed) + len(self.open)


def save_image_bytes(data: bytes) -> str:
    """Rasmni JPEG qilib saqlaydi, fayl nomini qaytaradi. Rasm bo'lmasa ValueError."""
    if len(data) > MAX_IMG:
        raise ValueError("Rasm juda katta (12 MB dan oshmasin).")
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            im.thumbnail((1800, 1800))
            IMG_DIR.mkdir(parents=True, exist_ok=True)
            name = f"imp_{uuid.uuid4().hex[:12]}.jpg"
            im.save(IMG_DIR / name, "JPEG", quality=88)
    except ValueError:
        raise
    except Exception:
        raise ValueError("Rasmni o'qib bo'lmadi (JPG yoki PNG yuboring).")
    return name


def image_ref(name: str) -> str:
    """Bazaga yoziladigan yo'l (mavjud rasmli savollar bilan bir xil)."""
    return (Path("app/question_images") / name).as_posix()


def _img_exists(name: str) -> bool:
    return (IMG_DIR / Path(name).name).is_file()


def compact(text: str) -> str:
    return "".join(ch for ch in (text or "").casefold() if ch.isalnum())


def _marker(value: str):
    value = (value or "").rstrip()
    m = re.search(r"\+{2,}\s*$", value)
    if m:
        return value[: m.start()].rstrip(), True
    m = re.search(r"\s+\.{2,}\s*$", value)
    if m:
        return value[: m.start()].rstrip(), True
    return value, False


def _clean(text: str) -> str:
    text = (text or "").replace("﻿", "").replace(" ", " ").replace("​", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


# ---------------------------------------------------------------
#  Yopiq savollar
# ---------------------------------------------------------------
def _new(number, line):
    return {"number": number, "line": line, "stem": [], "opts": {}, "opt_order": [],
            "marked": [], "answer": None, "exp": [], "src": [], "acc": [], "mode": "stem", "blank": False}


def _finish(q, idx, out, errors):
    tag = f"{idx}-savol ({q['line']}-qator)"
    stem = " ".join(" ".join(q["stem"]).split())
    problems = []
    if not stem and not q.get("img"):
        problems.append("savol matni yo'q")
    missing = [l for l in "ABCD" if l not in q["opts"]]
    if missing:
        problems.append("variant yetishmaydi: " + ", ".join(missing))
    empty = [l for l in "ABCD" if l in q["opts"] and not q["opts"][l].strip()]
    if empty:
        problems.append("bo'sh variant: " + ", ".join(empty))
    if not missing:
        right = set(q["marked"])
        if q["answer"]:
            right.add(q["answer"])
        if len(right) == 0:
            problems.append("to'g'ri javob ko'rsatilmagan (Javob: B yoki variant oxiriga ++)")
        elif len(right) > 1:
            problems.append("bir nechta to'g'ri javob: " + ", ".join(sorted(right)))
        else:
            q["correct"] = next(iter(right))
    if not missing and not empty:
        vals = [" ".join(q["opts"][l].split()).casefold() for l in "ABCD"]
        if len(set(vals)) < 4:
            problems.append("ikki variant bir xil yozilgan")
    if problems:
        errors.append(f"{tag}: " + "; ".join(problems))
        return
    out.append({
        "text": stem,
        "options": {l: " ".join(q["opts"][l].split()) for l in "ABCD"},
        "correct": q["correct"],
        "explanation": " ".join(" ".join(q["exp"]).split()) or None,
        "source": " ".join(" ".join(q.get("src") or []).split()) or None,
        "accept": split_accept(" | ".join(q.get("acc") or [])),
        "line": q["line"],
        "image": q.get("img"),
    })


def parse_closed(lines, base_line=1):
    """lines: [(qator_raqami, matn)] → (savollar, xatolar)"""
    out, errors = [], []
    cur, count, last_number = None, 0, 0
    pending_img = None
    row_label = None

    def close():
        nonlocal cur, count
        if cur is not None:
            count += 1
            _finish(cur, count, out, errors)
            cur = None

    for ln, raw in lines:
        s = raw.strip()
        if not s:
            if cur is not None:
                cur["blank"] = True
            continue
        s = s.translate(_HOMO) if OPT_RE.match(s.translate(_HOMO)) else s

        rm = ROW_RE.match(s)
        if rm:
            row_label = int(rm.group(1))
            continue

        im = IMG_RE.match(s)
        if im:
            done = cur is not None and len(cur["opt_order"]) == 4 and (cur["answer"] or cur["marked"])
            if cur is None or done:
                pending_img = im.group(1)
            else:
                cur["img"] = im.group(1)
            continue

        if cur is not None:
            m = ANS_RE.match(s)
            if m:
                letter = m.group(1).upper()
                if cur["answer"] and cur["answer"] != letter:
                    errors.append(f"{count + 1}-savol ({ln}-qator): «Javob» ikki xil yozilgan")
                cur["answer"] = letter
                cur["mode"] = "after"
                cur["blank"] = False
                continue
            m = EXP_RE.match(s)
            if m:
                cur["exp"] = [m.group(1)]
                cur["mode"] = "exp"
                cur["blank"] = False
                continue
            m = SRC_RE.match(s)
            if m:
                cur["src"] = [m.group(1)]
                cur["mode"] = "src"
                cur["blank"] = False
                continue
            m = ACC_RE.match(s)
            if m:
                cur["acc"].append(m.group(1))
                cur["mode"] = "acc"
                cur["blank"] = False
                continue

        om = OPT_RE.match(s)
        if cur is not None and om and cur["mode"] in ("stem", "opt"):
            letter = om.group(1).upper()
            expected = "ABCD"[len(cur["opt_order"])] if len(cur["opt_order"]) < 4 else None
            if letter == expected:
                body, mk = _marker(om.group(2))
                cur["opts"][letter] = body
                cur["opt_order"].append(letter)
                if mk:
                    cur["marked"].append(letter)
                cur["mode"] = "opt"
                cur["blank"] = False
                continue
            if letter in cur["opts"]:
                errors.append(f"{count + 1}-savol ({ln}-qator): {letter} varianti ikki marta yozilgan")
                continue
            if cur["mode"] == "opt":
                errors.append(
                    f"{count + 1}-savol ({ln}-qator): variantlar tartibi buzilgan "
                    f"({expected or 'oxiri'} kutilgan edi, {letter} topildi)"
                )
                continue

        nm = NUM_RE.match(s)
        if (nm and cur is not None and cur["mode"] in ("exp", "src", "acc") and int(nm.group(1)) != cur["number"] + 1
                and not STRONG_NUM_RE.match(s)):
            nm = None       # izoh ichidagi «395-yilda …» kabi qator yangi savol emas («14.» / «14)» esa yangi savol)
        complete = cur is not None and len(cur["opt_order"]) == 4
        starts_new = False
        if cur is None:
            starts_new = True
        elif nm and (complete or int(nm.group(1)) == cur["number"] + 1):
            starts_new = True
        elif complete and (cur["answer"] or cur["marked"] or cur["blank"]) and not nm and not (cur["mode"] in ("exp", "src", "acc") and not cur["blank"]):
            starts_new = True
        elif complete and nm:
            starts_new = True

        if starts_new:
            close()
            if row_label:
                ln, row_label = row_label, None
            if nm:
                number = int(nm.group(1))
                cur = _new(number, ln)
                if pending_img:
                    cur["img"], pending_img = pending_img, None
                cur["stem"].append(nm.group(2).strip())
            else:
                number = last_number + 1
                cur = _new(number, ln)
                if pending_img:
                    cur["img"], pending_img = pending_img, None
                cur["stem"].append(s)
            last_number = number
            continue

        # davomi
        if cur["mode"] == "stem":
            cur["stem"].append(s)
        elif cur["mode"] == "opt":
            last = cur["opt_order"][-1]
            body, mk = _marker((cur["opts"][last] + " " + s).strip())
            cur["opts"][last] = body
            if mk and last not in cur["marked"]:
                cur["marked"].append(last)
        elif cur["mode"] == "exp":
            cur["exp"].append(s)
        elif cur["mode"] == "src":
            cur["src"].append(s)
        elif cur["mode"] == "acc":
            cur["acc"].append(s)
        else:
            errors.append(f"{ln}-qator: tushunarsiz qator «{s[:40]}»")

    close()
    return out, errors


# ---------------------------------------------------------------
#  Ko'p tilli savollar: «RU:» / «EN:» bloklari
# ---------------------------------------------------------------
TR_RE = re.compile(r"^\s*(RU|RUS|EN|ENG)\s*[:：]\s*$", re.I)
TR_LANG = {"RU": "ru", "RUS": "ru", "EN": "en", "ENG": "en"}
TR_EXP_RE = re.compile(r"^(?:izoh|tushuntirish|sharh|explanation|объяснение|пояснение|комментарий|примечание|note)\s*[:\-–—]\s*(.*)$", re.I)
TR_ANS_RE = re.compile(
    r"^(?:to['’ʻ`]?g['’ʻ`]?ri\s+javob|javob|answer|correct(?:\s+answer)?|правильный\s+ответ|верный\s+ответ|ответ)\s*[:=\-–—]\s*[\(\[]?\s*([A-Da-dАВСас])\s*[\)\]]?\s*\.?\s*$",
    re.I,
)


def split_tr(lines):
    """lines: [(ln, matn)] -> (o'zbekcha qatorlar, [(til, bog'langan qator, blok qatori, [(ln, matn)])]).

    «RU:» yoki «EN:» qatoridan keyingi blok savolning tarjimasi. U bo'sh qatorda, «---» da,
    yangi marker yoki (D variantdan keyin) raqamlangan yangi savolda tugaydi."""
    uz, trs, cur = [], [], None
    last_uz = None
    for ln, raw in lines:
        m = TR_RE.match(raw)
        if m:
            cur = {"lang": TR_LANG[m.group(1).upper()], "anchor": last_uz, "ln": ln, "buf": [], "seen_d": False}
            trs.append(cur)
            continue
        if cur is not None:
            st = raw.strip()
            if not st:
                if cur["seen_d"]:
                    cur = None
                    uz.append((ln, raw))
                continue
            if cur["seen_d"] and NUM_RE.match(st) and not OPT_RE.match(st):
                cur = None
                uz.append((ln, raw))
                last_uz = ln
                continue
            cur["buf"].append((ln, raw))
            om = OPT_RE.match(st.translate(_HOMO))
            if om and om.group(1).upper() == "D":
                cur["seen_d"] = True
            continue
        uz.append((ln, raw))
        if raw.strip():
            last_uz = ln
    return uz, [(c["lang"], c["anchor"], c["ln"], c["buf"]) for c in trs]


def parse_tr(buf, ln0):
    """Tarjima bloki -> (dict | None, xato | None). Dict: text, options, explanation, answer."""
    stem, opts, order, exp, answer = [], {}, [], [], None
    mode = "stem"
    for ln, raw in buf:
        s = raw.strip()
        if not s:
            continue
        if IMG_RE.match(s):
            continue
        am = TR_ANS_RE.match(s)
        if am and order:
            answer = am.group(1).translate(_HOMO).upper()
            mode = "after"
            continue
        em = TR_EXP_RE.match(s)
        if em and order:
            exp = [em.group(1)]
            mode = "exp"
            continue
        om = OPT_RE.match(s.translate(_HOMO))   # faqat variant harfi lotinchaga o'giriladi, matn emas
        if om and mode in ("stem", "opt"):
            letter = om.group(1).upper()
            expected = "ABCD"[len(order)] if len(order) < 4 else None
            if letter == expected:
                body, _mk = _marker(s[om.start(2):om.end(2)])
                opts[letter] = body
                order.append(letter)
                mode = "opt"
                continue
            return None, f"{ln}-qator: tarjimada variantlar tartibi buzilgan ({expected or 'oxiri'} kutilgan edi, {letter} topildi)"
        if mode == "stem":
            if not stem:
                nm = NUM_RE.match(s)
                s = nm.group(2).strip() if nm else s
            stem.append(s)
        elif mode == "opt":
            last = order[-1]
            opts[last] = _marker((opts[last] + " " + s).strip())[0]
        elif mode == "exp":
            exp.append(s)
        else:
            return None, f"{ln}-qator: tarjimada tushunarsiz qator «{s[:40]}»"
    text = " ".join(" ".join(stem).split())
    if not text:
        return None, f"{ln0}-qator: tarjimada savol matni yo'q"
    missing = [l for l in "ABCD" if l not in opts]
    if missing:
        return None, f"{ln0}-qator: tarjimada variant yetishmaydi: " + ", ".join(missing)
    empty = [l for l in "ABCD" if not opts[l].strip()]
    if empty:
        return None, f"{ln0}-qator: tarjimada bo'sh variant: " + ", ".join(empty)
    return {"text": text, "options": {l: " ".join(opts[l].split()) for l in "ABCD"},
            "explanation": " ".join(" ".join(exp).split()) or None, "answer": answer}, None


def attach_tr(qs, trs, errors):
    """O'zbekcha savollarga tarjimalarni bog'laydi (q['tr'] = {'ru': {...}, 'en': {...}})."""
    for lang, anchor, ln0, buf in trs:
        owner = None
        if anchor is not None:
            for q in qs:
                if q["line"] <= anchor and (owner is None or q["line"] >= owner["line"]):
                    owner = q
        if owner is None:
            errors.append(f"{ln0}-qator: {lang.upper()} tarjimasi qaysi savolga tegishli ekani aniqlanmadi (tarjima o'zbekcha savol tagida bo'lsin)")
            continue
        d, err = parse_tr(buf, ln0)
        if err:
            errors.append(err)
            continue
        if d["answer"] and d["answer"] != owner["correct"]:
            errors.append(f"{ln0}-qator: {lang.upper()} tarjimadagi javob ({d['answer']}) asl savolnikidan ({owner['correct']}) farq qiladi")
            continue
        tr = owner.setdefault("tr", {})
        if lang in tr:
            errors.append(f"{ln0}-qator: {lang.upper()} tarjimasi bu savolga ikki marta yozilgan")
            continue
        tr[lang] = d


# ---------------------------------------------------------------
#  Umumiy
# ---------------------------------------------------------------
def parse_text(text: str, auto_book: bool = False) -> Parsed:
    """auto_book=True — «1-mavzu. …», «19–20-§. …» sarlavhalaridan darslik va mavzular avtomatik aniqlanadi
    (services/darsliklar.py). Faylda «Fan:»/«Mavzu:» qatorlari bo'lsa, ular ustun."""
    res = Parsed()
    text = _clean(text)
    if not text.strip():
        res.errors.append("Matn bo'sh.")
        return res
    if len(text) > MAX_TEXT:
        res.errors.append(f"Matn juda uzun (>{MAX_TEXT} belgi). Bir necha qismga bo'lib yuklang.")
        return res
    # botning o'zi eksport qilgan Word fayli qayta yuklansa: oxiridagi «JAVOBLAR KALITI» bo'limi savol emas
    key = re.search(r"(?mi)^[ \t]*JAVOBLAR KALITI[ \t]*$", text)
    if key:
        text = text[:key.start()]
    if auto_book:
        try:
            from .darsliklar import annotate

            text, book_notes, book_warn = annotate(text)
            res.warnings.extend(book_notes)
            res.warnings.extend(book_warn)
        except Exception:  # noqa: BLE001 — aniqlab bo'lmasa, oddiy yuklash davom etadi
            pass

    blocks, cur, start = [], [], 1
    ctx = {"subject": None, "topic": None}
    block_ctx = dict(ctx)
    for ln, raw in enumerate(text.split("\n"), start=1):
        st = raw.strip()
        if st.startswith("#") and not re.match(r"^#\s*\d", st) and not ROW_RE.match(st):
            continue
        h = HDR_RE.match(raw)
        if SEP_RE.match(raw) or h:
            blocks.append((start, cur, block_ctx))
            cur, start = [], ln + 1
            if h:
                if h.group(1).lower() == "fan":
                    ctx = {"subject": h.group(2).strip(), "topic": None}
                else:
                    ctx = {"subject": ctx["subject"], "topic": h.group(2).strip()}
            block_ctx = dict(ctx)
        else:
            m = PART_ARROW_RE.match(raw)
            if m:
                raw = m.group(1) + "=>" + m.group(2)
            cur.append((ln, raw))
    blocks.append((start, cur, block_ctx))

    for start, lines, bctx in blocks:
        if not any(t.strip() for _, t in lines):
            continue
        lines, trs = split_tr(lines)
        if trs and any("=>" in t for _, t in lines):
            for lang, _a, ln0, _b in trs:
                res.errors.append(f"{ln0}-qator: ochiq savolga tarjima qo'shib bo'lmaydi ({lang.upper()})")
            trs = []
        if any("=>" in t for _, t in lines):
            img = None
            keep = []
            for _, tl in lines:
                im = IMG_RE.match(tl.strip())
                if im and img is None:
                    img = im.group(1)
                elif not im:
                    keep.append(tl)
            qs, errs = parse_open_meta("\n".join(keep))
            for q in qs:
                q["line"] = start
                q["image"] = img
                q["subject"], q["topic"] = bctx["subject"], bctx["topic"]
                res.open.append(q)
            for e in errs:
                res.errors.append(f"Ochiq savol ({start}-qatordan boshlanadigan blok): {e}")
        else:
            qs, errs = parse_closed(lines, start)
            for q in qs:
                q["subject"], q["topic"] = bctx["subject"], bctx["topic"]
            if trs:
                attach_tr(qs, trs, errs)
            res.closed.extend(qs)
            res.errors.extend(errs)

    if res.total + len(res.errors) == 0:
        res.errors.append("Hech qanday savol topilmadi.")
    if res.total > MAX_QUESTIONS:
        res.errors.append(f"Bir yuklashda {MAX_QUESTIONS} tadan ko'p savol bo'lmasin (hozir {res.total}).")

    for q in list(res.closed):
        if q.get("image") and not _img_exists(q["image"]):
            res.errors.append(f"{q['line']}-qator: rasm topilmadi ({q['image']}). Rasmni qayta yuboring.")
            res.closed.remove(q)
    for q in list(res.open):
        if q.get("image") and not _img_exists(q["image"]):
            res.errors.append(f"{q['line']}-qator: ochiq savol rasmi topilmadi ({q['image']}).")
            res.open.remove(q)

    if len(res.closed) >= 8:
        from collections import Counter

        cnt = Counter(q["correct"] for q in res.closed)
        letter, n = cnt.most_common(1)[0]
        if n / len(res.closed) > 0.5:
            res.warnings.append(
                f"To'g'ri javoblar bir tomonlama: {letter} — {round(100 * n / len(res.closed))}%. "
                "Variantlar o'rnini aralashtirish tavsiya etiladi."
            )
    for q in res.closed:
        if len(q["text"]) < 8:
            res.warnings.append(f"{q['line']}-qator: savol matni juda qisqa")
        if any(len(v) > 250 for v in q["options"].values()):
            res.warnings.append(f"{q['line']}-qator: juda uzun variant bor (tugmaga sig'masligi mumkin)")
    seen = set()
    for q in list(res.closed):
        k = compact(q["text"])
        if k in seen:
            res.warnings.append(f"{q['line']}-qator: bir xil savol faylda ikki marta — keyingisi o'tkazib yuboriladi")
        seen.add(k)
    return res


def preview(res: Parsed, limit: int = 6) -> str:
    """Oddiy matnli ko'rinish (HTML belgilarisiz)."""
    out = [f"Yopiq: {len(res.closed)} ta, ochiq: {len(res.open)} ta, xato: {len(res.errors)} ta."]
    n_ru = sum(1 for q in res.closed if "ru" in (q.get("tr") or {}))
    n_en = sum(1 for q in res.closed if "en" in (q.get("tr") or {}))
    if n_ru or n_en:
        out.append(f"🌐 Tarjima: RU {n_ru} ta, EN {n_en} ta.")
    for q in res.closed[:limit]:
        out.append(f"\n• {'🖼 ' if q.get('image') else ''}{q['text'][:90]}\n   ✅ {q['correct']}) {q['options'][q['correct']][:60]}")
    for q in res.open[:limit]:
        out.append(f"\n• {'🖼 ' if q.get('image') else ''}[ochiq, {len(q['parts'])} qism] {q['stem'][:90]}")
    rest = res.total - min(len(res.closed), limit) - min(len(res.open), limit)
    if rest > 0:
        out.append(f"\n… va yana {rest} ta.")
    return "\n".join(out)


# ---------------------------------------------------------------
#  Fayldan matn olish
# ---------------------------------------------------------------
# ---------------------------------------------------------------
#  Word: avtomatik raqamlash va rasmlar
# ---------------------------------------------------------------
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _fmt_num(n: int, fmt: str) -> str:
    if fmt in ("lowerLetter", "upperLetter"):
        s, k = "", n
        while k > 0:
            k, r = divmod(k - 1, 26)
            s = chr(97 + r) + s
        return s.upper() if fmt == "upperLetter" else s
    return str(n)


def _numbering_defs(doc):
    """numId -> {ilvl: (fmt, lvlText, start)}, shuningdek startOverride lar."""
    try:
        root = doc.part.numbering_part.element
    except Exception:
        return {}
    abstract = {}
    for an in root.findall(_W + "abstractNum"):
        lv = {}
        for lvl in an.findall(_W + "lvl"):
            i = int(lvl.get(_W + "ilvl", "0"))
            fmt = lvl.find(_W + "numFmt")
            txt = lvl.find(_W + "lvlText")
            st = lvl.find(_W + "start")
            lv[i] = (
                fmt.get(_W + "val") if fmt is not None else "decimal",
                txt.get(_W + "val") if txt is not None else "%1.",
                int(st.get(_W + "val")) if st is not None else 1,
            )
        abstract[an.get(_W + "abstractNumId")] = lv
    out = {}
    for num in root.findall(_W + "num"):
        ab = num.find(_W + "abstractNumId")
        if ab is None:
            continue
        levels = dict(abstract.get(ab.get(_W + "val"), {}))
        for ov in num.findall(_W + "lvlOverride"):
            so = ov.find(_W + "startOverride")
            i = int(ov.get(_W + "ilvl", "0"))
            if so is not None and i in levels:
                f, tx, _ = levels[i]
                levels[i] = (f, tx, int(so.get(_W + "val")))
        out[num.get(_W + "numId")] = levels
    return out


def _para_numpr(p):
    """(numId, ilvl) yoki None — abzatdan yoki uslubidan."""
    ppr = p._p.pPr
    cand = [ppr.numPr] if ppr is not None and ppr.numPr is not None else []
    st = p.style
    hops = 0
    while not cand and st is not None and hops < 8:
        sp = st.element.pPr
        if sp is not None and sp.numPr is not None:
            cand.append(sp.numPr)
        st = st.base_style
        hops += 1
    if not cand:
        return None
    np_ = cand[0]
    nid = np_.numId.val if np_.numId is not None else None
    lvl = np_.ilvl.val if np_.ilvl is not None else 0
    if nid is None or int(nid) == 0:
        return None
    return str(nid), int(lvl or 0)


def docx_lines(doc):
    """Word hujjatidan matn qatorlari: avtomatik raqam/harflar matnga aylantiriladi,
    rasmlar saqlanib [rasm:nom] qatori bo'ladi. → (qatorlar, rasm_ok, rasm_xato)"""
    defs = _numbering_defs(doc)
    counters = {}
    lines, ok, bad = [], 0, 0
    for p in doc.paragraphs:
        text = p.text or ""
        label = ""
        np_ = _para_numpr(p)
        if np_ and np_[0] in defs:
            nid, ilvl = np_
            levels = defs[nid]
            cnt = counters.setdefault(nid, {})
            if ilvl in levels:
                fmt, lvltext, start = levels[ilvl]
                cnt[ilvl] = cnt.get(ilvl, start - 1) + 1
                for deeper in [k for k in cnt if k > ilvl]:
                    del cnt[deeper]
                if fmt != "bullet" and lvltext:
                    def sub(m):
                        k = int(m.group(1)) - 1
                        f2 = levels.get(k, ("decimal", "", 1))[0]
                        return _fmt_num(cnt.get(k, levels.get(k, ("", "", 1))[2]), f2)

                    label = re.sub(r"%(\d)", sub, lvltext).strip()
        body = text.split("\n")
        if label and body and body[0].strip():
            body[0] = f"{label} {body[0].lstrip()}"
        elif label:
            body = [label]
        has_img = any(True for _ in p._p.iter(_A + "blip"))
        if not (has_img and not text.strip() and not label):
            lines.extend(body)
        for blip in p._p.iter(_A + "blip"):
            rid = blip.get(_R + "embed")
            if not rid:
                continue
            try:
                blob = doc.part.related_parts[rid].blob
                lines.append(f"[rasm:{save_image_bytes(blob)}]")
                ok += 1
            except Exception:
                bad += 1
    return lines, ok, bad


# ---------------------------------------------------------------
#  Excel / CSV (Kahoot uslubidagi jadval)
# ---------------------------------------------------------------
SHEET_HEAD = ["Savol", "A", "B", "C", "D", "Javob", "Izoh", "Fan", "Mavzu", "Manba"]
_COLS = {
    "savol": ("savol", "question", "matn", "вопрос", "savol matni"),
    "A": ("a", "a variant", "variant a", "1-variant", "javob a", "a)"),
    "B": ("b", "b variant", "variant b", "2-variant", "javob b", "b)"),
    "C": ("c", "c variant", "variant c", "3-variant", "javob c", "c)"),
    "D": ("d", "d variant", "variant d", "4-variant", "javob d", "d)"),
    "javob": ("javob", "to'g'ri javob", "togri javob", "answer", "correct", "to'g'ri", "ответ"),
    "izoh": ("izoh", "tushuntirish", "explanation", "sharh"),
    "manba": ("manba", "source", "manbaa", "источник"),
    "fan": ("fan", "subject"),
    "mavzu": ("mavzu", "topic"),
}


def rows_to_text(rows):
    """Jadval qatorlari -> yagona matn formati. Xato qator raqami Excel qatori bo'ladi."""
    rows = [list(r) for r in rows if r is not None]
    if not rows:
        raise ValueError("Faylda savollar mavjud emas.")

    def cell(v):
        return " ".join(str(v).split()) if v is not None else ""

    heads = [cell(x).casefold().replace("’", "'").replace("ʻ", "'") for x in rows[0]]
    cols, header = {}, False
    for key, names in _COLS.items():
        for i, h in enumerate(heads):
            if h in names and key not in cols:
                cols[key] = i
    if "savol" in cols and "javob" in cols:
        header = True
    else:
        cols = {"savol": 0, "A": 1, "B": 2, "C": 3, "D": 4, "javob": 5, "izoh": 6}
    out, last_fan, last_topic, n = [], None, None, 0
    for idx, row in enumerate(rows, start=1):
        if header and idx == 1:
            continue

        def g(key):
            i = cols.get(key)
            return cell(row[i]) if i is not None and i < len(row) else ""

        if not any(cell(x) for x in row):
            continue
        fan, mavzu = g("fan"), g("mavzu")
        if fan and fan != last_fan:
            out.append(f"Fan: {fan}")
            last_fan = fan
        if mavzu and mavzu != last_topic:
            out.append(f"Mavzu: {mavzu}")
            last_topic = mavzu
        n += 1
        ans = g("javob").strip().rstrip(".)").upper()
        ans = {"1": "A", "2": "B", "3": "C", "4": "D"}.get(ans, ans)
        out.append(f"#@{idx}")
        out.append(f"{n}. {g('savol')}")
        for letter in "ABCD":
            out.append(f"{letter}) {g(letter)}")
        out.append(f"Javob: {ans}" if ans in ("A", "B", "C", "D") else f"Javob: ?{ans}")
        if g("izoh"):
            out.append(f"Izoh: {g('izoh')}")
        if g("manba"):
            out.append(f"Manba: {g('manba')}")
        out.append("")
    return "\n".join(out)


def template_xlsx_bytes() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Savollar"
    ws.append(SHEET_HEAD)
    ws.append(["Amir Temur qachon tug'ilgan?", "1326-yil", "1336-yil", "1346-yil", "1356-yil", "B",
               "Kesh yaqinida tug'ilgan.", "Tarix", "Amir Temur davri", "7-sinf O'zbekiston tarixi"])
    ws.append(["O'zbekiston poytaxti qaysi shahar?", "Samarqand", "Buxoro", "Toshkent", "Xiva", "C", "", "Tarix", "Amir Temur davri"])
    for col, w in zip("ABCDEFGHIJ", (46, 18, 18, 18, 18, 8, 30, 14, 24, 30)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def extract_text(filename: str, data: bytes):
    """(matn, ogohlantirishlar). Xatoda ValueError."""
    name = (filename or "").lower()
    warn = []
    if len(data) > MAX_FILE:
        raise ValueError("Fayl juda katta (15 MB dan oshmasin).")
    if name.endswith(".docx"):
        try:
            from docx import Document

            doc = Document(io.BytesIO(data))
        except Exception:
            raise ValueError("Word faylni o'qib bo'lmadi. Fayl .docx bo'lishi kerak (eski .doc emas).")
        lines, img_ok, img_bad = docx_lines(doc)
        if img_ok:
            warn.append(f"Worddan {img_ok} ta rasm olindi.")
        if img_bad:
            warn.append(f"{img_bad} ta rasm o'qilmadi (JPG yoki PNG ko'rinishida qo'ying).")
        if doc.tables:
            warn.append("Fayldagi jadvallar o'qilmadi (faqat oddiy matn).")
        return "\n".join(lines), warn
    if name.endswith(".xlsx"):
        try:
            from openpyxl import load_workbook

            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            rows = list(wb.active.iter_rows(values_only=True))
            wb.close()
        except Exception:
            raise ValueError("Excel faylni o'qib bo'lmadi (.xlsx bo'lishi kerak).")
        return rows_to_text(rows), warn
    if name.endswith(".csv"):
        import csv

        raw = None
        for enc in ("utf-8-sig", "utf-8", "cp1251"):
            try:
                raw = data.decode(enc)
                break
            except UnicodeDecodeError:
                pass
        if raw is None:
            raise ValueError("CSV kodlashini o'qib bo'lmadi. UTF-8 qilib saqlang.")
        try:
            dialect = csv.Sniffer().sniff(raw[:2000], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return rows_to_text(list(csv.reader(io.StringIO(raw), dialect))), warn
    if name.endswith(".txt") or name.endswith(".md") or "." not in name:
        for enc in ("utf-8-sig", "utf-16", "cp1251"):
            try:
                return data.decode(enc), warn
            except (UnicodeDecodeError, UnicodeError):
                pass
        raise ValueError("Matn kodlashini o'qib bo'lmadi. Faylni UTF-8 qilib saqlang.")
    raise ValueError("Faqat .txt, .docx, .xlsx yoki .csv fayl yuboring (yoki matnni to'g'ridan-to'g'ri yozing).")


# ---------------------------------------------------------------
#  Saqlash
# ---------------------------------------------------------------
def _nm(s):
    # apostrof (‘ ’ ʻ ') va tire (– —) farqi hisobga olinmaydi: «O'zbekiston» = «O‘zbekiston»
    from .darsliklar import norm

    return norm(s)


async def resolve_topics(session, res, default_topic_id, may_create=False, create=False):
    """Har bir savol uchun mavzu aniqlanadi.

    Qaytaradi: (xaritа {id(savol): topic_id}, yangi_mavzular [matn], xatolar [matn]).
    create=False — faqat tekshiradi (bazaga yozmaydi).
    """
    from sqlalchemy import select

    from ..models import Subject, Topic

    subjects = (await session.scalars(select(Subject))).all()
    by_subj = {_nm(s.name): s for s in subjects}
    topics = (await session.scalars(select(Topic))).all()
    cache, new_notes, errors, mapping = {}, [], [], {}

    for q in res.closed + res.open:
        sname, tname = q.get("subject"), q.get("topic")
        key = (_nm(sname), _nm(tname))
        if key in cache:
            mapping[id(q)] = cache[key]
            continue
        tid = None
        if not sname and not tname:
            tid = default_topic_id
            if not tid:
                errors.append(f"{q['line']}-qator: mavzu ko'rsatilmagan (Mavzu: … yozing yoki mavzuni tanlang)")
        elif not tname:
            tid = default_topic_id
            if not tid:
                errors.append(f"{q['line']}-qator: «Fan: {sname}» yozilgan, lekin «Mavzu: …» yo'q")
        else:
            subj = by_subj.get(_nm(sname)) if sname else None
            if sname and subj is None:
                errors.append(f"{q['line']}-qator: «{sname}» nomli fan topilmadi (fan botda oldindan bo'lishi kerak)")
            else:
                cands = [x for x in topics if _nm(x.name) == _nm(tname) and (subj is None or x.subject_id == subj.id)]
                if len(cands) == 1:
                    tid = cands[0].id
                elif len(cands) > 1:
                    errors.append(f"{q['line']}-qator: «{tname}» mavzusi bir nechta fanda bor — «Fan: …» yozing")
                elif subj is None:
                    errors.append(f"{q['line']}-qator: «{tname}» mavzusi topilmadi — «Fan: …» ham yozing")
                elif not may_create:
                    errors.append(f"{q['line']}-qator: «{tname}» mavzusi yo'q va yangi mavzu ochishga ruxsatingiz yo'q")
                else:
                    if create:
                        nt = Topic(subject_id=subj.id, name=tname.strip()[:200])
                        session.add(nt)
                        await session.flush()
                        topics.append(nt)
                        tid = nt.id
                    else:
                        tid = -1
                    new_notes.append(f"{subj.name} → {tname.strip()}")
        cache[key] = tid
        mapping[id(q)] = tid
    return mapping, new_notes, errors


async def save_parsed(session, default_topic_id, res: Parsed, uid: int, may_create: bool = False):
    """Bazaga yozadi. Qaytaradi: dict(closed, open, dup, batch, new_topics, errors)."""
    import json
    from datetime import datetime

    from sqlalchemy import select

    from ..models import ImportBatch, OpenQuestion, Question

    mapping, new_notes, errors = await resolve_topics(session, res, default_topic_id, may_create, create=True)
    if errors:
        await session.rollback()
        return {"closed": 0, "open": 0, "dup": 0, "batch": None, "new_topics": [], "errors": errors}

    have, have_open = {}, {}

    async def _have(tid):
        if tid not in have:
            have[tid] = {compact(x or "") for x in (await session.scalars(select(Question.text).where(Question.topic_id == tid))).all()}
            have_open[tid] = {compact(x or "") for x in (await session.scalars(select(OpenQuestion.stem).where(OpenQuestion.topic_id == tid))).all()}

    added_c, added_o, dup = [], [], 0
    dup_tr, new_tr = [], []
    for q in res.closed:
        tid = mapping[id(q)]
        await _have(tid)
        k = compact(q["text"])
        if k in have[tid]:
            dup += 1
            if q.get("tr"):
                dup_tr.append((tid, k, q["tr"]))
            continue
        have[tid].add(k)
        o = q["options"]
        row = Question(
            topic_id=tid, text=q["text"], image_path=image_ref(q["image"]) if q.get("image") else None,
            question_mode="closed",
            option_a=o["A"], option_b=o["B"], option_c=o["C"], option_d=o["D"],
            correct_option=q["correct"], correct_answer=o[q["correct"]],
            explanation=with_source(q.get("explanation"), q.get("source")),
        )
        session.add(row)
        added_c.append(row)
        if q.get("tr"):
            new_tr.append((row, q["tr"]))
    for q in res.open:
        tid = mapping[id(q)]
        await _have(tid)
        k = compact(q["stem"])
        if k in have_open[tid]:
            dup += 1
            continue
        have_open[tid].add(k)
        row = OpenQuestion(topic_id=tid, stem=q["stem"],
                           parts=open_parts_json(q, image_ref(q["image"]) if q.get("image") else None), created_by=uid)
        session.add(row)
        added_o.append(row)
    await session.flush()
    tr_count = 0
    new_ids = {r.id for r in added_c}
    if new_tr or dup_tr:
        from .. import qtrans

        for row, trs in new_tr:
            for lang, d in trs.items():
                await qtrans.upsert(session, row.id, lang, d["text"], d["options"], d.get("explanation"))
                tr_count += 1
        for tid_, k_, trs in dup_tr:
            qid_ = None
            for ex in (await session.scalars(select(Question).where(Question.topic_id == tid_))).all():
                if ex.id not in new_ids and compact(ex.text or "") == k_:
                    qid_ = ex.id
                    break
            if qid_ is None:
                continue
            for lang, d in trs.items():
                await qtrans.upsert(session, qid_, lang, d["text"], d["options"], d.get("explanation"))
                tr_count += 1
    batch = ImportBatch(
        user_tg_id=uid,
        closed_ids=json.dumps([r.id for r in added_c]),
        open_ids=json.dumps(
            [{"id": r.id, "topic_id": r.topic_id, "stem": r.stem, "parts": r.parts, "by": r.created_by} for r in added_o],
            ensure_ascii=False,
        ),
        created_at=datetime.utcnow(),
    )
    session.add(batch)
    await session.flush()
    batch_id = batch.id
    await session.commit()
    return {"closed": len(added_c), "open": len(added_o), "dup": dup, "tr": tr_count,
            "batch": batch_id, "new_topics": new_notes, "errors": []}


def _batch_counts(b):
    try:
        c = len(json.loads(b.closed_ids or "[]"))
    except ValueError:
        c = 0
    try:
        o = len(json.loads(b.open_ids or "[]"))
    except ValueError:
        o = 0
    return c, o


async def history(session, uid: int, is_admin: bool, limit: int = 10):
    from sqlalchemy import select

    from ..models import ImportBatch

    q = select(ImportBatch).order_by(ImportBatch.id.desc()).limit(limit)
    if not is_admin:
        q = select(ImportBatch).where(ImportBatch.user_tg_id == uid).order_by(ImportBatch.id.desc()).limit(limit)
    return list((await session.scalars(q)).all())


def _allowed(b, uid, is_admin):
    return b is not None and (b.user_tg_id == uid or is_admin)


async def undo_batch(session, batch_id: int, uid: int, is_admin: bool):
    """Yuklashni bekor qiladi — vaqt cheklovi yo'q. Yopiq savollar faol emas qilinadi
    (o'quvchilar tarixi saqlanadi), ochiq savollar o'chiriladi (nusxasi saqlanadi). → (ok, xabar)"""
    from sqlalchemy import delete, update

    from ..models import ImportBatch, OpenQuestion, Question

    b = await session.get(ImportBatch, batch_id)
    if b is None:
        return False, "Yuklash topilmadi."
    if not _allowed(b, uid, is_admin):
        return False, "Bu yuklash sizniki emas."
    if b.undone:
        return False, "Bu yuklash allaqachon bekor qilingan."
    cids = json.loads(b.closed_ids or "[]")
    oitems = json.loads(b.open_ids or "[]")
    oids = [x["id"] if isinstance(x, dict) else x for x in oitems]
    if cids:
        await session.execute(update(Question).where(Question.id.in_(cids)).values(is_active=False))
    if oids:
        await session.execute(delete(OpenQuestion).where(OpenQuestion.id.in_(oids)))
    b.undone = True
    await session.commit()
    return True, f"Bekor qilindi: {len(cids)} yopiq, {len(oids)} ochiq savol."


async def redo_batch(session, batch_id: int, uid: int, is_admin: bool):
    """Bekor qilingan yuklashni qayta tiklaydi. → (ok, xabar)"""
    from sqlalchemy import update

    from ..models import ImportBatch, OpenQuestion, Question

    b = await session.get(ImportBatch, batch_id)
    if b is None:
        return False, "Yuklash topilmadi."
    if not _allowed(b, uid, is_admin):
        return False, "Bu yuklash sizniki emas."
    if not b.undone:
        return False, "Bu yuklash bekor qilinmagan."
    cids = json.loads(b.closed_ids or "[]")
    oitems = json.loads(b.open_ids or "[]")
    if any(not isinstance(x, dict) for x in oitems):
        return False, "Bu yuklashdagi ochiq savollarni tiklab bo'lmaydi (eski yuklash)."
    if cids:
        await session.execute(update(Question).where(Question.id.in_(cids)).values(is_active=True))
    rows = []
    for x in oitems:
        row = OpenQuestion(topic_id=x["topic_id"], stem=x["stem"], parts=x["parts"], created_by=x.get("by", uid))
        session.add(row)
        rows.append((x, row))
    await session.flush()
    for x, row in rows:
        x["id"] = row.id
    b.open_ids = json.dumps(oitems, ensure_ascii=False)
    b.undone = False
    await session.commit()
    return True, f"Qayta tiklandi: {len(cids)} yopiq, {len(oitems)} ochiq savol."


async def can_import(uid: int) -> bool:
    from ..admin_roles import can_add_questions, is_full_admin, is_teacher

    return bool(is_full_admin(uid) or await can_add_questions(uid) or await is_teacher(uid))


# ---------------------------------------------------------------
#  Namuna
# ---------------------------------------------------------------
TEMPLATE_TEXT = """# SAVOL YUKLASH NAMUNASI
# «#» bilan boshlangan qatorlar o'qilmaydi — ularni o'chirmasangiz ham bo'ladi.

# Mavzuni botda tanlamasangiz, shu yerga yozing (fan botda oldindan bo'lishi kerak):
# Fan: Tarix
# Mavzu: Amir Temur davri

1. Amir Temur qachon tug'ilgan?
A) 1326-yil
B) 1336-yil
C) 1346-yil
D) 1356-yil
Javob: B
Izoh: Amir Temur 1336-yil 9-aprelda Kesh yaqinida tug'ilgan.
Manba: 7-sinf O'zbekiston tarixi

2. O'zbekiston poytaxti qaysi shahar?
A) Samarqand
B) Buxoro
C) Toshkent
D) Xiva
Javob: C

---

XIII asr boshlarida Xorazmshohlar davlatiga kim hukmronlik qilgan?
a) Hukmdor kim? => Muhammad Xorazmshoh | Alovuddin Muhammad
b) Poytaxti qaysi shahar? => Urganch | Gurganj
"""


def template_docx_bytes() -> bytes:
    from docx import Document

    doc = Document()
    for line in TEMPLATE_TEXT.splitlines():
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


HELP_HTML = (
    "<b>Yopiq savol</b> (har biri alohida, orasida bo'sh qator):\n"
    "<code>1. Savol matni\nA) variant\nB) variant\nC) variant\nD) variant\n"
    "Javob: B\nIzoh: nega bu javob to'g'ri (ixtiyoriy)\nManba: 8-sinf O'zbekiston tarixi, 52-bet (ixtiyoriy)</code>\n\n"
    "<b>Ochiq savol</b> (ochiq savollar <code>---</code> bilan ajratiladi):\n"
    "<code>Savol matni\na) qism savoli =&gt; javob1 | javob2\n"
    "b) qism savoli =&gt; javob1 + javob2</code>\n"
    "<code>|</code> — muqobil javoblar, <code>+</code> — ikkalasi ham kerak. Ochiq savolda ham "
    "<code>Izoh:</code>, <code>Manba:</code> va <code>Qabul: x | y</code> (boshqa to'g'ri javoblar) qatorlari bo'ladi.\n\n"
    "Bir faylda yopiq va ochiq savollar aralash bo'lishi mumkin.\n"
    "Bir fayldan bir necha mavzuga yuklash: <code>Fan: Tarix</code> va <code>Mavzu: …</code> qatorlarini yozing.\n"
    "<b>Excel/CSV</b> ham bo'ladi: ustunlar Savol, A, B, C, D, Javob, Izoh, Manba (namuna faylni oling).\n"
    "O'quvchi izoh va manbani javob bergandan keyin (imtihonda — imtihon yopilgach) ko'radi.\n"
    "Format Moodle'ning Aiken formatini ham qamrab oladi (<code>ANSWER: B</code>).\n"
    "Rasmli savol: Wordda rasmni savol matnidan keyin (variantlardan oldin) qo'ying, yoki botga rasmni "
    "savol matni bilan birga (izoh/caption qilib) yuboring.\n"
    "Eski usul ham ishlaydi: to'g'ri variant oxiriga <code>++</code> qo'ying.\n\n"
    "<b>Boshqa tilda variant</b> (yopiq savollar uchun): o'zbekcha savol tagiga <code>RU:</code> yoki <code>EN:</code> "
    "yozib, shu savolni tarjima qiling (variantlar tartibi bir xil):\n"
    "<code>RU:\nТекст вопроса\nA) вариант\nB) вариант\nC) вариант\nD) вариант\nОбъяснение: ixtiyoriy</code>\n"
    "Tarjima bo'sh qator bilan tugaydi. Savol bazada bor bo'lsa, unga faqat tarjima qo'shiladi."
)
