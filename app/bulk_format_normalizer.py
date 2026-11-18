"""Normalize mixed A-D question formats for Doniyor Academy bulk import.

Only text parsing: no DB, Telegram, or file writes.
The existing bulk parser remains responsible for final validation.
"""

import re


def _bulk_extract_correct_marker(value):
    value = (value or "").rstrip()
    match = re.search(r"\+{2,}\s*$", value)
    if match:
        return value[:match.start()].rstrip(), True
    match = re.search(r"\s+\.{2,}\s*$", value)
    if match:
        return value[:match.start()].rstrip(), True
    return value, False


_BULK_LINE_OPTION_RE = re.compile(
    r'^\s*(?:[\(\[]\s*([A-Da-d])\s*[\)\]]|([A-Da-d])(?:\s*[\)\]\.:;\-\u2011\u2013\u2014\u2212]\s*|\s+))\s*(.*?)\s*$'
)
_BULK_LONE_OPTION_RE = re.compile(r'^\s*([A-Da-d])\s*$')
_BULK_INLINE_RE = re.compile(
    r'(?<!\S)(?:[\(\[]\s*([A-Da-d])\s*[\)\]]|([A-Da-d])\s*[\)\]\.:;\-\u2011\u2013\u2014\u2212])\s*'
)
_BULK_NUMBER_HEADER_RE = re.compile(
    r'^\s*(?:\#|\u2116\s*)?\d{1,4}\s*(?:\.+|\)|:|;|-|\u2011|\u2013|\u2014|\u2212)\s*\S+'
)

def _bulk_tokenize(raw):
    lines = []
    for n, original in enumerate(raw.replace('\r\n', '\n').replace('\r', '\n').split('\n'), 1):
        s = original.strip()
        if not s:
            lines.append((n, ''))
            continue
        hits = list(_BULK_INLINE_RE.finditer(s))
        letters = [(h.group(1) or h.group(2)).upper() for h in hits]
        ordered = (len(hits) >= 2 and all(ord(letters[k + 1]) == ord(letters[k]) + 1 for k in range(len(letters) - 1)))
        if ordered and (hits[0].start() == 0 or len(hits) >= 4):
            if hits[0].start():
                lines.append((n, s[:hits[0].start()].strip()))
            for k, h in enumerate(hits):
                right = hits[k + 1].start() if k + 1 < len(hits) else len(s)
                lines.append((n, s[h.start():right].strip()))
        else:
            lines.append((n, original))
    return lines

def _bulk_option_header(line):
    match = _BULK_LINE_OPTION_RE.match(line)
    if match:
        return (match.group(1) or match.group(2)).upper(), match.group(3).strip(), (match.group(1) or match.group(2)).isupper()
    match = _BULK_LONE_OPTION_RE.match(line)
    if match:
        return match.group(1).upper(), '', match.group(1).isupper()
    return None

def normalize_bulk_questions(raw):
    lines = _bulk_tokenize(raw or '')
    candidates = [(i, head) for i, (_, line) in enumerate(lines) if (head := _bulk_option_header(line))]
    groups = []
    consumed_end = 0

    for k in range(len(candidates) - 3):
        indices = [candidates[k + j][0] for j in range(4)]
        heads = [candidates[k + j][1] for j in range(4)]
        if indices[0] < consumed_end or ''.join(head[0] for head in heads) != 'ABCD':
            continue
        next_marker = candidates[k + 4][0] if k + 4 < len(candidates) else len(lines)
        d_idx = indices[3]
        d_end = next_marker
        for i in range(d_idx + 1, next_marker):
            if not lines[i][1].strip():
                d_end = i
                break
            if _BULK_NUMBER_HEADER_RE.match(lines[i][1]):
                d_end = i
                break
        # If another set of options appears later, regard an unindented line after D
        # as the next question, rather than part of the D answer.
        if d_end == next_marker and next_marker < len(lines):
            if candidates[k + 4][1][0] == 'A':
                for i in range(d_idx + 1, next_marker):
                    if lines[i][1].strip() and not lines[i][1][:1].isspace():
                        d_end = i
                        break
        bounds = [*indices, d_end]
        option_texts = [
            ' '.join(lines[i][1].strip() for i in range(start + 1, end) if lines[i][1].strip()).strip()
            for start, end in zip(bounds[:4], bounds[1:])
        ]
        option_texts = [(heads[j][1] + ' ' + option_texts[j]).strip() for j in range(4)]
        markers = [ _bulk_extract_correct_marker(x)[1] for x in option_texts ]
        marked = sum(markers)
        # A lowercase a/b/c/d enumeration in the question is not an answer block
        # unless it contains a correct-answer marker.
        if marked == 0 and not any(head[2] for head in heads):
            future_marked = False
            for l in range(k + 1, len(candidates) - 3):
                sub = [candidates[l + j][1] for j in range(4)]
                if ''.join(x[0] for x in sub) != 'ABCD':
                    continue
                if any('++' in x[1] or re.search(r'\s\.{2,}\s*$', x[1]) for x in sub):
                    future_marked = True
                    break
            if future_marked:
                continue
        # Once we accept a complete A-D group, the original validator will detect
        # missing / multiple correct answers. 
        groups.append((indices[0], d_end, option_texts, indices))
        consumed_end = d_end

    if not groups:
        return '', ['A-D javoblar guruhi topilmadi. Variantlardan birini ++ bilan belgilang.']

    canonical = []
    previous_end = 0
    errors = []
    for n, (start, end, options, indices) in enumerate(groups, 1):
        question_lines = [lines[i][1].strip() for i in range(previous_end, start)]
        while question_lines and not question_lines[0]:
            question_lines.pop(0)
        while question_lines and not question_lines[-1]:
            question_lines.pop()
        question = '\ue000'.join(question_lines).strip()
        question = re.sub(
            r'^\s*(?:\#|\u2116\s*)?\d{1,4}\s*(?:\.+|\)|:|;|-|\u2011|\u2013|\u2014|\u2212)\s*',
            '', question, count=1,
        )
        if not question:
            errors.append(f'{n}-savol: savol matni topilmadi.')
        canonical.append(f'{n}. {question}')
        canonical.extend(f'{letter}) {body}' for letter, body in zip('ABCD', options))
        previous_end = end
    # detect trailing non-empty lines; after a final blank line D ends and later text means incomplete question
    if any(line.strip() for _, line in lines[previous_end:]):
        errors.append('Oxirgi savoldan keyin yakunlanmagan matn bor.')
    # require all strong markers to be contained in option values
    orig_count = len(re.findall(r'\+{2,}', raw or ''))
    contained_count = sum(len(re.findall(r'\+{2,}', opt)) for _, _, opts, _ in groups for opt in opts)
    if orig_count != contained_count:
        errors.append('Ba\u02bczi ++ belgilari javob variantiga biriktirilmadi.')
    return '\n'.join(canonical), errors
