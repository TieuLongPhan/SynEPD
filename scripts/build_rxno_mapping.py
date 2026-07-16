#!/usr/bin/env python3
"""Cross-reference SynEPD taxonomy names against the RSC Name Reaction Ontology.

RXNO (name reactions, ``RXNO:00000xx``) and its sibling MOP (molecular process
classes, ``MOP:xxxxxxx``) are CC-BY 4.0 OBO Foundry ontologies with stable IDs.
Mapping SynEPD taxa to them yields a portable external key that other datasets
can compare against, and surfaces where the two vocabularies diverge.

This is a small, deterministic pipeline:

    1. load ontology   - parse RXNO/MOP terms from the OBO release.
    2. load taxonomy   - read taxon names from the release database.
    3. match           - exact, token-set, token containment/overlap, shared
                         eponym, and fuzzy tiers (see review_candidates).
    4. classify        - assign a SKOS relation and decide accept vs review.
    5. render          - crosswalk TSV, SKOS Turtle, review TSV, JSON report.
    6. write / --check  - emit artifacts, or verify the committed ones are fresh.

Two relations are auto-accepted into the committed crosswalk:

    skos:exactMatch - exact name/synonym match, or an unambiguous content-token
                      match (same words after dropping stop/filler words).
    skos:broadMatch - the taxon's tokens strictly contain a multi-word term's
                      tokens (e.g. "Evans aldol addition" -> "aldol addition"),
                      i.e. the taxon is a narrower concept than the term.

Weaker guesses (generic single-word containment, token overlap, shared surname,
fuzzy string similarity) are written with a confidence score to a separate
``needs_review`` file for curation, never committed, because they produce
confident but wrong hits (e.g. "Martin sulfurane dehydration" vs "Dess-Martin
oxidation").

Committed artifacts: ``data/rxno_crosswalk.tsv`` and ``data/rxno_crosswalk.ttl``.
Working artifacts (git-ignored under ``data/check/``): the review TSV and a JSON
run report.

Usage
-----
    python scripts/build_rxno_mapping.py           # download OBO if needed
    python scripts/build_rxno_mapping.py --obo path/to/rxno.obo
    python scripts/build_rxno_mapping.py --check    # non-zero exit if a
                                                     # committed artifact is stale
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_DB = REPOSITORY_ROOT / "data" / "epdb.sqlite"
OBO_URL = "http://purl.obolibrary.org/obo/rxno.obo"
OBO_CACHE = REPOSITORY_ROOT / "data" / "check" / "rxno.obo"
CROSSWALK_TSV = REPOSITORY_ROOT / "data" / "rxno_crosswalk.tsv"
CROSSWALK_TTL = REPOSITORY_ROOT / "data" / "rxno_crosswalk.ttl"
NEEDS_REVIEW_TSV = (
    REPOSITORY_ROOT / "data" / "check" / "rxno_crosswalk_needs_review.tsv"
)
REPORT_JSON = REPOSITORY_ROOT / "data" / "check" / "rxno_crosswalk_report.json"

# Placeholder namespace for SynEPD taxonomy concepts in the SKOS export. Swap
# for the project's canonical base IRI once one is published.
SYNEPD_TAXON_BASE = "https://w3id.org/synepd/taxon/"
OBO_IRI_BASE = "http://purl.obolibrary.org/obo/"

# Content words that carry no discriminating meaning for a reaction name.
STOP_WORDS = frozenset(
    {"reaction", "synthesis", "type", "the", "of", "a", "an", "process", "reactions"}
)
# Capitalised tokens that look like surnames but are common chemistry words.
NON_EPONYMS = frozenset(
    {"polar", "lewis", "ring", "acid", "base", "type", "aromatic", "cationic"}
)


# --------------------------------------------------------------------------- #
# OBO parsing
# --------------------------------------------------------------------------- #
def load_obo(path: Path) -> list[dict]:
    """Return non-obsolete RXNO/MOP terms as dicts with id, name, synonyms."""
    terms: list[dict] = []
    cur: dict | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line == "[Term]":
            _commit_term(terms, cur)
            cur = {}
        elif cur is not None:
            if line.startswith("id: "):
                cur["id"] = line[4:]
            elif line.startswith("name: "):
                cur["name"] = line[6:]
            elif line.startswith("synonym: "):
                m = re.match(r'synonym: "(.*?)"', line)
                if m:
                    cur.setdefault("synonyms", []).append(m.group(1))
            elif line.startswith("is_obsolete: true"):
                cur["obsolete"] = True
    _commit_term(terms, cur)
    return terms


def _commit_term(terms: list[dict], cur: dict | None) -> None:
    if not cur or cur.get("obsolete") or "name" not in cur:
        return
    if cur.get("id", "").split(":")[0] in {"RXNO", "MOP"}:
        terms.append(cur)


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #
def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def normalise(s: str) -> str:
    """Lower-case, accent- and dash-folded, punctuation-stripped form.

    Digit pairs inside sigmatropic descriptors are joined so that "[2,3]" and
    "[3,3]" stay distinct tokens ("23" vs "33") instead of both collapsing to a
    bare "3" that would spuriously satisfy a subset test.
    """
    s = _strip_accents(s).lower()
    s = re.sub(r"[‐-―−]", "-", s)  # unify dashes
    s = re.sub(r"(?<=\d),(?=\d)", "", s)  # "2,3" -> "23", keep pairs distinct
    s = re.sub(r"[^a-z0-9\- ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def content_tokens(s: str) -> frozenset[str]:
    parts = re.split(r"[\s\-]+", normalise(s))
    return frozenset(p for p in parts if p and p not in STOP_WORDS)


def eponyms(s: str) -> frozenset[str]:
    """Capitalised surname-like tokens, e.g. {'paal', 'knorr'} from a name.

    Used only to propose *review* candidates: shared surnames are a strong hint
    but not proof (Meerwein arylation vs Meerwein-Ponndorf-Verley reduction).
    """
    out: set[str] = set()
    for token in re.split(r"\s+", _strip_accents(s)):
        for part in re.split(r"[‐-―−\-]", token):
            if (
                re.fullmatch(r"[A-Z][a-z]{3,}", part)
                and part.lower() not in NON_EPONYMS
            ):
                out.add(part.lower())
    return frozenset(out)


# --------------------------------------------------------------------------- #
# Index and match
# --------------------------------------------------------------------------- #
def build_indexes(terms: list[dict]):
    by_label: dict[str, dict] = {}
    by_tokens: dict[frozenset[str], list[dict]] = defaultdict(list)
    by_eponym: dict[frozenset[str], list[dict]] = defaultdict(list)
    term_tokens: list[tuple[frozenset[str], dict]] = []
    for term in terms:
        for name in [term["name"], *term.get("synonyms", [])]:
            by_label.setdefault(normalise(name), term)
            by_tokens[content_tokens(name)].append(term)
        ep = eponyms(term["name"])
        if ep:
            by_eponym[ep].append(term)
        toks = content_tokens(term["name"])
        if toks:
            term_tokens.append((toks, term))
    return by_label, by_tokens, by_eponym, term_tokens


# Recall-tier scores (0..1). Only exact/token land in the committed crosswalk;
# everything below is a *suggestion* written to the review file for curation.
MAX_REVIEW_PER_TAXON = 6
FUZZY_MIN = 0.86
JACCARD_MIN = 0.5


def _fuzzy(a: str, b: str) -> float:
    sm = SequenceMatcher(None, a, b)
    if sm.quick_ratio() < FUZZY_MIN:  # cheap upper-bound prefilter
        return 0.0
    return sm.ratio()


def review_candidates(name, toks, by_tokens, by_eponym, term_tokens):
    """Yield (match_type, score, term) recall suggestions for one taxon.

    Covers ambiguous exact-token sets, token containment/overlap (e.g.
    "Aromatic nitration" -> "nitration"), shared eponyms, and fuzzy string
    similarity. Deliberately high-recall; scores let a human triage.
    """
    label = normalise(name)
    ep = eponyms(name)
    seen: dict[str, tuple[str, float, dict]] = {}

    def offer(mtype: str, score: float, term: dict) -> None:
        prev = seen.get(term["id"])
        if prev is None or score > prev[1]:
            seen[term["id"]] = (mtype, round(score, 3), term)

    # Same content-token set but several ontology terms claim it.
    if toks and toks in by_tokens:
        for t in by_tokens[toks]:
            offer("token-ambiguous", 0.75, t)

    # Token containment and overlap against each ontology term.
    if toks:
        for tt, term in term_tokens:
            if tt == toks:
                continue
            if tt < toks:  # taxon is a more specific spelling of the term
                offer(
                    "token-superset" if len(tt) >= 2 else "token-superset-generic",
                    0.80 if len(tt) >= 2 else 0.45,
                    term,
                )
                continue
            inter = toks & tt
            if inter:
                jacc = len(inter) / len(toks | tt)
                if jacc >= JACCARD_MIN:
                    offer("token-overlap", 0.5 + 0.3 * jacc, term)

    # Shared surname sets: exact eponym match, then subset.
    if ep:
        if ep in by_eponym:
            for t in by_eponym[ep]:
                offer("eponym", 0.70, t)
        else:
            for k, ts in by_eponym.items():
                if ep <= k:
                    for t in ts:
                        offer("eponym-subset", 0.55, t)

    # Whole-string fuzzy similarity (catches paraphrase / word order).
    for tt, term in term_tokens:
        r = _fuzzy(label, normalise(term["name"]))
        if r >= FUZZY_MIN:
            offer("fuzzy", r, term)

    ranked = sorted(seen.values(), key=lambda c: (-c[1], c[2]["id"]))
    return ranked[:MAX_REVIEW_PER_TAXON]


# SKOS predicate assigned to each accepted match type.
RELATION = {
    "exact": "skos:exactMatch",
    "token": "skos:exactMatch",
    "token-superset": "skos:broadMatch",
}
# Suggested predicate for review tiers, so a curator knows the intended link.
REVIEW_RELATION = {
    "token-ambiguous": "skos:exactMatch?",
    "token-superset-generic": "skos:broadMatch?",
    "token-overlap": "skos:closeMatch?",
    "eponym": "skos:closeMatch?",
    "eponym-subset": "skos:closeMatch?",
    "fuzzy": "skos:closeMatch?",
}


def classify(taxa, indexes):
    """Convert taxa into (accepted, review) link records.

    accepted rows carry a resolved SKOS relation and land in the committed
    crosswalk: exact/unambiguous-token -> exactMatch, and the reliable
    token-superset tier -> broadMatch (taxon is narrower than the term).
    Everything weaker becomes a scored review suggestion.

    Row shape (both lists):
        (code, level, name, relation, match_type, score, ont_id, ont_name)
    """
    by_label, by_tokens, by_eponym, term_tokens = indexes
    accepted: list[tuple] = []
    review: list[tuple] = []
    for code, level, name in taxa:
        label = normalise(name)
        toks = content_tokens(name)
        if label in by_label:
            term = by_label[label]
            accepted.append(
                (
                    code,
                    level,
                    name,
                    RELATION["exact"],
                    "exact",
                    1.0,
                    term["id"],
                    term["name"],
                )
            )
            continue
        if toks and toks in by_tokens and len({t["id"] for t in by_tokens[toks]}) == 1:
            term = by_tokens[toks][0]
            accepted.append(
                (
                    code,
                    level,
                    name,
                    RELATION["token"],
                    "token",
                    0.97,
                    term["id"],
                    term["name"],
                )
            )
            continue
        promoted = False
        pending_review: list[tuple] = []
        for mtype, score, term in review_candidates(
            name, toks, by_tokens, by_eponym, term_tokens
        ):
            if mtype == "token-superset":
                accepted.append(
                    (
                        code,
                        level,
                        name,
                        RELATION[mtype],
                        mtype,
                        score,
                        term["id"],
                        term["name"],
                    )
                )
                promoted = True
            else:
                pending_review.append(
                    (
                        code,
                        level,
                        name,
                        REVIEW_RELATION.get(mtype, ""),
                        mtype,
                        score,
                        term["id"],
                        term["name"],
                    )
                )
        # Once a taxon has a resolved broadMatch, suppress only the low-value
        # generic-superset noise for it; keep genuine alternative candidates.
        if promoted:
            pending_review = [
                r for r in pending_review if r[4] != "token-superset-generic"
            ]
        review.extend(pending_review)
    return accepted, review


# --------------------------------------------------------------------------- #
# IO
# --------------------------------------------------------------------------- #
def ensure_obo(path: Path) -> Path:
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {OBO_URL} -> {path}", file=sys.stderr)
    with urllib.request.urlopen(OBO_URL, timeout=120) as resp:  # noqa: S310
        path.write_bytes(resp.read())
    return path


def read_taxa(db_path: Path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT code, level, name FROM taxon ORDER BY code"
        ).fetchall()
    finally:
        conn.close()


def obo_data_version(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("data-version:"):
            return line.split(":", 1)[1].strip()
        if line.startswith("[Term]"):
            break
    return "unknown"


# Both crosswalk and review share one schema:
#   taxon_code, taxon_level, taxon_name, relation, match_type, score,
#   ontology_id, ontology_name
HEADER = (
    "taxon_code",
    "taxon_level",
    "taxon_name",
    "relation",
    "match_type",
    "score",
    "ontology_id",
    "ontology_name",
)
_SCORE_IDX = HEADER.index("score")
_OID_IDX = HEADER.index("ontology_id")


def render_tsv(rows) -> str:
    # Deterministic: taxon code, then descending score, then ontology id.
    def key(r):
        return (r[0], -float(r[_SCORE_IDX]), r[_OID_IDX])

    lines = ["\t".join(HEADER)]
    for row in sorted(rows, key=key):
        lines.append("\t".join(str(c) for c in row))
    return "\n".join(lines) + "\n"


def _obo_iri(ontology_id: str) -> str:
    return OBO_IRI_BASE + ontology_id.replace(":", "_")


def render_skos(accepted, data_version: str) -> str:
    """Emit the accepted linkset as a SKOS mapping in Turtle."""
    head = [
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "@prefix obo: <http://purl.obolibrary.org/obo/> .",
        f"@prefix taxon: <{SYNEPD_TAXON_BASE}> .",
        "",
        "# SynEPD taxonomy -> RXNO/MOP crosswalk (SKOS mappings).",
        f"# Generated by scripts/build_rxno_mapping.py from RXNO {data_version}.",
        "# taxon: is a placeholder namespace; substitute the canonical base IRI.",
        "",
    ]
    body = []
    for r in sorted(accepted, key=lambda r: (r[0], r[_OID_IDX])):
        code, relation, oid = r[0], r[3], r[_OID_IDX]
        body.append(f"taxon:{code} {relation} obo:{oid.replace(':', '_')} .")
    return "\n".join(head + body) + "\n"


def build_report(taxa, terms, accepted, review, data_version: str) -> dict:
    linked = {r[0] for r in accepted}
    reviewed = {r[0] for r in review}
    return {
        "rxno_data_version": data_version,
        "ontology_terms": {
            "rxno": sum(1 for t in terms if t["id"].startswith("RXNO:")),
            "mop": sum(1 for t in terms if t["id"].startswith("MOP:")),
        },
        "taxa_total": len(taxa),
        "accepted_links": len(accepted),
        "accepted_by_relation": dict(Counter(r[3] for r in accepted)),
        "accepted_by_match_type": dict(Counter(r[4] for r in accepted)),
        "taxa_with_accepted_link": len(linked),
        "review_suggestions": len(review),
        "review_by_match_type": dict(Counter(r[4] for r in review)),
        "taxa_covered_incl_review": len(linked | reviewed),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument(
        "--obo",
        type=Path,
        default=OBO_CACHE,
        help="RXNO OBO file; downloaded to the cache path if absent.",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the committed crosswalk or SKOS file is stale.",
    )
    args = ap.parse_args()

    # Stage 1-2: load ontology and taxonomy.
    obo = ensure_obo(args.obo)
    terms = load_obo(obo)
    data_version = obo_data_version(obo)
    taxa = read_taxa(args.db)

    # Stage 3-4: match and classify into SKOS relations.
    indexes = build_indexes(terms)
    accepted, review = classify(taxa, indexes)

    # Stage 5: render artifacts.
    crosswalk = render_tsv(accepted)
    skos = render_skos(accepted, data_version)
    report = build_report(taxa, terms, accepted, review, data_version)

    by_rel = report["accepted_by_relation"]
    print(
        f"RXNO {data_version} | {report['ontology_terms']['rxno']} RXNO + "
        f"{report['ontology_terms']['mop']} MOP terms | taxa: {len(taxa)}\n"
        f"accepted: {len(accepted)} links "
        f"({by_rel.get('skos:exactMatch', 0)} exactMatch, "
        f"{by_rel.get('skos:broadMatch', 0)} broadMatch) "
        f"over {report['taxa_with_accepted_link']} taxa "
        f"({report['taxa_with_accepted_link'] / len(taxa):.1%})\n"
        f"review suggestions: {len(review)} | taxa covered incl. review: "
        f"{report['taxa_covered_incl_review']} "
        f"({report['taxa_covered_incl_review'] / len(taxa):.1%})",
        file=sys.stderr,
    )

    if args.check:
        stale = []
        if (CROSSWALK_TSV.read_text() if CROSSWALK_TSV.exists() else "") != crosswalk:
            stale.append(CROSSWALK_TSV.name)
        if (CROSSWALK_TTL.read_text() if CROSSWALK_TTL.exists() else "") != skos:
            stale.append(CROSSWALK_TTL.name)
        if stale:
            print(f"stale: {', '.join(stale)}; rerun without --check.", file=sys.stderr)
            return 1
        print("crosswalk artifacts are up to date.", file=sys.stderr)
        return 0

    # Stage 6: write committed linkset + working review/report artifacts.
    CROSSWALK_TSV.write_text(crosswalk)
    CROSSWALK_TTL.write_text(skos)
    NEEDS_REVIEW_TSV.parent.mkdir(parents=True, exist_ok=True)
    NEEDS_REVIEW_TSV.write_text(render_tsv(review))
    REPORT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        f"wrote {CROSSWALK_TSV.relative_to(REPOSITORY_ROOT)}, "
        f"{CROSSWALK_TTL.relative_to(REPOSITORY_ROOT)}, "
        f"{NEEDS_REVIEW_TSV.relative_to(REPOSITORY_ROOT)}, "
        f"{REPORT_JSON.relative_to(REPOSITORY_ROOT)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
