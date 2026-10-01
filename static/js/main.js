// ============ MAP VISUALIZATION ============

function renderMap(containerId, locations) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const cols = 4;
    const rows = 2;
    const width = 400;
    const height = 200;
    const cellW = width / cols;
    const cellH = height / rows;

    let svg = `
        <svg width="${width}" height="${height}" 
             style="border:1px solid #ccc; border-radius:8px; background:#f8f9fa;">
    `;

    locations.forEach((loc, index) => {
        const row = Math.floor(index / cols);
        const col = index % cols;
        const cx = col * cellW + cellW / 2;
        const cy = row * cellH + cellH / 2;

        svg += `
            <g>
                <circle cx="${cx}" cy="${cy}" r="16" fill="#007bff" stroke="#fff" stroke-width="2" />
                <text x="${cx}" y="${cy + 5}" text-anchor="middle" fill="white" font-weight="bold" font-size="14">
                    ${loc.letter}
                </text>
                <text x="${cx}" y="${cy + 30}" text-anchor="middle" font-size="12" fill="#333">
                    ${loc.name}
                </text>
            </g>
        `;
    });

    svg += `</svg>`;
    container.innerHTML = svg;
}

// ============ RENDER MAPS ON PAGE LOAD ============
document.addEventListener('DOMContentLoaded', function() {
    // Assume you have a global variable `questionsData` injected from the backend
    if (typeof questionsData !== 'undefined') {
        questionsData.forEach(q => {
            if (q.type === 'map_labeling') {
                const containerId = `map-container-${q.id}`;
                if (document.getElementById(containerId)) {
                    renderMap(containerId, q.map_locations);
                }
            }
        });
    }
});