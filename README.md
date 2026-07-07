# SynEPD

SynEPD is a hierarchical electron-pushing database for polar organic reaction mechanisms. It combines clean reaction records, a POLAR taxonomy, reaction-center templates, atom-mapped reaction graphs, and electron-pushing diagram (EPD) arrows in a local SQLite database with a web explorer.

Official web server: https://synepd.bioinf.uni-leipzig.de

Zenodo release: https://zenodo.org/records/21235892

<p align="center">
  <img
    src="data/synepd.gif"
    alt="SynEPD Explorer preview showing the search, taxonomy, statistics, and reaction graph interface"
    width="1200"
  />
</p>

## Current Data

The current local build uses the cleaned POLAR dataset:

| Item | Count |
| --- | ---: |
| Clean records | 1,901 |
| Database reactions | 1,887 |
| RC templates | 1,501 |
| EPD arrows | 7,095 |
| Taxon rows | 1,112 |
| Molecules | 2,125 |

Important files:

| Path | Purpose |
| --- | --- |
| `data/polar.json` | Clean reaction records, IDs starting at 1 |
| `data/hierarchy.md` | Clean hierarchy consumed by the database builder 
| `data/epdb.sqlite` | Built SQLite database used by the app |

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


Build the SQLite database:

```bash
PYTHONPATH=. python synepd/construct/build_release_db.py
```

The builder writes `data/epdb.sqlite`.

## Run The Explorer

Use the hosted explorer at:

```text
https://synepd.bioinf.uni-leipzig.de
```

For local development, start the app with:

```bash
./run_server.sh
```

Open:

```text
http://127.0.0.1:8000/
```

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

Query directly from the published 0.1.0 release on Zenodo:

```python
from synepd.core import query_epd_by_reaction

rsmi = "CC[O-].[NH4+]>>CCO"
result = query_epd_by_reaction(
    rsmi,
    db_source="zenodo",
    db_version="0.1.0",
)
```

Use the matching GitHub release archive instead:

```python
from synepd.core import get_default_db_path

db_path = get_default_db_path(version="0.1.0", source="github")
```

## Checks

Useful focused checks:

```bash
python -m py_compile synepd/core/ingest.py synepd/construct/build_release_db.py synepd/web/server.py
python -m pytest -q test/construct/test_build_release_db.py test/database/test_database_models.py
python -m pip check
```

## Database Architecture

SynEPD stores reaction metadata, molecules, taxonomy assignments, reaction-center templates, ITS graphs, and EPD arrows in a normalized SQLite database.

![Database Architecture Schema](synepd/web/static/data_arch.png)

## Publishing Notes

The 0.1.0 release is archived on Zenodo at https://zenodo.org/records/21235892.
For future releases, add the new Zenodo record ID to `ZENODO_RECORD_IDS` in
`synepd/core/data.py`. The package itself can then be built and uploaded with:

```bash
python -m build
python -m twine upload dist/*
```

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).

## Acknowledgments

This project has received funding from the European Union's Horizon Europe Doctoral Network programme under the Marie Skłodowska-Curie grant agreement No. 101072930 ([TACsy](https://tacsy.eu/) -- Training Alliance for Computational Systems Chemistry).
