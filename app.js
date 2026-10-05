// Aplicação GeoSuscetibilidade v2.0 - Movimentos de Massa (SUSC_Global_sem_Lin)
// Interatividade, Auto-zoom, Accordion Sidebar, Fine-Tuning em Tempo Real

let map = null;

let currentMode = 'movmassa'; // 'movmassa' or 'corridas'
let corridasSessionId = null;
let corridasVectorLayer = null;
let corridasAllBasinsLayer = null;
let corridasGeoJSONData = null;
let currentInundacaoSession = null;
let inundacaoLayer = null;

// Mapeamento de Camadas Modelo (Movimentos de Massa)
let currentOverlays = {
    susc: null,
    jenks: null,
    custom: null,
    realtime: null,
    slope: null,
    curv: null
};
let vectorLayer = null;
let movMassaVectorLayer = null;
let movMassaBounds = null;
let inundacaoVectorLayer = null;
let inundacaoBounds = null;
let rawVectorGeoJSON = null;
let activeOverlayType = 'susc';
let currentBounds = null;
let lastProcessData = null;
let currentSessionId = null;
let isdData = { slope: [], curvature: [] };

// Variáveis de Reclassificação Canvas Modelo
let rawImageElement = null;
let rawCanvas = null;
let rawCtx = null;
let rawImageData = null;
let rawMinISD = 0;
let rawMaxISD = 0;
let totalValidPixels = 0;
let pixelAreaHa = 0;

document.addEventListener('DOMContentLoaded', () => {
    initMap();
    fetchDefaultParams();
    setupDropzone();
});

// 1. Inicializa Mapa Leaflet
function initMap() {
    map = L.map('map', {
        center: [-27.0, -51.5],
        zoom: 11,
        zoomControl: true
    });

    const esriSat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
        attribution: 'Tiles &copy; Esri'
    });

    const cartoDark = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; OpenStreetMap &copy; CARTO'
    });

    const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{y}/{x}.png', {
        attribution: '&copy; OpenStreetMap contributors'
    });

    cartoDark.addTo(map);

    const baseMaps = {
        "CartoDB Escuro": cartoDark,
        "Esri Satélite": esriSat,
        "OpenStreetMap": osm
    };

    L.control.layers(baseMaps, null, { position: 'topright' }).addTo(map);
}

// 2. Accordion dos Cards
function toggleCard(headerElem) {
    const cardPanel = headerElem.closest('.card-panel');
    cardPanel.classList.toggle('collapsed');
}

// 3. Busca Parâmetros Padrão do Backend
async function fetchDefaultParams() {
    try {
        const res = await fetch('/api/default-params');
        const data = await res.json();
        isdData.slope = data.slope_isd;
        isdData.curvature = data.curvature_isd;

        renderISDTables();
    } catch (err) {
        console.error("Erro ao carregar parâmetros padrão:", err);
    }
}

// 4. Renderiza Tabelas ISD
function renderISDTables() {
    const slopeBody = document.querySelector('#table-slope-isd tbody');
    if (slopeBody) {
        slopeBody.innerHTML = isdData.slope.map((r, i) => `
            <tr>
                <td><input type="number" step="0.1" value="${r.min}" onchange="updateISDVal('slope', ${i}, 'min', this.value)"></td>
                <td><input type="number" step="0.1" value="${r.max}" onchange="updateISDVal('slope', ${i}, 'max', this.value)"></td>
                <td><input type="number" value="${r.weight}" onchange="updateISDVal('slope', ${i}, 'weight', this.value)"></td>
            </tr>
        `).join('');
    }

    const curvBody = document.querySelector('#table-curvature-isd tbody');
    if (curvBody) {
        curvBody.innerHTML = isdData.curvature.map((r, i) => `
            <tr>
                <td><input type="number" step="0.01" value="${r.min}" onchange="updateISDVal('curvature', ${i}, 'min', this.value)"></td>
                <td><input type="number" step="0.01" value="${r.max}" onchange="updateISDVal('curvature', ${i}, 'max', this.value)"></td>
                <td><input type="number" value="${r.weight}" onchange="updateISDVal('curvature', ${i}, 'weight', this.value)"></td>
            </tr>
        `).join('');
    }
}

function updateISDVal(type, index, field, value) {
    isdData[type][index][field] = parseFloat(value);
}

function switchTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

    if (tabName === 'slope') {
        document.querySelectorAll('.tab-btn')[0].classList.add('active');
        document.getElementById('tab-slope').classList.add('active');
    } else {
        document.querySelectorAll('.tab-btn')[1].classList.add('active');
        document.getElementById('tab-curvature').classList.add('active');
    }
}

// 5. Dropzone MDE
function setupDropzone() {
    const fileInput = document.getElementById('dem-file-input');
    const display = document.getElementById('file-name-display');

    if (fileInput) {
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                display.innerHTML = `Arquivo: <b>${e.target.files[0].name}</b>`;
            }
        });
    }
}

// ==============================================================================
// PROCESSAMENTO PRINCIPAL (SUSC_GLOBAL_SEM_LIN)
// ==============================================================================

async function runProcessing(useSample = false) {
    const btn = document.getElementById('btn-process');
    const status = document.getElementById('system-status');
    const fileInput = document.getElementById('dem-file-input');

    btn.disabled = true;
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Calculando...`;
    status.innerHTML = `<i class="fa-solid fa-hourglass-half"></i> Calculando...`;

    const formData = new FormData();
    if (useSample) {
        formData.append('use_sample', 'true');
    } else if (fileInput.files.length > 0) {
        formData.append('file', fileInput.files[0]);
    } else {
        formData.append('use_sample', 'true');
    }

    formData.append('slope_isd_json', JSON.stringify(isdData.slope));
    formData.append('curvature_isd_json', JSON.stringify(isdData.curvature));

    try {
        const res = await fetch('/api/process', {
            method: 'POST',
            body: formData
        });

        const data = await res.json();
        if (data.status === 'success') {
            status.innerHTML = `<i class="fa-solid fa-circle-check"></i> Concluído`;
            displayResults(data);
        } else {
            alert('Erro no processamento: ' + data.detail);
            status.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Erro`;
        }
    } catch (err) {
        console.error(err);
        alert('Erro ao comunicar com o servidor.');
        status.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Erro`;
    } finally {
        btn.disabled = false;
        btn.innerHTML = `<i class="fa-solid fa-gears"></i> Calcular Suscetibilidade`;
    }
}

function loadSampleData() {
    if (currentMode === 'movmassa') {
        runProcessing(true);
    } else if (currentMode === 'corridas') {
        runCorridasProcessing(true);
    } else if (currentMode === 'inundacao') {
        // No sample data for inundacao yet
        alert('Dados de exemplo não disponíveis para Inundação. Carregue seus próprios arquivos.');
    }
}

// 6. Exibe Resultados e Adiciona Overlays no Mapa
function displayResults(data) {
    lastProcessData = data;
    currentSessionId = data.session_id;

    // Converte limites WGS84
    const b = data.bounds;
    currentBounds = [[b.bottom, b.left], [b.top, b.right]];
    movMassaBounds = currentBounds;

    // Limpa overlays existentes
    Object.keys(currentOverlays).forEach(k => {
        if (currentOverlays[k]) {
            map.removeLayer(currentOverlays[k]);
            currentOverlays[k] = null;
        }
    });
    if (movMassaVectorLayer) {
        map.removeLayer(movMassaVectorLayer);
        movMassaVectorLayer = null;
    }
    vectorLayer = movMassaVectorLayer;

    const opacity = parseFloat(document.getElementById('opacity-slider').value);

    // Carrega Overlays PNG
    currentOverlays.susc = L.imageOverlay(data.overlays.susc_png, currentBounds, { opacity: opacity });
    currentOverlays.jenks = L.imageOverlay(data.overlays.jenks_png, currentBounds, { opacity: opacity });
    currentOverlays.slope = L.imageOverlay(data.overlays.slope_png, currentBounds, { opacity: opacity });
    currentOverlays.curv = L.imageOverlay(data.overlays.curv_png, currentBounds, { opacity: opacity });

    // Exibe overlay padrão (ISD Contínuo)
    currentOverlays.susc.addTo(map);
    map.fitBounds(currentBounds, { padding: [20, 20], maxZoom: 16 });

    // Mostra painéis laterais
    document.getElementById('panel-layers').style.display = 'block';
    document.getElementById('panel-fine-tuning').style.display = 'block';
    document.getElementById('panel-stats').style.display = 'block';

    // Configura links de download
    document.getElementById('link-download-susc').href = data.downloads.susc_tif;
    document.getElementById('link-download-jenks').href = data.downloads.jenks_tif;

    // Atualiza estatísticas
    renderContinuousStats(data.stats);

    // Inicializa Fine-Tuning em Tempo Real
    initRealtimeFineTuning(data);

    // Atualiza Legenda Dinâmica
    renderLegend('susc', data.stats);
}

// 7. Renderiza Estatísticas Contínuas
function renderContinuousStats(stats) {
    const grid = document.getElementById('stats-container-grid');
    document.getElementById('stats-continuous-view').style.display = 'block';
    document.getElementById('stats-jenks-view').style.display = 'none';

    grid.innerHTML = `
        <div class="stat-box">
            <span class="stat-label">ISD Mínimo</span>
            <span class="stat-value">${stats.min_isd}</span>
        </div>
        <div class="stat-box">
            <span class="stat-label">ISD Máximo</span>
            <span class="stat-value">${stats.max_isd}</span>
        </div>
        <div class="stat-box">
            <span class="stat-label">ISD Média</span>
            <span class="stat-value">${stats.mean_isd}</span>
        </div>
        <div class="stat-box">
            <span class="stat-label">Total de Pixels</span>
            <span class="stat-value">${stats.total_pixels ? stats.total_pixels.toLocaleString('pt-BR') : ''}</span>
        </div>
    `;
}

// 8. Inicializa Ajuste em Tempo Real (Canvas Raw PNG)
function initRealtimeFineTuning(data) {
    rawMinISD = data.stats.min_isd;
    rawMaxISD = data.stats.max_isd;
    totalValidPixels = data.stats.total_pixels;
    pixelAreaHa = 0.09;

    const j1 = data.stats.jenks_breaks[1];
    const j2 = data.stats.jenks_breaks[2];

    const s1 = document.getElementById('slider-break1');
    const i1 = document.getElementById('input-break1');
    const s2 = document.getElementById('slider-break2');
    const i2 = document.getElementById('input-break2');

    s1.min = Math.floor(rawMinISD);
    s1.max = Math.ceil(rawMaxISD);
    s1.value = j1;
    i1.value = j1;

    s2.min = Math.floor(rawMinISD);
    s2.max = Math.ceil(rawMaxISD);
    s2.value = j2;
    i2.value = j2;

    rawImageElement = new Image();
    rawImageElement.crossOrigin = 'Anonymous';
    rawImageElement.onload = () => {
        rawCanvas = document.createElement('canvas');
        rawCanvas.width = rawImageElement.width;
        rawCanvas.height = rawImageElement.height;
        rawCtx = rawCanvas.getContext('2d');
        rawCtx.drawImage(rawImageElement, 0, 0);
        rawImageData = rawCtx.getImageData(0, 0, rawCanvas.width, rawCanvas.height);

        updateRealtimeCanvas();
    };
    rawImageElement.src = data.overlays.raw_png;
}

// 9. Atualiza Canvas e Overlay Leaflet em Tempo Real
function updateRealtimeCanvas() {
    if (!rawImageData || !rawCanvas) return;

    const b1 = parseFloat(document.getElementById('input-break1').value);
    const b2 = parseFloat(document.getElementById('input-break2').value);

    const width = rawCanvas.width;
    const height = rawCanvas.height;
    const outCanvas = document.createElement('canvas');
    outCanvas.width = width;
    outCanvas.height = height;
    const outCtx = outCanvas.getContext('2d');
    const outImgData = outCtx.createImageData(width, height);

    const srcData = rawImageData.data;
    const dstData = outImgData.data;

    let count1 = 0, count2 = 0, count3 = 0;

    for (let i = 0; i < srcData.length; i += 4) {
        const alpha = srcData[i + 3];
        if (alpha > 0) {
            const rawVal = srcData[i];
            const isd = rawMinISD + (rawVal / 255.0) * (rawMaxISD - rawMinISD);

            if (isd <= b1) {
                dstData[i] = 46;     // Verde (#2ecc71)
                dstData[i + 1] = 204;
                dstData[i + 2] = 113;
                dstData[i + 3] = 210;
                count1++;
            } else if (isd <= b2) {
                dstData[i] = 241;    // Amarelo (#f1c40f)
                dstData[i + 1] = 196;
                dstData[i + 2] = 15;
                dstData[i + 3] = 210;
                count2++;
            } else {
                dstData[i] = 231;    // Vermelho (#e74c3c)
                dstData[i + 1] = 76;
                dstData[i + 2] = 60;
                dstData[i + 3] = 210;
                count3++;
            }
        }
    }

    outCtx.putImageData(outImgData, 0, 0);
    const dataUrl = outCanvas.toDataURL('image/png');

    const opacity = parseFloat(document.getElementById('opacity-slider').value);

    if (currentOverlays.realtime) {
        map.removeLayer(currentOverlays.realtime);
    }
    currentOverlays.realtime = L.imageOverlay(dataUrl, currentBounds, { opacity: opacity });

    if (activeOverlayType === 'realtime') {
        currentOverlays.realtime.addTo(map);
    }

    render3BreaksStats(count1, count2, count3, b1, b2, 'Real-Time Fine-Tuning');
}

// 10. Atualiza Sliders e Inputs de Quebra
function updateBreakFromSlider(type, val) {
    if (type === 'break1') {
        document.getElementById('input-break1').value = val;
    } else {
        document.getElementById('input-break2').value = val;
    }
    document.querySelector('input[name="active_layer"][value="realtime"]').checked = true;
    toggleLayer('realtime');
    updateRealtimeCanvas();
}

function updateBreakFromInput(type, val) {
    if (type === 'break1') {
        document.getElementById('slider-break1').value = val;
    } else {
        document.getElementById('slider-break2').value = val;
    }
    document.querySelector('input[name="active_layer"][value="realtime"]').checked = true;
    toggleLayer('realtime');
    updateRealtimeCanvas();
}

// 11. Renderiza Estatísticas de 3 Quebras
function render3BreaksStats(c1, c2, c3, b1, b2, title) {
    const container = document.getElementById('stats-jenks-container');
    document.getElementById('stats-continuous-view').style.display = 'none';
    document.getElementById('stats-jenks-view').style.display = 'block';

    const total = c1 + c2 + c3;
    const pct1 = total > 0 ? ((c1 / total) * 100).toFixed(1) : '0';
    const pct2 = total > 0 ? ((c2 / total) * 100).toFixed(1) : '0';
    const pct3 = total > 0 ? ((c3 / total) * 100).toFixed(1) : '0';

    const ha1 = (c1 * pixelAreaHa).toFixed(1);
    const ha2 = (c2 * pixelAreaHa).toFixed(1);
    const ha3 = (c3 * pixelAreaHa).toFixed(1);

    container.innerHTML = `
        <div class="jenks-item">
            <div class="jenks-header">
                <span class="jenks-title"><span class="jenks-color" style="background: #2ecc71;"></span> Classe 1 - Baixa</span>
                <b>${ha1} ha (${pct1}%)</b>
            </div>
            <div class="jenks-range">ISD &le; ${b1.toFixed(1)}</div>
        </div>
        <div class="jenks-item">
            <div class="jenks-header">
                <span class="jenks-title"><span class="jenks-color" style="background: #f1c40f;"></span> Classe 2 - Média</span>
                <b>${ha2} ha (${pct2}%)</b>
            </div>
            <div class="jenks-range">ISD entre ${b1.toFixed(1)} e ${b2.toFixed(1)}</div>
        </div>
        <div class="jenks-item">
            <div class="jenks-header">
                <span class="jenks-title"><span class="jenks-color" style="background: #e74c3c;"></span> Classe 3 - Alta</span>
                <b>${ha3} ha (${pct3}%)</b>
            </div>
            <div class="jenks-range">ISD &gt; ${b2.toFixed(1)}</div>
        </div>
    `;
}

// 12. Reclassificação e Vetorização Direct Shapefile (.shp)
async function runReclassifyAndVectorize() {
    if (!currentSessionId) return;

    const btn = document.getElementById('btn-reclassify-vector');
    const status = document.getElementById('system-status');

    const b1 = parseFloat(document.getElementById('input-break1').value);
    const b2 = parseFloat(document.getElementById('input-break2').value);

    btn.disabled = true;
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Gerando Vetor Shapefile...`;
    status.innerHTML = `<i class="fa-solid fa-hourglass-half"></i> Vetorizando...`;

    const formData = new FormData();
    formData.append('session_id', currentSessionId);
    formData.append('break1', b1);
    formData.append('break2', b2);

    try {
        const res = await fetch('/api/reclassify-vectorize', {
            method: 'POST',
            body: formData
        });

        const data = await res.json();
        if (data.status === 'success') {
            status.innerHTML = `<i class="fa-solid fa-circle-check"></i> Vetor Gerado`;

            currentOverlays.custom = L.imageOverlay(data.overlays.custom_png, currentBounds, {
                opacity: parseFloat(document.getElementById('opacity-slider').value)
            });

            // Carrega a Camada GeoJSON no Leaflet
            await loadVectorGeoJSON(data.geojson_url);

            // Exibe botão de download do Shapefile ZIP
            const shpBtn = document.getElementById('link-download-shp');
            shpBtn.href = data.shp_zip_url;
            shpBtn.style.display = 'flex';

            document.getElementById('layer-vector-item').style.display = 'block';
            document.querySelector('input[name="active_layer"][value="vector"]').checked = true;
            toggleLayer('vector');
        } else {
            alert('Erro ao vetorizar: ' + data.detail);
        }
    } catch (err) {
        console.error(err);
        alert('Erro ao vetorizar dados.');
    } finally {
        btn.disabled = false;
        btn.innerHTML = `<i class="fa-solid fa-draw-polygon"></i> Gerar Vetor Shapefile (.shp)`;
    }
}

// 13. Carrega GeoJSON do Vetor Suavizado no Leaflet
async function loadVectorGeoJSON(geojsonUrl) {
    try {
        const res = await fetch(geojsonUrl);
        rawVectorGeoJSON = await res.json();

        if (movMassaVectorLayer) {
            map.removeLayer(movMassaVectorLayer);
        }

        const opacity = parseFloat(document.getElementById('opacity-slider').value);

        movMassaVectorLayer = L.geoJSON(rawVectorGeoJSON, {
            style: function(feature) {
                const code = feature.properties.gridcode;
                const colorMap = { 1: '#2ecc71', 2: '#f1c40f', 3: '#e74c3c' };
                return {
                    fillColor: colorMap[code] || '#95a5a6',
                    weight: 1,
                    opacity: opacity,
                    color: '#2c3e50',
                    fillOpacity: opacity * 0.8
                };
            },
            onEachFeature: function(feature, layer) {
                const p = feature.properties;
                layer.bindPopup(`
                    <div style="font-family:Inter; font-size:12px;">
                        <b style="color:${p.gridcode === 3 ? '#e74c3c' : (p.gridcode === 2 ? '#f1c40f' : '#2ecc71')};">${p.Classe}</b><br>
                        <b>Área do Polígono:</b> ${p.AREA_HA} ha<br>
                        <b>Município:</b> ${p.MUNICIPIO} - ${p.UF}<br>
                        <b>Fonte:</b> ${p.FONTE}
                    </div>
                `);
            }
        });
        vectorLayer = movMassaVectorLayer;
    } catch (err) {
        console.error("Erro ao carregar GeoJSON:", err);
    }
}

// 14. Alterna Camadas no Mapa
function toggleLayer(type) {
    activeOverlayType = type;

    // Oculta todas as camadas
    Object.keys(currentOverlays).forEach(k => {
        if (currentOverlays[k] && map.hasLayer(currentOverlays[k])) {
            map.removeLayer(currentOverlays[k]);
        }
    });
    if (vectorLayer && map.hasLayer(vectorLayer)) {
        map.removeLayer(vectorLayer);
    }

    // Exibe a camada selecionada
    if (type === 'vector') {
        if (vectorLayer) vectorLayer.addTo(map);
    } else if (currentOverlays[type]) {
        currentOverlays[type].addTo(map);
    }

    renderLegend(type, lastProcessData ? lastProcessData.stats : null);
}

// 15. Altera Opacidade
function changeOpacity(val) {
    document.getElementById('opacity-val').innerText = Math.round(val * 100) + '%';
    const op = parseFloat(val);

    Object.keys(currentOverlays).forEach(k => {
        if (currentOverlays[k]) {
            currentOverlays[k].setOpacity(op);
        }
    });

    if (vectorLayer) {
        vectorLayer.setStyle({
            opacity: op,
            fillOpacity: op * 0.8
        });
    }

    if (corridasAllBasinsLayer) {
        corridasAllBasinsLayer.setStyle(function(feature) {
            const type = feature.properties._currentClass;
            const style = corridasAllBasinsLayer.options.style(feature);
            if (style) {
                style.opacity = type === 'none' ? op * 0.3 : op;
                style.fillOpacity = type === 'none' ? op * 0.2 : op * 0.8;
            }
            return style || {};
        });
    }
}

// 16. Legenda Dinâmica
function renderLegend(type, stats) {
    const legend = document.getElementById('map-legend');
    const legendTitle = document.getElementById('legend-title');
    const legendCont = document.getElementById('legend-continuous-box');
    const legendJenks = document.getElementById('legend-jenks-box');
    const legendItems = document.getElementById('legend-jenks-items');
    
    const corridasBox = document.getElementById('legend-corridas-box');
    if (corridasBox) corridasBox.style.display = 'none';
    const inundBox = document.getElementById('legend-inundacao-box');
    if (inundBox) inundBox.style.display = 'none';

    if (!stats && type !== 'realtime' && type !== 'vector') {
        legend.style.display = 'none';
        return;
    }

    legend.style.display = 'block';

    if (type === 'susc') {
        legendTitle.innerText = "Suscetibilidade ISD (Contínuo)";
        legendCont.style.display = 'block';
        legendJenks.style.display = 'none';
        document.getElementById('legend-min-val').innerText = stats.min_isd;
        document.getElementById('legend-max-val').innerText = stats.max_isd;
    } else if (type === 'slope') {
        legendTitle.innerText = "Declividade (°)";
        legendCont.style.display = 'block';
        legendJenks.style.display = 'none';
        document.getElementById('legend-min-val').innerText = (stats.slope_deg ? stats.slope_deg.min : 0) + '°';
        document.getElementById('legend-max-val').innerText = (stats.slope_deg ? stats.slope_deg.max : 90) + '°';
    } else if (type === 'curv') {
        legendTitle.innerText = "Curvatura Suavizada";
        legendCont.style.display = 'block';
        legendJenks.style.display = 'none';
        document.getElementById('legend-min-val').innerText = stats.curvature ? stats.curvature.min : -1;
        document.getElementById('legend-max-val').innerText = stats.curvature ? stats.curvature.max : 1;
    } else if (type === 'jenks' || type === 'realtime' || type === 'vector') {
        legendTitle.innerText = type === 'jenks' ? "Quebras Naturais (3 Classes)" : (type === 'vector' ? "Vetor Poligonal (.shp)" : "Fine-Tuning em Tempo Real");
        legendCont.style.display = 'none';
        legendJenks.style.display = 'block';

        const b1 = parseFloat(document.getElementById('input-break1').value);
        const b2 = parseFloat(document.getElementById('input-break2').value);

        legendItems.innerHTML = `
            <div class="legend-row"><span class="legend-color" style="background: #2ecc71;"></span> Baixa (&le; ${b1.toFixed(1)})</div>
            <div class="legend-row"><span class="legend-color" style="background: #f1c40f;"></span> Média (${b1.toFixed(1)} a ${b2.toFixed(1)})</div>
            <div class="legend-row"><span class="legend-color" style="background: #e74c3c;"></span> Alta (&gt; ${b2.toFixed(1)})</div>
        `;
    }
}

// ==============================================================================
// PROCESSAMENTO CORRIDAS E ENXURRADAS
// ==============================================================================

function switchMode(mode) {
    // Hide all layers before switching
    Object.keys(currentOverlays).forEach(k => {
        if (currentOverlays[k] && map.hasLayer(currentOverlays[k])) map.removeLayer(currentOverlays[k]);
    });
    if (movMassaVectorLayer && map.hasLayer(movMassaVectorLayer)) map.removeLayer(movMassaVectorLayer);
    if (corridasAllBasinsLayer && map.hasLayer(corridasAllBasinsLayer)) map.removeLayer(corridasAllBasinsLayer);
    if (inundacaoRealtimeOverlay && map.hasLayer(inundacaoRealtimeOverlay)) map.removeLayer(inundacaoRealtimeOverlay);
    if (inundacaoVectorLayer && map.hasLayer(inundacaoVectorLayer)) map.removeLayer(inundacaoVectorLayer);

    // Hide Legend
    const legend = document.getElementById('map-legend');
    if (legend) legend.style.display = 'none';

    currentMode = mode;
    document.querySelectorAll('.mode-btn').forEach(btn => btn.classList.remove('active'));
    document.getElementById(`mode-${mode}`).classList.add('active');

    const appSubtitle = document.getElementById('app-subtitle');

    if (mode === 'movmassa') {
        if (activeOverlayType && currentOverlays[activeOverlayType]) {
            currentOverlays[activeOverlayType].addTo(map);
        } else if (activeOverlayType === 'vector' && movMassaVectorLayer) {
            movMassaVectorLayer.addTo(map);
        }
        vectorLayer = movMassaVectorLayer;
        currentBounds = movMassaBounds;
        if (lastProcessData) renderLegend(activeOverlayType, lastProcessData.stats);

        document.getElementById('panels-movmassa').style.display = 'block';
        document.getElementById('panels-corridas').style.display = 'none';
        document.getElementById('panels-inundacao').style.display = 'none';
        appSubtitle.innerText = 'Modelagem Ponderada ISD (SUSC_Global_sem_Lin)';
    } else if (mode === 'corridas') {
        if (corridasAllBasinsLayer) {
            corridasAllBasinsLayer.addTo(map);
            renderCorridasLegend();
        }
        document.getElementById('panels-movmassa').style.display = 'none';
        document.getElementById('panels-corridas').style.display = 'block';
        document.getElementById('panels-inundacao').style.display = 'none';
        appSubtitle.innerText = 'Suscetibilidade a Corridas e Enxurradas';
    } else if (mode === 'inundacao') {
        // Restaurar bounds ANTES de adicionar layers
        currentBounds = inundacaoBounds;
        vectorLayer = inundacaoVectorLayer;

        // Restaurar overlay de preview em tempo real (se existir e não tiver vetor final)
        if (inundacaoRealtimeOverlay) {
            inundacaoRealtimeOverlay.addTo(map);
        }
        // Restaurar vetor final (se existir, sobrepõe ao preview)
        if (inundacaoVectorLayer) {
            inundacaoVectorLayer.addTo(map);
            if (typeof renderInundacaoLegend === 'function') renderInundacaoLegend();
        }

        document.getElementById('panels-movmassa').style.display = 'none';
        document.getElementById('panels-corridas').style.display = 'none';
        document.getElementById('panels-inundacao').style.display = 'block';
        appSubtitle.innerText = 'Suscetibilidade a Inundação (HAND & Fuzzy)';
    }
}

async function runCorridasProcessing(useSample = false) {
    const btn = document.getElementById('btn-process-corridas');
    const status = document.getElementById('system-status');
    const fileInput = document.getElementById('dem-file-input');

    btn.disabled = true;
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Calculando...`;
    status.innerHTML = `<i class="fa-solid fa-hourglass-half"></i> Processando Corridas...`;

    const formData = new FormData();
    if (useSample) {
        formData.append('use_sample', 'true');
    } else if (fileInput.files.length > 0) {
        formData.append('mde', fileInput.files[0]);
    } else {
        alert("Por favor, selecione um arquivo MDE.");
        btn.disabled = false;
        btn.innerHTML = `<i class="fa-solid fa-gears"></i> Calcular Sub-bacias`;
        return;
    }

    const drainThresh = document.getElementById('input-stream-threshold').value;
    formData.append('drain_threshold', drainThresh);

    try {
        const res = await fetch('/api/corridas/process', {
            method: 'POST',
            body: formData
        });

            if (res.ok) {
            status.innerHTML = `<i class="fa-solid fa-circle-check"></i> Concluído`;
            const data = await res.json();
            
            corridasSessionId = data.session_id;
            
            // Also display on map
            if (data.geojson_url) {
                await renderCorridasBasins(data.geojson_url);
            }
            
            // Atualizar links de download, se houver
            const dlGeo = document.getElementById('link-download-corridas-geojson');
            const dlShp = document.getElementById('link-download-corridas-shp');
            if (dlGeo && data.geojson_url) {
                dlGeo.href = data.geojson_url;
                dlGeo.style.display = 'flex';
            }
            if (dlShp && data.shp_zip_url) dlShp.href = data.shp_zip_url;
            
        } else {
            const data = await res.json();
            alert('Erro no processamento: ' + data.detail);
            status.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Erro`;
        }
    } catch (err) {
        console.error(err);
        alert('Erro ao processar as corridas no Front-End.');
        status.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Erro`;
    } finally {
        btn.disabled = false;
        btn.innerHTML = `<i class="fa-solid fa-gears"></i> Calcular Sub-bacias`;
    }
}

async function runInundacaoProcessing() {
    const fileInput = document.getElementById('mde-file-input') || document.getElementById('dem-file-input');
    const relevoInput = document.getElementById('relevo-file-input');
    const drainInput = document.getElementById('input-drain-threshold-inund');
    const status = document.getElementById('status-text') || document.getElementById('system-status');
    const btn = document.getElementById('btn-process-inundacao');

    if (!fileInput || !fileInput.files.length) {
        alert("Selecione o arquivo MDE (.tif)");
        return;
    }

    const formData = new FormData();
    formData.append("mde", fileInput.files[0]);
    if (relevoInput.files.length > 0) {
        formData.append("relevo_zip", relevoInput.files[0]);
    }
    formData.append("drain_threshold", drainInput.value);

    status.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Processando insumos de Inundação (HAND e Altimetria)... Isso pode levar alguns minutos.`;
    btn.disabled = true;
    document.getElementById('panel-inundacao-step2').style.display = 'none';
    document.getElementById('panel-inundacao-export').style.display = 'none';
    
    // Clear map
    if (inundacaoLayer) {
        map.removeLayer(inundacaoLayer);
        inundacaoLayer = null;
    }

    try {
        const res = await fetch('/api/inundacao/process_insumos', {
            method: 'POST',
            body: formData
        });

        if (res.ok) {
            const data = await res.json();
            currentInundacaoSession = data.session_id;
            
            // Populate Step 2 UI
            const p34 = Math.round(data.altimetria_stats.p34);
            const p66 = Math.round(data.altimetria_stats.p66);
            
            const a34_in = document.getElementById('input-alt-p34');
            const a34_sl = document.getElementById('slider-alt-p34');
            const a66_in = document.getElementById('input-alt-p66');
            const a66_sl = document.getElementById('slider-alt-p66');
            
            a34_in.value = p34;
            a34_sl.value = p34;
            a34_sl.min = Math.floor(data.altimetria_stats.min);
            a34_sl.max = Math.ceil(data.altimetria_stats.max);
            
            a66_in.value = p66;
            a66_sl.value = p66;
            a66_sl.min = Math.floor(data.altimetria_stats.min);
            a66_sl.max = Math.ceil(data.altimetria_stats.max);
            
            const tbody = document.getElementById('relevo-weights-tbody');
            tbody.innerHTML = '';
            
            if (data.relevo_codes && Object.keys(data.relevo_codes).length > 0) {
                for (const [code, weight] of Object.entries(data.relevo_codes)) {
                    tbody.innerHTML += `
                        <tr style="border-bottom: 1px solid var(--gr-border);">
                            <td style="padding: 8px;">${code}</td>
                            <td style="padding: 8px; text-align: right;">
                                <input type="number" class="relevo-weight-input" data-code="${code}" value="${weight}" min="0" max="3" style="width: 60px; background: var(--gr-bg-surface); color: var(--gr-text-main); border: 1px solid var(--gr-border); padding: 5px; border-radius: 4px;">
                            </td>
                        </tr>
                    `;
                }
            } else {
                tbody.innerHTML = `<tr><td colspan="2" style="padding: 8px; text-align:center; color:#888;">Nenhum arquivo de relevo fornecido ou sem códigos mapeados.</td></tr>`;
            }
            
            document.getElementById('panel-inundacao-step2').style.display = 'block';
            status.innerHTML = `<i class="fa-solid fa-circle-check"></i> Insumos gerados! Prossiga com o ajuste fino.`;
            
            // Add onchange to all weight inputs to trigger realtime update
            document.querySelectorAll('.relevo-weight-input').forEach(inp => {
                inp.addEventListener('input', updateInundacaoRealtimeCanvas);
            });
            
            initInundacaoRealtime(data);
        } else {
            const data = await res.json();
            status.innerHTML = `<i class="fa-solid fa-triangle-exclamation" style="color:var(--danger-color);"></i> Erro: ${data.detail || 'Falha no processamento.'}`;
        }
    } catch (e) {
        status.innerHTML = `<i class="fa-solid fa-triangle-exclamation" style="color:var(--danger-color);"></i> Erro na comunicação com o servidor.`;
    }

    btn.disabled = false;
}

async function runInundacaoFinal() {
    if (!currentInundacaoSession) return;
    
    const status = document.getElementById('system-status');
    const btn = document.getElementById('btn-process-final-inundacao');
    
    const formData = new FormData();
    formData.append("session_id", currentInundacaoSession);
    formData.append("b_alt_1", document.getElementById('input-alt-p34').value);
    formData.append("b_alt_2", document.getElementById('input-alt-p66').value);
    formData.append("b_hand_1", document.getElementById('input-hand-c1').value);
    formData.append("b_hand_2", document.getElementById('input-hand-c2').value);
    
    // Coleta pesos do relevo
    const weightInputs = document.querySelectorAll('.relevo-weight-input');
    const weightsDict = {};
    weightInputs.forEach(inp => {
        weightsDict[inp.getAttribute('data-code')] = parseInt(inp.value, 10);
    });
    formData.append("relevo_weights", JSON.stringify(weightsDict));
    
    status.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Realizando Álgebra de Mapas e Vetorização Topológica...`;
    btn.disabled = true;
    document.getElementById('panel-inundacao-export').style.display = 'none';
    
    if (inundacaoLayer) {
        map.removeLayer(inundacaoLayer);
        inundacaoLayer = null;
    }

    try {
        const res = await fetch('/api/inundacao/reclassify-vectorize', {
            method: 'POST',
            body: formData
        });

        if (res.ok) {
            status.innerHTML = `<i class="fa-solid fa-circle-check"></i> Concluído`;
            const data = await res.json();
            
            const dlBtnGeo = document.getElementById('link-download-inundacao-geojson');
            const dlBtnShp = document.getElementById('link-download-inundacao-shp');
            
            if (dlBtnGeo && data.geojson_url) {
                dlBtnGeo.href = data.geojson_url;
            }
            if (dlBtnShp && data.shp_zip_url) {
                dlBtnShp.href = data.shp_zip_url;
            }
            
            document.getElementById('panel-inundacao-export').style.display = 'block';
            
            await loadInundacaoVector(data.geojson_url);
            
        } else {
            const data = await res.json();
            status.innerHTML = `<i class="fa-solid fa-triangle-exclamation" style="color:var(--danger-color);"></i> Erro: ${data.detail || 'Falha no processamento.'}`;
        }
    } catch (e) {
        status.innerHTML = `<i class="fa-solid fa-triangle-exclamation" style="color:var(--danger-color);"></i> Erro na comunicação com o servidor.`;
    }

    btn.disabled = false;
}

async function loadInundacaoVector(geojsonUrl) {
    try {
        const res = await fetch(geojsonUrl);
        const geojson = await res.json();

        // Limpa vetor anterior de inundacao se existir
        if (inundacaoVectorLayer) {
            map.removeLayer(inundacaoVectorLayer);
        }

        inundacaoVectorLayer = L.geoJSON(geojson, {
            style: function(feature) {
                const classe = (feature.properties.Classe || feature.properties.classe || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
                let color = '#2ecc71'; // Baixa (Green)
                if (classe === 'alta') color = '#e74c3c'; // Red
                else if (classe === 'media') color = '#f1c40f'; // Yellow
                
                return {
                    fillColor: color,
                    weight: 1,
                    opacity: 0.8,
                    color: (classe === 'alta') ? '#c0392b' : (classe === 'media' ? '#f39c12' : '#27ae60'),
                    fillOpacity: 0.6
                };
            },
            onEachFeature: function(feature, layer) {
                const classeVal = feature.properties.Classe || feature.properties.classe || 'N/A';
                layer.bindPopup(`
                    <div style="font-family:Inter; font-size:12px;">
                        <b>Suscetibilidade à Inundação:</b> ${classeVal}
                    </div>
                `);
            }
        }).addTo(map);

        vectorLayer = inundacaoVectorLayer;
        map.fitBounds(inundacaoVectorLayer.getBounds(), { padding: [20, 20] });
        renderInundacaoLegend();
    } catch (err) {
        console.error("Erro ao carregar GeoJSON de inundação:", err);
    }
}

async function renderCorridasBasins(geojsonUrl) {
    try {
        const res = await fetch(geojsonUrl);
        corridasGeoJSONData = await res.json();
        
        updateCorridasFilters();
        
    } catch(err) {
        console.error("Erro ao carregar GeoJSON das bacias:", err);
    }
}

function updateCorridasFilters() {
    if (!corridasGeoJSONData) return;
    
    const maxArea = parseFloat(document.getElementById('input-max-area').value);
    const minAmpCorridas = parseFloat(document.getElementById('input-min-amp-corridas').value);
    const minAmpEnxurradas = parseFloat(document.getElementById('input-min-amp-enxurradas').value);
    const minMelton = parseFloat(document.getElementById('input-min-melton').value);
    
    if (corridasAllBasinsLayer) {
        map.removeLayer(corridasAllBasinsLayer);
    }
    
    let total = 0, corridas = 0, enxurradas = 0, semSusc = 0;
    
    // First pass: Classify all features and update counters
    corridasGeoJSONData.features.forEach(feature => {
        const p = feature.properties;
        const area = p.area_km2 || 0;
        const amp = p.h_amp || 0;
        const melton = p.melton || 0;
        
        let type = 'none';
        total++;
        
        if (area <= maxArea && melton >= minMelton) {
            if (amp >= minAmpCorridas) {
                type = 'corrida';
                corridas++;
            } else if (amp >= minAmpEnxurradas) {
                type = 'enxurrada';
                enxurradas++;
            } else {
                semSusc++;
            }
        } else {
            semSusc++;
        }
        
        feature.properties._currentClass = type;
    });
    
    const opacity = parseFloat(document.getElementById('opacity-slider') ? document.getElementById('opacity-slider').value : 0.8);
    
    // Second pass: Render only susceptible features on the map
    corridasAllBasinsLayer = L.geoJSON(corridasGeoJSONData, {
        filter: function(feature) {
            return feature.properties._currentClass !== 'none';
        },
        style: function(feature) {
            const type = feature.properties._currentClass;
            if (type === 'corrida') {
                return { fillColor: '#e74c3c', weight: 1, color: '#c0392b', opacity: opacity, fillOpacity: opacity * 0.8 };
            } else {
                return { fillColor: '#3498db', weight: 1, color: '#2980b9', opacity: opacity, fillOpacity: opacity * 0.8 };
            }
        },
        onEachFeature: function(feature, layer) {
            const p = feature.properties;
            layer.bindPopup(`
                <div style="font-family:Inter; font-size:12px;">
                    <b style="color:${p._currentClass === 'corrida' ? '#e74c3c' : '#3498db'};">${p._currentClass === 'corrida' ? 'Corrida de Massa' : 'Enxurrada'}</b><br>
                    <b>Área:</b> ${p.area_km2 ? p.area_km2.toFixed(2) : 0} km²<br>
                    <b>Amplitude:</b> ${p.h_amp ? p.h_amp.toFixed(1) : 0} m<br>
                    <b>Melton:</b> ${p.melton ? p.melton.toFixed(3) : 0}<br>
                    <b>Ordem Strahler:</b> ${p.strahler || '-'}
                </div>
            `);
        }
    });
    
    if (currentMode === 'corridas') {
        corridasAllBasinsLayer.addTo(map);
        renderCorridasLegend();
    }
    
    // Zoom to layer if valid
    if (corridasAllBasinsLayer && corridasAllBasinsLayer.getLayers().length > 0) {
        map.fitBounds(corridasAllBasinsLayer.getBounds(), { padding: [20, 20] });
    }
    
    // Mostra painéis laterais de corridas
    const panelFilters = document.getElementById('panel-corridas-filters');
    const panelStats = document.getElementById('panel-corridas-stats');
    if (panelFilters) panelFilters.style.display = 'block';
    if (panelStats) panelStats.style.display = 'block';
    
    renderCorridasStats(total, corridas, enxurradas, semSusc);
}

function renderCorridasStats(total, corridas, enxurradas, semSusc) {
    document.getElementById('stat-corridas-total').innerText = total;
    document.getElementById('stat-corridas-corridas').innerText = corridas;
    document.getElementById('stat-corridas-enxurradas').innerText = enxurradas;
    document.getElementById('stat-corridas-semsusc').innerText = semSusc;
}

function syncCorridasSlider(sourceId, targetId) {
    const val = document.getElementById(sourceId).value;
    document.getElementById(targetId).value = val;
}

async function exportCorridasShapefile() {
    if (!corridasSessionId) return;
    
    const btn = document.getElementById('btn-export-corridas');
    const status = document.getElementById('system-status');
    
    btn.disabled = true;
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Exportando...`;
    status.innerHTML = `<i class="fa-solid fa-hourglass-half"></i> Exportando...`;
    
    const formData = new FormData();
    formData.append('session_id', corridasSessionId);
    formData.append('max_area_km2', document.getElementById('input-max-area').value);
    formData.append('min_amp_corridas', document.getElementById('input-min-amp-corridas').value);
    formData.append('min_amp_enxurradas', document.getElementById('input-min-amp-enxurradas').value);
    formData.append('min_melton', document.getElementById('input-min-melton').value);
    
    try {
        const res = await fetch('/api/corridas/filter-export', {
            method: 'POST',
            body: formData
        });
        
        const data = await res.json();
        if (data.status === 'success') {
            status.innerHTML = `<i class="fa-solid fa-circle-check"></i> Exportado`;
            
            document.getElementById('link-download-corridas-shp').href = data.shp_zip_url;
            document.getElementById('link-download-corridas-shp').style.display = 'flex';
            
            document.getElementById('link-download-corridas-geojson').href = data.geojson_url;
            document.getElementById('link-download-corridas-geojson').style.display = 'flex';
        } else {
            alert('Erro ao exportar: ' + data.detail);
            status.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Erro`;
        }
    } catch(err) {
        console.error(err);
        alert('Erro ao exportar.');
        status.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Erro`;
    } finally {

        btn.disabled = false;
        btn.innerHTML = `<i class="fa-solid fa-file-export"></i> Exportar Shapefile`;
    }
}

function renderCorridasLegend() {
    const legend = document.getElementById('map-legend');
    legend.style.display = 'block';
    
    document.getElementById('legend-title').innerText = "Corridas e Enxurradas";
    
    const contBox = document.getElementById('legend-continuous-box');
    if (contBox) contBox.style.display = 'none';
    
    const jenksBox = document.getElementById('legend-jenks-box');
    if (jenksBox) jenksBox.style.display = 'none';
    
    const corridasBox = document.getElementById('legend-corridas-box');
    if (corridasBox) corridasBox.style.display = 'block';

    const inundBox = document.getElementById('legend-inundacao-box');
    if (inundBox) inundBox.style.display = 'none';
}

function renderInundacaoLegend() {
    const legend = document.getElementById('map-legend');
    legend.style.display = 'block';
    
    document.getElementById('legend-title').innerText = "Suscetibilidade à Inundação";
    
    const contBox = document.getElementById('legend-continuous-box');
    if (contBox) contBox.style.display = 'none';
    
    const jenksBox = document.getElementById('legend-jenks-box');
    if (jenksBox) jenksBox.style.display = 'none';
    
    const corridasBox = document.getElementById('legend-corridas-box');
    if (corridasBox) corridasBox.style.display = 'none';

    const inundBox = document.getElementById('legend-inundacao-box');
    if (inundBox) inundBox.style.display = 'block';
}

// -------------------------------------------------------------------------
// REALTIME INUNDACAO MAP ALGEBRA (CANVAS)
// -------------------------------------------------------------------------
let inundacaoRawImageData = null;
let inundacaoRawCanvas = null;
let inundacaoRawCtx = null;
let inundacaoRelevoMap = {};
let inundacaoStats = null;
let inundacaoCodeToId = {};
let inundacaoRealtimeOverlay = null;

function updateInundacaoBreak(idBase, val) {
    document.getElementById('input-' + idBase).value = val;
    document.getElementById('slider-' + idBase).value = val;
    updateInundacaoRealtimeCanvas();
}

function initInundacaoRealtime(data) {
    inundacaoStats = {
        alt: data.altimetria_stats,
        hand: data.hand_stats
    };
    
    inundacaoRelevoMap = data.relevo_codes || {};
    inundacaoCodeToId = data.code_to_id || {};
    
    const img = new Image();
    img.crossOrigin = 'Anonymous';
    img.onload = () => {
        inundacaoRawCanvas = document.createElement('canvas');
        inundacaoRawCanvas.width = img.width;
        inundacaoRawCanvas.height = img.height;
        inundacaoRawCtx = inundacaoRawCanvas.getContext('2d');
        inundacaoRawCtx.drawImage(img, 0, 0);
        inundacaoRawImageData = inundacaoRawCtx.getImageData(0, 0, inundacaoRawCanvas.width, inundacaoRawCanvas.height);
        
        const b = data.bounds;
        currentBounds = [[b.bottom, b.left], [b.top, b.right]];
        inundacaoBounds = currentBounds;
        
        map.fitBounds(inundacaoBounds, { padding: [20, 20] });
        updateInundacaoRealtimeCanvas();
    };
    img.src = data.overlays.raw_png;
}

function updateInundacaoRealtimeCanvas() {
    if (!inundacaoRawImageData || !inundacaoRawCanvas) return;
    
    const b_alt_1 = parseFloat(document.getElementById('input-alt-p34').value);
    const b_alt_2 = parseFloat(document.getElementById('input-alt-p66').value);
    
    const b_hand_1 = parseFloat(document.getElementById('input-hand-c1').value);
    const b_hand_2 = parseFloat(document.getElementById('input-hand-c2').value);
    
    // Build ID -> Weight map
    const weightInputs = document.querySelectorAll('.relevo-weight-input');
    const codeToWeight = {};
    weightInputs.forEach(inp => {
        codeToWeight[inp.getAttribute('data-code')] = parseInt(inp.value, 10);
    });
    
    const idToWeight = {};
    for (const [code, id] of Object.entries(inundacaoCodeToId)) {
        idToWeight[id] = codeToWeight[code] !== undefined ? codeToWeight[code] : 0;
    }
    
    const width = inundacaoRawCanvas.width;
    const height = inundacaoRawCanvas.height;
    const outCanvas = document.createElement('canvas');
    outCanvas.width = width;
    outCanvas.height = height;
    const outCtx = outCanvas.getContext('2d');
    const outData = outCtx.createImageData(width, height);
    
    const raw = inundacaoRawImageData.data;
    const out = outData.data;
    
    const alt_min = inundacaoStats.alt.min;
    const alt_max = inundacaoStats.alt.max === alt_min ? alt_min + 1.0 : inundacaoStats.alt.max;
    
    const hand_min = inundacaoStats.hand.min;
    const hand_max = inundacaoStats.hand.max === hand_min ? hand_min + 1.0 : inundacaoStats.hand.max;
    
    const opacity = document.getElementById('opacity-slider') ? parseFloat(document.getElementById('opacity-slider').value) : 0.8;
    const alpha = Math.round(opacity * 255);
    
    let red_h = 231, green_h = 76, blue_h = 60;   // Alta
    let red_m = 241, green_m = 196, blue_m = 15; // Media
    let red_b = 46, green_b = 204, blue_b = 113; // Baixa
    
    let no_relevo = Object.keys(inundacaoCodeToId).length === 0;

    // View mode: 'final' | 'alt' | 'hand'
    const viewModeRadios = document.getElementsByName('inund_view_mode');
    let viewMode = 'final';
    for (let i = 0; i < viewModeRadios.length; i++) {
        if (viewModeRadios[i].checked) {
            viewMode = viewModeRadios[i].value;
            break;
        }
    }

    for (let i = 0; i < raw.length; i += 4) {
        if (raw[i+3] === 0) continue; 
        
        let r_val = raw[i];
        let g_val = raw[i+1];
        let b_val = raw[i+2];
        
        let alt_val = alt_min + (r_val / 255.0) * (alt_max - alt_min);
        let hand_val = hand_min + (g_val / 255.0) * (hand_max - hand_min);
        
        let relevo_peso = 1;
        if (!no_relevo) {
            relevo_peso = idToWeight[b_val] || 0;
            if (relevo_peso === 0) continue; // masked out by relevo weight for ALL modes
        }
        
        let alt_peso = 1;
        if (alt_val <= b_alt_1) alt_peso = 3;
        else if (alt_val <= b_alt_2) alt_peso = 2;
        
        let hand_peso = 1;
        if (hand_val <= b_hand_1) hand_peso = 3;
        else if (hand_val <= b_hand_2) hand_peso = 2;
        
        let rr = 0, gg = 0, bb = 0;
        let is_valid = false;
        
        if (viewMode === 'final') {
            let soma = alt_peso + hand_peso + relevo_peso;
            if (soma >= 8) { rr = red_h; gg = green_h; bb = blue_h; is_valid = true; }
            else if (soma >= 6) { rr = red_m; gg = green_m; bb = blue_m; is_valid = true; }
            else if (soma >= 3) { rr = red_b; gg = green_b; bb = blue_b; is_valid = true; }
        } 
        else if (viewMode === 'alt') {
            if (alt_peso === 3) { rr = red_h; gg = green_h; bb = blue_h; is_valid = true; }
            else if (alt_peso === 2) { rr = red_m; gg = green_m; bb = blue_m; is_valid = true; }
            else if (alt_peso === 1) { rr = red_b; gg = green_b; bb = blue_b; is_valid = true; }
        }
        else if (viewMode === 'hand') {
            if (hand_peso === 3) { rr = red_h; gg = green_h; bb = blue_h; is_valid = true; }
            else if (hand_peso === 2) { rr = red_m; gg = green_m; bb = blue_m; is_valid = true; }
            else if (hand_peso === 1) { rr = red_b; gg = green_b; bb = blue_b; is_valid = true; }
        }
        
        if (is_valid) {
            out[i] = rr;
            out[i+1] = gg;
            out[i+2] = bb;
            out[i+3] = alpha;
        }
    }
    
    outCtx.putImageData(outData, 0, 0);
    const dataUrl = outCanvas.toDataURL();
    
    if (inundacaoRealtimeOverlay) {
        map.removeLayer(inundacaoRealtimeOverlay);
    }
    if (inundacaoBounds) {
        inundacaoRealtimeOverlay = L.imageOverlay(dataUrl, inundacaoBounds, { opacity: opacity });
        inundacaoRealtimeOverlay.addTo(map);
        inundacaoLayer = inundacaoRealtimeOverlay;
    }
}
