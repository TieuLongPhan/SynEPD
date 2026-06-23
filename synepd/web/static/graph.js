// D3-specific graph visualization variables and functions for SynEPD
// Shared global state variables (accessible by app.js)
var activeReaction = null;
var activeStepIndex = 1;
var playInterval = null;
var allSearchResults = [];
var resultOffset = 0;
const RESULTS_PER_PAGE = 20;
var jsmeApplet = null;
var arrowTypeVocab = {};
var rcOffset = 0;
const MAX_HISTORY = 10;
var currentQuery = '';

const stepColors = [
    "var(--accent-1)",
    "var(--accent-2)",
    "var(--accent-3)",
    "var(--accent-4)",
    "var(--accent-5)",
    "var(--accent-6)"
];

// D3 force simulation variables
var svg = null;
var simulation = null;
var linkGroup = null;
var linkLabelGroup = null;
var nodeGroup = null;
var arrowGroup = null;
var zoomBehavior = null;
var svgRoot = null;

// Colors for chemical elements
const elementColors = {
    "C": "#4b5563", // Dark grey
    "O": "#ef4444", // Red
    "N": "#3b82f6", // Blue
    "H": "#06b6d4", // Cyan
    "P": "#f59e0b", // Yellow/Orange
    "S": "#d97706", // Brownish/Orange
    "F": "#10b981", // Green
    "Cl": "#059669", // Dark Green
    "Br": "#047857", // Deep Green
};

const elementRadius = (element) => {
    const sizes = { "H": 8, "C": 14, "N": 14, "O": 13, "F": 11, "P": 15, "S": 15, "Cl": 15, "Br": 15 };
    return sizes[element] ?? 14;
};

// Draw Interactive D3 Graph
function drawGraph() {
    const viewport = document.getElementById('graph-viewport');
    
    // Remove existing SVG if any
    const existingSvg = viewport.querySelector('svg');
    if (existingSvg) existingSvg.remove();
    
    // Hide welcome panel
    document.getElementById('welcome-panel').style.display = "none";
    
    const width = viewport.clientWidth;
    const height = viewport.clientHeight;

    const graphData = activeReaction.its_graph;
    if (!graphData || !graphData.nodes || graphData.nodes.length === 0) {
        viewport.innerHTML = '<div style="color: var(--text-secondary); text-align: center; padding-top: 10rem;">Structure graph unavailable.</div>';
        return;
    }
    graphData.links.forEach(link => {
        if (link._origStatus === undefined) link._origStatus = link.status;
    });

    // Create SVG element
    svg = d3.select("#graph-viewport")
        .append("svg")
        .attr("viewBox", [0, 0, width, height]);

    // Add defs for marker markers (arrowheads)
    const defs = svg.append("defs");
    stepColors.forEach((color, idx) => {
        defs.append("marker")
            .attr("id", `epd-arrowhead-${idx + 1}`)
            .attr("viewBox", "0 -5 10 10")
            .attr("refX", 8)
            .attr("refY", 0)
            .attr("markerWidth", 6)
            .attr("markerHeight", 6)
            .attr("orient", "auto")
            .append("path")
            .attr("d", "M0,-4L10,0L0,4")
            .attr("fill", color);
    });

    // Define 3D radial gradients for atom elements
    const gradsConfig = {
        "C": ["#9ca3af", "#374151", "#111827"],
        "O": ["#fca5a5", "#ef4444", "#7f1d1d"],
        "N": ["#93c5fd", "#3b82f6", "#1e3a8a"],
        "H": ["#cffafe", "#06b6d4", "#083344"],
        "P": ["#fde047", "#eab308", "#713f12"],
        "S": ["#fed7aa", "#f97316", "#7c2d12"],
        "F": ["#a7f3d0", "#10b981", "#064e3b"],
        "Cl": ["#a7f3d0", "#059669", "#064e3b"],
        "Br": ["#a7f3d0", "#047857", "#064e3b"]
    };

    Object.entries(gradsConfig).forEach(([el, colors]) => {
        const grad = defs.append("radialGradient")
            .attr("id", `grad-${el}`)
            .attr("cx", "35%")
            .attr("cy", "35%")
            .attr("r", "65%");
        grad.append("stop").attr("offset", "0%").attr("stop-color", colors[0]);
        grad.append("stop").attr("offset", "70%").attr("stop-color", colors[1]);
        grad.append("stop").attr("offset", "100%").attr("stop-color", colors[2]);
    });

    // Default gradient for other elements
    const defaultGrad = defs.append("radialGradient")
        .attr("id", "grad-default")
        .attr("cx", "35%")
        .attr("cy", "35%")
        .attr("r", "65%");
    defaultGrad.append("stop").attr("offset", "0%").attr("stop-color", "#fed7aa");
    defaultGrad.append("stop").attr("offset", "70%").attr("stop-color", "#f97316");
    defaultGrad.append("stop").attr("offset", "100%").attr("stop-color", "#7c2d12");

    // Add zoom container
    const container = svg.append("g");

    // Force simulation Setup
    simulation = d3.forceSimulation(graphData.nodes)
        .force("link", d3.forceLink(graphData.links).id(d => d.id).distance(80))
        .force("charge", d3.forceManyBody().strength(-200))
        .force("center", d3.forceCenter(width / 2, height / 2))
        .force("collision", d3.forceCollide().radius(25));

    // Setup Groups
    linkGroup = container.append("g").selectAll("line")
        .data(graphData.links)
        .join("line")
        .attr("class", d => `link ${d.status}`);

    linkLabelGroup = container.append("g").selectAll("text")
        .data(graphData.links.filter(l => ['breaking', 'forming', 'changing'].includes(l.status)))
        .join("text")
        .attr("class", "link-label")
        .attr("text-anchor", "middle")
        .text(d => `${d.order_r}→${d.order_p}`);

    arrowGroup = container.append("g"); 

    nodeGroup = container.append("g").selectAll(".node")
        .data(graphData.nodes)
        .join("g")
        .attr("class", "node")
        .call(d3.drag()
            .on("start", dragstarted)
            .on("drag", dragged)
            .on("end", dragended));

    // Render node circles with aromatic ring highlight (FE-08) and custom sizes (FE-09)
    nodeGroup.append("circle")
        .attr("r", d => elementRadius(d.element))
        .attr("fill", d => gradsConfig[d.element] ? `url(#grad-${d.element})` : "url(#grad-default)")
        .style("stroke", d => d.aromatic ? "var(--accent-orange)" : "var(--bg-secondary)")
        .style("stroke-width", d => d.aromatic ? "2.5px" : "1.5px")
        .style("stroke-dasharray", d => d.aromatic ? "3 2" : "none")
        .on("mouseover", (event, d) => {
            highlightStepsForAtom(d.id);
        })
        .on("mouseout", () => {
            clearStepsHighlight();
        });

    // Element labels (hidden for hydrogen)
    nodeGroup.append("text")
        .attr("dy", ".3em")
        .attr("text-anchor", "middle")
        .style("display", d => d.element === "H" ? "none" : null)
        .text(d => d.element);

    // Atom Map number helper text
    nodeGroup.append("text")
        .attr("class", "map-label")
        .attr("dx", 12)
        .attr("dy", -12)
        .text(d => `:${d.atom_map}`);

    // Custom atom tooltip with aromaticity/hybridization
    const tooltip = d3.select('#atom-tooltip');
    nodeGroup.on('mouseover', (event, d) => {
        const charge = d.charge !== 0 ? (d.charge > 0 ? `+${d.charge}` : d.charge) : '';
        const stepsInvolved = activeReaction.arrows
            .filter(a => a.source_atoms.includes(d.id) || a.target_atoms.includes(d.id))
            .map(a => a.arrow_index).join(', ');
        const hybText = d.hybridization ? `<br><span style='color:var(--accent-purple)'>Hybridization: ${d.hybridization}</span>` : '';
        const aromText = d.aromatic ? ` <span style='color:var(--accent-orange)'>(aromatic)</span>` : '';
        tooltip.style('display', 'block')
            .html(`<strong>${d.element}${charge}</strong>${aromText} &nbsp;map:<strong>${d.atom_map}</strong>${hybText}${stepsInvolved ? `<br><span style='color:var(--accent-cyan)'>Steps: ${stepsInvolved}</span>` : ''}`);
        highlightStepsForAtom(d.id);
    })
    .on('mousemove', (event) => {
        tooltip.style('left', `${event.clientX + 14}px`).style('top', `${event.clientY - 10}px`);
    })
    .on('mouseout', () => {
        tooltip.style('display', 'none');
        clearStepsHighlight();
    });

    // Update loop on tick
    simulation.on("tick", () => {
        linkGroup
            .attr("x1", d => d.source.x)
            .attr("y1", d => d.source.y)
            .attr("x2", d => d.target.x)
            .attr("y2", d => d.target.y);

        nodeGroup
            .attr("transform", d => `translate(${d.x},${d.y})`);

        linkLabelGroup
            .attr("x", d => (d.source.x + d.target.x) / 2)
            .attr("y", d => (d.source.y + d.target.y) / 2 - 6);

        drawActiveEPDArrows();
    });

    // Zoom-to-fit after simulation settles
    svgRoot = svg;
    zoomBehavior = d3.zoom().scaleExtent([0.1, 4]).on("zoom", (event) => {
        container.attr("transform", event.transform);
    });
    svg.call(zoomBehavior);

    let fitTimer = setTimeout(() => zoomToFit(), 1200);
    simulation.on('end.fit', () => { clearTimeout(fitTimer); zoomToFit(); });
}

function zoomToFit() {
    if (!activeReaction || !svgRoot || !zoomBehavior) return;
    const nodes = activeReaction.its_graph.nodes.filter(n => n.x != null);
    if (nodes.length === 0) return;
    const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
    const xMin = Math.min(...xs), xMax = Math.max(...xs);
    const yMin = Math.min(...ys), yMax = Math.max(...ys);
    const viewport = document.getElementById('graph-viewport');
    const vw = viewport.clientWidth, vh = viewport.clientHeight;
    const padding = 80;
    const scale = Math.min(0.9, 0.9 * Math.min(vw / (xMax - xMin + padding), vh / (yMax - yMin + padding)));
    const tx = (vw - (xMin + xMax) * scale) / 2;
    const ty = (vh - (yMin + yMax) * scale) / 2;
    svgRoot.transition().duration(600).call(
        zoomBehavior.transform,
        d3.zoomIdentity.translate(tx, ty).scale(scale)
    );
}

function downloadSVG() {
    const svgEl = document.querySelector('#graph-viewport svg');
    if (!svgEl) { showToast('No graph loaded', 'warning'); return; }
    const clone = svgEl.cloneNode(true);
    const style = document.createElementNS('http://www.w3.org/2000/svg', 'style');
    const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    const bgPrimary = cssVar('--bg-primary') || '#070a13';
    const bgSecondary = cssVar('--bg-secondary') || '#101524';
    const textPrimary = cssVar('--text-primary') || '#f3f4f6';
    const textSecondary = cssVar('--text-secondary') || '#9ca3af';
    style.textContent = `
        .node circle { stroke: ${bgSecondary}; stroke-width: 1.5px; }
        .node text { font-family: Outfit, sans-serif; font-weight: 700; fill: ${textPrimary}; }
        .node .map-label { font-family: monospace; font-size: 0.65rem; fill: ${textSecondary}; }
        .link { stroke-opacity: 0.6; }
        .link.breaking { stroke: ${cssVar('--accent-red')}; stroke-dasharray: 4 4; stroke-width: 3.5px; }
        .link.forming  { stroke: ${cssVar('--accent-green')}; stroke-dasharray: 4 4; stroke-width: 3.5px; }
        .link.changing { stroke: ${cssVar('--accent-orange')}; stroke-width: 3px; }
        .link.transition { stroke: ${cssVar('--accent-pink')}; stroke-width: 3px; }
        .link.unchanged{ stroke: #4b5563; stroke-width: 2px; }
        .link-label { font-family: monospace; font-size: 8px; fill: ${textSecondary}; stroke: ${bgPrimary}; stroke-width: 3px; paint-order: stroke; }
        .epd-arrow { fill: none; stroke-width: 3.5px; }
    `;
    clone.prepend(style);
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    clone.style.background = bgPrimary;
    const blob = new Blob([new XMLSerializer().serializeToString(clone)], { type: 'image/svg+xml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `${activeReaction?.case_id || 'reaction'}_ITS.svg`;
    a.click(); URL.revokeObjectURL(url);
    showToast('SVG downloaded', 'success');
}

// Draw electron pushing curved arrows based on active step
function drawActiveEPDArrows() {
    if (!arrowGroup || !activeReaction) return;
    arrowGroup.selectAll("*").remove();

    const showAll = document.getElementById('show-all-checkbox').checked;
    const steps = showAll ? activeReaction.arrows : [activeReaction.arrows[activeStepIndex - 1]];

    // Reset node stroke borders to default first (aromatic nodes get dashed rings)
    nodeGroup.select("circle")
        .style("stroke", d => d.aromatic ? "var(--accent-orange)" : "var(--bg-secondary)")
        .style("stroke-width", d => d.aromatic ? "2.5px" : "1.5px")
        .style("stroke-dasharray", d => d.aromatic ? "3 2" : "none");

    const findNode = (mapId) => activeReaction.its_graph.nodes.find(n => n.id === mapId);

    const getCoord = (atomsList) => {
        if (!atomsList || atomsList.length === 0) return null;
        if (atomsList.length === 1) {
            const node = findNode(atomsList[0]);
            return node ? { x: node.x, y: node.y } : null;
        } else if (atomsList.length === 2) {
            const n1 = findNode(atomsList[0]);
            const n2 = findNode(atomsList[1]);
            if (n1 && n2) {
                return { x: (n1.x + n2.x) / 2, y: (n1.y + n2.y) / 2 };
            }
        }
        return null;
    };

    // Collect active highlights to apply them correctly at once
    const highlightedSources = new Set();
    const highlightedTargets = new Set();

    steps.forEach(step => {
        if (!step) return;

        const src = getCoord(step.source_atoms);
        const tgt = getCoord(step.target_atoms);

        if (src && tgt) {
            const dx = tgt.x - src.x;
            const dy = tgt.y - src.y;

            // Vary the curvature based on step index to prevent multiple overlaying paths
            const bend = 0.2 + ((step.arrow_index - 1) * 0.08);
            const cx = (src.x + tgt.x) / 2 - dy * bend;
            const cy = (src.y + tgt.y) / 2 + dx * bend;

            const color = stepColors[(step.arrow_index - 1) % stepColors.length];
            const arrowId = `arrow-path-${step.arrow_index}`;
            const isActive = step.arrow_index === activeStepIndex;

            // Track highlights
            step.source_atoms.forEach(id => highlightedSources.add(id));
            step.target_atoms.forEach(id => highlightedTargets.add(id));

            // Render path with safe marker index wrapping (1-6)
            const markerId = ((step.arrow_index - 1) % stepColors.length) + 1;
            const path = arrowGroup.append("path")
                .attr("id", arrowId)
                .attr("d", `M${src.x},${src.y} Q${cx},${cy} ${tgt.x},${tgt.y}`)
                .attr("class", "epd-arrow")
                .style("stroke", color)
                .style("marker-end", `url(#epd-arrowhead-${markerId})`);

            if (showAll && !isActive) {
                path.style("opacity", "0.4")
                    .style("stroke-dasharray", "6 6")
                    .style("filter", "none");
            } else {
                path.style("opacity", "1.0")
                    .style("stroke-dasharray", "8 6")
                    .style("filter", `drop-shadow(0 0 5px ${color})`);
            }

            // Add floating step number badges to intermediate curves
            if (showAll) {
                const mx = 0.25 * src.x + 0.5 * cx + 0.25 * tgt.x;
                const my = 0.25 * src.y + 0.5 * cy + 0.25 * tgt.y;

                const badgeGroup = arrowGroup.append("g")
                    .style("opacity", isActive ? "1.0" : "0.55");

                badgeGroup.append("circle")
                    .attr("cx", mx)
                    .attr("cy", my)
                    .attr("r", 9)
                    .attr("fill", "var(--bg-secondary)")
                    .attr("stroke", color)
                    .attr("stroke-width", isActive ? "2.5px" : "1.5px");

                badgeGroup.append("text")
                    .attr("x", mx)
                    .attr("y", my)
                    .attr("dy", ".3em")
                    .attr("text-anchor", "middle")
                    .style("font-size", "9px")
                    .style("font-weight", "bold")
                    .style("fill", "var(--text-primary)")
                    .text(step.arrow_index);
            }
        }
    });

    // Highlight node circle borders at once
    nodeGroup.select("circle")
        .style("stroke", d => {
            if (highlightedSources.has(d.id)) return "var(--accent-cyan)";
            if (highlightedTargets.has(d.id)) return "var(--accent-purple)";
            return d.aromatic ? "var(--accent-orange)" : "var(--bg-secondary)";
        })
        .style("stroke-width", d => {
            if (highlightedSources.has(d.id) || highlightedTargets.has(d.id)) return "3px";
            return d.aromatic ? "2.5px" : "1.5px";
        })
        .style("stroke-dasharray", d => {
            if (highlightedSources.has(d.id) || highlightedTargets.has(d.id)) return "none";
            return d.aromatic ? "3 2" : "none";
        });
}

// Drag handlers for D3 nodes
function dragstarted(event, d) {
    if (!event.active) simulation.alphaTarget(0.3).restart();
    d.fx = d.x;
    d.fy = d.y;
}

function dragged(event, d) {
    d.fx = event.x;
    d.fy = event.y;
}

function dragended(event, d) {
    if (!event.active) simulation.alphaTarget(0);
    d.fx = null;
    d.fy = null;
}

function toggleLayout() {
    if (!activeReaction) return;
    const type = document.getElementById('layout-select').value;
    const graphData = activeReaction.its_graph;
    const coords = activeReaction.rdkit_coords;
    
    if (type === 'rdkit' && coords && Object.keys(coords).length > 0) {
        const viewport = document.getElementById('graph-viewport');
        const cx = viewport.clientWidth / 2;
        const cy = viewport.clientHeight / 2;
        
        graphData.nodes.forEach(node => {
            const coord = coords[node.id];
            if (coord) {
                node.fx = coord.x + cx;
                node.fy = coord.y + cy;
            }
        });
    } else {
        graphData.nodes.forEach(node => {
            node.fx = null;
            node.fy = null;
        });
    }
    
    if (simulation) {
        simulation.alpha(0.3).restart();
    }
}

function highlightStepsForAtom(atomId) {
    if (!activeReaction) return;
    activeReaction.arrows.forEach(arr => {
        const involved = arr.source_atoms.includes(atomId) || arr.target_atoms.includes(atomId);
        const cards = document.querySelectorAll('.step-item');
        const card = cards[arr.arrow_index - 1];
        if (card) {
            if (involved) {
                card.style.borderColor = "var(--accent-cyan)";
                card.style.transform = "translateX(4px)";
            } else {
                card.style.opacity = "0.3";
            }
        }
    });
}

function clearStepsHighlight() {
    document.querySelectorAll('.step-item').forEach(card => {
        card.style.borderColor = "";
        card.style.transform = "";
        card.style.opacity = "";
    });
}
