#!/usr/bin/env python3
"""glossary-forge: build and police a term base from translations you already have.

Standard library only. No network access.

  mine     find term candidates (names, items, places, recurring proper nouns) and their
           established translations from existing source/target file pairs
  check    find strings that break the glossary (term present in source, approved
           translation missing in target) and terms translated several different ways
  report   write glossary.html — a reviewable, filterable view of the term base
  export   write glossary as TBX (CAT tools) or a simple JSON map
  add      add or approve a term from the command line
"""
import argparse
import html
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# @@CORE-IMPORT@@
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from l10n_core import *  # noqa: F401,F403  (inlined into this file when packaged)
# @@END-CORE-IMPORT@@

VERSION = "1.0.0"
NAME_KEY = re.compile(r"(?:^|[._\-/])(?:name|displayname|title)$", re.I)
STOP = set("""a an the of and or to in on at for with from by is are was were be been it its this that these those
you your yours i me my we our he she they them his her their not no yes all any some more most very just than
then there here what which who whom when where why how can could will would shall should may might must do does
did done have has had get got make made let lets go goes going come came see saw look take took give gave use
one two three new old good great little big small first last next other another each every such only own same so too
oh ah hey hi hello okay ok well yeah sure please thanks thank sorry mr mrs ms dr""".split())
UI_WORDS = set("""Enable Disable Enabled Disabled Show Hide Toggle Default Settings Setting Options Option Config
Mode Key Keys Button Menu Open Close Save Load Reset Apply Cancel Yes No On Off True False Left Right Up Down Max Min
Speed Size Color Colour Text Name Description Tooltip Debug Log Logs Value Values Item Items""".split())
# Thai characters that cannot start / end a word fragment (combining marks, following vowels / leading vowels)
TH_NO_START = set("ะัาำิีึืฺุูๅ็่้๊๋์ํ๎")
TH_NO_END = set("เแโใไั")


def log(m):
    print(m, file=sys.stderr)


def get_cfg(root):
    cfg = load_config(root)
    if not cfg["_found"]:
        cfg = detect_config(root)
    return cfg


def pairs(cfg):
    """All (group, key, source, target) with an existing, different translation."""
    out = []
    for s, t, g in discover(cfg):
        if not Path(t).exists():
            continue
        sd, td = load_doc(s).as_dict(), load_doc(t).as_dict()
        for k, v in sd.items():
            if k in td and isinstance(td[k], str) and td[k].strip() and td[k] != v:
                out.append((g, k, v, td[k]))
    return out


def clean(text, cfg):
    return re.sub(r"\s+", " ", strip_tokens(text, cfg["profile"], cfg.get("extra_tokens"))).strip()


# ----------------------------------------------------------------------------- candidate discovery

def proper_nouns(text):
    """Capitalised word sequences that are not just sentence-initial words."""
    out = []
    for m in re.finditer(r"(?:(?<=^)|(?<=[^\w'’]))((?:[A-Z][\w'’\-]*[a-z][\w'’\-]*|[A-Z]{2,})(?:\s+(?:of|the|de|du|von)?\s*(?:[A-Z][\w'’\-]*[a-z][\w'’\-]*|[A-Z]{2,})){0,3})", text):
        phrase = m.group(1).strip()
        start = m.start(1)
        before = text[:start].rstrip()
        sentence_start = not before or before[-1] in ".!?:\"“#^"
        words = [w for w in phrase.split() if not re.fullmatch(r"I(?:['’](?:m|ll|ve|d|re|s))?|[A-Z]['’]\w*", w)]
        if not words:
            continue
        if len(words) == 1 and (sentence_start or words[0].lower() in STOP or words[0] in UI_WORDS):
            continue
        if len(words) == 1 and len(words[0]) < 3:
            continue
        while words and (words[0].lower() in STOP or words[0] in UI_WORDS):
            words = words[1:]
        if words:
            out.append(" ".join(words))
    return out


def fragments(text, lang, lo=2, hi=24):
    """Candidate target renderings: word n-grams for spaced scripts, character spans for Thai-like scripts."""
    out = set()
    if uses_latin(lang) or base_lang(lang) in ("ru", "uk", "el", "ar", "he", "hi", "ko"):
        words = re.findall(r"[\w'’\-]+", text)
        for n in range(1, 5):
            for i in range(len(words) - n + 1):
                out.add(" ".join(words[i:i + n]))
        return out
    # split on whitespace/punctuation only: \W would also split at Thai vowel and tone marks (category Mn)
    for run in re.findall(r"[^\s\d.,!?;:\"'“”‘’()\[\]{}…—–\-/\\|<>=@#$%^&*+~`]+", text):
        L = len(run)
        for i in range(L):
            if run[i] in TH_NO_START:
                continue
            for j in range(i + lo, min(L, i + hi) + 1):
                frag = run[i:j]
                if frag[-1] in TH_NO_END or (j < L and run[j] in TH_NO_START):
                    continue
                out.add(frag)
    return out


def align(term, plus, minus_sample, lang):
    """Best target rendering for `term`: frequent in strings whose source has the term (plus),
    rare elsewhere (minus). Returns (rendering, coverage, specificity) or None."""
    if len(plus) < 2:
        return None
    cnt = Counter()
    for t in plus:
        for f in fragments(t, lang):
            cnt[f] += 1
    need = max(2, math.ceil(0.6 * len(plus)))
    cands = [(f, c) for f, c in cnt.items() if c >= need]
    if not cands:
        return None
    best = None
    for f, c in cands:
        bg = sum(1 for t in minus_sample if f in t) / max(1, len(minus_sample))
        cover = c / len(plus)
        spec = cover / (cover + bg * 4 + 1e-9)
        score = cover * spec * (1 + 0.04 * min(len(f), 15))
        if spec < 0.75:
            continue
        if best is None or score > best[3] + 1e-9:
            best = (f, cover, spec, score)
    if not best:
        return None
    # prefer the longest fragment with the same coverage (avoid a clipped syllable)
    same = [f for f, c in cands if c / len(plus) >= best[1] - 1e-9 and best[0] in f
            and sum(1 for t in minus_sample if f in t) / max(1, len(minus_sample)) <= 0.02]
    if same:
        f = max(same, key=len)
        return f, best[1], best[2]
    return best[0], best[1], best[2]


def cmd_mine(a):
    root = Path(a.root).resolve()
    cfg = get_cfg(root)
    lang = cfg["target_lang"]
    gpath = root / cfg["glossary"]
    existing = {r["source"].lower(): r for r in load_glossary(gpath)}
    P = pairs(cfg)
    if not P:
        sys.exit("no translated pairs found — run loc-translate init/scan first, or check .l10n.json layouts")
    found = {}  # source term -> dict

    def put(src, tgt, typ, conf, cover, how, where):
        k = src.lower()
        cur = found.get(k)
        cand = {"source": src, "target": tgt, "type": typ, "confidence": round(conf, 2), "coverage": round(cover, 2),
                "how": how, "where": where}
        if cur is None or cand["confidence"] > cur["confidence"]:
            found[k] = cand

    # 1. name-like keys: whole value is the term
    name_votes = defaultdict(Counter)
    for g, k, s, t in P:
        if NAME_KEY.search(k) or k.lower().endswith((".name", "_name", ".displayname")):
            cs, ct = clean(s, cfg), clean(t, cfg)
            if 0 < len(cs.split()) <= 5 and ct and len(ct) <= 40:
                name_votes[cs][ct] += 1
    for src, votes in name_votes.items():
        tgt, n = votes.most_common(1)[0]
        put(src, tgt, "item/name", 0.95 if len(votes) == 1 else 0.7, n / sum(votes.values()), "name key", "")

    # 2. "ไทย (English)" pairs left in target strings
    paren = re.compile(r"([^\s()（）\[\]{}]{2,40})\s*[（(]([A-Za-z][A-Za-z0-9 .'’\-]{1,40})[)）]")
    for g, k, s, t in P:
        for m in paren.finditer(t):
            tgt, src = m.group(1), m.group(2).strip()
            if any(is_target_char(c, lang) for c in tgt) and src.lower() in s.lower():
                # spaced scripts give a clean boundary; for Thai/Japanese/Chinese the span may include extra words
                put(src, tgt, "name", 0.9 if uses_latin(lang) else 0.7, 1.0, "parenthesised pair (check the boundary)", f"{g}::{k}")

    # 3. recurring proper nouns aligned statistically
    occ = defaultdict(list)
    for i, (g, k, s, t) in enumerate(P):
        for pn in set(proper_nouns(clean(s, cfg))):
            occ[pn].append(i)
    targets = [clean(t, cfg) for _, _, _, t in P]
    import random
    random.seed(0)
    for term, idx in sorted(occ.items(), key=lambda kv: -len(kv[1])):
        if len(idx) < a.min_count or term.lower() in found:
            continue
        plus = [targets[i] for i in idx]
        sidx = set(idx)
        minus = [targets[i] for i in random.sample(range(len(targets)), min(400, len(targets))) if i not in sidx]
        r = align(term, plus, minus, lang)
        if r:
            tgt, cover, spec = r
            put(term, tgt, "proper noun", 0.5 + 0.4 * cover * spec, cover, f"aligned over {len(idx)} strings", "")
        elif len(idx) >= a.min_count:
            # Latin-script term kept as-is in target (brand / untranslated name)
            kept = sum(1 for i in idx if term in P[i][3]) / len(idx)
            if kept >= 0.6:
                put(term, term, "keep as-is", 0.6, kept, f"kept in {kept:.0%} of {len(idx)} strings", "")

    # merge with existing glossary (approved rows win; never overwrite them)
    rows = list(load_glossary(gpath))
    have = {r["source"].lower() for r in rows}
    added = 0
    for k, c in sorted(found.items(), key=lambda kv: (-kv[1]["confidence"], kv[1]["source"])):
        if k in have or c["confidence"] < a.min_confidence:
            continue
        rows.append({"source": c["source"], "target": c["target"], "type": c["type"], "status": "candidate",
                     "case_sensitive": "", "note": f"{c['how']} · confidence {c['confidence']:.2f} · coverage {c['coverage']:.0%}",
                     "count": str(sum(1 for _, _, s, _ in P if c["source"].lower() in s.lower()))})
        added += 1
    save_glossary(gpath, rows)
    print(f"mined {len(found)} terms from {len(P)} translated strings; added {added} new candidates → {gpath}")
    show = sorted([r for r in rows if r.get("status") == "candidate"], key=lambda r: -int(r.get("count") or 0))[:a.top]
    print(f"{'source':<32} {'target':<26} {'type':<14} {'uses':>5}  note")
    for r in show:
        print(f"{r['source'][:32]:<32} {r['target'][:26]:<26} {r['type'][:14]:<14} {r.get('count', ''):>5}  {r['note'][:60]}")


# ----------------------------------------------------------------------------- consistency check

def term_findings(cfg, rows, statuses=("approved", "candidate")):
    P = pairs(cfg)
    lang = cfg["target_lang"]
    res = []
    for r in rows:
        if (r.get("status") or "approved") not in statuses or not r.get("target"):
            continue
        term = r["source"]
        flags = 0 if str(r.get("case_sensitive", "")).lower() in ("1", "true", "yes") else re.I
        rx = re.compile(r"(?<![\w])" + re.escape(term) + r"(?![\w])", flags)
        uses = [(g, k, s, t) for g, k, s, t in P if rx.search(clean(s, cfg))]
        if not uses:
            continue
        miss = [(g, k, s, t) for g, k, s, t in uses if r["target"] not in t]
        variants = Counter()
        if miss:
            plus = [clean(t, cfg) for *_, t in miss]
            for t in plus:
                for f in fragments(t, lang, 2, max(4, len(r["target"]) + 4)):
                    if abs(len(f) - len(r["target"])) <= max(2, len(r["target"]) // 2):
                        variants[f] += 1
        common = [v for v, c in variants.most_common(6) if c >= 2 and v != r["target"]][:3]
        res.append({"source": term, "target": r["target"], "status": r.get("status", ""), "type": r.get("type", ""),
                    "uses": len(uses), "consistent": len(uses) - len(miss), "missing": miss, "variants": common})
    return res


def cmd_check(a):
    root = Path(a.root).resolve()
    cfg = get_cfg(root)
    rows = load_glossary(root / cfg["glossary"])
    if not rows:
        sys.exit("glossary is empty — run `mine` first")
    st = ("approved",) if a.approved_only else ("approved", "candidate")
    res = term_findings(cfg, rows, st)
    bad = [r for r in res if r["missing"]]
    print(f"{len(res)} terms in use; {len(bad)} not applied consistently")
    for r in sorted(bad, key=lambda r: -len(r["missing"]))[:a.top]:
        pct = r["consistent"] / r["uses"]
        print(f"  {r['source']} → {r['target']}  [{r['status']}]  used {r['consistent']}/{r['uses']} ({pct:.0%})"
              + (f"  other renderings seen: {', '.join(r['variants'])}" if r["variants"] else ""))
        for g, k, s, t in r["missing"][:a.examples]:
            print(f"      {g}::{k}: {t[:110]}")
    if a.json:
        Path(a.json).write_text(json.dumps([{**r, "missing": [{"group": g, "key": k, "source": s, "target": t} for g, k, s, t in r["missing"]]} for r in res], ensure_ascii=False, indent=1), "utf-8")
    sys.exit(1 if (a.strict and bad) else 0)


# ----------------------------------------------------------------------------- add / export / report

def cmd_add(a):
    root = Path(a.root).resolve()
    cfg = get_cfg(root)
    gpath = root / cfg["glossary"]
    rows = load_glossary(gpath)
    for r in rows:
        if r["source"].lower() == a.source.lower():
            r.update({"target": a.target or r["target"], "status": a.status, "type": a.type or r.get("type", ""),
                      "note": a.note if a.note is not None else r.get("note", "")})
            break
    else:
        rows.append({"source": a.source, "target": a.target or "", "type": a.type or "", "status": a.status, "note": a.note or ""})
    save_glossary(gpath, rows)
    print(f"{a.status}: {a.source} → {a.target}")


def cmd_export(a):
    root = Path(a.root).resolve()
    cfg = get_cfg(root)
    rows = [r for r in load_glossary(root / cfg["glossary"]) if a.all or (r.get("status") or "approved") == "approved"]
    out = Path(a.out or (root / cfg.get("state_dir", "l10n") / ("glossary.tbx" if a.format == "tbx" else "glossary.json")))
    if a.format == "json":
        out.write_text(json.dumps({r["source"]: r["target"] for r in rows}, ensure_ascii=False, indent=2), "utf-8")
    else:
        sl, tl = cfg["source_lang"], cfg["target_lang"]
        body = "\n".join(
            f'      <termEntry id="t{i}">\n'
            f'        <langSet xml:lang="{sl}"><tig><term>{html.escape(r["source"])}</term></tig></langSet>\n'
            f'        <langSet xml:lang="{tl}"><tig><term>{html.escape(r["target"])}</term></tig></langSet>\n'
            + (f'        <descrip type="definition">{html.escape(r.get("note", ""))}</descrip>\n' if r.get("note") else "")
            + "      </termEntry>" for i, r in enumerate(rows, 1))
        out.write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<martif type="TBX" xml:lang="{sl}">\n  <text>\n    <body>\n{body}\n    </body>\n  </text>\n</martif>\n', "utf-8")
    print(f"exported {len(rows)} terms → {out}")


def cmd_report(a):
    root = Path(a.root).resolve()
    cfg = get_cfg(root)
    rows = load_glossary(root / cfg["glossary"])
    res = {r["source"]: r for r in term_findings(cfg, rows)}
    data = []
    for r in rows:
        f = res.get(r["source"])
        data.append({"source": r["source"], "target": r.get("target", ""), "type": r.get("type", ""), "status": r.get("status") or "approved",
                     "note": r.get("note", ""), "uses": f["uses"] if f else 0, "consistent": f["consistent"] if f else 0,
                     "variants": f["variants"] if f else [],
                     "examples": [{"where": f"{g}::{k}", "source": s[:200], "target": t[:200]} for g, k, s, t in (f["missing"][:4] if f else [])]})
    payload = {"title": a.title or f"Glossary · {cfg['source_lang']} → {cfg['target_lang']}", "lang": cfg["target_lang"],
               "terms": data, "files": len(discover(cfg))}
    tpl = load_template("glossary_report.html")
    out = Path(a.out or (root / cfg.get("state_dir", "l10n") / "glossary.html"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(tpl.replace("/*__DATA__*/null", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
                   .replace("__TITLE__", html.escape(payload["title"])), "utf-8")
    print(f"wrote {out} ({len(data)} terms)")


def load_template(name):
    emb = globals().get("EMBEDDED_TEMPLATES", {})
    if name in emb:
        return emb[name]
    return (Path(__file__).resolve().parent.parent / "assets" / name).read_text("utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="glossary.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--root", default=".", help="project root (where .l10n.json lives)")

    s = sub.add_parser("mine", help="mine term candidates from existing translations")
    common(s)
    s.add_argument("--min-count", type=int, default=3, help="minimum uses for a recurring proper noun")
    s.add_argument("--min-confidence", type=float, default=0.55)
    s.add_argument("--top", type=int, default=40)
    s.set_defaults(fn=cmd_mine)

    s = sub.add_parser("check", help="report inconsistent term use")
    common(s)
    s.add_argument("--approved-only", action="store_true")
    s.add_argument("--top", type=int, default=30)
    s.add_argument("--examples", type=int, default=2)
    s.add_argument("--json", help="also write findings to this JSON file")
    s.add_argument("--strict", action="store_true", help="exit 1 if any term is inconsistent (CI)")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("report", help="write glossary.html")
    common(s)
    s.add_argument("--out")
    s.add_argument("--title")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("export", help="export approved terms")
    common(s)
    s.add_argument("--format", choices=["tbx", "json"], default="tbx")
    s.add_argument("--all", action="store_true", help="include candidates")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("add", help="add or update a term")
    common(s)
    s.add_argument("source")
    s.add_argument("target", nargs="?")
    s.add_argument("--status", choices=["approved", "candidate", "rejected"], default="approved")
    s.add_argument("--type")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_add)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
