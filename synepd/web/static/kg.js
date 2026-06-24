// ===========================================================================
// SynEPD Knowledge Graph (kg.js)
// ---------------------------------------------------------------------------
// A directed, multi-relational explorer that renders into #kg-viewport.
// Kept fully isolated from the ITS renderer in graph.js: all globals here are
// prefixed `kg` so there is zero collision with `svg`, `simulation`, etc.
//
// Flow:  search seed  ->  render ego graph  ->  click node to expand (merge)
//        ->  double-click / "Open" a reaction to hand off to loadReaction().
// ===========================================================================

const KG_API = () => (window.SYNEPD_API_BASE || window.location.origin);

// live graph state (string-keyed maps for O(1) de-dup on uid)
const kgNodes = new Map();   // uid -> node object
const kgLinks = new Map();   // "src->tgt:rel" -> link object
let kgSim = null;
let kgSvg = null;
let kgRoot = null;            // <g> container that pans/zooms
let kgZoom = null;
let kgSelectedUid = null;
const kgUndoStack = [];   // [{nodeUids: Set, linkKeys: Set}]
let kgLastInspectedRsmi = '';    // set in kgShowReactionInfo; used by info-card buttons
let kgLastInspectedWlhash = '';  // same

const KG_NODE_STYLE = {
    // CRN view: a reaction IS its template, shown as an orange diamond.
    molecule: { color: 'var(--accent-cyan, #06b6d4)',  r: 16, glyph: '⬡' },
    reaction: { color: 'var(--accent-orange, #f59e0b)', r: 14, glyph: '◆' },
    template: { color: 'var(--accent-orange, #f59e0b)', r: 14, glyph: '◆' },
    taxon:    { color: 'var(--accent-green, #10b981)',  r: 12, glyph: '#' },
};

// Ubiquitous reagents / byproducts that clutter a CRN. Hidden when the
// "Hide common reagents" toggle is on (client-side, by canonical SMILES).
const KG_REAGENT_DENYLIST = new Set([
    'O', '[H+]', '[OH-]', 'O=C=O', '[Cl-]', 'Cl', 'Br', '[Br-]', 'I', '[I-]',
    '[Na+]', '[K+]', '[Li+]', 'N', 'O=O', '[H][H]', 'C(=O)=O'
]);
let kgHideReagents = false;
let kgTemplateView = false;
let kgTaxonFilter = '';   // level-2 POLAR prefix, e.g. 'POLAR.01', or '' = no filter

const KG_REL_STYLE = {
    reactant: { color: 'var(--accent-cyan, #06b6d4)',  label: 'reactant' },
    product:  { color: 'var(--accent-green, #10b981)', label: 'product'  },
    template: { color: 'var(--accent-orange, #f59e0b)', label: 'template' },
    class:    { color: 'var(--text-secondary, #94a3b8)', label: 'class'   },
};

// --------------------------------------------------------------------------- //
// Mode switching (show KG container, hide the ITS viewport)
// --------------------------------------------------------------------------- //
function kgEnterMode() {
    const gv = document.getElementById('graph-viewport');
    const gc = document.getElementById('graph-controls-panel');
    const kv = document.getElementById('kg-viewport');
    if (gv) gv.style.display = 'none';
    if (gc) gc.style.display = 'none';
    if (kv) kv.style.display = 'flex';
}

function kgExitMode() {
    const kv = document.getElementById('kg-viewport');
    const gv = document.getElementById('graph-viewport');
    if (kv) kv.style.display = 'none';
    if (gv) gv.style.display = '';
}

// --------------------------------------------------------------------------- //
// Search for a seed node
// --------------------------------------------------------------------------- //
function kgRenderSeedResults(nodes, { scoreKey = null, headerHtml = '' } = {}) {
    const listEl = document.getElementById('kg-seed-results');
    if (!listEl) return;
    listEl.innerHTML = headerHtml;
    nodes.forEach(node => {
        const card = document.createElement('div');
        card.className = 'kg-seed-card';
        card.tabIndex = 0;
        let sub = node.type === 'molecule'
            ? `${node.inchikey || ''} · ${node.reaction_count ?? '?'} reactions`
            : (node.case_id || node.type);
        if (scoreKey && node[scoreKey] != null) {
            sub = `${Math.round(node[scoreKey] * 100)}% similar · ${sub}`;
        }
        card.innerHTML = `
            <span class="kg-seed-type kg-type-${node.type}">${node.type}</span>
            <span class="kg-seed-label" title="${escapeHtml(node.smiles || node.label)}">${escapeHtml(node.label)}</span>
            <span class="kg-seed-sub">${escapeHtml(sub)}</span>`;
        const go = () => kgSeedFromNode(node);
        card.onclick = go;
        card.addEventListener('keydown', e => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); }
        });
        listEl.appendChild(card);
    });
}

async function kgSearchSeed() {
    const mode = document.getElementById('kg-search-mode')?.value || 'text';
    const input = document.getElementById('kg-search-input');
    const listEl = document.getElementById('kg-seed-results');
    const q = (input?.value || '').trim();
    if (!q) return;

    if (mode === 'rsmi')  { await kgFetchSimilarReactions(q, { fromSearch: true }); return; }
    if (mode === 'smarts'){ await kgFetchBySmarts(q); return; }
    if (mode === 'route') { await kgFindRoute(); return; }

    listEl.innerHTML = '<p class="kg-hint">Searching…</p>';
    try {
        const res = await fetch(`${KG_API()}/api/kg/search?q=${encodeURIComponent(q)}&limit=15`);
        const data = await res.json();
        if (!data.results || !data.results.length) {
            listEl.innerHTML = '<p class="kg-hint">No molecules or reactions matched.</p>';
            return;
        }
        listEl.innerHTML = '';
        kgRenderSeedResults(data.results);
    } catch (e) {
        listEl.innerHTML = '<p class="kg-hint">Search failed. Is the API reachable?</p>';
    }
}

// ---- Route / path finding ----
let kgPathUids = new Set();   // UIDs of on-path nodes + link keys currently highlighted

async function kgFindRoute() {
    const start  = (document.getElementById('kg-search-input')?.value  || '').trim();
    const end    = (document.getElementById('kg-route-target')?.value  || '').trim();
    const mode   =  document.getElementById('kg-route-mode')?.value    || 'both';
    const depth  =  document.getElementById('kg-route-depth')?.value   || '8';
    const listEl =  document.getElementById('kg-seed-results');

    if (!start || !end) {
        kgSetStatus('Enter both start and target molecules.');
        return;
    }

    kgSetStatus('Finding route…');
    if (listEl) listEl.innerHTML = '<p class="kg-hint">Searching for shortest path…</p>';

    try {
        const url = `${KG_API()}/api/kg/path`
            + `?start=${encodeURIComponent(start)}`
            + `&end=${encodeURIComponent(end)}`
            + `&mode=${mode}&max_depth=${depth}`;
        const res = await fetch(url);
        const data = await res.json();

        if (!res.ok) {
            kgSetStatus(`Error: ${data.detail || res.statusText}`);
            if (listEl) listEl.innerHTML = `<p class="kg-hint">Error: ${escapeHtml(data.detail || res.statusText)}</p>`;
            return;
        }

        kgPathUids = new Set(data.path_uids || []);

        if (!data.found) {
            kgSetStatus(data.message || 'No path found.');
            if (listEl) listEl.innerHTML = `<p class="kg-hint">${escapeHtml(data.message || 'No path found.')}</p>`;
            kgNodes.clear(); kgLinks.clear();
            kgMerge(data);
            kgRender(data.nodes[0]?.id || null);
            return;
        }

        kgNodes.clear(); kgLinks.clear();
        kgMerge(data);
        kgRender(data.nodes[0]?.id || null);

        const hops = data.hops;
        kgSetStatus(`Route found: ${hops} reaction step${hops === 1 ? '' : 's'}.`);
        if (listEl) listEl.innerHTML = `<p class="kg-hint" style="text-align:left; color:var(--accent-green);">✓ ${hops}-step route found — highlighted in graph.</p>`;
    } catch (e) {
        kgSetStatus('Route search failed.');
        if (listEl) listEl.innerHTML = `<p class="kg-hint">Error: ${escapeHtml(String(e))}</p>`;
    }
}

function kgOnSearchModeChange() {
    const mode = document.getElementById('kg-search-mode')?.value || 'text';
    const input = document.getElementById('kg-search-input');
    if (!input) return;
    const placeholders = {
        text:   'Start molecule: SMILES or InChIKey',
        rsmi:   'e.g. CCO.N>>CC(N)=O.O',
        smarts: 'e.g. [C:1](=O)[Cl] or [NH2]',
        route:  'Start molecule: SMILES or InChIKey',
    };
    input.placeholder = placeholders[mode] || 'e.g. CC(=O)Cl or a case id';
    const extras = document.getElementById('kg-route-extras');
    const btn = document.getElementById('kg-search-btn');
    if (extras) extras.style.display = mode === 'route' ? 'flex' : 'none';
    if (btn) btn.textContent = mode === 'route' ? 'Find Route' : 'Find';
}

async function kgFetchSimilarReactions(rsmi, { fromSearch = false, topK = 15 } = {}) {
    const listEl = document.getElementById('kg-seed-results');
    if (listEl) listEl.innerHTML = '<p class="kg-hint">Computing fingerprints…</p>';
    if (fromSearch) kgCloseInfo();
    try {
        const res = await fetch(
            `${KG_API()}/api/kg/similar-reactions?rsmi=${encodeURIComponent(rsmi)}&top_k=${topK}`
        );
        if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
        const data = await res.json();
        if (!data.results?.length) {
            if (listEl) listEl.innerHTML = '<p class="kg-hint">No similar reactions found.</p>';
            return;
        }
        const header = `<p class="kg-hint" style="text-align:left;margin-bottom:.3rem;">Top ${data.results.length} by Tanimoto similarity — click to seed graph</p>`;
        kgRenderSeedResults(data.results, { scoreKey: 'similarity', headerHtml: header });
    } catch (e) {
        if (listEl) listEl.innerHTML = `<p class="kg-hint">Error: ${escapeHtml(String(e))}</p>`;
    }
}

async function kgFetchBySmarts(smarts) {
    const listEl = document.getElementById('kg-seed-results');
    if (listEl) listEl.innerHTML = '<p class="kg-hint">Searching substructure…</p>';
    try {
        const res = await fetch(
            `${KG_API()}/api/kg/substructure-search?smarts=${encodeURIComponent(smarts)}&max_hits=50`
        );
        if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
        const data = await res.json();
        if (!data.results?.length) {
            if (listEl) listEl.innerHTML = '<p class="kg-hint">No molecules matched the SMARTS pattern.</p>';
            return;
        }
        const truncNote = data.truncated ? ' (first 50 shown)' : '';
        const header = `<p class="kg-hint" style="text-align:left;margin-bottom:.3rem;">${data.total} molecules match${truncNote} — click to seed</p>`;
        kgRenderSeedResults(data.results, { headerHtml: header });
    } catch (e) {
        if (listEl) listEl.innerHTML = `<p class="kg-hint">Error: ${escapeHtml(String(e))}</p>`;
    }
}

async function kgFetchByWlhash(wlhash) {
    const listEl = document.getElementById('kg-seed-results');
    if (listEl) listEl.innerHTML = '<p class="kg-hint">Searching…</p>';
    kgCloseInfo();
    try {
        const res = await fetch(
            `${KG_API()}/api/kg/reactions-by-wlhash?wlhash=${encodeURIComponent(wlhash)}&max_reactions=30`
        );
        if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
        const data = await res.json();
        if (!data.results?.length) {
            if (listEl) listEl.innerHTML = '<p class="kg-hint">No reactions with this mechanism found.</p>';
            return;
        }
        const truncNote = data.truncated ? ` (first 30 shown)` : '';
        const header = `<p class="kg-hint" style="text-align:left;margin-bottom:.3rem;">${data.total} reactions with identical mechanism${truncNote} — click to seed</p>`;
        kgRenderSeedResults(data.results, { headerHtml: header });
    } catch (e) {
        if (listEl) listEl.innerHTML = `<p class="kg-hint">Error: ${escapeHtml(String(e))}</p>`;
    }
}

function kgSeedFromNode(node) {
    kgNodes.clear();
    kgLinks.clear();
    kgSelectedUid = null;
    kgUndoStack.length = 0;
    kgEnterMode();
    kgExpandNode(node.type, node.ref_id, { center: true, seed: true });
}

// --------------------------------------------------------------------------- //
// Fetch + merge an ego subgraph
// --------------------------------------------------------------------------- //
function kgOptions() {
    const depthSel = document.getElementById('kg-maxrxn');
    const taxToggle = document.getElementById('kg-show-taxonomy');
    return {
        maxReactions: depthSel ? parseInt(depthSel.value) || 20 : 20,
        includeTaxonomy: taxToggle ? taxToggle.checked : false,
    };
}

async function kgExpandNode(type, refId, { center = false, seed = false } = {}) {
    const { maxReactions, includeTaxonomy } = kgOptions();
    const url = `${KG_API()}/api/kg/expand?type=${encodeURIComponent(type)}`
        + `&id=${encodeURIComponent(refId)}`
        + `&max_reactions=${maxReactions}`
        + `&include_taxonomy=${includeTaxonomy}`;
    kgSetStatus('Expanding…');
    try {
        const res = await fetch(url);
        if (!res.ok) { kgSetStatus('Could not expand node.'); return; }
        const data = await res.json();
        kgMerge(data);
        kgRender(center ? data.root : null);
        if (data.truncated) {
            const el = document.getElementById('kg-status');
            if (el) el.textContent += ` · truncated (${data.total_reactions} total — raise limit)`;
        }
        if (seed) kgEncodeState();
    } catch (e) {
        kgSetStatus('Expansion failed.');
    }
}

function kgMerge(data) {
    const addedNodes = new Set();
    const addedLinks = new Set();
    (data.nodes || []).forEach(n => {
        const existing = kgNodes.get(n.id);
        if (existing) {
            Object.keys(n).forEach(k => {
                if (existing[k] === undefined || existing[k] === null) existing[k] = n[k];
            });
        } else {
            addedNodes.add(n.id);
            kgNodes.set(n.id, { ...n });
        }
    });
    (data.links || []).forEach(l => {
        const key = `${l.source}->${l.target}:${l.relation}`;
        if (!kgLinks.has(key)) {
            addedLinks.add(key);
            kgLinks.set(key, { ...l });
        }
    });
    if (addedNodes.size || addedLinks.size) {
        kgUndoStack.push({ nodeUids: addedNodes, linkKeys: addedLinks });
    }
}

function kgUndo() {
    if (!kgUndoStack.length) { kgSetStatus('Nothing to undo.'); return; }
    const { nodeUids, linkKeys } = kgUndoStack.pop();
    nodeUids.forEach(uid => kgNodes.delete(uid));
    linkKeys.forEach(key => kgLinks.delete(key));
    // also remove any links whose endpoint was just removed
    for (const [key, l] of kgLinks) {
        const src = typeof l.source === 'object' ? l.source.id : l.source;
        const tgt = typeof l.target === 'object' ? l.target.id : l.target;
        if (!kgNodes.has(src) || !kgNodes.has(tgt)) kgLinks.delete(key);
    }
    kgRender(null);
}

function kgSetStatus(msg) {
    const el = document.getElementById('kg-status');
    if (el) el.textContent = msg;
}

// --------------------------------------------------------------------------- //
// View-data helper: returns {nodes, links} for the current display mode.
// kgRender() calls this so it never touches kgNodes/kgLinks directly.
// --------------------------------------------------------------------------- //
function kgGetViewData() {
    let nodes = Array.from(kgNodes.values());
    if (kgHideReagents) {
        nodes = nodes.filter(n => !(n.type === 'molecule' && KG_REAGENT_DENYLIST.has(n.smiles)));
    }
    if (kgTaxonFilter) {
        nodes = nodes.filter(n => n.type !== 'reaction' || (n.taxon && n.taxon.startsWith(kgTaxonFilter)));
    }

    if (!kgTemplateView) {
        const nodeIndex = new Map(nodes.map(n => [n.id, n]));
        let links = Array.from(kgLinks.values())
            .filter(l => nodeIndex.has(l.source?.id || l.source) && nodeIndex.has(l.target?.id || l.target))
            .map(l => ({
                ...l,
                source: typeof l.source === 'object' ? l.source.id : l.source,
                target: typeof l.target === 'object' ? l.target.id : l.target,
            }));
        // Remove orphan molecules that lost all connections after taxon filtering
        if (kgTaxonFilter) {
            const connected = new Set();
            links.forEach(l => { connected.add(l.source); connected.add(l.target); });
            nodes = nodes.filter(n => n.root || n.type !== 'molecule' || connected.has(n.id));
        }
        return { nodes, links };
    }

    // Template-hub view: collapse all reaction nodes sharing an rc_id into one
    // template node. Edge weight = number of reactions merged into that edge.
    const templateNodes = new Map();   // tid -> synthetic template node
    const reactionToTid = new Map();   // r:{rxn_id} -> tid
    nodes.forEach(n => {
        if (n.type === 'reaction' && n.rc_id != null) {
            const tid = `t:${n.rc_id}`;
            reactionToTid.set(n.id, tid);
            if (!templateNodes.has(tid)) {
                templateNodes.set(tid, {
                    id: tid, type: 'template',
                    label: `Template ${n.rc_id}`,
                    rc_id: n.rc_id, ref_id: n.rc_id,
                    reaction_count: 0, expandable: true,
                    x: n.x, y: n.y,   // seed position from one sibling
                });
            }
            templateNodes.get(tid).reaction_count++;
        }
    });

    const visibleNodes = [
        ...nodes.filter(n => n.type !== 'reaction'),
        ...templateNodes.values(),
    ];
    const visibleSet = new Set(visibleNodes.map(n => n.id));

    const linkMap = new Map();
    kgLinks.forEach(l => {
        let src = typeof l.source === 'object' ? l.source.id : l.source;
        let tgt = typeof l.target === 'object' ? l.target.id : l.target;
        src = reactionToTid.get(src) || src;
        tgt = reactionToTid.get(tgt) || tgt;
        if (src === tgt || !visibleSet.has(src) || !visibleSet.has(tgt)) return;
        const key = `${src}->${tgt}:${l.relation}`;
        if (!linkMap.has(key)) {
            linkMap.set(key, { source: src, target: tgt, relation: l.relation, weight: 1 });
        } else {
            linkMap.get(key).weight++;
        }
    });

    return { nodes: visibleNodes, links: Array.from(linkMap.values()) };
}

// --------------------------------------------------------------------------- //
// Render with D3 force layout
// --------------------------------------------------------------------------- //
function kgRender(centerUid) {
    const viewport = document.getElementById('kg-canvas');
    if (!viewport) return;

    const width = viewport.clientWidth || 800;
    const height = viewport.clientHeight || 600;

    const { nodes, links } = kgGetViewData();

    let svg = d3.select('#kg-canvas svg');
    if (svg.empty()) {
        svg = d3.select('#kg-canvas').append('svg')
            .attr('width', '100%').attr('height', '100%')
            .attr('viewBox', `0 0 ${width} ${height}`);

        const defs = svg.append('defs');
        Object.entries(KG_REL_STYLE).forEach(([rel, st]) => {
            defs.append('marker')
                .attr('id', `kg-arrow-${rel}`)
                .attr('viewBox', '0 -5 10 10')
                .attr('refX', 22).attr('refY', 0)
                .attr('markerWidth', 6).attr('markerHeight', 6)
                .attr('orient', 'auto')
                .append('path')
                .attr('d', 'M0,-5L10,0L0,5')
                .attr('fill', st.color);
        });

        kgRoot = svg.append('g');
        kgZoom = d3.zoom().scaleExtent([0.15, 4]).on('zoom', ev => {
            kgRoot.attr('transform', ev.transform);
        });
        svg.call(kgZoom);
        kgSvg = svg;
    }

    // groups (create once)
    let linkG = kgRoot.select('g.kg-links');
    if (linkG.empty()) linkG = kgRoot.append('g').attr('class', 'kg-links');
    let nodeG = kgRoot.select('g.kg-nodes');
    if (nodeG.empty()) nodeG = kgRoot.append('g').attr('class', 'kg-nodes');

    // ---- links ----
    const link = linkG.selectAll('line').data(links, d => `${d.source}->${d.target}:${d.relation}`);
    link.exit().remove();
    const linkEnter = link.enter().append('line')
        .attr('stroke-opacity', 0.7);
    linkEnter.merge(link)
        .attr('stroke', d => d.on_path ? 'var(--accent-yellow, #facc15)' : ((KG_REL_STYLE[d.relation] || {}).color || '#888'))
        .attr('stroke-dasharray', d => d.relation === 'class' ? '3,3' : null)
        .attr('stroke-width', d => d.on_path ? 3 : (d.weight ? Math.min(1.6 + (d.weight - 1) * 0.5, 6) : 1.6))
        .attr('stroke-opacity', d => d.on_path ? 1 : 0.7)
        .attr('marker-end', d => `url(#kg-arrow-${d.relation})`);

    // ---- nodes ----
    const node = nodeG.selectAll('g.kg-node').data(nodes, d => d.id);
    node.exit().remove();

    const nodeEnter = node.enter().append('g')
        .attr('class', 'kg-node')
        .style('cursor', 'pointer')
        .call(d3.drag()
            .on('start', kgDragStart)
            .on('drag', kgDragged)
            .on('end', kgDragEnd));

    nodeEnter.append('circle').attr('class', 'kg-main-circle');
    nodeEnter.append('circle').attr('class', 'kg-pin-ring')
        .attr('fill', 'none').attr('pointer-events', 'none');
    nodeEnter.append('text').attr('class', 'kg-node-glyph')
        .attr('text-anchor', 'middle').attr('dy', '0.35em')
        .attr('pointer-events', 'none')
        .style('font-size', '11px').style('fill', '#fff');
    nodeEnter.append('text').attr('class', 'kg-node-label')
        .attr('text-anchor', 'middle')
        .attr('pointer-events', 'none')
        .style('font-size', '10px');

    const allNodes = nodeEnter.merge(node);
    allNodes.select('circle.kg-main-circle')
        .attr('r', d => (KG_NODE_STYLE[d.type] || {}).r || 12)
        .attr('fill', d => (KG_NODE_STYLE[d.type] || {}).color || '#888')
        .attr('stroke', d => {
            if (d.id === kgSelectedUid) return 'var(--text-primary, #fff)';
            if (d.on_path) return 'var(--accent-yellow, #facc15)';
            if (d.root) return '#fff';
            return 'none';
        })
        .attr('stroke-width', d => {
            if (d.id === kgSelectedUid) return 3;
            if (d.on_path) return 3;
            if (d.root) return 2;
            return 0;
        })
        .attr('fill-opacity', 0.92);
    allNodes.select('circle.kg-pin-ring')
        .attr('r', d => ((KG_NODE_STYLE[d.type] || {}).r || 12) + 5)
        .attr('stroke', 'var(--accent-cyan, #06b6d4)')
        .attr('stroke-width', 1.5)
        .attr('stroke-dasharray', '3,2')
        .attr('opacity', d => d.fx != null ? 1 : 0);
    allNodes.select('text.kg-node-glyph')
        .text(d => (KG_NODE_STYLE[d.type] || {}).glyph || '');
    allNodes.select('text.kg-node-label')
        .attr('dy', d => ((KG_NODE_STYLE[d.type] || {}).r || 12) + 12)
        .attr('fill', 'var(--text-secondary, #cbd5e1)')
        .text(d => d.label);

    allNodes
        .on('click', (ev, d) => { ev.stopPropagation(); kgOnNodeClick(d); })
        .on('dblclick', (ev, d) => {
            ev.stopPropagation();
            d.fx = null; d.fy = null;
            kgRefreshStrokes();
            if (kgSim) kgSim.alpha(0.3).restart();
        })
        .on('mouseover', (ev, d) => { kgFocusNode(d); kgShowTooltip(ev, d); })
        .on('mousemove', (ev) => kgMoveTooltip(ev))
        .on('mouseout', () => { kgUnfocusAll(); kgHideTooltip(); });

    // ---- simulation ----
    if (!kgSim) {
        kgSim = d3.forceSimulation()
            .force('link', d3.forceLink().id(d => d.id).distance(90))
            .force('charge', d3.forceManyBody().strength(-280))
            .force('center', d3.forceCenter(width / 2, height / 2))
            .force('collision', d3.forceCollide().radius(28));
    }
    kgSim.nodes(nodes).on('tick', () => {
        linkG.selectAll('line')
            .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
        nodeG.selectAll('g.kg-node').attr('transform', d => `translate(${d.x},${d.y})`);
    });
    kgSim.force('link').links(links);
    kgSim.alpha(0.6).restart();

    if (centerUid) {
        setTimeout(() => kgZoomToFit(), 700);
    }

    kgSetStatus(`${nodes.length} nodes · ${links.length} edges`);
}

function kgOnNodeClick(d) {
    kgSelectedUid = d.id;
    kgRenderSelectionInfo(d);
    // Synthesised template-hub nodes have ref_id = rc_id (not a reaction_id),
    // so skip the rich info card — the selection card expand button is enough.
    if (kgTemplateView && d.type === 'template') {
        kgCloseInfo();
    } else {
        kgShowInfo(d);
    }
    kgRefreshStrokes();
}

function kgRefreshStrokes() {
    if (!kgRoot) return;
    kgRoot.selectAll('g.kg-node circle.kg-main-circle')
        .attr('stroke', d => d.id === kgSelectedUid ? 'var(--text-primary, #fff)' : (d.root ? '#fff' : 'none'))
        .attr('stroke-width', d => d.id === kgSelectedUid ? 3 : (d.root ? 2 : 0));
    kgRoot.selectAll('g.kg-node circle.kg-pin-ring')
        .attr('opacity', d => d.fx != null ? 1 : 0);
}

function kgOnNodeOpen(d) {
    // hand off reactions to the existing ITS / detail view
    if (d.type === 'reaction' && typeof loadReaction === 'function') {
        kgExitMode();
        loadReaction(d.ref_id);
    } else if (d.type === 'template' && typeof loadReaction !== 'undefined') {
        // open the first reaction of the template by expanding then nothing;
        // double-click on a template just expands it.
        kgExpandNode('template', d.ref_id);
    }
}

function kgRenderSelectionInfo(d) {
    const el = document.getElementById('kg-selection');
    if (!el) return;
    const typeLabel = d.type === 'reaction' ? 'template' : d.type;
    let html = `<div class="kg-sel-type kg-type-${d.type}">${typeLabel}</div>`;
    html += `<div class="kg-sel-label">${escapeHtml(d.label)}</div>`;
    if (d.smiles) html += `<div class="kg-sel-smiles">${escapeHtml(d.smiles)}</div>`;
    if (d.name) html += `<div class="kg-sel-sub">${escapeHtml(d.name)}</div>`;
    if (d.reaction_count !== undefined) html += `<div class="kg-sel-sub">${d.reaction_count} reactions</div>`;
    const actions = [];
    if (d.expandable) actions.push(`<button class="kg-mini-btn" onclick="kgExpandNode('${d.type}', '${d.ref_id}')">Expand neighbours</button>`);
    if (actions.length) html += `<div class="kg-sel-actions">${actions.join('')}</div>`;
    el.innerHTML = html;
}

// --------------------------------------------------------------------------- //
// Click-to-inspect info card (centre panel)
// --------------------------------------------------------------------------- //
function kgCdkUrl(smiles, withMap) {
    const isDark = !document.body.classList.contains('light-theme');
    const style = isDark ? 'cod' : 'cow';
    const annotate = withMap ? 'mapidx' : 'none';
    return `https://www.simolecule.com/cdkdepict/depict/${style}/svg`
        + `?smi=${encodeURIComponent(smiles)}&zoom=2&abbr=off&hdisp=bridgehead`
        + `&showtitle=false&annotate=${annotate}`;
}

function kgInfoEls() {
    return {
        panel: document.getElementById('kg-info'),
        body: document.getElementById('kg-info-body'),
        title: document.getElementById('kg-info-title'),
    };
}

function kgCloseInfo() {
    const { panel } = kgInfoEls();
    if (panel) panel.classList.remove('open');
}

function kgShowInfo(d) {
    const { panel } = kgInfoEls();
    if (!panel) return;
    panel.classList.add('open');
    if (d.type === 'molecule') kgShowMoleculeInfo(d);
    else if (d.type === 'reaction' || d.type === 'template') kgShowReactionInfo(d);
    else { const { title, body } = kgInfoEls(); title.textContent = d.label; body.innerHTML = ''; }
}

async function kgShowMoleculeInfo(d) {
    const { title, body } = kgInfoEls();
    title.textContent = 'Molecule';
    body.innerHTML = '<p class="kg-hint">Loading…</p>';
    let info = null;
    try {
        const res = await fetch(`${KG_API()}/api/kg/molecule-info/${d.ref_id}`);
        if (res.ok) info = await res.json();
    } catch (e) {}
    const smiles = (info && info.canonical_smiles) || d.smiles || '';
    let html = '';
    html += `<div class="kg-info-struct"><img alt="structure" src="${kgCdkUrl(smiles, false)}"
              onerror="this.style.display='none'"></div>`;
    html += `<div class="kg-info-smiles">${escapeHtml(smiles)}</div>`;
    if (info && info.inchikey) html += `<div class="kg-info-meta">InChIKey: <code>${escapeHtml(info.inchikey)}</code></div>`;
    const total = info ? info.total_reactions : d.reaction_count;
    if (total !== undefined) html += `<div class="kg-info-meta">${total} reaction${total === 1 ? '' : 's'}</div>`;
    html += `<div class="kg-info-actions">
                <button class="kg-mini-btn kg-mini-primary" onclick="kgExpandNode('molecule','${d.ref_id}')">Expand into graph</button>
             </div>`;
    if (info && info.reactions && info.reactions.length) {
        html += `<div class="kg-info-subtitle">Appears in</div><div class="kg-info-list">`;
        info.reactions.forEach(r => {
            const sideTag = `<span class="kg-side kg-side-${r.side}">${r.side}</span>`;
            html += `<div class="kg-info-row" role="button" tabindex="0"
                        onclick="kgExpandNode('reaction','${r.id}')"
                        title="Add this reaction to the graph">
                        ${sideTag}<span class="kg-info-row-label">${escapeHtml(r.name || r.case_id || ('reaction ' + r.id))}</span>
                     </div>`;
        });
        html += `</div>`;
    }
    body.innerHTML = html;
}

async function kgShowReactionInfo(d) {
    const { title, body } = kgInfoEls();
    title.textContent = d.rc_id != null ? `Template ${d.rc_id}` : 'Reaction';
    body.innerHTML = '<p class="kg-hint">Loading…</p>';
    let rxn = null;
    try {
        const res = await fetch(`${KG_API()}/api/reactions/${d.ref_id}`);
        if (res.ok) rxn = await res.json();
    } catch (e) {}
    if (!rxn) { body.innerHTML = '<p class="kg-hint">Could not load reaction.</p>'; return; }

    const depictSmiles = rxn.aam_key || rxn.canonical_rsmi || '';
    let html = '';
    if (rxn.name) html += `<div class="kg-info-rxn-name">${escapeHtml(rxn.name)}</div>`;
    const subBits = [];
    if (d.rc_id != null) subBits.push(`Template ${d.rc_id}`);
    if (rxn.case_id) subBits.push(escapeHtml(rxn.case_id));
    if (rxn.taxonomy && rxn.taxonomy.code) subBits.push(escapeHtml(rxn.taxonomy.code));
    if (subBits.length) html += `<div class="kg-info-meta">${subBits.join(' · ')}</div>`;
    // Trust badges
    if (rxn.balanced === true)  html += `<span class="kg-trust-badge kg-trust-ok">⚖ Balanced</span>`;
    if (rxn.balanced === false) html += `<span class="kg-trust-badge kg-trust-warn">⚠ Unbalanced</span>`;

    html += `<div class="kg-info-struct"><img alt="reaction" src="${kgCdkUrl(depictSmiles, true)}"
              onerror="this.style.display='none'"></div>`;

    if (rxn.arrows && rxn.arrows.length) {
        html += `<div class="kg-info-subtitle">EPD (${rxn.arrows.length} arrow${rxn.arrows.length === 1 ? '' : 's'})</div><div class="kg-info-list">`;
        rxn.arrows.forEach(a => {
            const at = (typeof arrowTypeVocab !== 'undefined') ? arrowTypeVocab[a.arrow_type_code] : null;
            const desc = at ? `${at.source_type} → ${at.target_type}` : '';
            html += `<div class="kg-info-epd"><span class="kg-epd-idx">${a.arrow_index}</span>
                        <span class="kg-epd-code">${escapeHtml(a.arrow_type_code)}</span>
                        <span class="kg-epd-atoms">[${a.source_atoms.join(',')}] → [${a.target_atoms.join(',')}]</span>
                        ${desc ? `<span class="kg-epd-desc">${desc}</span>` : ''}
                     </div>`;
        });
        html += `</div>`;
    }

    // Store for onclick handlers — avoids embedding SMILES/hashes in HTML attributes
    kgLastInspectedRsmi   = rxn.canonical_rsmi || '';
    kgLastInspectedWlhash = d.wlhash || '';

    html += `<div class="kg-info-actions">
                <button class="kg-mini-btn kg-mini-primary" onclick="kgOpenReaction('${d.ref_id}')">Open in ITS view</button>`;
    if (d.rc_id != null) html += `<button class="kg-mini-btn" onclick="kgExpandNode('template','${d.rc_id}')">Reactions sharing this template</button>`;
    html += `<button class="kg-mini-btn" onclick="kgExpandNode('reaction','${d.ref_id}')">Show reactants / products</button>`;
    if (kgLastInspectedRsmi)   html += `<button class="kg-mini-btn" onclick="kgFetchSimilarReactions(kgLastInspectedRsmi)">🔍 Find similar reactions</button>`;
    if (kgLastInspectedWlhash) html += `<button class="kg-mini-btn" onclick="kgFetchByWlhash(kgLastInspectedWlhash)">⚙ Same mechanism</button>`;
    html += `</div>`;
    body.innerHTML = html;
}

// --------------------------------------------------------------------------- //
// Export the visible sub-network (JSON, importable into Cytoscape/Gephi tools)
// --------------------------------------------------------------------------- //
function kgExportJSON() {
    const data = {
        nodes: Array.from(kgNodes.values()).map(n => ({
            id: n.id, type: n.type, label: n.label, smiles: n.smiles || null,
            rc_id: n.rc_id ?? null, name: n.name || null, ref_id: n.ref_id
        })),
        links: Array.from(kgLinks.values()).map(l => ({
            source: typeof l.source === 'object' ? l.source.id : l.source,
            target: typeof l.target === 'object' ? l.target.id : l.target,
            relation: l.relation
        }))
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'synepd_knowledge_graph.json';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function kgToggleReagents() {
    const cb = document.getElementById('kg-hide-reagents');
    kgHideReagents = cb ? cb.checked : !kgHideReagents;
    kgRender(null);
}

function kgSetTaxonFilter() {
    const sel = document.getElementById('kg-taxon-filter');
    kgTaxonFilter = sel ? sel.value : '';
    kgRender(null);
}

function kgOpenReaction(refId) {
    if (typeof loadReaction === 'function') {
        kgExitMode();
        loadReaction(parseInt(refId));
    }
}

// --------------------------------------------------------------------------- //
// Focus-on-hover (dim non-adjacent nodes and edges)
// --------------------------------------------------------------------------- //
function kgFocusNode(d) {
    if (!kgRoot) return;
    const adjacent = new Set([d.id]);
    kgLinks.forEach(l => {
        const src = typeof l.source === 'object' ? l.source.id : l.source;
        const tgt = typeof l.target === 'object' ? l.target.id : l.target;
        if (src === d.id) adjacent.add(tgt);
        if (tgt === d.id) adjacent.add(src);
    });
    kgRoot.selectAll('g.kg-node').classed('kg-dim', nd => !adjacent.has(nd.id));
    kgRoot.selectAll('g.kg-links line').classed('kg-dim', l => {
        const src = typeof l.source === 'object' ? l.source.id : l.source;
        const tgt = typeof l.target === 'object' ? l.target.id : l.target;
        return !(adjacent.has(src) && adjacent.has(tgt));
    });
}

function kgUnfocusAll() {
    if (!kgRoot) return;
    kgRoot.selectAll('g.kg-node').classed('kg-dim', false);
    kgRoot.selectAll('g.kg-links line').classed('kg-dim', false);
}

// --------------------------------------------------------------------------- //
// Tooltip
// --------------------------------------------------------------------------- //
function kgShowTooltip(ev, d) {
    let tip = document.getElementById('kg-tooltip');
    if (!tip) {
        tip = document.createElement('div');
        tip.id = 'kg-tooltip';
        tip.className = 'kg-tooltip';
        document.body.appendChild(tip);
    }
    const typeLabel = (kgTemplateView && d.type === 'reaction') ? 'template' : d.type;
    let html = `<strong>${escapeHtml(d.label)}</strong><br><span class="kg-tt-type">${typeLabel}</span>`;

    const depictSmi = d.smiles || d.rsmi || null;
    const isRxn = !!(d.rsmi && !d.smiles);
    if (depictSmi) {
        const imgUrl = kgCdkUrl(depictSmi, isRxn);
        html += `<div class="kg-tt-struct"><img src="${imgUrl}" alt="structure"
                      onerror="this.parentElement.style.display='none'"></div>`;
    }

    if (d.smiles) html += `<code style="font-size:0.68rem;">${escapeHtml(d.smiles.length > 48 ? d.smiles.slice(0, 46) + '…' : d.smiles)}</code><br>`;
    if (d.case_id) html += `<span style="font-size:0.72rem;">${escapeHtml(d.case_id)}</span><br>`;
    if (d.reaction_count !== undefined) html += `<span style="font-size:0.72rem;">${d.reaction_count} reaction${d.reaction_count === 1 ? '' : 's'}</span><br>`;
    html += `<em>${d.type === 'reaction' ? 'double-click to open · ' : ''}click to expand</em>`;
    tip.innerHTML = html;
    tip.style.display = 'block';
    kgMoveTooltip(ev);
}
function kgMoveTooltip(ev) {
    const tip = document.getElementById('kg-tooltip');
    if (!tip) return;
    tip.style.left = (ev.pageX + 14) + 'px';
    tip.style.top = (ev.pageY + 14) + 'px';
}
function kgHideTooltip() {
    const tip = document.getElementById('kg-tooltip');
    if (tip) tip.style.display = 'none';
}

// --------------------------------------------------------------------------- //
// Drag + zoom-to-fit
// --------------------------------------------------------------------------- //
function kgDragStart(ev, d) {
    if (!ev.active) kgSim.alphaTarget(0.3).restart();
    d.fx = d.x; d.fy = d.y;
}
function kgDragged(ev, d) { d.fx = ev.x; d.fy = ev.y; }
function kgDragEnd(ev, d) {
    if (!ev.active) kgSim.alphaTarget(0);
    // keep d.fx / d.fy — node stays where dropped; dblclick to release
    kgRefreshStrokes();
}

function kgZoomToFit() {
    if (!kgRoot || !kgSvg || !kgZoom) return;
    const bounds = kgRoot.node().getBBox();
    const parent = kgSvg.node().getBoundingClientRect();
    const fullW = parent.width, fullH = parent.height;
    if (bounds.width === 0 || bounds.height === 0) return;
    const scale = 0.85 / Math.max(bounds.width / fullW, bounds.height / fullH);
    const tx = fullW / 2 - scale * (bounds.x + bounds.width / 2);
    const ty = fullH / 2 - scale * (bounds.y + bounds.height / 2);
    kgSvg.transition().duration(500).call(
        kgZoom.transform,
        d3.zoomIdentity.translate(tx, ty).scale(Math.min(scale, 2))
    );
}

function kgClear() {
    kgNodes.clear();
    kgLinks.clear();
    kgSelectedUid = null;
    kgUndoStack.length = 0;
    kgPathUids.clear();
    if (kgSim) { kgSim.stop(); kgSim = null; }
    const canvas = document.getElementById('kg-canvas');
    if (canvas) { const s = canvas.querySelector('svg'); if (s) s.remove(); }
    kgSvg = kgRoot = kgZoom = null;
    const sel = document.getElementById('kg-selection');
    if (sel) sel.innerHTML = '<p class="kg-hint">Click a node to inspect it.</p>';
    kgCloseInfo();
    kgSetStatus('Graph cleared.');
}

// --------------------------------------------------------------------------- //
// Template-hub toggle
// --------------------------------------------------------------------------- //
function kgToggleTemplateView() {
    kgTemplateView = !kgTemplateView;
    // Reset simulation — node IDs change between views so positions are garbage
    if (kgSim) { kgSim.stop(); kgSim = null; }
    kgCloseInfo();
    kgRender(null);
    const btn = document.getElementById('kg-hub-toggle');
    if (btn) btn.classList.toggle('kg-mini-primary', kgTemplateView);
}

// --------------------------------------------------------------------------- //
// Shareable URL state  (seed + options encoded in window.location.hash)
// --------------------------------------------------------------------------- //
function kgEncodeState() {
    const rootNode = Array.from(kgNodes.values()).find(n => n.root);
    if (!rootNode) return;
    const { maxReactions, includeTaxonomy } = kgOptions();
    const p = new URLSearchParams({
        kgt:   rootNode.type,
        kgid:  String(rootNode.ref_id),
        kgmax: String(maxReactions),
        kgtax: includeTaxonomy ? '1' : '0',
    });
    history.replaceState(null, '', '#' + p.toString());
}

function kgDecodeAndRestore() {
    const hash = window.location.hash.slice(1);
    if (!hash || !hash.includes('kgt=')) return false;
    try {
        const p   = new URLSearchParams(hash);
        const type = p.get('kgt'), id = p.get('kgid');
        if (!type || !id) return false;
        const max = p.get('kgmax'), tax = p.get('kgtax');
        const sel = document.getElementById('kg-maxrxn');
        if (sel && max) sel.value = max;
        const cb = document.getElementById('kg-show-taxonomy');
        if (cb && tax !== null) cb.checked = tax === '1';
        if (typeof switchTab === 'function') switchTab('kg');
        kgSeedFromNode({ type, ref_id: id });
        return true;
    } catch (e) { return false; }
}

async function kgShare() {
    kgEncodeState();
    const btn = document.getElementById('kg-share-btn');
    try {
        await navigator.clipboard.writeText(window.location.href);
        if (btn) {
            const orig = btn.textContent;
            btn.textContent = '✓ Copied';
            setTimeout(() => { btn.textContent = orig; }, 2000);
        }
    } catch (e) {
        kgSetStatus('URL updated — copy from the address bar');
    }
}

// --------------------------------------------------------------------------- //
// Tab lifecycle hook (called by switchTab in app.js)
// --------------------------------------------------------------------------- //
function kgOnEnterTab() {
    if (kgNodes.size > 0) {
        kgEnterMode();
        setTimeout(() => kgZoomToFit(), 200);
    }
    const input = document.getElementById('kg-search-input');
    if (input) setTimeout(() => input.focus(), 50);
}

// Auto-restore from URL hash when the page loads (e.g. shared link).
// app.js (switchTab) is guaranteed to be loaded by DOMContentLoaded.
document.addEventListener('DOMContentLoaded', () => kgDecodeAndRestore());
