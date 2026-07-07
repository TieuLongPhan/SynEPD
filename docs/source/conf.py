# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys

sys.path.insert(0, os.path.abspath("../../"))

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = "SynEPD"
copyright = "2026, SynEco Team"
author = "SynEco Team"
release = "1.1.0"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
]

templates_path = ["_templates"]
exclude_patterns = []


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "pydata_sphinx_theme"
html_logo = "../../synepd/web/static/synepd-logo.svg"
html_static_path = ["_static"]
html_css_files = [
    "custom.css",
]
html_js_files = [
    "custom.js",
]

html_theme_options = {
    "logo": {
        "text": "SynEPD Explorer",
        "image_light": "../../synepd/web/static/synepd-logo.svg",
        "image_dark": "../../synepd/web/static/synepd-logo.svg",
        "link": "https://synepd.bioinf.uni-leipzig.de",
    },
    "navbar_align": "left",
    "github_url": "https://github.com/TieuLongPhan/SynEPD",
    "show_prev_next": True,
    "search_bar_text": "Search docs...",
    "icon_links": [
        {
            "name": "PyPI",
            "url": "https://pypi.org/project/synepd/",
            "icon": "fab fa-python",
            "type": "fontawesome",
        }
    ],
}
