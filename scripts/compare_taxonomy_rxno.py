#!/usr/bin/env python3
"""Compare the SynEPD taxonomy against RXNO/MOP and report the divergence.

Where ``build_rxno_mapping.py`` *produces* the committed linkset, this script
*analyses* it: how much of the taxonomy maps, how much of RXNO the taxonomy
covers, and — most usefully — where the two vocabularies genuinely diverge
(SynEPD's mechanistic steps that RXNO has no concept for, and RXNO named
reactions absent from SynEPD).

It reuses the matching machinery from ``build_rxno_mapping`` so the numbers
always agree with the committed crosswalk.

Usage
-----
    python scripts/compare_taxonomy_rxno.py                 # printed report
    python scripts/compare_taxonomy_rxno.py --top 30        # longer samples
    python scripts/compare_taxonomy_rxno.py --markdown out.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

from build_rxno_mapping import (  # noqa: E402
    DEFAULT_DB,
    OBO_CACHE,
    build_indexes,
    classify,
    ensure_obo,
    load_obo,
    obo_data_version,
    read_taxa,
)


def _bar(part: int, whole: int, width: int = 24) -> str:
    filled = round(width * part / whole) if whole else 0
    return "█" * filled + "·" * (width - filled)


def pct(part: int, whole: int) -> str:
    return f"{part / whole:.1%}" if whole else "n/a"


def analyse(taxa, terms, accepted, review):
    codes_exact = {r[0] for r in accepted if r[3] == "skos:exactMatch"}
    codes_broad = {r[0] for r in accepted if r[3] == "skos:broadMatch"}
    codes_review = {r[0] for r in review}
    linked = codes_exact | codes_broad
    review_only = codes_review - linked
    unmatched = {c for c, _, _ in taxa} - linked - codes_review

    rxno_terms = {t["id"] for t in terms if t["id"].startswith("RXNO:")}
    mop_terms = {t["id"] for t in terms if t["id"].startswith("MOP:")}
    linked_terms = {r[6] for r in accepted}
    rxno_hit = linked_terms & rxno_terms
    mop_hit = linked_terms & mop_terms

    return {
        "taxa": taxa,
        "n_taxa": len(taxa),
        "exact": codes_exact,
        "broad": codes_broad - codes_exact,
        "review_only": review_only,
        "unmatched": unmatched,
        "rxno_terms": rxno_terms,
        "mop_terms": mop_terms,
        "rxno_hit": rxno_hit,
        "mop_hit": mop_hit,
        "review": review,
    }


def render_report(a, terms, data_version, top: int) -> str:
    n = a["n_taxa"]
    by_name = {c: nm for c, _, nm in a["taxa"]}
    out: list[str] = []
    w = out.append

    w("=" * 72)
    w("  SynEPD taxonomy  ×  RXNO / MOP   comparison report")
    w(
        f"  ontology: RXNO {data_version} "
        f"({len(a['rxno_terms'])} RXNO + {len(a['mop_terms'])} MOP terms)"
    )
    w("=" * 72)

    # 1. Forward coverage: how much of SynEPD maps.
    exact, broad = len(a["exact"]), len(a["broad"])
    rev, unm = len(a["review_only"]), len(a["unmatched"])
    w("\n[1] Taxonomy coverage (SynEPD -> RXNO/MOP)")
    for label, cnt in [
        ("exactMatch (committed)", exact),
        ("broadMatch (committed)", broad),
        ("review candidate only", rev),
        ("no candidate at all", unm),
    ]:
        w(f"    {label:26s} {cnt:5d}  {pct(cnt, n):>6}  {_bar(cnt, n)}")
    linked = exact + broad
    w(f"    {'-' * 26} {'-' * 5}")
    w(f"    {'committed links':26s} {linked:5d}  {pct(linked, n):>6}")
    w(
        f"    {'reachable incl. review':26s} {linked + rev:5d}  "
        f"{pct(linked + rev, n):>6}"
    )

    # 2. Reverse coverage: how much of RXNO SynEPD uses.
    rx, mo = len(a["rxno_hit"]), len(a["mop_hit"])
    w("\n[2] Ontology coverage (which RXNO/MOP terms SynEPD links to)")
    w(
        f"    RXNO terms referenced {rx:5d} / {len(a['rxno_terms'])}  "
        f"{pct(rx, len(a['rxno_terms'])):>6}  {_bar(rx, len(a['rxno_terms']))}"
    )
    w(
        f"    MOP  terms referenced {mo:5d} / {len(a['mop_terms'])}  "
        f"{pct(mo, len(a['mop_terms'])):>6}  {_bar(mo, len(a['mop_terms']))}"
    )

    # 3. Coverage by taxonomy level.
    w("\n[3] Committed coverage by taxonomy level")
    linked_codes = a["exact"] | a["broad"]
    per_level: dict[int, list[int]] = {}
    for c, lv, _ in a["taxa"]:
        tot, hit = per_level.setdefault(lv, [0, 0])
        per_level[lv][0] = tot + 1
        if c in linked_codes:
            per_level[lv][1] = hit + 1
    for lv in sorted(per_level):
        tot, hit = per_level[lv]
        w(f"    level {lv}: {hit:4d} / {tot:4d}  {pct(hit, tot):>6}  {_bar(hit, tot)}")

    # 4. Divergence A: SynEPD steps RXNO has no concept for.
    w(
        "\n[4] SynEPD taxa with NO RXNO/MOP candidate "
        f"(mechanistic granularity gap) — showing {min(top, unm)} of {unm}"
    )
    for c in sorted(a["unmatched"])[:top]:
        w(f"    {c:20s} {by_name[c]}")

    # 5. Divergence B: strongest review candidates worth promoting.
    w("\n[5] Highest-confidence review candidates (not yet committed) — " f"top {top}")
    ranked = sorted(a["review"], key=lambda r: (-float(r[5]), r[0], r[6]))
    seen: set[str] = set()
    shown = 0
    for r in ranked:
        if r[0] in seen:
            continue
        seen.add(r[0])
        w(f"    {r[5]:>5}  {r[4]:22s} {by_name[r[0]]}" f"  ->  {r[6]} {r[7]}")
        shown += 1
        if shown >= top:
            break

    # 6. Headline divergence summary.
    w("\n[6] Interpretation")
    w(
        f"    - {pct(linked, n)} of taxa carry a committed ontology link; the "
        f"{pct(unm, n)} with"
    )
    w("      no candidate are elementary electron-pushing steps (protonation,")
    w("      enolate generation, SNAr) that RXNO does not model.")
    w(
        f"    - SynEPD references only {pct(rx, len(a['rxno_terms']))} of RXNO: it is "
        "mechanism-first and"
    )
    w("      finer-grained, not a subset of RXNO's named-reaction catalogue.")
    return "\n".join(out) + "\n"


def render_markdown(a, terms, data_version, top: int) -> str:
    n = a["n_taxa"]
    by_name = {c: nm for c, _, nm in a["taxa"]}
    exact, broad = len(a["exact"]), len(a["broad"])
    rev, unm = len(a["review_only"]), len(a["unmatched"])
    rx = len(a["rxno_hit"])
    md = [
        "# SynEPD × RXNO/MOP comparison",
        "",
        f"Ontology: **RXNO {data_version}** "
        f"({len(a['rxno_terms'])} RXNO + {len(a['mop_terms'])} MOP terms). "
        f"Taxonomy: **{n} taxa**.",
        "",
        "## Coverage",
        "",
        "| Bucket | Taxa | Share |",
        "|---|---:|---:|",
        f"| exactMatch (committed) | {exact} | {pct(exact, n)} |",
        f"| broadMatch (committed) | {broad} | {pct(broad, n)} |",
        f"| review candidate only | {rev} | {pct(rev, n)} |",
        f"| no candidate | {unm} | {pct(unm, n)} |",
        "",
        f"SynEPD references **{rx}/{len(a['rxno_terms'])}** RXNO terms "
        f"({pct(rx, len(a['rxno_terms']))}).",
        "",
        f"## Mechanistic gap: taxa with no RXNO concept (first {top})",
        "",
    ]
    for c in sorted(a["unmatched"])[:top]:
        md.append(f"- `{c}` {by_name[c]}")
    return "\n".join(md) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--obo", type=Path, default=OBO_CACHE)
    ap.add_argument("--top", type=int, default=15, help="Sample rows per section.")
    ap.add_argument("--markdown", type=Path, help="Also write a Markdown report.")
    args = ap.parse_args()

    obo = ensure_obo(args.obo)
    terms = load_obo(obo)
    data_version = obo_data_version(obo)
    taxa = read_taxa(args.db)
    accepted, review = classify(taxa, build_indexes(terms))

    a = analyse(taxa, terms, accepted, review)
    print(render_report(a, terms, data_version, args.top))

    if args.markdown:
        args.markdown.write_text(render_markdown(a, terms, data_version, args.top))
        print(f"wrote {args.markdown}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
