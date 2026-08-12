#!/usr/bin/env python3
"""Render the complete POLAR taxonomy as interactive HTML and an SVG overview.

The HTML output contains every node from ``data/hierarchy.md`` and supports
search, expansion, and collapse without external JavaScript dependencies. The
SVG/PNG overview shows all tier-1 families and all tier-2 subclasses, with leaf
counts, while leaving individual leaf labels to the interactive tree.

Usage::

    python scripts/render_taxonomy.py
    python scripts/render_taxonomy.py --hierarchy data/hierarchy.md --output-dir .
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape as xml_escape


HEADING_RE = re.compile(
    r"^#{1,4}\s+"
    r"(POLAR(?:\.\d{2})?(?:\.\d{2})?(?:\.\d{3})?)"
    r"\s+[—–-]\s+(.+?)\s*$"
)

FAMILY_COLORS = {
    "01": "#2E7D32",
    "02": "#1565C0",
    "03": "#00838F",
    "04": "#6A1B9A",
    "05": "#EF6C00",
    "06": "#C62828",
    "07": "#5D4037",
    "08": "#455A64",
}


@dataclass
class Taxon:
    code: str
    name: str
    children: list["Taxon"] = field(default_factory=list)

    @property
    def depth(self) -> int:
        return self.code.count(".")

    @property
    def family(self) -> str | None:
        parts = self.code.split(".")
        return parts[1] if len(parts) > 1 else None

    @property
    def leaf_count(self) -> int:
        if not self.children:
            return 1 if self.depth == 3 else 0
        return sum(child.leaf_count for child in self.children)

    @property
    def descendant_text(self) -> str:
        values = [self.code, self.name]
        for child in self.children:
            values.append(child.descendant_text)
        return " ".join(values)


def parse_hierarchy(path: Path) -> Taxon:
    nodes: dict[str, Taxon] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        match = HEADING_RE.match(raw_line.strip())
        if match:
            code, name = match.groups()
            nodes[code] = Taxon(code=code, name=name.strip())

    if "POLAR" not in nodes:
        raise ValueError(f"No POLAR root found in {path}")

    for code in sorted(nodes, key=lambda value: (value.count("."), value)):
        if code == "POLAR":
            continue
        parent_code = code.rsplit(".", 1)[0]
        try:
            nodes[parent_code].children.append(nodes[code])
        except KeyError as exc:
            raise ValueError(f"Missing parent {parent_code} for {code}") from exc

    for node in nodes.values():
        node.children.sort(key=lambda child: child.code)
    return nodes["POLAR"]


def flatten(root: Taxon) -> list[Taxon]:
    result: list[Taxon] = []

    def visit(node: Taxon) -> None:
        result.append(node)
        for child in node.children:
            visit(child)

    visit(root)
    return result


def family_stats(root: Taxon) -> list[dict[str, object]]:
    stats = []
    for family in root.children:
        stats.append(
            {
                "node": family,
                "subclasses": len(family.children),
                "leaves": sum(len(child.children) for child in family.children),
            }
        )
    return stats


def node_id(code: str) -> str:
    return code.replace(".", "-")


def render_tree_node(node: Taxon) -> str:
    family = node.family or "root"
    color = FAMILY_COLORS.get(family, "#176B3A")
    search_text = html.escape(node.descendant_text.lower(), quote=True)
    code = html.escape(node.code)
    name = html.escape(node.name)
    item_id = node_id(node.code)

    if not node.children:
        return (
            f'<li class="tree-item leaf" data-search="{search_text}" '
            f'style="--family:{color}">'
            f'<div class="leaf-row" id="{item_id}">'
            f'<span class="code">{code}</span><span class="name">{name}</span>'
            "</div></li>"
        )

    if node.depth == 0:
        family_count = len(node.children)
        subclass_count = sum(len(family.children) for family in node.children)
        count_text = (
            f"{family_count} families · {subclass_count} subclasses · "
            f"{node.leaf_count} leaves"
        )
        open_attr = " open"
    elif node.depth == 1:
        count_text = f"{len(node.children)} subclasses · {node.leaf_count} leaves"
        open_attr = ""
    else:
        count_text = f"{len(node.children)} leaves"
        open_attr = ""

    children = "".join(render_tree_node(child) for child in node.children)
    return (
        f'<li class="tree-item branch depth-{node.depth}" '
        f'data-search="{search_text}" style="--family:{color}">'
        f'<details id="{item_id}"{open_attr}><summary>'
        f'<span class="code">{code}</span><span class="name">{name}</span>'
        f'<span class="count">{count_text}</span></summary>'
        f'<ul>{children}</ul></details></li>'
    )


def render_html(root: Taxon, hierarchy_path: Path) -> str:
    nodes = flatten(root)
    counts = {depth: sum(node.depth == depth for node in nodes) for depth in range(4)}
    cards = []
    for item in family_stats(root):
        family = item["node"]
        assert isinstance(family, Taxon)
        color = FAMILY_COLORS[family.family or "01"]
        cards.append(
            f'<a class="family-card" href="#{node_id(family.code)}" '
            f'style="--family:{color}">'
            f'<span class="family-code">{html.escape(family.code)}</span>'
            f'<strong>{html.escape(family.name)}</strong>'
            f'<small>{item["subclasses"]} subclasses · {item["leaves"]} leaves</small>'
            "</a>"
        )

    tree = render_tree_node(root)
    source = html.escape(str(hierarchy_path))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SynEPD POLAR taxonomy</title>
  <style>
    :root {{ color-scheme: light; --ink:#17211b; --muted:#607066; --line:#d9e4dc; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font:15px/1.45 Inter,ui-sans-serif,system-ui,-apple-system,
      BlinkMacSystemFont,"Segoe UI",sans-serif; color:var(--ink); background:#f5f8f6; }}
    header {{ padding:30px clamp(20px,5vw,72px) 22px; color:white;
      background:linear-gradient(125deg,#0d4f2d,#197044 55%,#238354); }}
    header h1 {{ margin:0 0 7px; font-size:clamp(25px,4vw,42px); letter-spacing:-.025em; }}
    header p {{ max-width:900px; margin:0; color:#dcefe3; }}
    .stats {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:20px; }}
    .stat {{ padding:8px 12px; border:1px solid #ffffff38; border-radius:999px;
      background:#ffffff12; font-variant-numeric:tabular-nums; }}
    main {{ width:min(1500px,94vw); margin:24px auto 50px; }}
    .family-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
      gap:11px; margin-bottom:22px; }}
    .family-card {{ display:flex; min-height:112px; flex-direction:column; gap:4px;
      padding:14px 15px; border:1px solid color-mix(in srgb,var(--family),white 64%);
      border-left:5px solid var(--family); border-radius:10px; color:var(--ink);
      background:white; text-decoration:none; box-shadow:0 4px 14px #12351d0a; }}
    .family-card:hover {{ transform:translateY(-1px); box-shadow:0 8px 22px #12351d16; }}
    .family-code,.code {{ color:var(--family); font:600 12px/1.3 ui-monospace,SFMono-Regular,
      Menlo,Consolas,monospace; }}
    .family-card strong {{ font-size:14px; }}
    .family-card small,.count {{ color:var(--muted); }}
    .toolbar {{ position:sticky; top:0; z-index:5; display:flex; flex-wrap:wrap; gap:9px;
      align-items:center; padding:12px; margin-bottom:12px; border:1px solid var(--line);
      border-radius:11px; background:#ffffffed; backdrop-filter:blur(10px); }}
    #search {{ flex:1 1 320px; min-width:180px; padding:10px 12px; border:1px solid #baccc0;
      border-radius:8px; font:inherit; outline:none; }}
    #search:focus {{ border-color:#197044; box-shadow:0 0 0 3px #19704420; }}
    button {{ padding:9px 12px; border:1px solid #b7c8bc; border-radius:8px;
      background:white; color:#23442f; cursor:pointer; font:600 13px/1 inherit; }}
    button:hover {{ background:#edf5f0; }}
    #result-count {{ color:var(--muted); font-size:13px; min-width:110px; text-align:right; }}
    .tree-shell {{ padding:10px 12px 18px; border:1px solid var(--line); border-radius:12px;
      background:white; box-shadow:0 5px 20px #12351d0a; }}
    ul {{ list-style:none; margin:0; padding-left:20px; }}
    .tree-shell > ul {{ padding-left:0; }}
    .tree-item {{ position:relative; }}
    .tree-item[hidden] {{ display:none; }}
    .tree-item ul {{ border-left:1px solid color-mix(in srgb,var(--family),white 70%); }}
    summary,.leaf-row {{ display:grid; grid-template-columns:minmax(150px,210px) 1fr auto;
      align-items:baseline; gap:12px; padding:7px 9px; margin:2px 0; border-radius:7px; }}
    summary {{ cursor:pointer; font-weight:600; }}
    summary:hover,.leaf-row:hover {{ background:color-mix(in srgb,var(--family),white 94%); }}
    summary::marker {{ color:var(--family); }}
    .leaf-row {{ grid-template-columns:minmax(150px,210px) 1fr; font-size:14px; }}
    .depth-0 > details > summary {{ padding:11px 12px; font-size:17px; background:#eef7f1; }}
    .depth-1 > details > summary {{ border-left:4px solid var(--family); }}
    mark {{ padding:0 2px; border-radius:2px; background:#fff2a8; }}
    footer {{ margin-top:14px; color:var(--muted); font-size:13px; }}
    body.embedded header,body.embedded .family-grid,body.embedded footer {{ display:none; }}
    body.embedded main {{ width:100%; margin:0; padding:10px; }}
    body.embedded .toolbar {{ top:0; }}
    @media (max-width:700px) {{
      ul {{ padding-left:11px; }}
      summary,.leaf-row {{ grid-template-columns:1fr; gap:2px; }}
      .count {{ display:block; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>SynEPD POLAR taxonomy</h1>
    <p>Complete interactive hierarchy of polar reaction labels. Search by code,
      mechanism, substrate class, or named reaction.</p>
    <div class="stats">
      <span class="stat">{counts[0]} root</span>
      <span class="stat">{counts[1]} families</span>
      <span class="stat">{counts[2]} subclasses</span>
      <span class="stat">{counts[3]} leaves</span>
      <span class="stat">{len(nodes)} nodes total</span>
    </div>
  </header>
  <main>
    <section class="family-grid" aria-label="Top-level families">
      {''.join(cards)}
    </section>
    <div class="toolbar">
      <input id="search" type="search" placeholder="Search e.g. SN2, oxidation, POLAR.06.03…"
        autocomplete="off" aria-label="Search taxonomy">
      <button id="expand" type="button">Expand all</button>
      <button id="collapse" type="button">Collapse all</button>
      <button id="reset" type="button">Reset</button>
      <span id="result-count">{len(nodes)} nodes</span>
    </div>
    <section class="tree-shell" aria-label="Complete taxonomy tree">
      <ul>{tree}</ul>
    </section>
    <footer>Generated from <code>{source}</code>. The Markdown hierarchy remains the canonical source.</footer>
  </main>
  <script>
    if (window.self !== window.top) document.body.classList.add('embedded');
    const items = [...document.querySelectorAll('.tree-item')];
    const details = [...document.querySelectorAll('details')];
    const search = document.getElementById('search');
    const result = document.getElementById('result-count');

    function filterTree() {{
      const q = search.value.trim().toLowerCase();
      let visible = 0;
      for (const item of items) {{
        const show = !q || item.dataset.search.includes(q);
        item.hidden = !show;
        if (show) visible += 1;
      }}
      if (q) {{
        for (const detail of details) {{
          const item = detail.closest('.tree-item');
          if (item && !item.hidden) detail.open = true;
        }}
      }}
      result.textContent = `${{visible}} node${{visible === 1 ? '' : 's'}}`;
    }}

    search.addEventListener('input', filterTree);
    document.getElementById('expand').addEventListener('click', () => details.forEach(d => d.open = true));
    document.getElementById('collapse').addEventListener('click', () => {{
      details.forEach(d => d.open = false);
      const root = document.getElementById('POLAR');
      if (root) root.open = true;
    }});
    document.getElementById('reset').addEventListener('click', () => {{
      search.value = '';
      items.forEach(item => item.hidden = false);
      details.forEach(d => d.open = false);
      const root = document.getElementById('POLAR');
      if (root) root.open = true;
      result.textContent = '{len(nodes)} nodes';
      search.focus();
    }});
    document.querySelectorAll('.family-card').forEach(card => card.addEventListener('click', () => {{
      const target = document.querySelector(card.getAttribute('href'));
      if (target && target.tagName === 'DETAILS') target.open = true;
    }}));
  </script>
</body>
</html>
"""


def wrap_label(text: str, width: int = 39, max_lines: int = 2) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and len(candidate) > width:
            lines.append(" ".join(current))
            current = [word]
            if len(lines) == max_lines - 1:
                break
        else:
            current.append(word)
    if current and len(lines) < max_lines:
        consumed = " ".join(lines + [" ".join(current)])
        final = " ".join(current)
        if len(consumed) < len(text) and not final.endswith("…"):
            final = final.rstrip(" ,;:") + "…"
        lines.append(final)
    return lines


def render_svg(root: Taxon) -> str:
    family_count = len(root.children)
    subclass_count = sum(len(family.children) for family in root.children)
    leaf_count = root.leaf_count
    width, height = 2360, 1470
    card_width, card_height = 545, 590
    xs = [40, 605, 1170, 1735]
    ys = [235, 855]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        "<defs><filter id=\"shadow\" x=\"-10%\" y=\"-10%\" width=\"120%\" height=\"130%\">"
        "<feDropShadow dx=\"0\" dy=\"5\" stdDeviation=\"8\" flood-color=\"#173d25\" flood-opacity=\"0.10\"/>"
        "</filter></defs>",
        f'<rect width="{width}" height="{height}" fill="#f6f9f7"/>',
        '<rect x="825" y="25" width="710" height="105" rx="18" fill="#e0f0e5" '
        'stroke="#176b3a" stroke-width="3" filter="url(#shadow)"/>',
        '<text x="1180" y="69" text-anchor="middle" font-family="monospace" font-size="30" '
        'font-weight="700" fill="#176b3a">POLAR</text>',
        '<text x="1180" y="104" text-anchor="middle" font-family="sans-serif" font-size="22" '
        f'fill="#315c40">{family_count} families · {subclass_count} subclasses · '
        f'{leaf_count} leaves</text>',
        '<path d="M1180 130 V173" stroke="#176b3a" stroke-width="4"/>',
        '<path d="M1168 164 L1180 180 L1192 164" fill="#176b3a"/>',
        '<text x="1180" y="210" text-anchor="middle" font-family="sans-serif" font-size="20" '
        'font-weight="650" fill="#315c40">Tier 1 families, each containing tier 2 subclasses</text>',
    ]

    for index, item in enumerate(family_stats(root)):
        family = item["node"]
        assert isinstance(family, Taxon)
        x, y = xs[index % 4], ys[index // 4]
        color = FAMILY_COLORS[family.family or "01"]
        parts.extend(
            [
                f'<rect x="{x}" y="{y}" width="{card_width}" height="{card_height}" rx="15" '
                f'fill="#ffffff" stroke="#d7e3da" stroke-width="2" filter="url(#shadow)"/>',
                f'<rect x="{x}" y="{y}" width="10" height="{card_height}" rx="5" fill="{color}"/>',
                f'<text x="{x + 30}" y="{y + 42}" font-family="monospace" font-size="24" '
                f'font-weight="700" fill="{color}">{xml_escape(family.code)}</text>',
            ]
        )
        title_lines = wrap_label(family.name, width=42, max_lines=2)
        for line_index, line in enumerate(title_lines):
            parts.append(
                f'<text x="{x + 30}" y="{y + 78 + line_index * 25}" font-family="sans-serif" '
                f'font-size="21" font-weight="650" fill="#17211b">{xml_escape(line)}</text>'
            )
        count_y = y + 111 + (len(title_lines) - 1) * 25
        parts.extend(
            [
                f'<text x="{x + 30}" y="{count_y}" font-family="sans-serif" font-size="17" '
                f'fill="#607066">{item["subclasses"]} subclasses · {item["leaves"]} leaves</text>',
                f'<line x1="{x + 28}" y1="{count_y + 18}" x2="{x + card_width - 24}" '
                f'y2="{count_y + 18}" stroke="#e2eae4" stroke-width="2"/>',
                f'<text x="{x + 30}" y="{count_y + 49}" font-family="sans-serif" font-size="16" '
                f'font-weight="700" fill="{color}">Tier 2 subclasses</text>',
            ]
        )
        start_y = count_y + 80
        for child_index, child in enumerate(family.children):
            label = child.name
            if len(label) > 31:
                label = label[:29].rstrip() + "…"
            line_y = start_y + child_index * 34
            parts.extend(
                [
                    f'<text x="{x + 30}" y="{line_y}" font-family="monospace" font-size="15" '
                    f'font-weight="650" fill="{color}">{xml_escape(child.code)}</text>',
                    f'<text x="{x + 145}" y="{line_y}" font-family="sans-serif" font-size="14" '
                    f'fill="#28352d">{xml_escape(label)}</text>',
                    f'<text x="{x + card_width - 28}" y="{line_y}" text-anchor="end" '
                    f'font-family="sans-serif" font-size="14" fill="#607066">'
                    f'{len(child.children)} leaves</text>',
                ]
            )

    parts.append("</svg>")
    return "".join(parts)


def render_png(svg_path: Path, png_path: Path) -> bool:
    renderer = shutil.which("rsvg-convert")
    if not renderer:
        return False
    subprocess.run(
        [renderer, "-w", "2360", "-o", str(png_path), str(svg_path)],
        check=True,
    )
    return True


def validate(root: Taxon) -> None:
    nodes = flatten(root)
    counts = {depth: sum(node.depth == depth for node in nodes) for depth in range(4)}
    if counts[0] != 1 or counts[1] != 8 or counts[2] == 0 or counts[3] == 0:
        raise ValueError(f"Unexpected hierarchy shape: {counts}")


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hierarchy", type=Path, default=Path("data/hierarchy.md"))
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    parser.add_argument("--no-png", action="store_true", help="Do not render the PNG preview")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    root = parse_hierarchy(args.hierarchy)
    validate(root)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    html_path = args.output_dir / "taxonomy.html"
    svg_path = args.output_dir / "taxonomy_overview.svg"
    png_path = args.output_dir / "taxonomy_overview.png"

    html_path.write_text(render_html(root, args.hierarchy), encoding="utf-8")
    svg_path.write_text(render_svg(root), encoding="utf-8")
    png_written = False if args.no_png else render_png(svg_path, png_path)

    nodes = flatten(root)
    print(f"Rendered {len(nodes)} taxonomy nodes")
    print(f"HTML: {html_path}")
    print(f"SVG:  {svg_path}")
    if png_written:
        print(f"PNG:  {png_path}")
    elif not args.no_png:
        print("PNG not rendered: rsvg-convert is unavailable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
