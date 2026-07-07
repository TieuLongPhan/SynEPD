Changelog
=========

All notable changes to SynEPD are documented here.

0.1.0 - 2026-07-07
------------------

Initial public release of SynEPD, a hierarchical electron-pushing database and
web explorer for polar organic reaction mechanisms.

Added
~~~~~

* Released the first SynEPD Python package metadata with Python 3.11+ support.
* Added the curated POLAR-derived SynEPD data release:

  * 1,901 cleaned source records.
  * 1,887 database reactions.
  * 1,501 reaction-center templates.
  * 7,095 electron-pushing arrows.
  * 1,112 taxonomy rows.
  * 2,125 molecules.

* Added a normalized SQLite release database containing reactions, molecules,
  taxonomy assignments, reaction-center templates, ITS graphs, and
  electron-pushing diagram arrows.
* Added database construction utilities for building the release database from
  cleaned reaction records and hierarchy data.
* Added validation and precheck utilities for atom-map consistency, reaction
  balance, and hydrogen-completion checks.
* Added high-level Python query helpers for finding reactions by
  reaction-center template, querying EPD arrows from reaction SMILES, loading
  JSON/JSONL case records, searching labels, and filtering cases.
* Added database manager APIs for reactions, molecules, taxonomy, EPD arrows,
  reaction centers, and ITS records.
* Added an interactive FastAPI web explorer with search, taxonomy browsing,
  reaction detail views, graph rendering, RDKit rendering, statistics,
  submissions, and balance-check endpoints.
* Added a knowledge-graph explorer API and frontend for molecule, reaction,
  template, and taxon navigation, ego-network expansion, SynRFP similarity,
  substructure search, and path finding.
* Added hosted service and documentation navigation:

  * Official web server: https://synepd.bioinf.uni-leipzig.de
  * Documentation: https://synepd.readthedocs.io/en/latest/

* Added Zenodo release archive metadata:

  * Zenodo record: https://zenodo.org/records/21235892
  * DOI: https://doi.org/10.5281/zenodo.21235892

* Added Sphinx documentation under ``docs/source``, including an API reference
  and querying guide.
* Added Read the Docs configuration via ``.readthedocs.yaml`` and
  ``docs/requirements.txt``.
* Added GitHub Actions workflows for lint/test CI and release-triggered PyPI
  publishing.

Changed
~~~~~~~

* Standardized project packaging around ``pyproject.toml``.
* Declared runtime dependencies for the web server, chemistry stack, graph
  handling, and fingerprinting: ``fastapi``, ``pydantic``, ``uvicorn``,
  ``networkx``, ``numpy``, ``rdkit``, ``synkit>=1.1.2``, and
  ``synrfp>=0.0.3``.
* Updated CI to run on Python 3.11 and 3.12.
* Updated GitHub Actions dependencies to current major versions:
  ``actions/checkout@v7`` and ``actions/setup-python@v6``.
* Updated the web explorer documentation button to point to Read the Docs.
* Updated documentation navigation links so the docs return to the official
  SynEPD web server.
* Updated the database downloader to resolve versioned releases from Zenodo or
  matching GitHub release tag archives.
* Added explicit ignore rules for generated Sphinx build output.

Fixed
~~~~~

* Fixed lint failures caused by module-level imports appearing after executable
  code in the FastAPI server module.
* Added a clean step to the publish workflow so generated ``build``, ``dist``,
  and egg-info directories cannot interfere with package builds.
* Updated the web-server docs test so CI no longer requires generated
  ``docs/build/html`` output.
* Verified Sphinx documentation builds successfully from ``docs/source``.
* Verified local package distributions build and pass ``twine check``.

Known notes
~~~~~~~~~~~

* The package version is ``0.1.0``; future release tags should match the
  package version in ``pyproject.toml``.
* PyPI publishing is configured for published GitHub releases only.
* PyPI trusted publishing must be configured for the GitHub repository and the
  ``pypi`` environment before the release workflow can upload packages.
* The ``0.1.0`` database can be resolved from the Zenodo record or the matching
  GitHub tag archive; future releases should add their record IDs to
  ``synepd.core.data.ZENODO_RECORD_IDS``.
* Setuptools currently emits a deprecation warning for the TOML table form of
  ``project.license``; this does not block ``0.1.0`` but should be modernized
  in a future release.
