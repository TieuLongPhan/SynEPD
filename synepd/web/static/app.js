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
        const onclickStr = btn.getAttribute('onclick');
        const isActive = Boolean(onclickStr && onclickStr.includes(`'${tabId}'`));
        btn.classList.toggle('active', isActive);
        btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });
    document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));
    targetPane.classList.add('active');

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

// Load Taxonomy Tree
async function loadTaxonomyTree() {
    const container = document.getElementById('taxonomy-tree-container');
    try {
        const res = await fetch(`${API_V1}/taxonomy`);
        const data = await res.json();
        container.innerHTML = '';
        buildTreeNode(data.taxonomy, container);
    } catch (err) {
        container.innerHTML = '<p style="color: var(--accent-red);">Failed to load taxonomy.</p>';
    }
}

function countSubtreeReactions(node) {
    let count = node.reaction_count || 0;
    if (node.children) node.children.forEach(c => { count += countSubtreeReactions(c); });
    return count;
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
        header.innerHTML = `
            <span class="tree-toggle ${hasChildren ? '' : 'leaf'}">${hasChildren ? '▶' : '•'}</span>
            <span style="font-weight: 500; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${node.code} — ${node.name}</span>
            ${subtreeCount > 0 ? `<span class="tree-count-badge">${subtreeCount}</span>` : ''}
        `;
        
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

function copyText(elementId) {
    const text = document.getElementById(elementId).innerText;
    navigator.clipboard.writeText(text).then(() => {
        const btn = event.currentTarget;
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
    activeReaction = {
        id: data.id || data.reaction_id || null,
        case_id: data.case_id || "Projected Query",
        name: data.name || (data.case_id ? data.case_id : "Projected Query"),
        canonical_rsmi: data.canonical_rsmi || (data.mapped_rsmi ? data.mapped_rsmi.replace(/:\d+/g, '') : ''),
        aam_key: data.mapped_rsmi,
        canonical_aam_key: data.canonical_aam_key || null,
        taxonomy: data.taxonomy || { code: "DYNAMIC", name: "Custom EPD Projection", level: 4 },
        arrows: data.arrows,
        its_graph: data.its_graph,
        mechanism_context: data.mechanism_context || null,
        mechanism_ambiguous: data.mechanism_ambiguous || false,
        mechanism_candidate_count: data.mechanism_candidate_count || 0,
        mechanism_candidates: data.mechanism_candidates || [],
        balanced_from_imbalanced: data.balanced_from_imbalanced || false,
        original_imbalanced_query: data.original_imbalanced_query || null
    };
    renderReactionDetails();
}

let reactionLoadSeq = 0;
let reactionLoadAbort = null;

async function loadReaction(rxnId) {
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

        history.pushState({ reactionId: rxnId }, '', `#reaction/${rxnId}`);
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
                    <button class="copy-btn" onclick="copyText('detail-smiles')" title="Copy to Clipboard">📋 Copy</button>
                </div>
                <div id="detail-smiles">CC[O-]>>CCO</div>
            </div>

            <div class="smiles-box">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.25rem;">
                    <span class="smiles-label">Atom-Mapped key</span>
                    <button class="copy-btn" onclick="copyText('detail-aam')" title="Copy to Clipboard">📋 Copy</button>
                </div>
                <div id="detail-aam">AAM Key</div>
            </div>

            <!-- CDK Depict 2D Reaction Diagram -->
            <div class="section-collapsible" id="cdk-depict-section">
                <button class="section-toggle" onclick="toggleSection('cdk-depict-body')">2D Reaction Diagram ▾</button>
                <div id="cdk-depict-body" class="section-toggle-body">
                    <div style="display:flex; flex-wrap:wrap; align-items:center; gap:0.75rem; margin-bottom:0.5rem;">
                        <label style="display:flex; align-items:center; gap:0.3rem; font-size:0.75rem; color:var(--text-secondary); cursor:pointer; user-select:none;">
                            <input type="checkbox" id="cdk-aam-toggle" onchange="renderCDKDepict()" style="cursor:pointer; accent-color:var(--accent-cyan);">
                            <span>Atom mapping</span>
                        </label>
                        <label style="display:flex; align-items:center; gap:0.3rem; font-size:0.75rem; color:var(--text-secondary); cursor:pointer; user-select:none;">
                            <input type="checkbox" id="cdk-abbr-toggle" onchange="renderCDKDepict()" style="cursor:pointer; accent-color:var(--accent-cyan);">
                            <span>Abbreviations</span>
                        </label>
                        <div style="display:flex; align-items:center; gap:0.3rem; font-size:0.75rem; color:var(--text-secondary);">
                            <span>H:</span>
                            <select id="cdk-hdisp" onchange="renderCDKDepict()" style="background:var(--bg-tertiary); border:1px solid var(--border); color:var(--text-primary); border-radius:4px; padding:0.1rem 0.3rem; font-size:0.72rem; cursor:pointer;">
                                <option value="bridgehead">Bridgehead</option>
                                <option value="stereo">Stereo</option>
                                <option value="implicit">Implicit</option>
                                <option value="all">All</option>
                            </select>
                        </div>
                    </div>
                    <div id="cdk-depict-container"></div>
                </div>
            </div>

            <!-- SMILES change summary diff -->
            <div id="change-summary" style="display:flex; gap:0.5rem; flex-wrap:wrap; margin-bottom:0.75rem;"></div>

            <div class="section-collapsible" id="mechanism-context-section" style="display:none;">
                <button class="section-toggle" onclick="toggleSection('mechanism-context-body')">Mechanistic Center ▾</button>
                <div id="mechanism-context-body" class="section-toggle-body">
                    <div id="mechanism-context-summary" style="font-size:0.78rem; color:var(--text-secondary); line-height:1.55;"></div>
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
    renderMechanismContextSummary();

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

    const cdkToggle = document.getElementById('cdk-aam-toggle');
    if (cdkToggle) cdkToggle.checked = false;
    const cdkAbbrToggle = document.getElementById('cdk-abbr-toggle');
    if (cdkAbbrToggle) cdkAbbrToggle.checked = false;
    const cdkHdisp = document.getElementById('cdk-hdisp');
    if (cdkHdisp) cdkHdisp.value = 'bridgehead';
    renderCDKDepict();
}

function renderMechanismContextSummary() {
    const section = document.getElementById('mechanism-context-section');
    const summary = document.getElementById('mechanism-context-summary');
    const toggle = document.getElementById('mechanism-view-toggle');
    if (!section || !summary || !toggle) return;

    const context = activeReaction.mechanism_context;
    if (!context) {
        section.style.display = activeReaction.mechanism_ambiguous ? 'block' : 'none';
        if (activeReaction.mechanism_ambiguous) {
            summary.textContent = `${activeReaction.mechanism_candidate_count} product-verified mechanisms remain. No candidate was guessed; inspect the candidate payload through the API.`;
            toggle.style.display = 'none';
        }
        return;
    }

    section.style.display = 'block';
    toggle.style.display = 'inline-block';
    const diagnostics = context.diagnostics || {};
    const nodeRoles = Object.values(diagnostics.node_roles || {}).flat();
    const edgeRoles = Object.values(diagnostics.edge_roles || {}).flat();
    const count = (values, role) => values.filter(value => value === role).length;
    const eventCount = (context.events || []).length;
    summary.textContent = [
        `${count(nodeRoles, 'net_center')} net-center atoms`,
        `${count(nodeRoles, 'epd_context')} EPD-context atoms`,
        `${count(edgeRoles, 'net_change')} net-change edges`,
        `${count(edgeRoles, 'transition')} transition edges`,
        `${count(edgeRoles, 'transient_only')} transient-only edges`,
        `${eventCount} ordered edit events`,
    ].join(' · ');
    toggle.textContent = activeReaction._showMechanismContext
        ? 'Show full ITS'
        : 'Show mechanistic center';
}

function toggleMechanismView() {
    const anchor = activeReaction?.mechanism_context?.anchor_graph;
    if (!anchor) return;
    if (!activeReaction._endpointItsGraph) {
        activeReaction._endpointItsGraph = activeReaction.its_graph;
    }
    activeReaction._showMechanismContext = !activeReaction._showMechanismContext;
    activeReaction.its_graph = activeReaction._showMechanismContext
        ? anchor
        : activeReaction._endpointItsGraph;
    renderMechanismContextSummary();
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

let _cdkGen = 0; // generation counter to drop stale onerror callbacks

function rdkitDepictUrl(smiles, kind = 'auto') {
    return `${API_V1}/render/rdkit.svg?smi=${encodeURIComponent(smiles)}&kind=${encodeURIComponent(kind)}`;
}

function renderCDKDepict() {
    if (!activeReaction) return;
    const container = document.getElementById('cdk-depict-container');
    if (!container) return;

    // Skip render when the section is collapsed
    const body = document.getElementById('cdk-depict-body');
    if (body && body.style.display === 'none') return;

    const showAAM = document.getElementById('cdk-aam-toggle')?.checked ?? false;
    const smiles = showAAM && activeReaction.aam_key ? activeReaction.aam_key : activeReaction.canonical_rsmi;
    // Derive annotate after the smiles ternary so mapidx isn't sent for unmapped SMILES
    const annotate = (showAAM && smiles === activeReaction.aam_key) ? 'mapidx' : 'none';

    if (!smiles) {
        container.innerHTML = '<p style="color:var(--text-secondary); font-size:0.8rem; text-align:center;">No SMILES available</p>';
        return;
    }

    const isDark = !document.body.classList.contains('light-theme');
    const style = isDark ? 'cod' : 'cow';
    const abbr = document.getElementById('cdk-abbr-toggle')?.checked ? 'on' : 'off';
    const hdisp = document.getElementById('cdk-hdisp')?.value || 'bridgehead';
    const url = `https://www.simolecule.com/cdkdepict/depict/${style}/svg?smi=${encodeURIComponent(smiles)}&zoom=2&abbr=${abbr}&hdisp=${hdisp}&showtitle=false&annotate=${annotate}`;
    const fallbackUrl = rdkitDepictUrl(smiles, smiles.includes('>') ? 'reaction' : 'molecule');

    container.innerHTML = '';
    const gen = ++_cdkGen;

    const img = document.createElement('img');
    img.alt = '2D reaction diagram';
    img.style.cssText = 'max-width:100%; border-radius:4px; display:block; margin:0 auto;';
    img.onerror = () => {
        if (gen !== _cdkGen) return; // stale — a newer render has already taken over
        if (img.dataset.renderer !== 'rdkit') {
            img.dataset.renderer = 'rdkit';
            img.src = fallbackUrl;
            link.href = fallbackUrl;
            link.textContent = 'Rendered locally with RDKit';
            return;
        }
        container.innerHTML = '<p style="color:var(--accent-orange); font-size:0.8rem; text-align:center; padding:0.5rem 0;">2D depiction unavailable</p>';
    };
    img.dataset.renderer = 'cdk';
    img.src = url;

    const link = document.createElement('a');
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.style.cssText = 'font-size:0.72rem; color:var(--text-secondary); display:block; margin-top:4px; text-align:right;';
    link.textContent = 'Open in CDK Depict ↗';

    container.appendChild(img);
    container.appendChild(link);
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
}

function togglePlay() {
    const btn = document.getElementById('btn-play');
    if (playInterval) {
        clearInterval(playInterval);
        playInterval = null;
        btn.innerText = "▶";
    } else {
        btn.innerText = "⏸";
        const speed = parseInt(document.getElementById('play-speed').value) || 2000;
        playInterval = setInterval(() => {
            if (activeStepIndex < activeReaction.arrows.length) {
                selectStep(activeStepIndex + 1);
            } else {
                selectStep(1); 
            }
        }, speed);
    }
}

function goHome() {
    activeReaction = null;
    if (playInterval) { clearInterval(playInterval); playInterval = null; }
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
    history.pushState({}, '', window.location.pathname);
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
async function fetchStats() {
    try {
        const res = await fetch(`${API_V1}/stats`);
        const data = await res.json();
        const insightsSec = document.getElementById('db-insights-section');
        if (insightsSec) insightsSec.style.display = 'block';

        const totals = data.totals || {};
        renderInsightKpis(totals);

        const arrowTypeData = Object.entries(data.arrow_type_distribution || {})
            .map(([code, count]) => ({code, label: code, count}))
            .sort((a, b) => b.count - a.count);
        renderDonutChart('#arrow-type-chart', arrowTypeData, 'Arrow Type Share', {maxItems: 8});

        const arrowsPerReactionData = Object.entries(data.arrows_per_reaction_distribution || {})
            .map(([code, count]) => ({code, label: `${code} arrows`, count}))
            .sort((a, b) => Number(a.code) - Number(b.code));
        renderVerticalBarChart('#arrows-per-reaction-chart', arrowsPerReactionData, 'Arrows Per Reaction');

        const topTaxaData = (data.top_taxonomy_nodes || [])
            .map(t => ({
                code: t.code,
                label: t.name || t.code,
                count: t.count,
                detail: t.code
            }));
        renderHorizontalBarChart('#top-taxa-chart', topTaxaData, 'Most Populated Reaction Classes', {maxItems: 8});

        const summaryData = [
            {code: 'Molecules', label: 'Molecules', count: totals.molecules || 0},
            {code: 'RC templates', label: 'RC templates', count: totals.reaction_centers || data.reaction_center_count || 0},
            {code: 'Taxons', label: 'Taxons', count: totals.taxons || 0},
            {code: 'EPD arrows', label: 'EPD arrows', count: totals.epd_arrows || 0},
        ];
        renderHorizontalBarChart('#summary-ratio-chart', summaryData, 'Database Object Counts', {compact: true});

        const taxonomyLevelData = Object.entries(data.taxonomy_level_distribution || {})
            .map(([code, count]) => ({code, label: `Level ${code}`, count}))
            .sort((a, b) => Number(a.code) - Number(b.code));
        renderVerticalBarChart('#taxonomy-level-chart', taxonomyLevelData, 'Taxonomy Depth');

        const rcReuseData = Object.entries(data.rc_reuse_distribution || {})
            .map(([code, count]) => ({code, label: `${code} reaction${Number(code) === 1 ? '' : 's'}`, count}))
            .sort((a, b) => Number(a.code) - Number(b.code));
        renderVerticalBarChart('#rc-reuse-chart', rcReuseData, 'RC Template Reuse');
    } catch (e) {
        console.error("Failed to fetch stats:", e);
    }
}

function renderInsightKpis(totals) {
    const container = document.getElementById('insight-kpi-grid');
    if (!container) return;
    const reactions = totals.reactions || 0;
    const epdArrows = totals.epd_arrows || 0;
    const rcTemplates = totals.reaction_centers || 0;
    const molecules = totals.molecules || 0;
    const avgArrows = reactions ? (epdArrows / reactions).toFixed(2) : '0.00';
    const rxnPerTemplate = rcTemplates ? (reactions / rcTemplates).toFixed(2) : '0.00';
    container.innerHTML = [
        {label: 'Avg arrows / reaction', value: avgArrows},
        {label: 'Reactions / RC template', value: rxnPerTemplate},
        {label: 'Molecules indexed', value: molecules.toLocaleString()},
        {label: 'Classified reactions', value: (totals.classified_reactions || 0).toLocaleString()},
    ].map(item => `
        <div class="insight-kpi">
            <span>${item.label}</span>
            <strong>${item.value}</strong>
        </div>
    `).join('');
}

function prepareChart(selector, title, chartData) {
    const container = d3.select(selector);
    container.selectAll("*").remove();
    container
        .attr("role", "button")
        .attr("tabindex", 0)
        .attr("title", `${title}. Click to open full screen.`)
        .on("click", (event) => {
            if (event.target.closest?.("[data-chart-caption]")) return;
            openChartModal(selector, title);
        })
        .on("keydown", (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                openChartModal(selector, title);
            }
        });

    const titleRow = container.append("div")
        .attr("class", "insight-title-row");
    titleRow.append("p")
        .attr("class", "insight-title")
        .text(title);
    titleRow.append("span")
        .attr("class", "insight-open-hint")
        .text("Click to expand");

    if (!chartData.length) {
        container.append("p")
            .attr("class", "insight-empty")
            .text("No data available");
        return null;
    }
    return container;
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
    if (!source || !modal || !body || !heading) return;
    heading.textContent = title;
    body.innerHTML = source.innerHTML;
    body.querySelectorAll('[id]').forEach((el, idx) => {
        el.id = `fullscreen-chart-${idx}`;
    });
    body.querySelectorAll("[data-chart-caption]").forEach(el => {
        el.setAttribute("tabindex", "0");
    });
    attachStaticChartTooltips(body);
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

function chartColors() {
    return [
        'var(--accent-cyan)',
        'var(--accent-purple)',
        'var(--accent-pink)',
        'var(--accent-green)',
        'var(--accent-orange)',
        'var(--accent-red)',
        '#64748b',
        '#0ea5e9',
    ];
}

function renderHorizontalBarChart(selector, chartData, title, options = {}) {
    const visibleData = options.maxItems ? chartData.slice(0, options.maxItems) : chartData;
    const container = prepareChart(selector, title, visibleData);
    if (!container) return;

    const margin = {
        top: 8,
        right: 54,
        bottom: 8,
        left: options.compact ? 118 : 160
    };
    const frameWidth = 560;
    const width = frameWidth - margin.left - margin.right;
    const barHeight = options.compact ? 22 : 25;
    const height = Math.max(visibleData.length * barHeight, 64);

    const gradientId = `bar-gradient-${selector.replace(/[^a-zA-Z0-9_-]/g, '')}`;
    const svgEl = container.append("svg")
        .attr("class", "chart-svg chart-horizontal")
        .attr("width", "100%")
        .attr("height", height + margin.top + margin.bottom)
        .attr("viewBox", `0 0 ${frameWidth} ${height + margin.top + margin.bottom}`)
        .attr("role", "img")
        .attr("aria-label", title);

    const defs = svgEl.append("defs");
    const grad = defs.append("linearGradient")
        .attr("id", gradientId)
        .attr("x1", "0%")
        .attr("y1", "0%")
        .attr("x2", "100%")
        .attr("y2", "0%");
    grad.append("stop").attr("offset", "0%").attr("stop-color", "var(--accent-purple)");
    grad.append("stop").attr("offset", "100%").attr("stop-color", "var(--accent-cyan)");

    const svg = svgEl.append("g")
        .attr("transform", `translate(${margin.left},${margin.top})`);

    const x = d3.scaleLinear()
        .domain([0, d3.max(visibleData, d => d.count) || 1])
        .range([0, width]);

    const y = d3.scaleBand()
        .domain(visibleData.map(d => d.code))
        .range([0, height])
        .padding(0.2);

    const bars = svg.append("g")
        .selectAll("rect")
        .data(visibleData)
        .join("rect")
        .attr("x", 0)
        .attr("y", d => y(d.code))
        .attr("width", 0)
        .attr("height", y.bandwidth())
        .attr("fill", `url(#${gradientId})`)
        .attr("rx", 3);
    attachChartTooltip(bars, d => chartCaption(d, 'items'));
    bars.transition()
        .duration(800)
        .attr("width", d => x(d.count));

    const valueLabels = svg.append("g")
        .selectAll("text")
        .data(visibleData)
        .join("text")
        .attr("class", "chart-label")
        .attr("x", -5)
        .attr("y", d => y(d.code) + y.bandwidth() / 2)
        .attr("dy", ".35em")
        .attr("text-anchor", "end")
        .attr("fill", "var(--text-primary)")
        .text(d => truncateLabel(d.label || d.code, options.compact ? 18 : 24))
        .append("title")
        .text(d => d.detail ? `${d.detail}: ${d.label}` : (d.label || d.code));

    svg.append("g")
        .selectAll("text")
        .data(visibleData)
        .join("text")
        .attr("class", "chart-value")
        .attr("x", d => x(d.count) + 5)
        .attr("y", d => y(d.code) + y.bandwidth() / 2)
        .attr("dy", ".35em")
        .attr("text-anchor", "start")
        .attr("fill", "var(--text-primary)")
        .text(d => Number(d.count).toLocaleString());
    attachChartTooltip(valueLabels, d => chartCaption(d, 'items'));
}

function renderVerticalBarChart(selector, chartData, title) {
    const container = prepareChart(selector, title, chartData);
    if (!container) return;
    const margin = {top: 14, right: 12, bottom: 34, left: 44};
    const frameWidth = 420;
    const frameHeight = 230;
    const width = frameWidth - margin.left - margin.right;
    const height = frameHeight - margin.top - margin.bottom;
    const svgEl = container.append("svg")
        .attr("class", "chart-svg chart-vertical")
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
    const y = d3.scaleLinear()
        .domain([0, d3.max(chartData, d => d.count) || 1])
        .nice()
        .range([height, 0]);

    svg.append("g")
        .attr("class", "chart-axis")
        .attr("transform", `translate(0,${height})`)
        .call(d3.axisBottom(x).tickValues(chartData.map(d => d.code).filter((_, i) => i % 2 === 0)).tickSizeOuter(0));
    svg.append("g")
        .attr("class", "chart-axis")
        .call(d3.axisLeft(y).ticks(4).tickSize(-width));

    const bars = svg.append("g")
        .selectAll("rect")
        .data(chartData)
        .join("rect")
        .attr("x", d => x(d.code))
        .attr("y", height)
        .attr("width", x.bandwidth())
        .attr("height", 0)
        .attr("rx", 3)
        .attr("fill", (d, i) => chartColors()[i % chartColors().length]);
    attachChartTooltip(bars, d => chartCaption(d, 'reactions'));

    bars.transition()
        .duration(800)
        .attr("y", d => y(d.count))
        .attr("height", d => height - y(d.count));
}

function renderDonutChart(selector, chartData, title, options = {}) {
    const visibleData = options.maxItems ? chartData.slice(0, options.maxItems) : chartData;
    const container = prepareChart(selector, title, visibleData);
    if (!container) return;

    const width = 520;
    const height = 245;
    const radius = 82;
    const colors = chartColors();
    const svgEl = container.append("svg")
        .attr("class", "chart-svg chart-donut")
        .attr("width", "100%")
        .attr("height", height)
        .attr("viewBox", `0 0 ${width} ${height}`)
        .attr("role", "img")
        .attr("aria-label", title);

    const total = d3.sum(visibleData, d => d.count);
    const pie = d3.pie()
        .sort(null)
        .value(d => d.count);
    const arc = d3.arc()
        .innerRadius(radius * 0.58)
        .outerRadius(radius);
    const group = svgEl.append("g")
        .attr("transform", `translate(125,122)`);

    const slices = group.selectAll("path")
        .data(pie(visibleData))
        .join("path")
        .attr("fill", (d, i) => colors[i % colors.length])
        .attr("stroke", "var(--bg-secondary)")
        .attr("stroke-width", 2)
        .attr("d", arc);
    attachChartTooltip(slices, d => chartCaption(d, 'arrows'));

    group.append("text")
        .attr("class", "donut-total")
        .attr("text-anchor", "middle")
        .attr("y", -3)
        .text(Number(total).toLocaleString());
    group.append("text")
        .attr("class", "donut-caption")
        .attr("text-anchor", "middle")
        .attr("y", 17)
        .text("arrows");

    const legend = svgEl.append("g")
        .attr("transform", "translate(245,35)");
    const rows = legend.selectAll("g")
        .data(visibleData)
        .join("g")
        .attr("transform", (_, i) => `translate(0,${i * 23})`);
    rows.append("rect")
        .attr("width", 10)
        .attr("height", 10)
        .attr("rx", 2)
        .attr("y", -8)
        .attr("fill", (_, i) => colors[i % colors.length]);
    rows.append("text")
        .attr("class", "chart-label")
        .attr("x", 17)
        .attr("y", 0)
        .text(d => truncateLabel(d.label, 18));
    rows.append("text")
        .attr("class", "chart-value")
        .attr("x", 210)
        .attr("y", 0)
        .attr("text-anchor", "end")
        .text(d => Number(d.count).toLocaleString());
    attachChartTooltip(rows, d => chartCaption(d, 'arrows'));
}

function truncateLabel(label, maxLength) {
    const text = String(label || '');
    return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
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
    if (!graphData?.links || !arrows?.length) return;

    const transitionKeys = new Set();
    arrows.forEach(arr => {
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
    document.body.classList.toggle('light-theme');
    renderCDKDepict();
}

// Similar reactions loading (FE-01)
async function loadNeighbors(reactionId) {
    try {
        const res = await fetch(`${API_V1}/reactions/${reactionId}/neighbors?limit=6`);
        const data = await res.json();
        const container = document.getElementById('neighbors-list');
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
    }
}

function animateCount(elementId, target) {
    const el = document.getElementById(elementId);
    if (!el) return;
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
    ['dash-reactions-val','dash-templates-val','dash-arrows-val','dash-taxons-val'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerText = '—';
    });
    try {
        const res = await fetch(`${API_V1}/db-info`);
        const data = await res.json();
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
        animateCount('dash-arrows-val', data.counts.epd_arrows || 0);
        animateCount('dash-taxons-val', data.counts.taxons);
    } catch (err) {
        console.error("Failed to load db info:", err);
    }
}

function openSchemaModal() {
    const modal = document.getElementById('schema-modal');
    modal.classList.add('show');
}
function closeSchemaModal() {
    const modal = document.getElementById('schema-modal');
    modal.classList.remove('show');
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
    if (match) loadReaction(parseInt(match[1]));
});
window.addEventListener('popstate', (e) => {
    if (e.state?.reactionId) loadReaction(e.state.reactionId);
    else goHome();
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
fetchArrowTypes();
fetchStats();
checkConnection();
fetchDbInfo();
