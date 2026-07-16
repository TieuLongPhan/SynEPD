# Changelog

All notable changes to SynEPD are documented in this file. The project follows
[Semantic Versioning](https://semver.org/).

## 🏷️ [0.3.0] - 2026-07-16

SynEPD 0.3.0 links the taxonomy to external reaction ontologies, adds a
publication-quality reaction-diagram PDF generator, and tightens API error
handling. It builds on the 0.2.0 mechanistic-center release without changing the
release-database contract.

### ✨ Added

- Added `scripts/build_rxno_mapping.py`, a deterministic pipeline that maps
  the SynEPD taxonomy onto the RSC Name Reaction Ontology (RXNO) and Molecular
  Process Ontology (MOP). It commits a SKOS crosswalk (`data/rxno_crosswalk.tsv`
  and `data/rxno_crosswalk.ttl`) with 138 `skos:exactMatch` and 89
  `skos:broadMatch` links, and writes scored lower-confidence suggestions and a
  JSON run report to `data/check/` for curation. `--check` verifies the
  committed artifacts are fresh.
- Added `scripts/compare_taxonomy_rxno.py`, which reports the SynEPD-vs-RXNO/MOP
  comparison: forward and reverse coverage, coverage by taxonomy level, the
  mechanistic-granularity gap (taxa RXNO has no concept for), and the strongest
  uncommitted review candidates. Optional `--markdown` export.
- Added `synepd/render_epd_pdf.py`, a reaction-diagram PDF generator that renders
  polar records through the CDK Depict service (matching the web explorer's 2D
  reaction diagram): atom-map indices, fully explicit atoms without condensed
  abbreviations, and hydrogens that are explicit in the SMILES. It mirrors the web
  depiction controls (atom mapping, abbreviations, hydrogen-display mode) and falls
  back to local RDKit rendering when CDK Depict is unreachable.

### 🛡️ Security

- Redacted backend exception text from client-visible 4xx responses in the
  balance-check, similarity-search, and substructure-search endpoints; causes
  are logged server-side and regression tests pin the fixed messages.

## 🏷️ [0.2.0] - 2026-07-15

SynEPD 0.2.0 introduces EPD-aware mechanistic centers, product-verified
mechanism projection, a reproducible release database, and a versioned web API.
The original direct reaction center remains the net-transformation index; the
new mechanistic context records atoms and edges involved in the electron-pushing
path without changing that established meaning.

### ✨ Added

- Added `MechanisticCenter`, with explicit roles for net-center,
  EPD-context, and normalization-context atoms.
- Added net-change, transition, context, and transient-only edge roles.
- Recorded ordered `LWGEditor` edit events while applying each EPD.
- Added a materialized `mechanism_context` table containing the context anchor,
  construction version, deterministic context hash, transition events, and
  diagnostics for every admitted reaction.
- Added product-verified path-2 mechanism projection using the materialized
  context anchor. Multiple valid mechanisms are returned as explicit
  candidates instead of selecting one by database order.
- Added documented representation metadata for mechanisms that require a
  pair-only closed-shell surrogate, including Jones oxidation.
- Added a compressed, type-preserving JSON graph codec:
  `synepd.node-link-json.zlib.v1`.
- Added transactional, source-assisted migration from the v0.1 release schema,
  including graph conversion and a schema migration ledger.
- Added atomic offline database construction, structured admission reports,
  integrity invariants, and preservation of the previous artifact if a build
  fails.
- Added [the release manifest](data/release-manifest.json), containing release
  metadata, semantic counts, graph formats, integrity status, and the database
  SHA-256 digest.
- Added a manifest verification command:

  ```bash
  python -m synepd.construct.release_manifest data/epdb.sqlite \
    --verify data/release-manifest.json
  ```

- Added a typed, read-only release repository for reactions, EPD arrows, and
  mechanism contexts.
- Added stable `/api/v1` routes while retaining the original `/api` routes as
  compatibility aliases.
- Added bounded rate limits for anonymous rendering, chemistry-query,
  KG-search, and submission operations, plus request-body limits for chemistry,
  bulk-export, and submission POST requests.
- Added an explorer control for switching between the complete ITS and the
  EPD-aware mechanistic center.
- Added distinct patterned visualization for transition and context edges,
  EPD-context atoms, and normalization-context atoms.
- Added exact mechanism-context lookup and context metadata to the knowledge
  graph API.
- Added CI gates for formatting, tests, strict corpus EPD verification, and
  release-manifest integrity.

### 🔄 Changed

- Rebuilt `data/epdb.sqlite` as release `v0.2.0` with an offline,
  reproducible-by-default pipeline.
- Preserved the curated mapped reaction SMILES as the mechanism-bearing
  `aam_key` and stored the separately canonicalized mapping as
  `canonical_aam_key`.
- Exposed canonical AAM and mechanism-context information in reaction, query,
  and export responses.
- Changed reaction-center construction to use chemistry-aware isomorphism
  checks rather than WL hash equality alone; hash collisions receive stable
  suffixes.
- Evaluated all bounded reference/context mappings during mechanism projection
  and required complete endpoint-cardinality preservation.
- Renamed internal database classes to distinguish the in-memory case index,
  case SQLite store, and normalized release database. Compatibility aliases are
  retained for v0.1 callers.
- Changed knowledge-graph fingerprint and topology caches to initialize lazily,
  avoiding large duplicate allocations during service startup.
- Updated the Python package version to `0.2.0`.

### 🐛 Fixed

- Fixed projection that previously omitted atoms and edges used by EPD arrows
  but absent from the direct reaction center.
- Fixed projection paths that could silently lose multi-atom endpoint
  cardinality.
- Fixed deterministic handling of ambiguous mechanisms: ambiguity is now
  reported rather than guessed.
- Fixed aromatic/Kekulé verification failures caused by executing curated EPDs
  in a different canonical atom-map namespace.
- Fixed bulk export by joining templates through `its.rc_id` instead of a
  nonexistent EPD column.
- Fixed static API route precedence so paths such as
  `/reactions/export-bulk` are not captured by `{reaction_id}`.
- Fixed health responses that exposed database configuration details.
- Fixed submission administration to fail closed when no admin token is
  configured and to compare configured tokens in constant time.

### 🛡️ Security

- Removed pickle decoding from normal application and query paths.
- Restricted legacy pickle decoding to explicitly enabled migration mode.
- Added regression coverage for malicious graph payloads.
- Added checksum verification for downloaded database artifacts.
- Added stable, redacted API error responses for internal failures, oversized
  requests, and rate-limit rejection.

### 📊 Data release

| Item | v0.2.0 |
| :--- | :---: |
| 📝 Curated records | 1,915 |
| 🧪 Database reactions | 1,915 |
| 📐 Reaction-center templates | 1,497 |
| 🕸️ ITS graphs | 1,915 |
| 📋 EPD records | 1,915 |
| ➡️ EPD arrows | 7,303 |
| 🧬 Mechanism contexts | 1,915 |
| ⚛️ Molecules | 2,179 |
| 🏷️ Taxonomy rows | 1,051 |

- ✅ **SQLite integrity check:** `ok`
- ✅ **Foreign-key violations:** `0`
- 🔒 **Database SHA-256:** `dea811c906c2a73c564c69536ab061cacc8e210cdd4a56977ea2a8943261a802`
- 🔎 **Strict EPD audit:** 1,914 exact passes, one documented closed-shell surrogate, zero mismatches, and zero execution errors.

### ⚠️ Compatibility notes

- Existing unversioned `/api` routes remain available, but new clients should
  use `/api/v1`.
- Legacy database class names remain available as compatibility aliases.
- v0.1 graph blobs can be upgraded through the migration path; runtime code
  intentionally rejects them unless legacy migration mode is explicitly
  enabled.
- The configured Zenodo download remains v0.1.0 until the v0.2.0 artifact is
  published and its record ID is added to `ZENODO_RECORD_IDS`.

## 🏷️ [0.1.0] - 2026-07-07

### ✨ Added

- Initial SynEPD package and curated POLAR-derived data release.
- Hierarchical reaction taxonomy, reaction-center templates, ITS graphs, and
  electron-pushing diagram arrows.
- SQLite release database and construction utilities.
- Reaction balance, atom-map consistency, hydrogen-completion, and EPD
  prechecks.
- Python query helpers, database managers, FastAPI service, interactive web
  explorer, and relationally derived knowledge graph.
- Sphinx documentation, Read the Docs configuration, CI, and release-triggered
  PyPI publishing.

[0.3.0]: https://github.com/TieuLongPhan/SynEPD/releases/tag/v0.3.0
[0.2.0]: https://github.com/TieuLongPhan/SynEPD/releases/tag/v0.2.0
[0.1.0]: https://github.com/TieuLongPhan/SynEPD/releases/tag/v0.1.0
