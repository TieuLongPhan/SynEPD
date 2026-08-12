# SynEPD

SynEPD is a hierarchical electron-pushing database for polar organic reaction mechanisms. It combines clean reaction records, a POLAR taxonomy, reaction-center templates, atom-mapped reaction graphs, and electron-pushing diagram (EPD) arrows in a local SQLite database with a web explorer.

The POLAR dataset contains closed-shell, paired-electron mechanisms. Concerted
pericyclic reactions are included when every electron movement can be expressed
with two-electron EPD arrows; radical and single-electron-transfer mechanisms
are excluded because the current vocabulary has no fishhook arrow. For named
multistage reactions, one record may encode the chemically defining polar
elementary step and document later workup stages as a representation note.

Official web server: https://synepd.bioinf.uni-leipzig.de

Zenodo release: https://zenodo.org/records/21235892

<p align="center">
  <img
    src="docs/source/_static/synepd.gif"
    alt="SynEPD Explorer preview showing the search, taxonomy, statistics, and reaction graph interface"
    width="1200"
  />
</p>

## Current Data

The current local build uses the cleaned POLAR dataset:

| Item | Count |
| --- | ---: |
| Curated records | 1,926 |
| Database reactions | 1,926 |
| RC templates | 1,521 |
| MC templates (induced RC + EPD transition edges) | 1,540 |
| MC templates EPD-enriched vs RC | 284 (18.44%) |
| MC templates structurally extending RC | 229 (14.87%) |
| EPD arrows | 8,123 |
| Mechanism contexts | 1,926 |
| Taxon rows | 939 |
| RXNO/MOP taxon links | 218 |
| Reactions with direct or inherited RXNO/MOP linkage | 1,449 |
| Molecules | 2,277 |

Important files:

| Path | Purpose |
| --- | --- |
| `data/polar.json` | Clean reaction records, IDs starting at 1 |
| `data/hierarchy.md` | Clean hierarchy consumed by the database builder |
| `data/epdb.sqlite` | Built SQLite database used by the app |
| `data/rxno_crosswalk.tsv` | Tabular SynEPD taxonomy to RXNO/MOP SKOS mappings |
| `docs/source/_static/rxno_crosswalk.ttl` | RDF/Turtle form of the SynEPD to RXNO/MOP crosswalk |
| `data/rxno.obo` | Pinned RXNO 2021-12-16 ontology snapshot used to build the linkage |
| `data/rxno_mapping_overrides.tsv` | Reviewed mappings layered over deterministic name matching |
| `data/taxonomy_redirects.tsv` | Traceable replacements for retired taxonomy codes |
| `data/release-manifest.json` | Current artifact checksums, semantic version, and counts |

## Environment

Create or update the Conda environment:

```bash
conda env create -f env.yaml
conda activate synepd
```

For an existing environment:

```bash
conda activate synepd
python -m pip install -r requirements.txt
```

The project metadata lives in `pyproject.toml`. Runtime dependencies are declared there, and developer tools are available through the `dev` extra:

```bash
python -m pip install -e ".[dev]"
```

## Build The Data


Build the SQLite chemistry database:

```bash
PYTHONPATH=. python -m synepd.construct.build_release_db
```

RXNO/MOP is an optional external linkage, not duplicated into SQLite. The web
API and typed repository resolve `data/rxno_crosswalk.tsv` on demand. Regenerate
and validate that artifact when curated overrides or retired-code redirects
change:

```bash
python scripts/build_rxno_mapping.py --obo data/rxno.obo
python scripts/build_rxno_mapping.py --check --obo data/rxno.obo
```

Accepted taxonomy mappings are returned from `/api/taxonomy`. Reaction-detail
responses derive their `ontology_xrefs` through every assigned taxon's parent
lineage, preserving the assigned node, mapping-owning node, inheritance depth,
relation, and pinned ontology-release provenance. The typed SQLite repository
exposes the same distinction through `get_taxon_xrefs(...)`,
`get_reaction_xrefs(...)`, and `get_ontology_releases()`.

Verify the checked-in artifact against its release manifest:

```bash
python -m synepd.construct.release_manifest data/epdb.sqlite \
  --verify data/release-manifest.json
```

## Run The Explorer

Use the hosted explorer at:

```text
https://synepd.bioinf.uni-leipzig.de
```

For local development, start the app with:

```bash
./run_server.sh
```

For a non-reloading multi-process deployment, use:

```bash
SYNEPD_THREAD_TOKENS=64 ./run_server.sh run --no-reload \
  --workers 4 --backlog 2048
```

The checked-in release SQLite artifact opens in immutable read-only mode;
custom SQLite inputs remain WAL-aware and read-only. Response compression is
enabled, and frequently requested reaction details are cached per worker. Put
a reverse proxy or CDN in front of the app for a public service;
for sustained write traffic or horizontal scaling, use the supported PostgreSQL
backend with connection pooling rather than sharing a writable SQLite file.

Open:

```text
http://127.0.0.1:8000/
```

Stable service routes are exposed under `/api/v1`; the original `/api`
routes remain compatibility aliases for v0.1 clients.

The explorer uses CDK Depict for 2D structures and automatically falls back to
the local RDKit endpoint when CDK is unavailable. Set
`window.SYNEPD_CDK_DEPICT_BASE` before loading `app.js` to use a self-hosted CDK
Depict service; the public default sends the displayed reaction SMILES to the
configured CDK service.

The editable TikZ source for the database relation figure is
`synepd/web/static/data_arch.tex`; its rendered SVG is used by the explorer and
the PNG is retained as a fallback.

By default the server reads:

```bash
SYNEPD_DATABASE_URL=data/epdb.sqlite
```

To use another database:

```bash
SYNEPD_DATABASE_URL=/path/to/other.sqlite ./run_server.sh
```

## Query Examples

Find reactions that share a reaction-center template:

```python
from pathlib import Path
from synepd.core import find_reactions_by_template

db_path = Path("data/epdb.sqlite")
template_smiles = "[H:2][NH3+:3].[O-:1][CH3:4]>>[NH3:3].[O:1]([H:2])[CH3:4]"

reactions = find_reactions_by_template(template_smiles, db_path=db_path)
print(f"Found {len(reactions)} matching reactions")
```

Query EPD arrows by reaction SMILES:

```python
from pathlib import Path
from synepd.core import query_epd_by_reaction

db_path = Path("data/epdb.sqlite")
rsmi = "CC[O-].[NH4+]>>CCO"

result = query_epd_by_reaction(rsmi, db_path=db_path)
print(result["success"])
print(result.get("path"))
for arrow in result.get("arrows", []):
    print(arrow["arrow_index"], arrow["arrow_type_code"], arrow["source_atoms"], "->", arrow["target_atoms"])
```

Query directly from a published release on Zenodo:

```python
from synepd.core import query_epd_by_reaction

rsmi = "CC[O-].[NH4+]>>CCO"
result = query_epd_by_reaction(
    rsmi,
    db_source="zenodo",
    db_version="0.1.0",  # latest configured Zenodo record until v0.2 is published
)
```

Use the matching GitHub Release asset instead (with a tag-archive fallback):

```python
from synepd.core import get_default_db_path

db_path = get_default_db_path(version="0.1.0", source="github")
```

For a portable client, prefer Zenodo and fall back to the matching GitHub
release automatically:

```python
result = query_epd_by_reaction(
    rsmi,
    db_source="auto",
    db_version="0.1.0",
)
```

## Checks

Useful focused checks:

```bash
python -m py_compile synepd/core/ingest.py synepd/construct/build_release_db.py synepd/web/server.py
python -m pytest -q test/construct/test_build_release_db.py test/database/test_database_models.py
python -m pip check
```

## Database Architecture

The current local release database stores the curated reactions,
chemistry-aware reaction-center templates, ordered EPD arrows, ITS graphs, and
one materialized mechanistic context per reaction in a normalized SQLite
database. Reaction aliases, taxonomy-linked entry codes, typed named-reaction
relations, and RXNO/MOP cross-references are stored in dedicated tables.
Mechanistic contexts
combine an ITS-derived anchor graph with ordered transition and transient-edge
events.

`data/polar.json` contains production build fields only. Narrative curation
notes are excluded from release artifacts; the two non-exact records keep only
the formal-charge overrides required for deterministic surrogate replay.

The core release schema is documented in the web explorer and kept in sync
with the database builder.

## Publishing Notes

The 0.1.0 release is archived on Zenodo at https://zenodo.org/records/21235892.
For future releases, add the new Zenodo record ID to `ZENODO_RECORD_IDS` in
`synepd/core/data.py`. The package itself can then be built and uploaded with:

```bash
python -m build
python -m twine upload dist/*
```

## License

The software is licensed under the Apache License 2.0. The curated data
release is distributed under CC BY 4.0 where stated in the release metadata.
See [LICENSE](LICENSE) for the software license text.

## Acknowledgments

This project has received funding from the European Union's Horizon Europe Doctoral Network programme under the Marie Skłodowska-Curie grant agreement No. 101072930 ([TACsy](https://tacsy.eu/) -- Training Alliance for Computational Systems Chemistry).
