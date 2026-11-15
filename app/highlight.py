"""Find where component IDs, wire numbers and labels sit on a page, using the OCR word boxes.

Scanned manuals have noisy OCR (O/0, I/1, S/5, B/8 confusion, broken words), so matching is done
on a 'confusable key' and allows a small edit distance on longer tokens.
"""
import re

from . import ingest

_CONFUSABLE = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "|": "1", "S": "5", "B": "8", "Z": "2"})
WIRE_SUFFIX = re.compile(r"^[A-Z]{1,2}\d{1,2}N?$")
WIRE_BASE = re.compile(r"^[A-Z]{1,2}\d{1,4}$")            # 'P25' -> all segments of wire P25
DESIGNATOR = re.compile(r"^[A-Z]{1,3}\d{1,4}[A-Z]?$")     # K45, S102, CB12, BT1


def key(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper()).translate(_CONFUSABLE)


def _plain(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def _edit_distance_le(a: str, b: str, k: int) -> bool:
    if abs(len(a) - len(b)) > k:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        if min(cur) > k:
            return False
        prev = cur
    return prev[-1] <= k


def _merge_line_boxes(boxes: list[tuple]) -> list[tuple]:
    """Merge word boxes that sit on the same text line into one rectangle."""
    out: list[list] = []
    for b in sorted(boxes, key=lambda b: (round(b[1]), b[0])):
        if out and abs(out[-1][1] - b[1]) < 4 and b[0] - out[-1][2] < 20:
            o = out[-1]
            out[-1] = [min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])]
        else:
            out.append(list(b))
    return [tuple(o) for o in out]


def _part_eq(wk: str, pk: str) -> bool:
    return bool(wk) and (wk == pk or (len(pk) >= 5 and _edit_distance_le(wk, pk, 1)))


def _next_word(words: list, wkeys: list, chain: list[int], pk: str, used: set) -> int | None:
    """Nearest word matching `pk` that continues the label: right on the same line, or on one of
    the next lines underneath the words matched so far."""
    last = words[chain[-1]]
    x0 = min(words[j][0] for j in chain)
    x1 = max(words[j][2] for j in chain)
    lh = max(4.0, last[3] - last[1])
    best, best_d = None, 1e9
    for j, w in enumerate(words):
        if j in used or j in chain or not _part_eq(wkeys[j], pk):
            continue
        same_line = abs(w[1] - last[1]) < lh * 0.6 and -2 <= w[0] - last[2] < lh * 2.5
        below = 0 < w[1] - last[1] < lh * 2.6 and w[0] < x1 + lh * 3 and w[2] > x0 - lh * 3
        if same_line or below:
            d = abs(w[1] - last[1]) * 2 + abs(w[0] - last[0])
            if d < best_d:
                best, best_d = j, d
    return best


def find_term(words: list, term: str, max_hits: int = 12) -> list[tuple]:
    """Return boxes (x0, y0, x1, y1 in page points) where `term` appears."""
    tkey = key(term)
    if not tkey:
        return []
    tparts = [p for p in term.split() if key(p)]
    n = max(1, len(tparts))
    wkeys = [key(w[4]) for w in words]
    hits: list[tuple] = []
    used: set[int] = set()

    # 1) single-word matches (exact, wire-run prefix, or fuzzy for longer tokens)
    if n == 1:
        tplain = _plain(term)
        for i, (w, wk) in enumerate(zip(words, wkeys)):
            if not wk:
                continue
            ok = wk == tkey
            if not ok and WIRE_BASE.match(tplain) and wk.startswith(tkey):
                rest = _plain(w[4])[len(tplain):]
                ok = bool(WIRE_SUFFIX.match(rest) or WIRE_SUFFIX.match(rest.replace("O", "0")))  # 'P25' matches wire segments P25A20, P25B20N
            if not ok and len(tkey) >= 5:
                ok = _edit_distance_le(wk, tkey, 1)
            if not ok and DESIGNATOR.match(tplain):
                # designator glued to other text by OCR, e.g. 'Contactor(K45)': box just that part
                raw = w[4].upper()
                m = re.search(rf"(?<![A-Z0-9]){re.escape(tplain)}(?![A-Z0-9])", raw)
                if m:
                    cw = (w[2] - w[0]) / max(1, len(raw))
                    hits.append((w[0] + cw * m.start(), w[1], w[0] + cw * m.end(), w[3]))
                    used.add(i)
                    continue
            if ok:
                hits.append(tuple(w[:4]))
                used.add(i)

    # 2) phrases laid out spatially: schematic labels are often stacked over two or three lines
    if n > 1:
        pkeys = [key(p) for p in tparts]
        for i, wk in enumerate(wkeys):
            if i in used or not _part_eq(wk, pkeys[0]):
                continue
            chain = [i]
            for pk in pkeys[1:]:
                nxt = _next_word(words, wkeys, chain, pk, used)
                if nxt is None:
                    break
                chain.append(nxt)
            if len(chain) == n:
                bx = [words[j] for j in chain]
                hits.append((min(b[0] for b in bx), min(b[1] for b in bx), max(b[2] for b in bx), max(b[3] for b in bx)))
                used.update(chain)

    # 3) phrases in reading order (also catches OCR that glued words together) whose joined key equals the term key
    if not hits:
        for i in range(len(words)):
            if i in used or not wkeys[i] or not tkey.startswith(wkeys[i][: max(1, min(3, len(wkeys[i])))]):
                continue
            joined, span = "", []
            for j in range(i, min(len(words), i + n + 3)):
                if not wkeys[j]:
                    continue
                joined += wkeys[j]
                span.append(j)
                if len(joined) >= len(tkey):
                    break
            tol = 0 if len(tkey) < 8 else max(1, len(tkey) // 10)
            if joined == tkey or (tol and _edit_distance_le(joined, tkey, tol)):
                hits.extend(_merge_line_boxes([tuple(words[j][:4]) for j in span]))
                used.update(span)
        if len(hits) > max_hits:
            hits = hits[:max_hits]
    return hits[:max_hits]


def locate_terms(doc_id: int, page_no: int, terms: list[str]) -> dict:
    """Map each term to normalised boxes {x, y, w, h} in 0..1 page coordinates."""
    words, width, height = ingest.page_words(doc_id, page_no)
    result = {}
    for term in terms:
        boxes = find_term(words, term) if words else []
        result[term] = [
            {"x": b[0] / width, "y": b[1] / height, "w": (b[2] - b[0]) / width, "h": (b[3] - b[1]) / height}
            for b in boxes
        ]
    return result
