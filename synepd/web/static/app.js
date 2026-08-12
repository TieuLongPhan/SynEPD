// Global functions for SynEPD explorer application logic
const API_BASE = window.SYNEPD_API_BASE || window.location.origin;
const API_V1 = `${API_BASE}/api/v1`;
let legendCollapsed = false;

// JSME load callback
function jsmeOnLoad() {
    setSketchStatus('Sketcher library loaded.', 'success');
    if (document.getElementById('sketch-modal')?.classList.contains('show')) {
        ensureSketcher();
    }
}

function setSketchStatus(message, type = 'info') {
    const colors = {
        info: 'var(--text-secondary)',
        success: 'var(--accent-green)',
        error: 'var(--accent-red)',
        warning: 'var(--accent-orange)'
    };
    ['sketch-status', 'sketch-modal-status'].forEach(id => {
        const status = document.getElementById(id);
        if (!status) return;
        status.innerText = message;
        status.style.color = colors[type] || colors.info;
    });
}

function openSketchModal() {
    const modal = document.getElementById('sketch-modal');
    if (!modal) return;
    modal.classList.add('show');
    document.body.classList.add('modal-open');
    setTimeout(() => {
        ensureSketcher();
        document.querySelector('.sketch-close-btn')?.focus({ preventScroll: true });
    }, 80);
}

function closeSketchModal() {
    const modal = document.getElementById('sketch-modal');
    if (!modal) return;
    modal.classList.remove('show');
    document.body.classList.remove('modal-open');
}

function ensureSketcher() {
    if (jsmeApplet) {
        if (typeof jsmeApplet.repaint === 'function') {
            try { jsmeApplet.repaint(); } catch (e) {}
        }
        return true;
    }

    if (!window.JSApplet || !JSApplet.JSME) {
        setSketchStatus('Sketcher library is still loading. No API key is required.', 'warning');
        return false;
    }

    try {
        jsmeApplet = new JSApplet.JSME("jsme_container", "100%", "100%", {
            "options": "reaction,nocanon,newlook"
        });
        setSketchStatus('Sketcher ready.', 'success');
        setTimeout(() => {
            if (typeof jsmeApplet.repaint === 'function') {
                try { jsmeApplet.repaint(); } catch (e) {}
            }
        }, 100);
        return true;
    } catch(e) {
        console.warn('JSME init failed:', e);
        setSketchStatus('Sketcher failed to initialize. Check that the JSME script loaded.', 'error');
        return false;
    }
}

function searchFromSketcher() {
    const fallback = document.getElementById('sketch-rsmi-fallback')?.value.trim() || '';
    if (!ensureSketcher() && !fallback) {
        showError("Sketcher not loaded yet.");
        return;
    }
    let smiles = '';
    if (jsmeApplet) {
        try {
            smiles = jsmeApplet.smiles();
        } catch (e) {
            if (!fallback) {
                showError("Could not read sketcher SMILES.");
                return;
            }
        }
    }
    const hasDrawnAtoms = smiles.replace(/[>.\s]/g, '').length > 0;
    if ((!smiles || !hasDrawnAtoms) && fallback) smiles = fallback;
    if (!smiles) {
        showError("Please draw a reaction first.");
        return;
    }
    document.getElementById('search-input').value = smiles;
    closeSketchModal();
    switchTab('search');
    triggerSearch();
}

async function checkConnection() {
    try {
        const res = await fetch(`${API_V1}/health`);
        if (res.ok) {
            document.getElementById('db-badge').innerText = "Online";
            document.getElementById('db-badge').style.borderColor = "var(--accent-green)";
            document.getElementById('db-badge').style.color = "var(--accent-green)";
            document.getElementById('db-badge').style.backgroundColor = "rgba(16, 185, 129, 0.1)";
            loadTaxonomyTree();
        } else {
            document.getElementById('db-badge').innerText = "Offline";
            document.getElementById('db-badge').style.borderColor = "var(--accent-red)";
            document.getElementById('db-badge').style.color = "var(--accent-red)";
            document.getElementById('db-badge').style.backgroundColor = "rgba(239, 68, 68, 0.1)";
        }
    } catch (err) {
        document.getElementById('db-badge').innerText = "Offline";
        document.getElementById('db-badge').style.borderColor = "var(--accent-red)";
        document.getElementById('db-badge').style.color = "var(--accent-red)";
        document.getElementById('db-badge').style.backgroundColor = "rgba(239, 68, 68, 0.1)";
    }
}

function showToast(msg, type = 'error') {
    const colors = { success: 'var(--accent-green)', error: 'var(--accent-red)', warning: 'var(--accent-orange)' };
    const icons = { success: '✔', error: '⚠️', warning: '⚡' };
    const el = document.createElement('div');
    el.className = 'toast-item';
    el.style.cssText = `border: 1px solid ${colors[type]}; border-left: 4px solid ${colors[type]};`;
    el.innerHTML = `<span style="color:${colors[type]}; font-size:1rem;">${icons[type]}</span> ${escapeHtml(msg)}`;
    document.getElementById('toast-stack').appendChild(el);
    setTimeout(() => el.remove(), type === 'error' ? 5000 : 2500);
}
function showError(msg) { showToast(msg, 'error'); }

function switchTab(tabId) {
    const targetPane = document.getElementById(`tab-${tabId}`);
    if (!targetPane) {
        console.warn(`Unknown tab: ${tabId}`);
        return;
    }

    document.querySelectorAll('.tab-btn').forEach(btn => {
        const isActive = btn.dataset.tab === tabId;
        btn.classList.toggle('active', isActive);
        btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
        btn.setAttribute('tabindex', isActive ? '0' : '-1');
    });
    document.querySelectorAll('.tab-pane').forEach(pane => {
        const isActive = pane === targetPane;
        pane.classList.toggle('active', isActive);
        pane.hidden = !isActive;
    });

    if (tabId === 'history') {
        renderHistory();
    } else if (tabId === 'sketch') {
        openSketchModal();
    } else if (tabId === 'kg') {
        if (typeof kgOnEnterTab === 'function') kgOnEnterTab();
    }

    // Leaving the knowledge-graph tab restores the ITS / welcome viewport.
    if (tabId !== 'kg' && typeof kgExitMode === 'function') {
        kgExitMode();
    }
    // Leaving the taxonomy tab closes its full-workspace views.
    if (tabId !== 'taxonomy') {
        tmapExitMode();
        taxonomyOverviewExitMode();
    }
}

function tmapEnterMode() {
    const vp = document.getElementById('tmap-viewport');
    if (!vp) return;
    taxonomyOverviewExitMode();
    vp.style.display = 'flex';
    // Lazy-load: only set src the first time
    const frame = document.getElementById('tmap-frame');
    if (frame && frame.src !== window.location.origin + '/static/tmap.html') {
        const loading = document.getElementById('tmap-loading');
        if (loading) loading.style.display = 'flex';
        frame.src = '/static/tmap.html';
    }
    switchTab('taxonomy');
}

function tmapOnFrameLoad() {
    const frame = document.getElementById('tmap-frame');
    if (!frame || frame.src === 'about:blank') return;
    const loading = document.getElementById('tmap-loading');
    if (loading) loading.style.display = 'none';
}

function tmapExitMode() {
    const vp = document.getElementById('tmap-viewport');
    if (vp) vp.style.display = 'none';
}

function taxonomyOverviewEnterMode() {
    const vp = document.getElementById('taxonomy-overview-viewport');
    if (!vp) return;
    tmapExitMode();
    vp.style.display = 'flex';
    vp.setAttribute('aria-hidden', 'false');
    
    // If tree view is active, ensure iframe src is loaded
    const btnTree = document.getElementById('tax-view-btn-tree');
    if (btnTree && btnTree.classList.contains('active')) {
        const frame = document.getElementById('taxonomy-tree-frame');
        if (frame && frame.src !== window.location.origin + '/static/taxonomy.html') {
            frame.src = '/static/taxonomy.html';
        }
    }
    
    switchTab('taxonomy');
}

function taxonomyOverviewExitMode() {
    const vp = document.getElementById('taxonomy-overview-viewport');
    if (!vp) return;
    vp.style.display = 'none';
    vp.setAttribute('aria-hidden', 'true');
}

function switchTaxonomyView(viewMode) {
    const btnDiagram = document.getElementById('tax-view-btn-diagram');
    const btnTree = document.getElementById('tax-view-btn-tree');
    const paneDiagram = document.getElementById('taxonomy-view-diagram');
    const paneTree = document.getElementById('taxonomy-view-tree');
    
    if (!btnDiagram || !btnTree || !paneDiagram || !paneTree) return;
    
    if (viewMode === 'diagram') {
        btnDiagram.classList.add('active');
        btnTree.classList.remove('active');
        paneDiagram.style.display = 'block';
        paneTree.style.display = 'none';
    } else {
        btnDiagram.classList.remove('active');
        btnTree.classList.add('active');
        paneDiagram.style.display = 'none';
        paneTree.style.display = 'block';
        
        const frame = document.getElementById('taxonomy-tree-frame');
        if (frame && frame.src !== window.location.origin + '/static/taxonomy.html') {
            frame.src = '/static/taxonomy.html';
        }
    }
}

// Search Reactions
async function triggerSearch() {
    let val = document.getElementById('search-input').value.trim();
    if (!val) return;
    
    if (val.includes(">") && !val.includes(">>")) {
        val = val.replace(">", ">>");
        document.getElementById('search-input').value = val;
    }

    currentQuery = val;
    
    const resultsContainer = document.getElementById('search-results');
    resultsContainer.innerHTML = '<p style="color: var(--text-secondary); text-align: center;">Searching...</p>';

    try {
        let res;
        if (val.includes(">>")) {
            res = await fetch(`${API_V1}/query-epd`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ rsmi: val })
            });
            const data = await res.json();
            if (data.success) {
                resultsContainer.innerHTML = '';
                const card = document.createElement('div');
                card.className = "result-card";
                card.onclick = () => loadQueryEPDResult(data);
                
                let balanceNotice = '';
                if (data.balanced_from_imbalanced) {
                    balanceNotice = `<p style="color: var(--accent-orange); font-size: 11px; margin-top: 4px; font-weight: 500; margin-bottom: 0;">⚠️ Imbalanced query: automatically balanced</p>`;
                }
                
                card.innerHTML = `
                    <h4>Projected Template Match</h4>
                    <p style="color: var(--accent-cyan); font-size: 12px; margin-bottom: 4px; margin-top: 2px;">Path: ${data.path === 1 ? 'Direct DB Match' : 'Template Balanced Projection'}</p>
                    <p style="font-family: 'JetBrains Mono', monospace; font-size: 11px; word-break: break-all; margin-bottom: 0; color: var(--text-secondary);">${escapeHtml(data.canonical_rsmi || '')}</p>
                    ${balanceNotice}
                `;
                resultsContainer.appendChild(card);
            } else {
                resultsContainer.innerHTML = `<p style="color: var(--accent-red); text-align: center;">No match: ${escapeHtml(data.error || 'Check balance')}</p>`;
            }
            return;
        }
        
        resultOffset = 0;
        allSearchResults = [];
        resultsContainer.innerHTML = '';
        await fetchMoreSearchResults();
    } catch (err) {
        showError("Search failed.");
    }
}

async function fetchMoreSearchResults() {
    const resultsContainer = document.getElementById('search-results');
    const limit = RESULTS_PER_PAGE;
    try {
        const res = await fetch(`${API_V1}/reactions/search?query=${encodeURIComponent(currentQuery)}&limit=${limit}&offset=${resultOffset}`);
        const data = await res.json();
        
        const total = data.total;
        const rows = data.results;
        
        if (resultOffset === 0 && rows.length === 0) {
            resultsContainer.innerHTML = '<p style="color: var(--text-secondary); text-align: center;">No matching reactions found</p>';
            return;
        }
        
        allSearchResults = allSearchResults.concat(rows);
        resultOffset += rows.length;
        
        let meta = resultsContainer.querySelector('.search-meta');
        if (!meta) {
            meta = document.createElement('div');
            meta.className = 'search-meta';
            resultsContainer.appendChild(meta);
        }
        
        rows.forEach(rxn => {
            const card = document.createElement('div');
            card.className = "result-card";
            card.onclick = () => loadReaction(rxn.id);
            
            // FE-17: accessibility tags
            card.setAttribute('tabindex', '0');
            card.setAttribute('role', 'button');
            card.setAttribute('aria-label', `View details for reaction ${rxn.name || rxn.case_id}`);
            card.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); card.click(); }
            });
            
            const nameHtml = highlightMatch(rxn.name || rxn.case_id, currentQuery);
            const caseIdHtml = rxn.name ? highlightMatch(rxn.case_id, currentQuery) : '';
            const rsmiHtml = highlightMatch(rxn.canonical_rsmi, currentQuery);
            
            card.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                    <h4 style="margin: 0; font-family: 'Outfit', sans-serif;">${nameHtml}</h4>
                    ${rxn.taxonomy ? `<span style="font-size: 10px; background: rgba(0, 242, 255, 0.12); color: var(--accent-cyan); padding: 2px 6px; border-radius: 4px; font-weight: 500; font-family: 'Outfit', sans-serif;">${escapeHtml(rxn.taxonomy)}</span>` : ''}
                </div>
                ${rxn.name ? `<p style="font-size: 11px; margin: 0 0 4px 0; color: var(--text-secondary); font-family: 'JetBrains Mono', monospace;">${caseIdHtml}</p>` : ''}
                <p style="font-family: 'JetBrains Mono', monospace; font-size: 11px; word-break: break-all; margin: 0; color: var(--text-secondary);">${rsmiHtml}</p>
            `;
            resultsContainer.appendChild(card);
        });
        
        meta.innerHTML = `
            <span>Showing ${allSearchResults.length} of ${total}</span>
            ${allSearchResults.length < total ? `<button class="load-more-btn" onclick="fetchMoreSearchResults()">Load more</button>` : ''}
        `;
        resultsContainer.prepend(meta);
    } catch (e) {
        showError("Could not load search results.");
    }
}

function highlightMatch(text, query) {
    const safeText = escapeHtml(text || '');
    if (!query || !text) return safeText;
    const escaped = escapeHtml(query).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return safeText.replace(new RegExp(`(${escaped})`, 'gi'),
        '<mark style="background:rgba(0,242,255,0.25); color:var(--accent-cyan); border-radius:2px;">$1</mark>');
}

function escapeHtml(value) {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function ontologyTermUrl(ontologyId) {
    const match = String(ontologyId || '').match(/^([A-Za-z][A-Za-z0-9]*):(\d+)$/);
    if (!match) return null;
    return `https://purl.obolibrary.org/obo/${match[1].toUpperCase()}_${match[2]}`;
}

function readableRelation(value) {
    const labels = {
        'skos:exactMatch': 'exact match',
        'skos:broadMatch': 'broader concept',
        'skos:closeMatch': 'close match',
        'dcterms:isPartOf': 'part of',
    };
    if (labels[value]) return labels[value];
    return String(value || '')
        .replace(/^[^:]+:/, '')
        .replace(/_/g, ' ')
        .replace(/\b\w/g, character => character.toUpperCase());
}

function buildOntologyXrefList(xrefs, compact = false) {
    const entries = Array.isArray(xrefs) ? xrefs : [];
    if (!entries.length) return null;

    const list = document.createElement('div');
    list.className = compact ? 'ontology-xrefs compact' : 'ontology-xrefs';
    entries.forEach(xref => {
        const url = ontologyTermUrl(xref.ontology_id);
        const item = document.createElement(url ? 'a' : 'span');
        item.className = 'ontology-xref';
        if (url) {
            item.href = url;
            item.target = '_blank';
            item.rel = 'noopener noreferrer';
            item.addEventListener('click', event => event.stopPropagation());
        }
        item.textContent = compact
            ? `${xref.ontology_id} · ${readableRelation(xref.relation)}`
            : xref.ontology_id;
        item.title = [xref.name, readableRelation(xref.relation)]
            .filter(Boolean)
            .join(' · ');
        list.appendChild(item);
    });
    return list;
}

// Load Taxonomy Tree
async function loadTaxonomyTree() {
    const container = document.getElementById('taxonomy-tree-container');
    try {
        const res = await fetch(`${API_V1}/taxonomy`);
        const data = await res.json();
        container.innerHTML = '';
        buildTreeNode(data.taxonomy, container);
        const releaseBox = document.getElementById('taxonomy-ontology-release');
        const release = Array.isArray(data.ontology_releases)
            ? data.ontology_releases[0]
            : null;
        if (releaseBox && release) {
            releaseBox.replaceChildren();
            const label = document.createElement('span');
            label.textContent = `External mappings: RXNO ${release.data_version}`;
            releaseBox.appendChild(label);
            if (release.version_iri) {
                const link = document.createElement('a');
                link.href = release.version_iri;
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                link.textContent = 'release provenance ↗';
                releaseBox.appendChild(link);
            }
            releaseBox.hidden = false;
        }
    } catch (err) {
        container.innerHTML = '<p style="color: var(--accent-red);">Failed to load taxonomy.</p>';
    }
}

function countSubtreeReactions(node) {
    return node.subtree_reaction_count ?? node.reaction_count ?? 0;
}

function makeTaxonomyReactionItem(rxn) {
    const li = document.createElement('li');
    li.className = "tree-rxn-item";
    li.dataset.name = (rxn.name || rxn.case_id || '').toLowerCase();
    li.innerText = `${rxn.name || rxn.case_id} | ${rxn.canonical_rsmi || ''}`;
    li.setAttribute('tabindex', '0');
    li.setAttribute('role', 'button');
    li.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); li.click(); }
    });
    li.onclick = (e) => {
        e.stopPropagation();
        loadReaction(rxn.id);
    };
    return li;
}

async function loadTaxonReactions(node, nodeDiv, childrenDiv) {
    if (nodeDiv.dataset.rxnLoaded === '1' || !node.reaction_count) return;
    nodeDiv.dataset.rxnLoaded = '1';

    const list = document.createElement('ul');
    list.className = 'tree-reactions';
    list.innerHTML = '<li class="tree-rxn-item" style="color:var(--text-secondary);">Loading...</li>';
    childrenDiv.appendChild(list);

    try {
        const res = await fetch(`${API_V1}/taxonomy/${encodeURIComponent(node.code)}/reactions?limit=50`);
        const data = await res.json();
        list.innerHTML = '';
        (data.results || []).forEach(rxn => list.appendChild(makeTaxonomyReactionItem(rxn)));
        if (data.total > (data.results || []).length) {
            const more = document.createElement('li');
            more.className = 'tree-rxn-item';
            more.style.color = 'var(--accent-cyan)';
            more.innerText = `+ ${data.total - data.results.length} more - search "${node.code}" to see all`;
            list.appendChild(more);
        }
    } catch (e) {
        list.innerHTML = '<li class="tree-rxn-item" style="color:var(--accent-red);">Failed to load reactions.</li>';
        nodeDiv.dataset.rxnLoaded = '';
    }
}

function buildTreeNode(nodes, container) {
    nodes.forEach(node => {
        const nodeDiv = document.createElement('div');
        nodeDiv.className = "tree-node";
        nodeDiv.dataset.code = node.code;
        nodeDiv.dataset.name = node.name;
        
        const hasChildren = (node.children && node.children.length > 0) || node.reaction_count > 0;
        const subtreeCount = countSubtreeReactions(node);
        
        const header = document.createElement('div');
        header.className = "tree-header";
        const toggle = document.createElement('span');
        toggle.className = `tree-toggle ${hasChildren ? '' : 'leaf'}`;
        toggle.textContent = hasChildren ? '▶' : '•';
        header.appendChild(toggle);

        const label = document.createElement('span');
        label.className = 'taxonomy-node-label';
        const title = document.createElement('span');
        title.className = 'taxonomy-node-title';
        title.textContent = `${node.code} — ${node.name}`;
        label.appendChild(title);
        const xrefs = buildOntologyXrefList(node.xrefs, true);
        if (xrefs) label.appendChild(xrefs);
        header.appendChild(label);

        if (subtreeCount > 0) {
            const count = document.createElement('span');
            count.className = 'tree-count-badge';
            count.textContent = String(subtreeCount);
            count.title = 'Distinct reactions in this taxonomy branch';
            header.appendChild(count);
        }
        
        nodeDiv.appendChild(header);

        if (hasChildren) {
            const childrenDiv = document.createElement('div');
            childrenDiv.className = "tree-children";
            
            if (node.children && node.children.length > 0) {
                buildTreeNode(node.children, childrenDiv);
            }
            
            nodeDiv.appendChild(childrenDiv);

            header.onclick = async () => {
                const expanded = childrenDiv.classList.toggle('show');
                header.querySelector('.tree-toggle').classList.toggle('expanded', expanded);
                if (expanded) {
                    await loadTaxonReactions(node, nodeDiv, childrenDiv);
                }
            };
        }
        
        container.appendChild(nodeDiv);
    });
}

function filterTaxonomyTree(query) {
    const q = query.toLowerCase().trim();
    const allNodes = document.querySelectorAll('#taxonomy-tree-container .tree-node');
    if (!q) {
        allNodes.forEach(n => { n.style.display = ''; });
        document.querySelectorAll('#taxonomy-tree-container .tree-rxn-item').forEach(li => { li.style.display = ''; });
        return;
    }
    allNodes.forEach(n => {
        const code = (n.dataset.code || '').toLowerCase();
        const name = (n.dataset.name || '').toLowerCase();
        const match = code.includes(q) || name.includes(q);
        n.style.display = match ? '' : 'none';
        if (match) {
            let parent = n.parentElement;
            while (parent) {
                if (parent.classList.contains('tree-children')) {
                    parent.classList.add('show');
                    const tog = parent.previousElementSibling?.querySelector('.tree-toggle');
                    if (tog) tog.classList.add('expanded');
                }
                parent = parent.parentElement;
            }
        }
    });
    document.querySelectorAll('#taxonomy-tree-container .tree-rxn-item').forEach(li => {
        const match = (li.dataset.name || '').includes(q);
        li.style.display = match ? '' : 'none';
    });
}

function copyText(elementId, btn) {
    const text = document.getElementById(elementId).innerText;
    navigator.clipboard.writeText(text).then(() => {
        if (!btn) return;
        const origText = btn.innerText;
        btn.innerText = "✓ Copied";
        btn.style.color = "var(--accent-green)";
        setTimeout(() => { 
            btn.innerText = origText; 
            btn.style.color = "";
        }, 2000);
    }).catch(err => {
        showError("Failed to copy text.");
    });
}

function loadQueryEPDResult(data) {
    stopPlayback();
    const mechanismCandidates = Array.isArray(data.mechanism_candidates)
        ? data.mechanism_candidates
        : [];
    const projectedArrows = Array.isArray(data.arrows) ? data.arrows : [];
    const selectedCandidateIndex = (
        projectedArrows.length === 0 && mechanismCandidates.length > 0
    ) ? 0 : null;

    activeReaction = {
        id: data.id || data.reaction_id || null,
        case_id: data.case_id || "Projected Query",
        name: data.name || (data.case_id ? data.case_id : "Projected Query"),
        canonical_rsmi: data.canonical_rsmi || (data.mapped_rsmi ? data.mapped_rsmi.replace(/:\d+/g, '') : ''),
        aam_key: data.mapped_rsmi,
        canonical_aam_key: data.canonical_aam_key || null,
        taxonomy: data.taxonomy || { code: "DYNAMIC", name: "Custom EPD Projection", level: 4 },
        arrows: selectedCandidateIndex === null
            ? projectedArrows
            : (mechanismCandidates[selectedCandidateIndex].arrows || []),
        its_graph: data.its_graph,
        mechanistic_center: data.mechanistic_center || null,
        mechanism_ambiguous: data.mechanism_ambiguous || false,
        mechanism_candidate_count: data.mechanism_candidate_count || mechanismCandidates.length,
        mechanism_candidates: mechanismCandidates,
        selected_mechanism_candidate_index: selectedCandidateIndex,
        ontology_xrefs: Array.isArray(data.ontology_xrefs) ? data.ontology_xrefs : [],
        ontology_releases: Array.isArray(data.ontology_releases) ? data.ontology_releases : [],
        balanced_from_imbalanced: data.balanced_from_imbalanced || false,
        original_imbalanced_query: data.original_imbalanced_query || null
    };
    renderReactionDetails();
}

let reactionLoadSeq = 0;
let reactionLoadAbort = null;

async function loadReaction(rxnId, { historyMode = 'push' } = {}) {
    stopPlayback();
    const seq = ++reactionLoadSeq;
    if (reactionLoadAbort) reactionLoadAbort.abort();
    reactionLoadAbort = new AbortController();
    showRightPanelSkeleton();
    try {
        const res = await fetch(`${API_V1}/reactions/${rxnId}`, {
            signal: reactionLoadAbort.signal,
        });
        if (!res.ok) throw new Error(`Reaction request failed: ${res.status}`);
        const reaction = await res.json();
        if (seq !== reactionLoadSeq) return;
        activeReaction = reaction;
        
        addToHistory(activeReaction);

        const state = { reactionId: Number(rxnId) };
        if (historyMode === 'replace') {
            history.replaceState(state, '', `#reaction/${rxnId}`);
        } else if (historyMode === 'push') {
            history.pushState(state, '', `#reaction/${rxnId}`);
        }
        renderReactionDetails();
    } catch (err) {
        if (err.name === 'AbortError') return;
        if (seq !== reactionLoadSeq) return;
        showError("Failed to fetch reaction details.");
    }
}

function showRightPanelSkeleton() {
    document.getElementById('detail-fallback').style.display = 'none';
    const panel = document.getElementById('detail-panel');
    panel.style.display = 'block';
    panel.innerHTML = `
        <div class="rxn-header">
            <div class="skeleton" style="height:11px; width:55%; margin-bottom:8px;"></div>
            <div class="skeleton" style="height:20px; width:80%; margin-bottom:6px;"></div>
            <div class="skeleton" style="height:11px; width:40%;"></div>
        </div>
        <div class="skeleton" style="height:62px; margin-bottom:12px; border-radius:8px;"></div>
        <div class="skeleton" style="height:62px; margin-bottom:12px; border-radius:8px;"></div>
        <div class="skeleton" style="height:42px; margin-bottom:8px; border-radius:8px;"></div>
        <div class="skeleton" style="height:42px; margin-bottom:8px; border-radius:8px;"></div>
        <div class="skeleton" style="height:42px; margin-bottom:8px; border-radius:8px;"></div>
    `;
}

function downloadReaction() {
    if (!activeReaction) return;
    try {
        if (activeReaction.id) {
            const a = document.createElement('a');
            a.href = `${API_V1}/reactions/${activeReaction.id}/export`;
            a.download = `${activeReaction.case_id}_epd.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } else {
            const exportData = {
                case_id: activeReaction.case_id,
                reaction_name: activeReaction.name,
                canonical_smiles: activeReaction.canonical_rsmi,
                atom_mapped_smiles: activeReaction.aam_key,
                taxonomy_code: activeReaction.taxonomy ? activeReaction.taxonomy.code : null,
                mechanism_ambiguous: activeReaction.mechanism_ambiguous,
                selected_mechanism_candidate_index: activeReaction.selected_mechanism_candidate_index,
                mechanism_candidates: activeReaction.mechanism_candidates,
                ontology_xrefs: activeReaction.ontology_xrefs || [],
                epd_lw: activeReaction.arrows.map(arr => [
                    arr.arrow_type_code,
                    arr.source_atoms,
                    arr.target_atoms
                ])
            };
            const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${activeReaction.case_id || 'reaction'}_epd.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }
    } catch (err) {
        showError("Failed to download reaction EPD JSON.");
    }
}

function renderReactionDetails() {
    if (!activeReaction) return;

    const panel = document.getElementById('detail-panel');
    if (!document.getElementById('detail-name')) {
        panel.innerHTML = `
            <div id="breadcrumb-bar" class="breadcrumb-bar" style="display: none;"></div>
            <div class="rxn-header" style="display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem;">
                <div style="min-width: 0; flex: 1;">
                    <span class="taxonomy-path" id="detail-tax-path">Taxonomy Path</span>
                    <div style="display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;">
                        <h2 id="detail-name" style="margin: 0.25rem 0 0 0; font-size: 1.15rem; color: var(--text-primary); font-family: 'Outfit', sans-serif; word-break: break-word;">Reaction Name</h2>
                        <button id="btn-copy-link" class="copy-btn" onclick="copyReactionLink()" title="Copy shareable link" style="font-size:0.75rem; padding: 2px 6px; margin-top: 0.25rem; display: none;">🔗 Copy Link</button>
                    </div>
                    <div id="detail-case-id" style="display: none; font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; color: var(--text-secondary); margin-top: 0.25rem;">POLAR_1</div>
                </div>
	                <div style="display: flex; flex-direction: column; gap: 0.4rem; flex-shrink: 0;">
	                    <button class="download-btn" onclick="downloadReaction()" style="margin-top: 0;" title="Download EPD JSON">📥 JSON</button>
	                    <button class="download-btn" onclick="downloadCSV()" style="margin-top: 0; background: linear-gradient(135deg, var(--accent-cyan), #0891b2);" title="Download EPD CSV">📄 CSV</button>
	                    <button class="download-btn" onclick="downloadSVG()" style="margin-top: 0; background: linear-gradient(135deg, var(--accent-purple), #6366f1);" title="Download SVG graph">🖼 SVG</button>
	                </div>
            </div>
            
            <div class="smiles-box" id="detail-balance-warning-box" style="display: none; background: rgba(245, 158, 11, 0.15); border-left: 4px solid var(--accent-orange); color: var(--accent-orange); margin-bottom: 1rem; font-weight: 500; font-size: 0.85rem; padding: 0.75rem 1rem; border-radius: 4px;">
                ⚠️ Imbalanced query: automatically balanced and matched to a mechanistic pattern.
            </div>
            
            <div class="smiles-box">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.25rem;">
                    <span class="smiles-label">Canonical SMILES</span>
                    <button class="copy-btn" onclick="copyText('detail-smiles', this)" title="Copy to Clipboard">📋 Copy</button>
                </div>
                <div id="detail-smiles">CC[O-]>>CCO</div>
            </div>

            <div class="smiles-box">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.25rem;">
                    <span class="smiles-label">Atom-Mapped key</span>
                    <button class="copy-btn" onclick="copyText('detail-aam', this)" title="Copy to Clipboard">📋 Copy</button>
                </div>
                <div id="detail-aam">AAM Key</div>
            </div>

            <!-- CDK Depict 2D reaction diagram with local RDKit fallback -->
            <div class="section-collapsible" id="reaction-depict-section">
                <button class="section-toggle" onclick="toggleSection('reaction-depict-body')">2D Reaction Diagram ▾</button>
                <div id="reaction-depict-body" class="section-toggle-body">
                    <div style="display:flex; flex-wrap:wrap; align-items:center; gap:0.75rem; margin-bottom:0.5rem;">
                        <label style="display:flex; align-items:center; gap:0.3rem; font-size:0.75rem; color:var(--text-secondary); cursor:pointer; user-select:none;">
                            <input type="checkbox" id="depict-aam-toggle" onchange="renderReactionDepict()" style="cursor:pointer; accent-color:var(--accent-cyan);">
                            <span>Atom mapping</span>
                        </label>
                        <label style="display:flex; align-items:center; gap:0.3rem; font-size:0.75rem; color:var(--text-secondary); cursor:pointer; user-select:none;">
                            <input type="checkbox" id="depict-local-toggle" onchange="setDepictPreference(this.checked)" style="cursor:pointer; accent-color:var(--accent-cyan);">
                            <span>Use local RDKit</span>
                        </label>
                    </div>
                    <p class="depict-privacy-note">CDK Depict is an external service and receives the displayed SMILES. Choose local RDKit to keep rendering on this server.</p>
                    <div id="reaction-depict-container"></div>
                </div>
            </div>

            <!-- SMILES change summary diff -->
            <div id="change-summary" style="display:flex; gap:0.5rem; flex-wrap:wrap; margin-bottom:0.75rem;"></div>

            <div class="section-collapsible" id="linked-data-section" style="display:none;">
                <button class="section-toggle" onclick="toggleSection('linked-data-body')">External Ontology Links ▾</button>
                <div id="linked-data-body" class="section-toggle-body">
                    <div id="linked-data-summary"></div>
                </div>
            </div>

            <div class="section-collapsible" id="mechanistic-center-section" style="display:none;">
                <button class="section-toggle" onclick="toggleSection('mechanistic-center-body')">Mechanistic Center ▾</button>
                <div id="mechanistic-center-body" class="section-toggle-body">
                    <div id="mechanistic-center-summary" style="font-size:0.78rem; color:var(--text-secondary); line-height:1.55;"></div>
                    <button id="mechanism-view-toggle" class="download-btn" onclick="toggleMechanismView()" style="margin-top:0.65rem;">Show mechanistic center</button>
                </div>
            </div>

            <div class="section-collapsible" id="epd-steps-section">
                <button class="section-toggle" onclick="toggleSection('epd-steps-body')">EPD Arrow Steps ▾</button>
                <div id="epd-steps-body" class="section-toggle-body">
                    <div class="step-list" id="detail-step-list">
                        <!-- Steps injected dynamically -->
                    </div>
                </div>
            </div>

            <!-- Similar Reactions Collapsible Section -->
            <div class="section-collapsible" id="neighbors-section" style="display: none; margin-top: 1.25rem;">
                <button class="section-toggle" onclick="toggleSection('neighbors-body')">
                    Similar Reactions ▾
                </button>
                <div id="neighbors-body" class="section-toggle-body">
                    <div id="neighbors-list" style="margin-top: 0.5rem;"></div>
                </div>
            </div>
        `;
    }

    document.getElementById('detail-fallback').style.display = "none";
    panel.style.display = "block";
    document.getElementById('graph-controls-panel').style.display = "flex";

    const selectEl = document.getElementById('layout-select');
    if (selectEl) selectEl.value = 'force';

    document.getElementById('detail-name').innerText = activeReaction.name || activeReaction.case_id;
    document.title = `${activeReaction.name || activeReaction.case_id} - SynEPD Explorer`;
    document.getElementById('detail-case-id').innerText = activeReaction.case_id;
    document.getElementById('detail-smiles').innerText = activeReaction.canonical_rsmi;
    document.getElementById('detail-aam').innerText = activeReaction.aam_key;
    
    // Focus management (FE-17)
    const detailName = document.getElementById('detail-name');
    if (detailName) {
        detailName.setAttribute('tabindex', '-1');
        detailName.focus();
    }
    
    const warningBox = document.getElementById('detail-balance-warning-box');
    if (warningBox) warningBox.style.display = activeReaction.balanced_from_imbalanced ? 'block' : 'none';

    // Show/hide DB actions
    const copyLinkBtn = document.getElementById('btn-copy-link');
    if (copyLinkBtn) copyLinkBtn.style.display = activeReaction.id ? 'inline-block' : 'none';
    
    if (activeReaction.taxonomy) {
        const taxCode = typeof activeReaction.taxonomy === 'string' ? activeReaction.taxonomy : activeReaction.taxonomy.code;
        document.getElementById('detail-tax-path').innerText = taxCode;
        renderBreadcrumb(taxCode);
    } else {
        document.getElementById('detail-tax-path').innerText = "DYNAMIC";
        const bb = document.getElementById('breadcrumb-bar');
        if (bb) bb.style.display = 'none';
    }

    // Load similar reactions
    const neighborsSec = document.getElementById('neighbors-section');
    if (neighborsSec) {
        if (activeReaction.id) {
            neighborsSec.style.display = 'block';
            loadNeighbors(activeReaction.id);
        } else {
            neighborsSec.style.display = 'none';
        }
    }

    // Change summary (FE-10)
    markTransitionBonds(activeReaction.its_graph, activeReaction.arrows);
    const summary = computeChangeSummary(activeReaction.its_graph);
    renderChangeSummary(summary);
    renderLinkedDataSummary();
    renderMechanisticCenterSummary();

    // Update steps list
    const stepsContainer = document.getElementById('detail-step-list');
    stepsContainer.innerHTML = '';
    activeReaction.arrows.forEach(arr => {
        const step = document.createElement('div');
        const color = stepColors[(arr.arrow_index - 1) % stepColors.length];
        step.className = `step-item ${arr.arrow_index === activeStepIndex ? 'active' : ''}`;
        step.style.borderLeftColor = color;
        step.onclick = () => selectStep(arr.arrow_index);
        
        const at = arrowTypeVocab[arr.arrow_type_code];
        const tooltip = at
            ? `${at.electron_count}e⁻ · ${at.source_type} → ${at.target_type}`
            : arr.arrow_type_code;
        
        step.innerHTML = `
            <div class="step-header">
                <span class="step-number">Step ${arr.arrow_index}</span>
                <span class="step-badge" 
                      style="background:${color}22; border-color:${color}; color:${color}; cursor:help;"
                      title="${tooltip}">
                    ${arr.arrow_type_code}
                </span>
            </div>
            <div class="step-desc">
                ${at ? `<span style="color:var(--text-secondary); font-size:0.75rem; display:block; margin-bottom:4px;">${tooltip}</span>` : ''}
                <span style="color:var(--accent-cyan)">e⁻ from</span> atoms [${arr.source_atoms.join(', ')}]
                <span style="color:var(--accent-purple)">→ to</span> atoms [${arr.target_atoms.join(', ')}]
            </div>
        `;
        stepsContainer.appendChild(step);
    });

    // Show legend with step colors and descriptions
    const legendSteps = document.getElementById('legend-epd-steps');
    legendSteps.innerHTML = `<p class="legend-title" style="margin-top:0.5rem;">EPD Steps</p>`;
    activeReaction.arrows.forEach(arr => {
        const color = stepColors[(arr.arrow_index - 1) % stepColors.length];
        const at = arrowTypeVocab[arr.arrow_type_code];
        const text = at ? `${arr.arrow_type_code} (${at.source_type} → ${at.target_type})` : arr.arrow_type_code;
        legendSteps.innerHTML += `<div class="legend-row" title="${at ? at.electron_count + 'e-' : ''}"><span class="legend-swatch" style="background:${color}; height:3px;"></span> Step ${arr.arrow_index}: ${text}</div>`;
    });
    document.getElementById('graph-legend').style.display = 'block';

    activeStepIndex = 1;
    updateStepNavigation();
    drawGraph();

    const depictToggle = document.getElementById('depict-aam-toggle');
    if (depictToggle) depictToggle.checked = false;
    const localDepictToggle = document.getElementById('depict-local-toggle');
    if (localDepictToggle) localDepictToggle.checked = prefersLocalDepict();
    renderReactionDepict();
    startPlayback();
}

function renderLinkedDataSummary() {
    const section = document.getElementById('linked-data-section');
    const summary = document.getElementById('linked-data-summary');
    if (!section || !summary) return;

    const xrefs = Array.isArray(activeReaction.ontology_xrefs)
        ? activeReaction.ontology_xrefs
        : [];
    summary.replaceChildren();
    section.style.display = xrefs.length ? 'block' : 'none';
    if (!xrefs.length) return;

    if (xrefs.length) {
        const group = document.createElement('section');
        group.className = 'linked-data-group';
        const heading = document.createElement('h4');
        heading.textContent = 'External reaction ontology';
        group.appendChild(heading);

        const seen = new Set();
        xrefs.forEach(xref => {
            const key = [
                xref.ontology_id,
                xref.relation,
                xref.assigned_taxon_code,
                xref.mapping_taxon_code,
            ].join('|');
            if (seen.has(key)) return;
            seen.add(key);

            const row = document.createElement('div');
            row.className = 'linked-data-row ontology-row';
            const linkList = buildOntologyXrefList([xref]);
            if (linkList) row.appendChild(linkList);

            const description = document.createElement('span');
            description.className = 'linked-data-description';
            description.textContent = xref.name || xref.ontology_id;
            row.appendChild(description);

            const provenance = document.createElement('small');
            const inheritance = xref.inherited
                ? `${xref.assigned_taxon_code} via ${xref.mapping_taxon_code}`
                : xref.mapping_taxon_code;
            provenance.textContent = `${readableRelation(xref.relation)} · ${inheritance}`;
            row.appendChild(provenance);
            group.appendChild(row);
        });

        const releaseIds = new Set(xrefs.map(xref => xref.ontology_release_id).filter(Boolean));
        const releases = Array.isArray(activeReaction.ontology_releases)
            ? activeReaction.ontology_releases
            : [];
        releases.filter(release => releaseIds.has(release.id)).forEach(release => {
            const provenance = document.createElement('a');
            provenance.className = 'ontology-release-link';
            provenance.href = release.version_iri;
            provenance.target = '_blank';
            provenance.rel = 'noopener noreferrer';
            provenance.textContent = `Ontology release ${release.data_version} ↗`;
            group.appendChild(provenance);
        });
        summary.appendChild(group);
    }
}

function renderMechanisticCenterSummary() {
    const section = document.getElementById('mechanistic-center-section');
    const summary = document.getElementById('mechanistic-center-summary');
    const toggle = document.getElementById('mechanism-view-toggle');
    if (!section || !summary || !toggle) return;

    const mc = activeReaction.mechanistic_center;
    if (!mc) {
        section.style.display = activeReaction.mechanism_ambiguous ? 'block' : 'none';
        if (activeReaction.mechanism_ambiguous) {
            summary.replaceChildren();

            const status = document.createElement('p');
            status.className = 'mechanism-candidate-status';
            status.textContent = `${activeReaction.mechanism_candidate_count} product-verified mechanisms remain. Showing one remapped candidate for inspection; this does not resolve the ambiguity.`;
            summary.appendChild(status);

            const label = document.createElement('label');
            label.className = 'mechanism-candidate-label';
            label.htmlFor = 'mechanism-candidate-select';
            label.textContent = 'Displayed mechanism';

            const select = document.createElement('select');
            select.id = 'mechanism-candidate-select';
            select.className = 'mechanism-candidate-select';
            activeReaction.mechanism_candidates.forEach((candidate, index) => {
                const option = document.createElement('option');
                option.value = String(index);
                const identity = candidate.name || candidate.reference_case_id || 'Unnamed mechanism';
                const caseSuffix = candidate.name && candidate.reference_case_id
                    ? ` · ${candidate.reference_case_id}`
                    : '';
                option.textContent = `Candidate ${index + 1}: ${identity}${caseSuffix}`;
                select.appendChild(option);
            });
            select.value = String(activeReaction.selected_mechanism_candidate_index ?? 0);
            select.onchange = () => selectMechanismCandidate(Number(select.value));

            label.appendChild(select);
            summary.appendChild(label);
            toggle.style.display = 'none';
        }
        return;
    }

    section.style.display = 'block';
    toggle.style.display = 'inline-block';
    summary.textContent = [
        `${mc.transition_edge_count ?? 0} transition edges`,
        `${mc.rc_extension_edge_count ?? 0} RC-extension edges`,
        `${mc.transient_only_edge_count ?? 0} transient-only edges`,
    ].join(' · ');
    toggle.textContent = activeReaction._showMechanismContext
        ? 'Show full ITS'
        : 'Show mechanistic center';
}

function selectMechanismCandidate(index) {
    const candidate = activeReaction?.mechanism_candidates?.[index];
    if (!candidate) return;

    activeReaction.selected_mechanism_candidate_index = index;
    activeReaction.arrows = Array.isArray(candidate.arrows) ? candidate.arrows : [];
    activeReaction.reference_reaction_id = candidate.reference_reaction_id || null;
    activeReaction.reference_case_id = candidate.reference_case_id || null;
    activeStepIndex = 1;
    renderReactionDetails();
    document.getElementById('mechanism-candidate-select')?.focus();
}

function toggleMechanismView() {
    const template = activeReaction?.mechanistic_center?.template_graph;
    if (!template) return;
    if (!activeReaction._endpointItsGraph) {
        activeReaction._endpointItsGraph = activeReaction.its_graph;
    }
    activeReaction._showMechanismContext = !activeReaction._showMechanismContext;
    activeReaction.its_graph = activeReaction._showMechanismContext
        ? template
        : activeReaction._endpointItsGraph;
    renderMechanisticCenterSummary();
    markTransitionBonds(activeReaction.its_graph, activeReaction.arrows);
    drawGraph();
}

function toggleSection(bodyId) {
    const body = document.getElementById(bodyId);
    if (!body) return;
    body.style.display = body.style.display === 'none' ? '' : 'none';
}

function renderBreadcrumb(taxonCode) {
    const bar = document.getElementById('breadcrumb-bar');
    if (!bar) return;
    const parts = taxonCode.split('.');
    const html = parts.map((p, i) => {
        const code = parts.slice(0, i + 1).join('.');
        return `<a onclick="switchTab('taxonomy')">${code}</a>`;
    }).join('<span class="sep"> › </span>');
    bar.innerHTML = html;
    bar.style.display = 'flex';
}

function toggleLegend() {
    legendCollapsed = !legendCollapsed;
    document.getElementById('legend-body').style.display = legendCollapsed ? 'none' : '';
    document.getElementById('legend-chevron').innerText = legendCollapsed ? '▸' : '▾';
}

let _depictGen = 0; // generation counter to drop stale onerror callbacks
const CDK_DEPICT_BASE = (
    window.SYNEPD_CDK_DEPICT_BASE || 'https://www.simolecule.com/cdkdepict'
).replace(/\/$/, '');

function prefersLocalDepict() {
    try {
        return localStorage.getItem('synepd_depict_renderer') === 'rdkit';
    } catch (_) {
        return false;
    }
}

function setDepictPreference(useLocal) {
    try {
        localStorage.setItem('synepd_depict_renderer', useLocal ? 'rdkit' : 'cdk');
    } catch (_) {}
    renderReactionDepict();
}

function cdkDepictUrl(smiles, showAtomMapping = false) {
    const query = new URLSearchParams({
        smi: smiles,
        zoom: '1.5',
        abbr: 'off',
        hdisp: 'bridgehead',
        showtitle: 'false',
        annotate: showAtomMapping ? 'mapidx' : 'none',
    });
    return `${CDK_DEPICT_BASE}/depict/cow/svg?${query.toString()}`;
}

function rdkitDepictUrl(smiles, kind = 'auto') {
    return `${API_V1}/render/rdkit.svg?smi=${encodeURIComponent(smiles)}&kind=${encodeURIComponent(kind)}`;
}

function renderReactionDepict() {
    if (!activeReaction) return;
    const container = document.getElementById('reaction-depict-container');
    if (!container) return;

    // Skip render when the section is collapsed
    const body = document.getElementById('reaction-depict-body');
    if (body && body.style.display === 'none') return;

    const showAAM = document.getElementById('depict-aam-toggle')?.checked ?? false;
    const smiles = showAAM && activeReaction.aam_key ? activeReaction.aam_key : activeReaction.canonical_rsmi;

    if (!smiles) {
        container.innerHTML = '<p style="color:var(--text-secondary); font-size:0.8rem; text-align:center;">No SMILES available</p>';
        return;
    }

    const kind = smiles.includes('>') ? 'reaction' : 'molecule';
    const cdkUrl = cdkDepictUrl(smiles, showAAM);
    const fallbackUrl = rdkitDepictUrl(smiles, kind);
    const useLocal = prefersLocalDepict();

    container.innerHTML = '';
    const gen = ++_depictGen;

    const img = document.createElement('img');
    img.alt = '2D reaction diagram';
    img.style.cssText = 'max-width:100%; border-radius:4px; display:block; margin:0 auto;';
    const renderer = document.createElement('span');
    renderer.className = 'depict-renderer-badge';
    renderer.textContent = useLocal ? 'Local RDKit' : 'CDK Depict';
    if (useLocal) renderer.classList.add('fallback');

    const link = document.createElement('a');
    link.href = useLocal ? fallbackUrl : cdkUrl;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.className = 'depict-source-link';
    link.textContent = useLocal ? 'Open local RDKit SVG ↗' : 'Open CDK Depict SVG ↗';

    let usingFallback = false;
    img.onerror = () => {
        if (gen !== _depictGen) return; // stale — a newer render has taken over
        if (!useLocal && !usingFallback) {
            usingFallback = true;
            renderer.textContent = 'RDKit fallback';
            renderer.classList.add('fallback');
            link.href = fallbackUrl;
            link.textContent = 'Open local RDKit SVG ↗';
            img.dataset.renderer = 'rdkit';
            img.src = fallbackUrl;
            return;
        }
        const message = useLocal ? 'Local RDKit depiction unavailable' : 'CDK and RDKit depictions unavailable';
        container.innerHTML = `<p style="color:var(--accent-orange); font-size:0.8rem; text-align:center; padding:0.5rem 0;">${message}</p>`;
    };
    if (useLocal) {
        img.dataset.renderer = 'rdkit';
        img.src = fallbackUrl;
    } else {
        img.dataset.renderer = 'cdk';
        img.src = cdkUrl;
    }

    container.appendChild(img);
    const footer = document.createElement('div');
    footer.className = 'depict-source-row';
    footer.appendChild(renderer);
    footer.appendChild(link);
    container.appendChild(footer);
}

function fetchMoleculeReactions(smiles) {
    document.getElementById('search-input').value = smiles;
    switchTab('search');
    triggerSearch();
}

function selectStep(idx) {
    activeStepIndex = idx;
    document.querySelectorAll('.step-item').forEach((item, i) => {
        item.classList.toggle('active', (i + 1) === idx);
    });
    updateStepNavigation();
    drawActiveEPDArrows();
}

function changeStep(dir) {
    const nextIdx = activeStepIndex + dir;
    if (nextIdx >= 1 && nextIdx <= activeReaction.arrows.length) {
        selectStep(nextIdx);
    }
}

function updateStepNavigation() {
    const total = activeReaction.arrows.length;
    document.getElementById('step-indicator').innerText = `Step ${activeStepIndex} / ${total}`;
    document.getElementById('btn-prev').disabled = activeStepIndex <= 1;
    document.getElementById('btn-next').disabled = activeStepIndex >= total;
    document.getElementById('btn-play').disabled = total <= 1;
}

function stopPlayback() {
    const btn = document.getElementById('btn-play');
    if (playInterval) {
        clearInterval(playInterval);
        playInterval = null;
    }
    if (btn) btn.innerText = "▶";
}

function startPlayback({ userInitiated = false } = {}) {
    stopPlayback();
    if (!activeReaction || !Array.isArray(activeReaction.arrows) || activeReaction.arrows.length <= 1) return;
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const autoplayEnabled = document.getElementById('autoplay-toggle')?.checked !== false;
    if (!userInitiated && (reduceMotion || !autoplayEnabled)) return;
    const btn = document.getElementById('btn-play');
    if (btn) btn.innerText = "⏸";
    const speed = parseInt(document.getElementById('play-speed')?.value, 10) || 500;
    playInterval = setInterval(() => {
        const next = activeStepIndex < activeReaction.arrows.length
            ? activeStepIndex + 1
            : 1;
        selectStep(next);
    }, speed);
}

function changePlaybackSpeed() {
    const speed = document.getElementById('play-speed')?.value;
    if (speed) localStorage.setItem('synepd_play_speed', speed);
    if (playInterval) startPlayback({ userInitiated: true });
}

function setAutoplayPreference(enabled) {
    localStorage.setItem('synepd_autoplay', enabled ? 'on' : 'off');
    if (enabled) startPlayback({ userInitiated: true });
    else stopPlayback();
}

function togglePlay() {
    if (playInterval) stopPlayback();
    else startPlayback({ userInitiated: true });
}

function goHome({ updateHistory = true } = {}) {
    activeReaction = null;
    stopPlayback();
    document.title = 'SynEPD Explorer';
    
    const viewport = document.getElementById('graph-viewport');
    const svgEl = viewport.querySelector('svg');
    if (svgEl) svgEl.remove();

    if (typeof kgExitMode === 'function') kgExitMode();
    tmapExitMode();
    taxonomyOverviewExitMode();
    document.getElementById('welcome-panel').style.display = "block";
    document.getElementById('detail-panel').style.display = "none";
    document.getElementById('detail-fallback').style.display = "block";
    document.getElementById('graph-controls-panel').style.display = "none";
    document.getElementById('graph-legend').style.display = "none";
    if (updateHistory) history.pushState({}, '', window.location.pathname);
}

async function loadRandomReaction() {
    try {
        const res = await fetch(`${API_V1}/reactions/random`);
        const data = await res.json();
        if (data.reaction_id) {
            loadReaction(data.reaction_id);
        } else {
            showToast('No random reaction available', 'warning');
        }
    } catch (e) {
        showError("Failed to load a random reaction.");
    }
}

// Arrow types vocab cache (FE-03)
async function fetchArrowTypes() {
    try {
        const res = await fetch(`${API_V1}/arrow-types`);
        const data = await res.json();
        arrowTypeVocab = Object.fromEntries(data.map(t => [t.code, t]));
    } catch (e) {
        console.error("Failed to fetch arrow types:", e);
    }
}

// Database stats for dashboard (FE-05)
async function resolveMechanisticCenterCount(counts = {}) {
    const reported = Number(counts.mechanistic_centers);
    if (Number.isFinite(reported) && reported >= 0) return reported;

    // Compatibility with an older db-info/stats response that omitted MC.
    try {
        const res = await fetch(`${API_V1}/mechanistic-centers?limit=1`);
        if (res.ok) {
            const data = await res.json();
            const total = Number(data.total);
            if (Number.isFinite(total)) return total;
        }
    } catch (err) {
        console.warn('Failed to fetch the MC count directly:', err);
    }

    return 0;
}

async function fetchStats() {
    try {
        const res = await fetch(`${API_V1}/stats`);
        if (!res.ok) throw new Error(`Statistics request failed: ${res.status}`);
        const data = await res.json();
        const insightsSec = document.getElementById('db-insights-section');
        if (insightsSec) insightsSec.style.display = 'block';

        const totals = data.totals || {};
        const molecules = Number(totals.molecules || 0);
        if (molecules) animateCount('dash-molecules-val', molecules);

        const arrowTypeData = Object.entries(data.arrow_type_distribution || {})
            .map(([code, count]) => ({code, label: code, count: Number(count)}));
        renderArrowTypeMatrix('#arrow-type-chart', arrowTypeData, 'Electron-Flow Grammar');

        const arrowsPerReactionData = Object.entries(data.arrows_per_reaction_distribution || {})
            .map(([code, count]) => ({code, label: `${code} arrows`, count: Number(count)}))
            .sort((a, b) => Number(a.code) - Number(b.code));
        const arrowMedian = weightedQuantile(arrowsPerReactionData, 0.5);
        const arrowP95 = weightedQuantile(arrowsPerReactionData, 0.95);
        renderVerticalBarChart(
            '#arrows-per-reaction-chart',
            arrowsPerReactionData,
            'Arrows Per Reaction',
            {
                color: 'var(--series-1)',
                marker: arrowMedian,
                caption: `Median ${arrowMedian}; 95th percentile ${arrowP95}. Each bar opens the matching reactions.`,
                filterKind: 'arrow-count',
            }
        );

        renderMechanisticCenterComparison(
            '#mc-comparison-chart',
            data.mechanistic_center_comparison || {},
            'How MC Extends RC'
        );

        const rcReuseData = Object.entries(data.rc_reuse_distribution || {})
            .map(([code, count]) => ({code, label: `${code} reaction${Number(code) === 1 ? '' : 's'}`, count: Number(count)}))
            .sort((a, b) => Number(a.code) - Number(b.code));
        const templateTotal = d3.sum(rcReuseData, d => d.count);
        const reactionTotal = d3.sum(rcReuseData, d => Number(d.code) * d.count);
        const singleUse = rcReuseData.find(d => Number(d.code) === 1)?.count || 0;
        renderVerticalBarChart(
            '#rc-reuse-chart',
            rcReuseData,
            'RC Template Reuse',
            {
                color: 'var(--series-4)',
                logY: true,
                caption: `${formatPercent(singleUse, templateTotal)} of RC templates are used once; they cover ${formatPercent(singleUse, reactionTotal)} of reactions. Select a bin to inspect its templates.`,
                filterKind: 'rc-reuse',
            }
        );
    } catch (e) {
        console.error("Failed to fetch stats:", e);
        renderStatsError('Database insights could not be loaded. The server may be busy or this page may be stale.');
    }
}

const insightChartRegistry = new Map();

function prepareChart(selector, title, chartData, caption = '') {
    const container = d3.select(selector);
    container.selectAll("*").remove();
    container.attr("aria-label", title);

    const titleRow = container.append("div")
        .attr("class", "insight-title-row");
    titleRow.append("p")
        .attr("class", "insight-title")
        .text(title);
    const actions = titleRow.append("div")
        .attr("class", "insight-title-actions");
    actions.append("button")
        .attr("type", "button")
        .attr("class", "insight-action insight-table-toggle")
        .text("Table")
        .on("click", event => {
            event.stopPropagation();
            toggleInsightTable(selector);
        });
    actions.append("button")
        .attr("type", "button")
        .attr("class", "insight-action")
        .attr("title", "Download CSV data")
        .text("CSV")
        .on("click", event => {
            event.stopPropagation();
            downloadInsightCSV(selector);
        });
    actions.append("button")
        .attr("type", "button")
        .attr("class", "insight-action")
        .attr("title", "Download SVG figure")
        .text("SVG")
        .on("click", event => {
            event.stopPropagation();
            downloadInsightSVG(selector);
        });
    actions.append("button")
        .attr("type", "button")
        .attr("class", "insight-action")
        .attr("title", "Download 2x PNG figure")
        .text("PNG")
        .on("click", event => {
            event.stopPropagation();
            downloadInsightPNG(selector);
        });
    actions.append("button")
        .attr("type", "button")
        .attr("class", "insight-action")
        .text("Expand")
        .on("click", event => {
            event.stopPropagation();
            openChartModal(selector, title);
        });

    if (!chartData.length) {
        container.append("p")
            .attr("class", "insight-empty")
            .text("No data available");
        return null;
    }
    insightChartRegistry.set(selector, {title, rows: chartData, columns: null});
    if (caption) {
        container.append("p")
            .attr("class", "insight-caption insight-caption-pending")
            .text(caption);
    }
    return container;
}

function finishChart(selector, rows, columns, caption = '') {
    const container = document.querySelector(selector);
    if (!container) return;
    container.querySelector('.insight-caption-pending')?.remove();
    const tableWrap = document.createElement('div');
    tableWrap.className = 'insight-table-wrap';
    tableWrap.hidden = true;
    const table = document.createElement('table');
    table.className = 'insight-table';
    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');
    columns.forEach(column => {
        const th = document.createElement('th');
        th.textContent = column.label;
        headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    rows.forEach(row => {
        const tr = document.createElement('tr');
        columns.forEach(column => {
            const td = document.createElement('td');
            const value = column.format ? column.format(row[column.key], row) : row[column.key];
            td.textContent = value == null ? '' : String(value);
            tr.appendChild(td);
        });
        tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    tableWrap.appendChild(table);
    container.appendChild(tableWrap);
    if (caption) {
        const p = document.createElement('p');
        p.className = 'insight-caption';
        p.textContent = caption;
        container.appendChild(p);
    }
    const entry = insightChartRegistry.get(selector) || {};
    insightChartRegistry.set(selector, {...entry, rows, columns});
}

function toggleInsightTable(selectorOrRoot) {
    const root = typeof selectorOrRoot === 'string'
        ? document.querySelector(selectorOrRoot)
        : selectorOrRoot;
    if (!root) return;
    const table = root.querySelector('.insight-table-wrap');
    const visual = root.querySelector('.chart-visual');
    if (!table || !visual) return;
    const showTable = table.hidden;
    table.hidden = !showTable;
    visual.hidden = showTable;
    const button = root.querySelector('.insight-table-toggle');
    if (button) button.textContent = showTable ? 'Chart' : 'Table';
}

function renderInlineError(root, message, retryFn) {
    if (!root) return;
    root.replaceChildren();
    const box = document.createElement('div');
    box.className = 'insight-error';
    const text = document.createElement('span');
    text.textContent = message;
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'insight-action';
    retry.textContent = 'Retry';
    retry.addEventListener('click', retryFn);
    box.append(text, retry);
    root.appendChild(box);
}

function renderStatsError(message) {
    const section = document.getElementById('db-insights-section');
    if (section) section.style.display = 'block';
    [
        'arrow-type-chart', 'arrows-per-reaction-chart',
        'mc-comparison-chart', 'rc-reuse-chart',
    ].forEach(id => renderInlineError(document.getElementById(id), message, fetchStats));
}

function weightedQuantile(rows, quantile) {
    const total = d3.sum(rows, row => Number(row.count));
    const threshold = total * quantile;
    let cumulative = 0;
    for (const row of rows) {
        cumulative += Number(row.count);
        if (cumulative >= threshold) return Number(row.code);
    }
    return rows.length ? Number(rows[rows.length - 1].code) : 0;
}

function formatPercent(value, total) {
    return total ? `${(100 * Number(value) / Number(total)).toFixed(1)}%` : '0.0%';
}

function chartCaption(d, unit = 'items') {
    const datum = d?.data || d;
    const label = datum.detail ? `${datum.detail} · ${datum.label}` : (datum.label || datum.code);
    return `${label}: ${Number(datum.count).toLocaleString()} ${unit}`;
}

function attachChartTooltip(selection, formatter) {
    selection
        .attr("data-chart-caption", d => formatter(d))
        .attr("tabindex", 0)
        .on("mousemove", (event, d) => {
            event.stopPropagation();
            showChartTooltip(event.currentTarget.getAttribute("data-chart-caption") || formatter(d), event.clientX, event.clientY);
        })
        .on("focus", (event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            showChartTooltip(event.currentTarget.getAttribute("data-chart-caption"), rect.left + rect.width / 2, rect.top + rect.height / 2);
        })
        .on("blur", hideChartTooltip)
        .on("mouseleave", hideChartTooltip);
}

function attachStaticChartTooltips(root) {
    root.onmousemove = event => {
        const mark = event.target.closest?.("[data-chart-caption]");
        if (!mark || !root.contains(mark)) {
            hideChartTooltip();
            return;
        }
        showChartTooltip(mark.getAttribute("data-chart-caption"), event.clientX, event.clientY);
    };
    root.onmouseleave = hideChartTooltip;
    root.onfocusin = event => {
        const mark = event.target.closest?.("[data-chart-caption]");
        if (!mark) return;
        const rect = mark.getBoundingClientRect();
        showChartTooltip(mark.getAttribute("data-chart-caption"), rect.left + rect.width / 2, rect.top + rect.height / 2);
    };
    root.onfocusout = hideChartTooltip;
    root.onclick = event => {
        const mark = event.target.closest?.('[data-filter-kind]');
        if (!mark) return;
        event.stopPropagation();
        applyInsightFilter(
            mark.dataset.filterKind,
            mark.dataset.filterValue,
            mark.dataset.filterLabel || mark.dataset.chartCaption
        );
    };
    root.onkeydown = event => {
        if (!['Enter', ' '].includes(event.key)) return;
        const mark = event.target.closest?.('[data-filter-kind]');
        if (!mark) return;
        event.preventDefault();
        root.onclick({target: mark, stopPropagation() {}});
    };
}

function makeInsightMarksInteractive(selection, kind, valueFn, labelFn = valueFn) {
    selection
        .classed('insight-mark', true)
        .attr('data-filter-kind', kind)
        .attr('data-filter-value', valueFn)
        .attr('data-filter-label', labelFn)
        .attr('role', 'button')
        .on('click.insight-filter', (event, datum) => {
            event.stopPropagation();
            applyInsightFilter(kind, valueFn(datum), labelFn(datum));
        })
        .on('keydown.insight-filter', (event, datum) => {
            if (!['Enter', ' '].includes(event.key)) return;
            event.preventDefault();
            applyInsightFilter(kind, valueFn(datum), labelFn(datum));
        });
}

async function applyInsightFilter(kind, value, label = '') {
    if (kind === 'rc-reuse') {
        await showRcReuseTemplates(Number(value));
        return;
    }
    const endpoints = {
        'arrow-type': `${API_V1}/reactions/by-arrow-type?code=${encodeURIComponent(value)}&limit=100`,
        'arrow-count': `${API_V1}/reactions/by-arrow-count?n=${encodeURIComponent(value)}&limit=100`,
    };
    const endpoint = endpoints[kind];
    if (endpoint) await showInsightReactions(endpoint, label || String(value));
}

async function showInsightReactions(endpoint, label) {
    switchTab('search');
    const resultsContainer = document.getElementById('search-results');
    resultsContainer.innerHTML = '<p style="color:var(--text-secondary); text-align:center;">Loading filtered reactions…</p>';
    try {
        const res = await fetch(endpoint);
        if (!res.ok) throw new Error(`Filter request failed: ${res.status}`);
        const data = await res.json();
        const rows = Array.isArray(data.results) ? data.results : [];
        resultsContainer.replaceChildren();
        const meta = document.createElement('div');
        meta.className = 'search-meta';
        meta.textContent = `${label} · ${Number(data.total || rows.length).toLocaleString()} reactions`;
        resultsContainer.appendChild(meta);
        rows.forEach(rxn => resultsContainer.appendChild(makeReactionResultCard(rxn)));
        if (!rows.length) {
            const empty = document.createElement('p');
            empty.className = 'insight-empty';
            empty.textContent = 'No matching reactions found.';
            resultsContainer.appendChild(empty);
        }
    } catch (error) {
        renderInlineError(resultsContainer, 'Filtered reactions could not be loaded.', () => showInsightReactions(endpoint, label));
    }
}

function makeReactionResultCard(rxn) {
    const card = document.createElement('div');
    card.className = 'result-card';
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', `View details for reaction ${rxn.name || rxn.case_id}`);
    card.addEventListener('click', () => loadReaction(rxn.id));
    card.addEventListener('keydown', event => {
        if (!['Enter', ' '].includes(event.key)) return;
        event.preventDefault();
        card.click();
    });
    const heading = document.createElement('h4');
    heading.textContent = rxn.name || rxn.case_id;
    const caseId = document.createElement('p');
    caseId.textContent = rxn.case_id || '';
    const smiles = document.createElement('p');
    smiles.className = 'result-card-smiles';
    smiles.textContent = rxn.canonical_rsmi || '';
    card.append(heading, caseId, smiles);
    return card;
}

async function showRcReuseTemplates(reuseCount) {
    const modal = document.getElementById('chart-modal');
    const body = document.getElementById('chart-modal-body');
    const heading = document.getElementById('chart-modal-title');
    if (!modal || !body || !heading) return;
    heading.textContent = `RC templates used by ${reuseCount} reaction${reuseCount === 1 ? '' : 's'}`;
    body.innerHTML = '<p class="insight-empty">Loading templates…</p>';
    modal.classList.add('show');
    document.body.classList.add('modal-open');
    try {
        const res = await fetch(`${API_V1}/reaction-centers?limit=2000`);
        if (!res.ok) throw new Error(`Template request failed: ${res.status}`);
        const data = await res.json();
        const rows = (data.results || []).filter(row => Number(row.reaction_count) === reuseCount);
        body.replaceChildren();
        const wrap = document.createElement('div');
        wrap.className = 'insight-table-wrap';
        const table = document.createElement('table');
        table.className = 'insight-table';
        table.innerHTML = '<thead><tr><th>RC ID</th><th>WL hash</th><th>SMARTS</th><th>Reactions</th></tr></thead>';
        const tbody = document.createElement('tbody');
        rows.forEach(row => {
            const tr = document.createElement('tr');
            [row.id, row.wlhash, row.smarts || '—', row.reaction_count].forEach(value => {
                const td = document.createElement('td');
                td.textContent = String(value);
                tr.appendChild(td);
            });
            tr.className = 'insight-mark';
            tr.tabIndex = 0;
            tr.title = `Show reactions using RC ${row.id}`;
            const open = () => showInsightReactions(`${API_V1}/reaction-centers/${row.id}/reactions?limit=100`, `RC ${row.id}`);
            tr.addEventListener('click', open);
            tr.addEventListener('keydown', event => {
                if (!['Enter', ' '].includes(event.key)) return;
                event.preventDefault();
                open();
            });
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        wrap.appendChild(table);
        body.appendChild(wrap);
    } catch (error) {
        renderInlineError(body, 'RC templates could not be loaded.', () => showRcReuseTemplates(reuseCount));
    }
}

function showChartTooltip(text, x, y) {
    const tooltip = document.getElementById('chart-tooltip');
    if (!tooltip) return;
    tooltip.textContent = text;
    tooltip.classList.add('show');
    const left = Math.max(12, Math.min(x + 14, window.innerWidth - tooltip.offsetWidth - 12));
    const top = Math.max(12, Math.min(y + 14, window.innerHeight - tooltip.offsetHeight - 12));
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
}

function hideChartTooltip() {
    const tooltip = document.getElementById('chart-tooltip');
    if (tooltip) tooltip.classList.remove('show');
}

function openChartModal(selector, title) {
    const source = document.querySelector(selector);
    const modal = document.getElementById('chart-modal');
    const body = document.getElementById('chart-modal-body');
    const heading = document.getElementById('chart-modal-title');
    const tableToggle = document.getElementById('chart-modal-table-toggle');
    if (!source || !modal || !body || !heading) return;
    heading.textContent = title;
    body.innerHTML = source.innerHTML;
    const idMap = new Map();
    body.querySelectorAll('[id]').forEach((el, idx) => {
        const oldId = el.id;
        const newId = `fullscreen-chart-${idx}-${oldId}`;
        idMap.set(oldId, newId);
        el.id = newId;
    });
    body.querySelectorAll('*').forEach(el => {
        for (const attr of el.getAttributeNames()) {
            let value = el.getAttribute(attr);
            if (!value) continue;
            idMap.forEach((newId, oldId) => {
                value = value.replaceAll(`url(#${oldId})`, `url(#${newId})`);
                if (value === `#${oldId}`) value = `#${newId}`;
            });
            el.setAttribute(attr, value);
        }
    });
    body.querySelectorAll("[data-chart-caption]").forEach(el => {
        el.setAttribute("tabindex", "0");
    });
    attachStaticChartTooltips(body);
    if (tableToggle) {
        const hasTable = Boolean(body.querySelector('.insight-table-wrap'));
        tableToggle.hidden = !hasTable;
        tableToggle.textContent = 'Table view';
        tableToggle.onclick = event => {
            event.stopPropagation();
            toggleInsightTable(body);
            tableToggle.textContent = body.querySelector('.insight-table-wrap')?.hidden
                ? 'Table view'
                : 'Chart view';
        };
    }
    modal.classList.add('show');
    document.body.classList.add('modal-open');
}

function closeChartModal() {
    const modal = document.getElementById('chart-modal');
    if (!modal) return;
    modal.classList.remove('show');
    if (!document.getElementById('sketch-modal')?.classList.contains('show')) {
        document.body.classList.remove('modal-open');
    }
}

function openSchemaModal() {
    const modal = document.getElementById('schema-modal');
    if (!modal) return;
    modal.classList.add('show');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
}

function closeSchemaModal() {
    const modal = document.getElementById('schema-modal');
    if (!modal) return;
    modal.classList.remove('show');
    modal.setAttribute('aria-hidden', 'true');
    if (!document.getElementById('sketch-modal')?.classList.contains('show') &&
        !document.getElementById('chart-modal')?.classList.contains('show')) {
        document.body.classList.remove('modal-open');
    }
}

function chartSequentialColors() {
    return Array.from({length: 7}, (_, index) => `var(--sequential-${index + 1})`);
}

function insightFilename(title, extension) {
    const stem = String(title || 'synepd-insight')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-|-$/g, '');
    return `${stem || 'synepd-insight'}.${extension}`;
}

function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
}

function standaloneInsightSVG(selector) {
    const source = document.querySelector(`${selector} svg.chart-visual`);
    if (!source) return null;
    const clone = source.cloneNode(true);
    const originals = [source, ...source.querySelectorAll('*')];
    const copies = [clone, ...clone.querySelectorAll('*')];
    const properties = [
        'fill', 'stroke', 'stroke-width', 'stroke-dasharray', 'opacity',
        'font-family', 'font-size', 'font-weight', 'paint-order',
    ];
    originals.forEach((element, index) => {
        const computed = getComputedStyle(element);
        properties.forEach(property => {
            const value = computed.getPropertyValue(property);
            if (value) copies[index].style.setProperty(property, value);
        });
    });
    const viewBox = source.viewBox?.baseVal;
    const width = viewBox?.width || source.clientWidth || 800;
    const height = viewBox?.height || source.clientHeight || 500;
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    clone.setAttribute('width', width);
    clone.setAttribute('height', height);
    const background = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    background.setAttribute('x', '0');
    background.setAttribute('y', '0');
    background.setAttribute('width', width);
    background.setAttribute('height', height);
    background.setAttribute('fill', getComputedStyle(document.documentElement).getPropertyValue('--bg-primary').trim() || '#070a13');
    clone.insertBefore(background, clone.firstChild);
    return {
        xml: new XMLSerializer().serializeToString(clone),
        width,
        height,
    };
}

function downloadInsightCSV(selector) {
    const entry = insightChartRegistry.get(selector);
    if (!entry?.columns || !entry.rows) return;
    const escapeCell = value => `"${String(value ?? '').replaceAll('"', '""')}"`;
    const lines = [entry.columns.map(column => escapeCell(column.label)).join(',')];
    entry.rows.forEach(row => {
        lines.push(entry.columns.map(column => {
            const value = column.format ? column.format(row[column.key], row) : row[column.key];
            return escapeCell(value);
        }).join(','));
    });
    downloadBlob(
        new Blob([lines.join('\n')], {type: 'text/csv;charset=utf-8'}),
        insightFilename(entry.title, 'csv')
    );
}

function downloadInsightSVG(selector) {
    const entry = insightChartRegistry.get(selector);
    const exportData = standaloneInsightSVG(selector);
    if (!entry || !exportData) return;
    downloadBlob(
        new Blob([exportData.xml], {type: 'image/svg+xml'}),
        insightFilename(entry.title, 'svg')
    );
}

function downloadInsightPNG(selector) {
    const entry = insightChartRegistry.get(selector);
    const exportData = standaloneInsightSVG(selector);
    if (!entry || !exportData) return;
    const blob = new Blob([exportData.xml], {type: 'image/svg+xml'});
    const url = URL.createObjectURL(blob);
    const image = new Image();
    image.onload = () => {
        const canvas = document.createElement('canvas');
        canvas.width = Math.ceil(exportData.width * 2);
        canvas.height = Math.ceil(exportData.height * 2);
        const context = canvas.getContext('2d');
        context.scale(2, 2);
        context.drawImage(image, 0, 0, exportData.width, exportData.height);
        URL.revokeObjectURL(url);
        canvas.toBlob(png => {
            if (png) downloadBlob(png, insightFilename(entry.title, 'png'));
        }, 'image/png');
    };
    image.onerror = () => {
        URL.revokeObjectURL(url);
        showToast('Chart PNG export failed', 'error');
    };
    image.src = url;
}

function renderVerticalBarChart(selector, chartData, title, options = {}) {
    const container = prepareChart(selector, title, chartData, options.caption);
    if (!container) return;
    const margin = {top: 24, right: 14, bottom: 38, left: 50};
    const frameWidth = selector === '#rc-reuse-chart' ? 720 : 460;
    const frameHeight = 250;
    const width = frameWidth - margin.left - margin.right;
    const height = frameHeight - margin.top - margin.bottom;
    const svgEl = container.append("svg")
        .attr("class", "chart-svg chart-vertical chart-visual")
        .attr("width", "100%")
        .attr("height", frameHeight)
        .attr("viewBox", `0 0 ${frameWidth} ${frameHeight}`)
        .attr("role", "img")
        .attr("aria-label", title);
    const svg = svgEl.append("g").attr("transform", `translate(${margin.left},${margin.top})`);
    const x = d3.scaleBand()
        .domain(chartData.map(d => d.code))
        .range([0, width])
        .padding(0.22);
    const maxCount = d3.max(chartData, d => d.count) || 1;
    const y = options.logY
        ? d3.scaleLog().domain([1, maxCount]).range([height, 0])
        : d3.scaleLinear().domain([0, maxCount]).nice().range([height, 0]);

    svg.append("g")
        .attr("class", "chart-axis")
        .attr("transform", `translate(0,${height})`)
        .call(d3.axisBottom(x).tickValues(
            chartData.map(d => d.code).filter((_, i) => selector === '#rc-reuse-chart' || i % 2 === 0)
        ).tickSizeOuter(0));
    svg.append("g")
        .attr("class", "chart-axis")
        .call(
            options.logY
                ? d3.axisLeft(y).ticks(4, '~s').tickSize(-width)
                : d3.axisLeft(y).ticks(4).tickSize(-width)
        );

    const bars = svg.append("g")
        .selectAll("rect")
        .data(chartData)
        .join("rect")
        .attr("x", d => x(d.code))
        .attr("y", height)
        .attr("width", x.bandwidth())
        .attr("height", 0)
        .attr("rx", 3)
        .attr("fill", options.color || 'var(--series-1)');
    attachChartTooltip(bars, d => chartCaption(d, 'reactions'));
    if (options.filterKind) {
        makeInsightMarksInteractive(bars, options.filterKind, d => d.code, d => d.label);
    }
    const renderedBarHeight = d => Math.max(options.logY ? 3 : 0, height - y(Math.max(1, d.count)));
    const setBarGeometry = selection => selection
        .attr("y", d => height - renderedBarHeight(d))
        .attr("height", renderedBarHeight);
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        setBarGeometry(bars);
    } else {
        setBarGeometry(bars.transition().duration(500));
    }

    if (Number.isFinite(options.marker)) {
        const markerX = x(String(options.marker)) + x.bandwidth() / 2;
        svg.append('line')
            .attr('class', 'chart-median-line')
            .attr('x1', markerX)
            .attr('x2', markerX)
            .attr('y1', 0)
            .attr('y2', height);
        svg.append('text')
            .attr('class', 'chart-median-label')
            .attr('x', markerX + 5)
            .attr('y', 9)
            .text(`median ${options.marker}`);
    }
    finishChart(
        selector,
        chartData,
        [
            {key: 'label', label: title === 'RC Template Reuse' ? 'Reuse' : 'Arrow count'},
            {key: 'count', label: title === 'RC Template Reuse' ? 'RC templates' : 'Reactions', format: value => Number(value).toLocaleString()},
        ],
        options.caption
    );
}

function parseArrowFlow(code) {
    const match = String(code).match(/^(LP|Pi|Sigma)-\/(LP|Pi|Sigma)\+$/i);
    if (!match) return null;
    const normalize = value => ({lp: 'LP', pi: 'PI', sigma: 'SIGMA'})[value.toLowerCase()];
    return {source: normalize(match[1]), target: normalize(match[2])};
}

function renderArrowTypeMatrix(selector, chartData, title) {
    const types = ['LP', 'PI', 'SIGMA'];
    const lookup = new Map();
    chartData.forEach(row => {
        const flow = parseArrowFlow(row.code);
        if (flow) lookup.set(`${flow.source}:${flow.target}`, row);
    });
    const cells = types.flatMap(source => types.map(target => {
        const row = lookup.get(`${source}:${target}`);
        return {
            source,
            target,
            code: row?.code || `${source}-/${target}+`,
            label: `${source} → ${target}`,
            count: Number(row?.count || 0),
        };
    }));
    const caption = 'Lone-pair → σ* dominates; σ → π is rarest, and the absent LP → LP cell is itself part of the mechanistic grammar.';
    const container = prepareChart(selector, title, cells, caption);
    if (!container) return;

    const frameWidth = 760;
    const frameHeight = 350;
    const originX = 126;
    const originY = 54;
    const cellWidth = 150;
    const cellHeight = 72;
    const maxCount = d3.max(cells, d => d.count) || 1;
    const sequential = chartSequentialColors();
    const color = d3.scaleQuantize().domain([1, maxCount]).range(sequential);
    const svg = container.append('svg')
        .attr('class', 'chart-svg chart-heatmap chart-visual')
        .attr('width', '100%')
        .attr('height', frameHeight)
        .attr('viewBox', `0 0 ${frameWidth} ${frameHeight}`)
        .attr('role', 'img')
        .attr('aria-label', `${title}: source electron domain by target acceptor domain`);

    svg.append('text')
        .attr('class', 'chart-axis-label')
        .attr('x', originX + cellWidth * 1.5)
        .attr('y', 16)
        .attr('text-anchor', 'middle')
        .text('TARGET ACCEPTOR');
    svg.append('text')
        .attr('class', 'chart-axis-label')
        .attr('transform', `translate(18,${originY + cellHeight * 1.5}) rotate(-90)`)
        .attr('text-anchor', 'middle')
        .text('SOURCE ELECTRON DOMAIN');

    svg.selectAll('.matrix-column-label')
        .data(types)
        .join('text')
        .attr('class', 'chart-label matrix-column-label')
        .attr('x', (_, index) => originX + index * cellWidth + cellWidth / 2)
        .attr('y', originY - 12)
        .attr('text-anchor', 'middle')
        .text(value => value);
    svg.selectAll('.matrix-row-label')
        .data(types)
        .join('text')
        .attr('class', 'chart-label matrix-row-label')
        .attr('x', originX - 12)
        .attr('y', (_, index) => originY + index * cellHeight + cellHeight / 2)
        .attr('dy', '.35em')
        .attr('text-anchor', 'end')
        .text(value => value);

    const cellGroups = svg.append('g')
        .selectAll('g')
        .data(cells)
        .join('g')
        .attr('transform', d => `translate(${originX + types.indexOf(d.target) * cellWidth},${originY + types.indexOf(d.source) * cellHeight})`);
    const rects = cellGroups.append('rect')
        .attr('width', cellWidth - 7)
        .attr('height', cellHeight - 7)
        .attr('rx', 6)
        .attr('fill', d => d.count ? color(d.count) : 'var(--bg-tertiary)')
        .attr('stroke', d => d.count ? 'transparent' : 'var(--border)')
        .attr('stroke-dasharray', d => d.count ? null : '4 3');
    attachChartTooltip(rects, d => d.count
        ? `${d.label}: ${d.count.toLocaleString()} arrows`
        : `${d.label}: absent from the vocabulary`);
    makeInsightMarksInteractive(
        rects.filter(d => d.count > 0),
        'arrow-type',
        d => d.code,
        d => `${d.label} · ${d.code}`
    );
    cellGroups.append('text')
        .attr('class', 'matrix-count')
        .attr('x', (cellWidth - 7) / 2)
        .attr('y', (cellHeight - 7) / 2)
        .attr('dy', '.18em')
        .attr('text-anchor', 'middle')
        .attr('fill', d => !d.count ? 'var(--text-secondary)' : (d.count > maxCount * 0.42 ? '#ffffff' : '#0f172a'))
        .text(d => d.count ? d.count.toLocaleString() : 'absent');

    const rowTotals = types.map(source => d3.sum(cells.filter(d => d.source === source), d => d.count));
    const columnTotals = types.map(target => d3.sum(cells.filter(d => d.target === target), d => d.count));
    svg.append('text').attr('class', 'chart-axis-label').attr('x', originX + cellWidth * 3 + 16).attr('y', originY - 12).text('TOTAL');
    svg.selectAll('.matrix-row-total')
        .data(rowTotals)
        .join('text')
        .attr('class', 'chart-value matrix-row-total')
        .attr('x', originX + cellWidth * 3 + 16)
        .attr('y', (_, index) => originY + index * cellHeight + cellHeight / 2)
        .attr('dy', '.35em')
        .text(value => value.toLocaleString());
    svg.selectAll('.matrix-column-total')
        .data(columnTotals)
        .join('text')
        .attr('class', 'chart-value matrix-column-total')
        .attr('x', (_, index) => originX + index * cellWidth + cellWidth / 2)
        .attr('y', originY + cellHeight * 3 + 11)
        .attr('text-anchor', 'middle')
        .text(value => value.toLocaleString());

    finishChart(
        selector,
        cells,
        [
            {key: 'source', label: 'Source'},
            {key: 'target', label: 'Target'},
            {key: 'code', label: 'Arrow type'},
            {key: 'count', label: 'Arrows', format: value => Number(value).toLocaleString()},
        ],
        caption
    );
}

function renderMechanisticCenterComparison(selector, comparison, title) {
    const total = Number(comparison.template_total || 0);
    const rows = [
        {
            code: 'EPD-enriched',
            label: 'EPD-enriched MC',
            count: Number(comparison.epd_enriched_template_count || 0),
            percent: Number(comparison.epd_enriched_template_percent || 0),
            color: 'var(--sequential-5)',
        },
        {
            code: 'Structural extension',
            label: 'Structurally extends RC',
            count: Number(comparison.structurally_extended_template_count || 0),
            percent: Number(comparison.structurally_extended_template_percent || 0),
            color: 'var(--sequential-7)',
        },
    ];
    const caption = comparison.definition || 'MC templates add EPD transition context to their parent RC templates.';
    const container = prepareChart(selector, title, rows, caption);
    if (!container) return;
    const width = 560;
    const height = 205;
    const trackX = 178;
    const trackWidth = 330;
    const svg = container.append('svg')
        .attr('class', 'chart-svg chart-meter chart-visual')
        .attr('viewBox', `0 0 ${width} ${height}`)
        .attr('role', 'img')
        .attr('aria-label', title);
    const groups = svg.selectAll('g.meter-row')
        .data(rows)
        .join('g')
        .attr('class', 'meter-row')
        .attr('transform', (_, index) => `translate(0,${42 + index * 78})`);
    groups.append('text')
        .attr('class', 'chart-label')
        .attr('x', 8)
        .attr('y', 12)
        .text(d => d.label);
    groups.append('rect')
        .attr('x', trackX)
        .attr('y', -3)
        .attr('width', trackWidth)
        .attr('height', 22)
        .attr('rx', 11)
        .attr('fill', 'var(--bg-tertiary)');
    const meters = groups.append('rect')
        .attr('x', trackX)
        .attr('y', -3)
        .attr('width', d => trackWidth * d.percent / 100)
        .attr('height', 22)
        .attr('rx', 11)
        .attr('fill', d => d.color);
    attachChartTooltip(meters, d => `${d.label}: ${d.count.toLocaleString()} of ${total.toLocaleString()} templates (${d.percent.toFixed(2)}%)`);
    groups.append('text')
        .attr('class', 'chart-value')
        .attr('x', trackX + trackWidth + 10)
        .attr('y', 12)
        .text(d => `${d.percent.toFixed(1)}%`);
    groups.append('text')
        .attr('class', 'meter-detail')
        .attr('x', trackX)
        .attr('y', 38)
        .text(d => `${d.count.toLocaleString()} of ${total.toLocaleString()} MC templates`);
    finishChart(
        selector,
        rows,
        [
            {key: 'label', label: 'Comparison'},
            {key: 'count', label: 'MC templates', format: value => Number(value).toLocaleString()},
            {key: 'percent', label: 'Percent', format: value => `${Number(value).toFixed(2)}%`},
        ],
        caption
    );
}

// Recently viewed reactions history (FE-07)
function addToHistory(reaction) {
    if (!reaction || !reaction.id) return;
    let historyData = JSON.parse(localStorage.getItem('synepd_history') || '[]');
    historyData = historyData.filter(h => h.id !== reaction.id);
    historyData.unshift({
        id: reaction.id,
        case_id: reaction.case_id,
        name: reaction.name,
        taxonomy: reaction.taxonomy,
        ts: Date.now()
    });
    historyData = historyData.slice(0, MAX_HISTORY);
    localStorage.setItem('synepd_history', JSON.stringify(historyData));
    renderHistory();
}

function renderHistory() {
    const historyData = JSON.parse(localStorage.getItem('synepd_history') || '[]');
    const container = document.getElementById('history-list');
    if (!container) return;
    if (!historyData.length) {
        container.innerHTML = '<p style="color:var(--text-secondary); font-size:0.8rem;">No recently viewed reactions.</p>';
        return;
    }
    container.innerHTML = historyData.map(h => {
        const taxCode = h.taxonomy ? (typeof h.taxonomy === 'string' ? h.taxonomy : h.taxonomy.code) : '';
        const safeId = Number(h.id);
        const safeName = escapeHtml(h.name || h.case_id || '');
        const safeTaxCode = escapeHtml(taxCode);
        return `
            <div class="result-card" onclick="loadReaction(${safeId})" style="padding:0.5rem 0.75rem; cursor:pointer;" tabindex="0" role="button" aria-label="View ${safeName}">
                <div style="display:flex; justify-content:space-between;">
                    <span style="font-size:0.82rem; font-weight:600;">${safeName}</span>
                    <span style="font-size:0.68rem; color:var(--text-secondary);">
                        ${new Date(h.ts).toLocaleTimeString()}
                    </span>
                </div>
                ${safeTaxCode ? `<span style="font-size:0.7rem; color:var(--accent-purple);">${safeTaxCode}</span>` : ''}
            </div>
        `;
    }).join('');
    // Attach event listeners for history cards
    container.querySelectorAll('.result-card').forEach(card => {
        card.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); card.click(); }
        });
    });
}

// Shareable Link Copying (FE-11)
function copyReactionLink() {
    if (!activeReaction || !activeReaction.id) return;
    const url = `${window.location.origin}${window.location.pathname}#reaction/${activeReaction.id}`;
    navigator.clipboard.writeText(url).then(() => {
        showToast('Link copied to clipboard', 'success');
    }).catch(() => {
        showError('Failed to copy link');
    });
}

// CSV download of arrows EPD table (FE-15)
function downloadCSV() {
    if (!activeReaction) return;
    const rows = [
        ['arrow_index', 'arrow_type_code', 'source_atoms', 'target_atoms'],
        ...activeReaction.arrows.map(a => [
            a.arrow_index,
            a.arrow_type_code,
            `"[${a.source_atoms.join(',')}]"`,
            `"[${a.target_atoms.join(',')}]"`,
        ])
    ];
    const csv = rows.map(r => r.join(',')).join('\n');
    const blob = new Blob([csv], {type: 'text/csv'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${activeReaction.case_id || 'reaction'}_epd.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// Compute change summary (FE-10)
function bondKey(a, b) {
    return [Number(a), Number(b)].sort((x, y) => x - y).join('-');
}

function endpointId(endpoint) {
    return typeof endpoint === 'object' ? endpoint.id : endpoint;
}

function markTransitionBonds(graphData, arrows) {
    if (!graphData?.links) return;

    const transitionKeys = new Set();
    (arrows || []).forEach(arr => {
        [arr.source_atoms, arr.target_atoms].forEach(atoms => {
            if (atoms?.length === 2) {
                transitionKeys.add(bondKey(atoms[0], atoms[1]));
            }
        });
    });

    graphData.links.forEach(link => {
        const originalStatus = link._origStatus ?? link.original_status ?? link.status;
        link._origStatus = originalStatus;
        link.status = originalStatus;
        if (originalStatus !== 'unchanged') {
            return;
        }

        const key = bondKey(endpointId(link.source), endpointId(link.target));
        link.status = transitionKeys.has(key) ? 'transition' : 'unchanged';
    });
}

function computeChangeSummary(graphData) {
    if (!graphData || !graphData.links) return {breaking: 0, forming: 0, transition: 0, changedAtomIds: new Set()};
    const breaking = graphData.links.filter(l => l.status === 'breaking');
    const forming  = graphData.links.filter(l => l.status === 'forming');
    const transition = graphData.links.filter(l => l.status === 'transition');
    const changedAtomIds = new Set([
        ...breaking.flatMap(l => [l.source.id || l.source, l.target.id || l.target]),
        ...forming.flatMap(l  => [l.source.id || l.source, l.target.id || l.target]),
        ...transition.flatMap(l => [l.source.id || l.source, l.target.id || l.target]),
    ]);
    return {
        breaking: breaking.length,
        forming: forming.length,
        transition: transition.length,
        changedAtomIds
    };
}

function renderChangeSummary(summary) {
    const el = document.getElementById('change-summary');
    if (!el) return;
    el.innerHTML = [
        summary.breaking ? `<span class="change-badge breaking">${summary.breaking} breaking</span>` : '',
        summary.forming  ? `<span class="change-badge forming">${summary.forming} forming</span>`   : '',
        summary.transition ? `<span class="change-badge transition">${summary.transition} transition</span>` : '',
    ].join('');
}

// Theme toggler and structure update (FE-14)
function toggleTheme() {
    const useLight = !document.body.classList.contains('light-theme');
    document.body.classList.toggle('light-theme', useLight);
    document.documentElement.classList.toggle('light-theme', useLight);
    localStorage.setItem('synepd_theme', useLight ? 'light' : 'dark');
    renderReactionDepict();
}

// Similar reactions loading (FE-01)
async function loadNeighbors(reactionId) {
    const container = document.getElementById('neighbors-list');
    try {
        const res = await fetch(`${API_V1}/reactions/${reactionId}/neighbors?limit=6`);
        if (!res.ok) throw new Error(`Neighbor request failed: ${res.status}`);
        const data = await res.json();
        if (!container) return;
        container.innerHTML = '';
        if (!data.neighbors?.length) {
            container.innerHTML = '<p style="color:var(--text-secondary); font-size:0.82rem;">No similar reactions found.</p>';
            return;
        }
        data.neighbors.forEach(n => {
            const card = document.createElement('div');
            card.className = 'result-card';
            card.style.padding = '0.6rem 0.75rem';
            card.style.cursor = 'pointer';
            
            // FE-17 accessibility tags
            card.setAttribute('tabindex', '0');
            card.setAttribute('role', 'button');
            card.setAttribute('aria-label', `View details for similar reaction ${n.name || n.case_id}`);
            card.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); card.click(); }
            });
            
            card.onclick = () => loadReaction(n.id);
            card.innerHTML = `
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-size:0.82rem; font-weight:600;">${escapeHtml(n.name || n.case_id)}</span>
                    ${n.taxonomy ? `<span style="font-size:0.7rem; color:var(--accent-cyan);">${escapeHtml(n.taxonomy)}</span>` : ''}
                </div>
                <p style="font-size:0.75rem; font-family:'JetBrains Mono',monospace; margin-top:3px; word-break: break-all; color: var(--text-secondary);">${escapeHtml((n.canonical_rsmi||'').slice(0,60))}…</p>
            `;
            container.appendChild(card);
        });
    } catch (e) {
        console.error("Failed to load neighbors:", e);
        renderInlineError(container, 'Similar reactions could not be loaded.', () => loadNeighbors(reactionId));
    }
}

function animateCount(elementId, target) {
    const el = document.getElementById(elementId);
    if (!el) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        el.innerText = Number(target).toLocaleString();
        return;
    }
    const duration = 900;
    const start = performance.now();
    function step(now) {
        const t = Math.min((now - start) / duration, 1);
        const eased = 1 - Math.pow(1 - t, 3);
        el.innerText = Math.round(eased * target).toLocaleString();
        if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
}

async function fetchDbInfo() {
    ['dash-reactions-val','dash-templates-val','dash-mechanistic-centers-val','dash-arrows-val','dash-taxons-val','dash-molecules-val'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerText = '—';
    });
    try {
        const res = await fetch(`${API_V1}/db-info`);
        if (!res.ok) throw new Error(`Database metadata request failed: ${res.status}`);
        const data = await res.json();
        document.getElementById('db-info-error')?.remove();
        const setText = (id, text) => {
            const el = document.getElementById(id);
            if (el) el.innerText = text;
        };
        setText('db-version-val', data.version);
        setText('db-release-date-val', data.release_date);
        setText('db-license-val', data.license);
        setText('db-engine-val', data.backend);
        setText('dash-db-version', data.version);
        setText('dash-db-updated', data.last_update || data.release_date);
        animateCount('dash-reactions-val', data.counts.reactions);
        animateCount('dash-templates-val', data.counts.reaction_centers || 0);
        const mechanisticCenterCount = await resolveMechanisticCenterCount(data.counts);
        animateCount('dash-mechanistic-centers-val', mechanisticCenterCount);
        animateCount('dash-arrows-val', data.counts.epd_arrows || 0);
        animateCount('dash-taxons-val', data.counts.taxons);
        animateCount('dash-molecules-val', data.counts.molecules || 0);
    } catch (err) {
        console.error("Failed to load db info:", err);
        const grid = document.querySelector('.dashboard-grid');
        if (grid && !document.getElementById('db-info-error')) {
            const error = document.createElement('div');
            error.id = 'db-info-error';
            error.className = 'dashboard-inline-error';
            renderInlineError(error, 'Database summary could not be loaded.', fetchDbInfo);
            grid.appendChild(error);
        }
    }
}

let submitType = 'reaction';
let submitBalanceDebounce = null;

function openSubmitPanel() {
    const overlay = document.getElementById('submit-panel-overlay');
    const panel = document.getElementById('submit-panel');
    if (!overlay || !panel) return;
    overlay.classList.add('show');
    overlay.setAttribute('aria-hidden', 'false');
    panel.classList.add('show');
    panel.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    clearSubmitFeedback();

    if (activeReaction?.canonical_rsmi) {
        const rxn = document.getElementById('submit-rxn');
        if (rxn && !rxn.value.trim()) {
            rxn.value = activeReaction.canonical_rsmi;
            onSubmitRxnInput();
        }
    }
    if (activeReaction?.name || activeReaction?.case_id) {
        const label = document.getElementById('submit-label');
        if (label && !label.value.trim()) {
            label.value = activeReaction.name || activeReaction.case_id;
        }
    }
    setTimeout(() => document.getElementById('submit-label')?.focus(), 40);
}

function closeSubmitPanel() {
    const overlay = document.getElementById('submit-panel-overlay');
    const panel = document.getElementById('submit-panel');
    if (!overlay || !panel) return;
    overlay.classList.remove('show');
    overlay.setAttribute('aria-hidden', 'true');
    panel.classList.remove('show');
    panel.setAttribute('aria-hidden', 'true');
    if (!document.getElementById('sketch-modal')?.classList.contains('show')) {
        document.body.classList.remove('modal-open');
    }
}

function setSubmitType(type) {
    submitType = type === 'issue' ? 'issue' : 'reaction';
    const isReaction = submitType === 'reaction';
    document.getElementById('submit-type-reaction')?.classList.toggle('active', isReaction);
    document.getElementById('submit-type-issue')?.classList.toggle('active', !isReaction);
    const rxnField = document.getElementById('submit-rxn-field');
    const epdField = document.getElementById('submit-epd-field');
    if (rxnField) rxnField.style.display = isReaction ? '' : 'none';
    if (epdField) epdField.style.display = isReaction ? '' : 'none';
    const labelHint = document.getElementById('submit-label-hint');
    const noteHint = document.getElementById('submit-note-hint');
    const label = document.getElementById('submit-label');
    if (labelHint) labelHint.innerText = isReaction ? 'Reaction name / label' : 'Issue title';
    if (noteHint) noteHint.innerText = isReaction ? 'Additional notes' : 'Describe the issue';
    if (label) {
        label.placeholder = isReaction
            ? 'e.g. Acyl substitution example'
            : 'e.g. Wrong EPD arrow direction for polar06_123';
    }
    clearSubmitFeedback();
}

function onSubmitRxnInput() {
    const field = document.getElementById('submit-rxn');
    const status = document.getElementById('submit-balance-status');
    if (!field || !status) return;
    const val = field.value.trim();
    clearTimeout(submitBalanceDebounce);
    status.innerHTML = '';
    if (!val.includes('>>')) return;

    submitBalanceDebounce = setTimeout(async () => {
        try {
            const res = await fetch(`${API_V1}/check-balance`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({rsmi: val})
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.message || data.detail || 'Balance check failed');
            if (data.balanced) {
                status.innerHTML = `<span style="color:var(--accent-green);">Balanced (${data.reactant_atom_count} atoms, charge ${data.reactant_formal_charge})</span>`;
            } else {
                const errors = data.errors ? data.errors.map(escapeHtml).join(' · ') : 'atom/charge discrepancy';
                status.innerHTML = `<span style="color:var(--accent-orange);">Imbalance: ${errors}</span>`;
            }
        } catch (e) {
            status.innerHTML = `<span style="color:var(--accent-orange);">${escapeHtml(e.message || 'Could not check balance')}</span>`;
        }
    }, 600);
}

function clearSubmitFeedback() {
    const feedback = document.getElementById('submit-feedback');
    if (!feedback) return;
    feedback.className = 'submit-feedback';
    feedback.innerHTML = '';
}

function showSubmitFeedback(message, type = 'info') {
    const feedback = document.getElementById('submit-feedback');
    if (!feedback) return;
    feedback.className = `submit-feedback show ${type}`;
    feedback.innerHTML = escapeHtml(message);
}

async function sendSubmission() {
    const labelEl = document.getElementById('submit-label');
    const rxnEl = document.getElementById('submit-rxn');
    const epdEl = document.getElementById('submit-epd');
    const noteEl = document.getElementById('submit-note');
    const button = document.getElementById('submit-send-btn');
    const label = labelEl?.value.trim() || '';
    const rsmi = submitType === 'reaction' ? (rxnEl?.value.trim() || '') : '';
    const epd = submitType === 'reaction' ? (epdEl?.value.trim() || '') : '';
    const note = noteEl?.value.trim() || '';

    if (!label) {
        showSubmitFeedback(submitType === 'reaction' ? 'Please provide a reaction label.' : 'Please provide an issue title.', 'error');
        labelEl?.focus();
        return;
    }
    if (submitType === 'reaction' && !rsmi) {
        showSubmitFeedback('Please provide a reaction SMILES.', 'error');
        rxnEl?.focus();
        return;
    }

    if (button) {
        button.disabled = true;
        button.innerText = 'Submitting...';
    }
    try {
        const res = await fetch(`${API_V1}/submissions`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({type: submitType, label, rsmi, epd_lw: epd, note})
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.message || data.detail || 'Submission failed');
        showSubmitFeedback(`Submitted successfully. Review ID #${data.submission_id}.`, 'success');
        ['submit-label', 'submit-rxn', 'submit-epd', 'submit-note'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });
        const balance = document.getElementById('submit-balance-status');
        if (balance) balance.innerHTML = '';
    } catch (e) {
        showSubmitFeedback(e.message || 'Submission failed. Please try again.', 'error');
    } finally {
        if (button) {
            button.disabled = false;
            button.innerText = 'Submit';
        }
    }
}

// URL routing
window.addEventListener('load', () => {
    const match = location.hash.match(/^#reaction\/(\d+)$/);
    if (match) loadReaction(parseInt(match[1]), { historyMode: 'replace' });
});
window.addEventListener('popstate', (e) => {
    if (e.state?.reactionId) loadReaction(e.state.reactionId, { historyMode: 'none' });
    else goHome({ updateHistory: false });
});

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.getElementById('sketch-modal')?.classList.contains('show')) {
        closeSketchModal();
        e.preventDefault();
        return;
    }
    if (e.key === 'Escape' && document.getElementById('chart-modal')?.classList.contains('show')) {
        closeChartModal();
        e.preventDefault();
        return;
    }
    if (e.key === 'Escape' && document.getElementById('schema-modal')?.classList.contains('show')) {
        closeSchemaModal();
        e.preventDefault();
        return;
    }
    if (e.key === 'Escape' && document.getElementById('submit-panel')?.classList.contains('show')) {
        closeSubmitPanel();
        e.preventDefault();
        return;
    }
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (!activeReaction) return;
    switch (e.key) {
        case 'ArrowLeft':  changeStep(-1); break;
        case 'ArrowRight': changeStep(1);  break;
        case ' ':          togglePlay();   e.preventDefault(); break;
        case 'Escape':     goHome();       break;
        case 'a': case 'A': {
            const cb = document.getElementById('show-all-checkbox');
            if (cb) { cb.checked = !cb.checked; drawActiveEPDArrows(); }
            break;
        }
        case 'l': case 'L': {
            const sel = document.getElementById('layout-select');
            if (sel) { sel.value = sel.value === 'force' ? 'rdkit' : 'force'; toggleLayout(); }
            break;
        }
        case 'f': case 'F': zoomToFit(); break;
    }
});

// Inline balance checker event listener (FE-02)
let balanceDebounce = null;
let balanceAbortController = null;
const balanceCache = new Map();
const searchInputEl = document.getElementById('search-input');
if (searchInputEl) {
    searchInputEl.addEventListener('input', (e) => {
        const val = e.target.value.trim();
        clearTimeout(balanceDebounce);
        if (balanceAbortController) {
            balanceAbortController.abort();
            balanceAbortController = null;
        }
        const balanceStatus = document.getElementById('balance-status');
        if (balanceStatus) balanceStatus.innerHTML = '';
        if (!val.includes('>>')) return;

        // Check client-side cache
        if (balanceCache.has(val)) {
            const data = balanceCache.get(val);
            if (balanceStatus) {
                if (data.balanced) {
                    balanceStatus.innerHTML = `<span style="color:var(--accent-green); font-weight:500;">✔ Balanced (${data.reactant_atom_count} atoms, charge ${data.reactant_formal_charge})</span>`;
                } else {
                    const errors = data.errors ? data.errors.map(escapeHtml).join(' · ') : 'atom/charge discrepancy';
                    balanceStatus.innerHTML = `<span style="color:var(--accent-orange); font-weight:500;">⚠ Imbalance: ${errors}</span>`;
                }
            }
            return;
        }

        balanceDebounce = setTimeout(async () => {
            balanceAbortController = new AbortController();
            try {
                const res = await fetch(`${API_V1}/check-balance`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({rsmi: val}),
                    signal: balanceAbortController.signal
                });
                const data = await res.json();
                balanceCache.set(val, data);
                if (balanceStatus) {
                    if (data.balanced) {
                        balanceStatus.innerHTML = `<span style="color:var(--accent-green); font-weight:500;">✔ Balanced (${data.reactant_atom_count} atoms, charge ${data.reactant_formal_charge})</span>`;
                    } else {
                        const errors = data.errors ? data.errors.map(escapeHtml).join(' · ') : 'atom/charge discrepancy';
                        balanceStatus.innerHTML = `<span style="color:var(--accent-orange); font-weight:500;">⚠ Imbalance: ${errors}</span>`;
                    }
                }
            } catch (err) {
                if (err.name !== 'AbortError') {
                    console.error("Balance check error:", err);
                }
            } finally {
                balanceAbortController = null;
            }
        }, 600);
    });
}

// Initial initialization calls
const savedTheme = localStorage.getItem('synepd_theme');
const useLightTheme = savedTheme === 'light'
    || (!savedTheme && window.matchMedia('(prefers-color-scheme: light)').matches);
document.documentElement.classList.toggle('light-theme', useLightTheme);
document.body.classList.toggle('light-theme', useLightTheme);
const savedPlaySpeed = localStorage.getItem('synepd_play_speed');
const playSpeedSelect = document.getElementById('play-speed');
if (['500', '1000', '2000', '3000'].includes(savedPlaySpeed) && playSpeedSelect) {
    playSpeedSelect.value = savedPlaySpeed;
}
const autoplayToggle = document.getElementById('autoplay-toggle');
if (autoplayToggle) autoplayToggle.checked = localStorage.getItem('synepd_autoplay') !== 'off';

const tabList = document.querySelector('[role="tablist"]');
tabList?.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const tabs = Array.from(tabList.querySelectorAll('[role="tab"]'));
    const current = tabs.indexOf(document.activeElement);
    if (current < 0) return;
    let next = current;
    if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = tabs.length - 1;
    else next = (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault();
    switchTab(tabs[next].dataset.tab);
    tabs[next].focus();
});

fetchArrowTypes();
fetchStats();
checkConnection();
fetchDbInfo();
