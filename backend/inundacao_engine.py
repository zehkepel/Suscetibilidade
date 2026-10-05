import numpy as np
import rasterio
from rasterio.features import shapes, rasterize, sieve
from rasterio.warp import reproject, calculate_default_transform, Resampling
from shapely.geometry import shape, Polygon, MultiPolygon
from shapely.ops import unary_union
import geopandas as gpd
import heapq
from collections import deque
import math
import os
import zipfile
import json
import shutil
import tempfile
import pyproj
from shapely.ops import transform as shapely_transform

def chaikin_smooth_geometry(geom, iters=2):
    if geom is None or geom.is_empty or iters <= 0:
        return geom
    def smooth_ring(coords):
        pts = np.array(coords, dtype=np.float64)
        for _ in range(iters):
            if len(pts) < 3:
                break
            p0 = pts[:-1]
            p1 = pts[1:]
            q = 0.75 * p0 + 0.25 * p1
            r = 0.25 * p0 + 0.75 * p1
            new_pts = np.empty((2 * len(q), 2), dtype=np.float64)
            new_pts[0::2] = q
            new_pts[1::2] = r
            new_pts = np.vstack([new_pts, new_pts[0]])
            pts = new_pts
        return pts
    def smooth_poly(poly):
        if poly.is_empty:
            return poly
        ext = smooth_ring(poly.exterior.coords)
        interiors = [smooth_ring(h.coords) for h in poly.interiors]
        return Polygon(ext, holes=interiors)
    if geom.geom_type == 'Polygon':
        return smooth_poly(geom)
    elif geom.geom_type == 'MultiPolygon':
        smoothed_list = [smooth_poly(p) for p in geom.geoms if not p.is_empty]
        return MultiPolygon(smoothed_list)
    return geom

def fill_depressions(dem, nodata_val=-9999):
    filled = dem.astype(np.float64, copy=True)
    rows, cols = filled.shape
    is_nodata = (dem == nodata_val) | np.isnan(dem)
    filled[is_nodata] = np.inf

    edge_cells = []
    for r in range(rows):
        if not is_nodata[r, 0]: edge_cells.append((filled[r, 0], r, 0))
        if not is_nodata[r, cols-1]: edge_cells.append((filled[r, cols-1], r, cols-1))
    for c in range(1, cols-1):
        if not is_nodata[0, c]: edge_cells.append((filled[0, c], 0, c))
        if not is_nodata[rows-1, c]: edge_cells.append((filled[rows-1, c], rows-1, c))

    heapq.heapify(edge_cells)
    visited = np.zeros_like(filled, dtype=bool)
    visited[is_nodata] = True
    for val, r, c in edge_cells:
        visited[r, c] = True

    dr = [-1, 1, 0, 0, -1, -1, 1, 1]
    dc = [0, 0, -1, 1, -1, 1, -1, 1]
    epsilon = 1e-3

    while edge_cells:
        val, r, c = heapq.heappop(edge_cells)
        for i in range(8):
            nr, nc = r + dr[i], c + dc[i]
            if 0 <= nr < rows and 0 <= nc < cols and not visited[nr, nc]:
                visited[nr, nc] = True
                if filled[nr, nc] < val + epsilon:
                    filled[nr, nc] = val + epsilon
                heapq.heappush(edge_cells, (filled[nr, nc], nr, nc))
                
    filled[is_nodata] = nodata_val
    return filled

def d8_flow(dem, nodata_val=-9999):
    rows, cols = dem.shape
    flow_dir = np.full((rows, cols), -1, dtype=np.int32)
    in_degree = np.zeros((rows, cols), dtype=np.int32)
    dr = [-1, -1, -1, 0, 1, 1, 1, 0]
    dc = [-1, 0, 1, 1, 1, 0, -1, -1]
    dists = [math.sqrt(2), 1.0, math.sqrt(2), 1.0, math.sqrt(2), 1.0, math.sqrt(2), 1.0]

    for r in range(rows):
        for c in range(cols):
            if dem[r, c] == nodata_val: continue
            min_drop = 0.0
            best_dir = -1
            best_nr, best_nc = -1, -1
            for i in range(8):
                nr, nc = r + dr[i], c + dc[i]
                if 0 <= nr < rows and 0 <= nc < cols and dem[nr, nc] != nodata_val:
                    drop = (dem[r, c] - dem[nr, nc]) / dists[i]
                    if drop > min_drop:
                        min_drop = drop
                        best_dir = i
                        best_nr, best_nc = nr, nc
            if best_dir != -1:
                flow_dir[r, c] = best_dir
                in_degree[best_nr, best_nc] += 1
    return flow_dir, in_degree

def compute_hand(dem, flow_dir, in_degree, threshold, nodata_val=-9999):
    rows, cols = dem.shape
    acc = np.ones((rows, cols), dtype=np.int32)
    acc[dem == nodata_val] = 0
    topo_order = []
    q = deque()
    for r in range(rows):
        for c in range(cols):
            if dem[r, c] != nodata_val and in_degree[r, c] == 0:
                q.append((r, c))
    dr = [-1, -1, -1, 0, 1, 1, 1, 0]
    dc = [-1, 0, 1, 1, 1, 0, -1, -1]
    while q:
        r, c = q.popleft()
        topo_order.append((r, c))
        d = flow_dir[r, c]
        if d != -1:
            nr, nc = r + dr[d], c + dc[d]
            acc[nr, nc] += acc[r, c]
            in_degree[nr, nc] -= 1
            if in_degree[nr, nc] == 0:
                q.append((nr, nc))
    drainage_mask = (acc >= threshold) & (dem != nodata_val)
    drainage_elev = np.full((rows, cols), np.nan, dtype=np.float64)
    drainage_elev[drainage_mask] = dem[drainage_mask]
    for r, c in reversed(topo_order):
        if not drainage_mask[r, c]:
            d = flow_dir[r, c]
            if d != -1:
                nr, nc = r + dr[d], c + dc[d]
                if not np.isnan(drainage_elev[nr, nc]):
                    drainage_elev[r, c] = drainage_elev[nr, nc]
    hand = dem - drainage_elev
    hand[hand < 0] = 0
    hand[dem == nodata_val] = np.nan
    return hand

def get_peso_relevo_relfei(cod):
    c = str(cod).strip()
    c1 = ['R1a2', 'R1b0', 'R1b1', 'R1b1_Fpra', 'R1b1_Fpar', 'R1b1_Fcrp', 'R1b1_Flac', 'R1b1_Filb', 'R1b1_Ftom', 'R1b1_Fdic', 'R1b2', 'R1b4', 'R1b7', 'R1b8', 'R1b8_Fabr', 'R1b8_Fcvm', 'R1b8_Farc', 'R1b8_Frec', 'R1b8_Fbea', 'R6b1']
    c2 = ['R1a1_Fdmg', 'R5a0', 'R5a1', 'R5a1_Fdol', 'R5a1_Fuva', 'R5a1_Fpol', 'R5a2', 'R5a2_Fvce', 'R5a2_Fsum', 'R5a2_Fcvc', 'R5b0', 'R5b1_Fab', 'R6b3', 'R6b3_Flla']
    c3 = ['R1a0', 'R1a1', 'R1a1_Fmab', 'R1a3', 'R1a4', 'R1a4_Fbpo', 'R1a4_Fbca', 'R1a4_Fifv', 'R1a4_Flaj', 'R1a4_Fcac', 'R1b3', 'R1b3_Fplm', 'R1b3_Fpla', 'R1b3_Fapi', 'R1b5', 'R1b5_Fcd', 'R1b5_Fid', 'R1b6', 'R6c0', 'R6c1', 'R6c1_Fbar', 'R6c2', 'R6c2_Fccm', 'R6c2_Fdcm', 'Água']
    if c in c1: return 1
    if c in c2: return 2
    if c in c3: return 3
    return 0

def get_peso_relevo_rel(cod):
    cod_str = str(cod).lower().strip()
    # Classe 3 (Alta suscetibilidade): planícies fluviais, leques aluviais, áreas alagáveis
    if cod_str in ['r1a', 'r1g', 'r1d1', 'r1d1a', 'r1d2', 'r1d3', 'r1d4', 'r1d5', 'r1d6a', 'r1d6b', 'r1e', 'r1c3']:
        return 3
    # Classe 2 (Média suscetibilidade): baixadas, depressões cársticas
    if cod_str in ['r1b4', 'r5a']:
        return 2
    # Classe 1 (Baixa suscetibilidade): rampas de alúvio-colúvio, terraços, depósitos
    if cod_str in ['r1b1', 'r1b2', 'r1b3', 'r1c1', 'r1h1']:
        return 1
    # Fallback por nome descritivo (para shapefiles com PADRAO ao invés de código)
    if 'plan' in cod_str or 'recife' in cod_str or 'maré' in cod_str or 'mare' in cod_str or 'restinga' in cod_str or 'brejo' in cod_str or 'mangue' in cod_str or 'leque' in cod_str:
        return 3
    if 'baixada' in cod_str or 'carstica' in cod_str or 'cárstica' in cod_str:
        return 2
    if 'terraco' in cod_str or 'terraço' in cod_str or 'rampa' in cod_str or 'deposito' in cod_str or 'depósito' in cod_str:
        return 1
    # R1c2, R2*, R4*, Agua e desconhecidos → excluídos
    return 0

def get_peso_relevo(cod, use_relfei=True):
    if use_relfei:
        peso = get_peso_relevo_relfei(cod)
        if peso > 0:
            return peso
        # Fallback: código não encontrado na tabela RELFEI, tenta pela tabela REL
        return get_peso_relevo_rel(cod)
    return get_peso_relevo_rel(cod)

def process_inundacao_insumos(mde_path, relevo_zip_path, drain_threshold, output_dir):
    """Passo 1: Calcula Altimetria e HAND, recorta pelo relevo, e retorna estatisticas e códigos de relevo"""
    os.makedirs(output_dir, exist_ok=True)
    with rasterio.open(mde_path) as src:
        dem = src.read(1)
        nodata = src.nodata or -9999
        transform = src.transform
        crs = src.crs
        profile = src.profile.copy()
        # O MDE fica na resolução nativa (conforme QGIS).
        # Apenas a rasterização do vetor de relevo usa 2.5m no QGIS original.

    rows, cols = dem.shape
    relevo_grid = np.ones((rows, cols), dtype=np.float32)
    relevo_fornecido = False
    relevo_codes_found = {} # cod: peso default
    
    # Process Shapefile
    if relevo_zip_path:
        temp_dir = tempfile.mkdtemp()
        try:
            with zipfile.ZipFile(relevo_zip_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            shp_file = next((os.path.join(r, f) for r, _, fs in os.walk(temp_dir) for f in fs if f.endswith('.shp')), None)
            if shp_file:
                gdf = gpd.read_file(shp_file)
                if gdf.crs is not None and gdf.crs != crs:
                    try:
                        gdf = gdf.to_crs(crs)
                    except: pass
                
                if gdf.empty:
                    raise Exception("O shapefile de relevo enviado está vazio (zero polígonos). Verifique se você não exportou uma seleção vazia no seu software de SIG.")
                
                col_cod_relfei = next((c for c in gdf.columns if 'cod_relfei' in c.lower()), None)
                col_cod_rel = next((c for c in gdf.columns if c.lower() == 'cod_rel'), None) if not col_cod_relfei else None
                col_relevo = col_cod_relfei or col_cod_rel
                use_relfei = col_cod_relfei is not None

                if col_relevo:
                    relevo_fornecido = True
                    relevo_grid = np.zeros((rows, cols), dtype=np.float32)
                    # Get unique codes
                    unique_codes = gdf[col_relevo].dropna().unique()
                    for c in unique_codes:
                        relevo_codes_found[str(c)] = get_peso_relevo(c, use_relfei)
                    
                    code_to_id = {c: i+1 for i, c in enumerate(unique_codes)}
                    geom_id_pairs = []
                    for idx, row in gdf.iterrows():
                        cod = row[col_relevo]
                        if cod in code_to_id:
                            geom = row['geometry']
                            peso = get_peso_relevo(cod, use_relfei)
                            if peso > 0 and geom and not geom.is_empty:
                                try:
                                    geom = geom.buffer(15.0).buffer(0)
                                except:
                                    pass
                            geom_id_pairs.append((geom, code_to_id[cod]))
                    
                    if geom_id_pairs:
                        rasterized = rasterize(geom_id_pairs, out_shape=(rows, cols), transform=transform, fill=0, dtype=np.int32)
                        relevo_grid = rasterized
                        # Save mapping
                        with open(os.path.join(output_dir, "relevo_mapping.json"), "w", encoding="utf-8") as f:
                            json.dump({"code_to_id": code_to_id, "default_weights": relevo_codes_found, "use_relfei": use_relfei}, f)
                        
                        # Save vector for final clipping
                        gdf.to_file(os.path.join(output_dir, "relevo_vetor.shp"), driver='ESRI Shapefile', encoding='utf-8')
                        
                        # Save original (unbuffered) relief for final clipping
                        # Salva TODAS as geometrias — a filtragem por peso acontece na etapa 2
                        gdf.to_file(os.path.join(output_dir, 'relevo_original.shp'), driver='ESRI Shapefile', encoding='utf-8')
        finally:
            shutil.rmtree(temp_dir)
            
    filled = fill_depressions(dem, nodata)
    altimetria = filled.copy().astype(np.float64)
    altimetria[dem == nodata] = np.nan
    
    flow_dir, in_degree = d8_flow(filled, nodata)
    hand = compute_hand(filled, flow_dir, in_degree, drain_threshold, nodata)
    hand = hand.astype(np.float64)
    
    # Mask outside susceptible relief
    if relevo_fornecido:
        altimetria[relevo_grid == 0] = np.nan
        hand[relevo_grid == 0] = np.nan
        
    # Save base rasters
    profile.update(dtype=rasterio.float64, nodata=np.nan, transform=transform, width=cols, height=rows)
    with rasterio.open(os.path.join(output_dir, "altimetria.tif"), "w", **profile) as dst:
        dst.write(altimetria, 1)
    with rasterio.open(os.path.join(output_dir, "hand.tif"), "w", **profile) as dst:
        dst.write(hand, 1)
        
    profile_int = profile.copy()
    profile_int.update(dtype=rasterio.int32, nodata=0)
    with rasterio.open(os.path.join(output_dir, "relevo.tif"), "w", **profile_int) as dst:
        dst.write(relevo_grid.astype(np.int32), 1)
        
    # Meta
    with open(os.path.join(output_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"crs": crs.to_string() if crs else "", "transform": list(transform)}, f)
        
    def calc_stats_alt(arr):
        v = arr[~np.isnan(arr)]
        if len(v) == 0: return {"p34": 0, "p66": 0, "min": 0, "max": 0}
        return {
            "p34": float(np.percentile(v, 34)),
            "p66": float(np.percentile(v, 66)),
            "min": float(np.min(v)),
            "max": float(np.max(v))
        }

    def calc_stats_hand(arr):
        v = arr[~np.isnan(arr)]
        if len(v) == 0: return {"min": 0, "max": 0}
        return {
            "min": float(np.min(v)),
            "max": float(np.max(v))
        }
        
    return {
        "status": "success",
        "altimetria_stats": calc_stats_alt(altimetria),
        "hand_stats": calc_stats_hand(hand),
        "relevo_codes": relevo_codes_found,
        "code_to_id": code_to_id if relevo_fornecido else {}
    }

def reclassify_vectorize_inundacao(session_dir, b_alt, b_hand, relevo_weights):
    """Passo 2: Álgebra de Mapas e Vetorização Topológica"""
    alt_path = os.path.join(session_dir, "altimetria.tif")
    hand_path = os.path.join(session_dir, "hand.tif")
    rel_path = os.path.join(session_dir, "relevo.tif")
    map_path = os.path.join(session_dir, "relevo_mapping.json")
    meta_path = os.path.join(session_dir, "meta.json")
    
    with rasterio.open(alt_path) as src: altimetria = src.read(1)
    with rasterio.open(hand_path) as src: hand = src.read(1)
    with rasterio.open(rel_path) as src: relevo_grid = src.read(1)
    
    with open(meta_path, "r") as f: meta = json.load(f)
    transform = rasterio.transform.Affine(*meta["transform"])
    crs_str = meta.get("crs", "")
    crs = rasterio.crs.CRS.from_string(crs_str) if crs_str else None
    
    # Relevo
    relevo_peso_grid = np.zeros(relevo_grid.shape, dtype=np.float32)
    relevo_fornecido = False
    use_relfei = True
    if os.path.exists(map_path):
        relevo_fornecido = True
        with open(map_path, "r") as f: rmap = json.load(f)
        use_relfei = rmap.get("use_relfei", True)
        for cod, r_id in rmap["code_to_id"].items():
            peso = float(relevo_weights.get(cod, 0))
            relevo_peso_grid[relevo_grid == r_id] = peso
    else:
        relevo_peso_grid = np.ones_like(relevo_grid, dtype=np.float32)
        
    # Reclass Altimetria (Invertido)
    alt_class = np.zeros_like(altimetria, dtype=np.float32)
    alt_class[altimetria <= b_alt[0]] = 3.0
    alt_class[(altimetria > b_alt[0]) & (altimetria <= b_alt[1])] = 2.0
    alt_class[altimetria > b_alt[1]] = 1.0
    alt_class[np.isnan(altimetria)] = np.nan
    
    # Reclass HAND (Invertido)
    hand_class = np.zeros_like(hand, dtype=np.float32)
    hand_class[hand <= b_hand[0]] = 3.0
    hand_class[(hand > b_hand[0]) & (hand <= b_hand[1])] = 2.0
    hand_class[hand > b_hand[1]] = 1.0
    hand_class[np.isnan(hand)] = np.nan
    
    soma = relevo_peso_grid + alt_class + hand_class
    
    custom_arr = np.full(soma.shape, -9999, dtype=np.int16)
    valid_mask = ~np.isnan(soma)
    if relevo_fornecido: valid_mask &= (relevo_peso_grid > 0)
    
    custom_arr[valid_mask & (soma >= 3) & (soma <= 5)] = 1 # Baixa
    custom_arr[valid_mask & (soma >= 6) & (soma <= 7)] = 2 # Media
    custom_arr[valid_mask & (soma >= 8) & (soma <= 9)] = 3 # Alta
    
    # Liberar arrays intermediários
    del soma, valid_mask, alt_class, hand_class, relevo_peso_grid
    import gc; gc.collect()
    
    # Reamostrar para 2.5m antes da vetorização (conforme QGIS)
    # Usa disco para o reproject, mas precisa ler o resultado na RAM para sieve/shapes
    native_res = abs(transform[0])
    target_res = 2.5
    
    if native_res > target_res * 1.5:
        rows_native, cols_native = custom_arr.shape
        scale = native_res / target_res
        new_cols = int(cols_native * scale)
        new_rows = int(rows_native * scale)
        new_transform = rasterio.transform.from_bounds(
            transform.c,
            transform.f + transform.e * rows_native,
            transform.c + transform.a * cols_native,
            transform.f,
            new_cols, new_rows
        )
        
        # Gravar nativo e reamostrar via disco
        tmp_native = os.path.join(session_dir, "_class_native.tif")
        tmp_resampled = os.path.join(session_dir, "_class_25m.tif")
        with rasterio.open(tmp_native, 'w', driver='GTiff', height=rows_native,
                           width=cols_native, count=1, dtype='int16',
                           crs=crs, transform=transform, nodata=-9999) as dst:
            dst.write(custom_arr, 1)
        
        del custom_arr; gc.collect()
        
        with rasterio.open(tmp_native) as src:
            with rasterio.open(tmp_resampled, 'w', driver='GTiff',
                               height=new_rows, width=new_cols, count=1,
                               dtype='int16', crs=crs, transform=new_transform,
                               nodata=-9999) as dst:
                reproject(
                    source=rasterio.band(src, 1),
                    destination=rasterio.band(dst, 1),
                    src_transform=transform,
                    src_crs=crs,
                    dst_transform=new_transform,
                    dst_crs=crs,
                    src_nodata=-9999,
                    dst_nodata=-9999,
                    resampling=Resampling.nearest
                )
        
        try: os.remove(tmp_native)
        except: pass
        
        # Ler reamostrado do disco
        with rasterio.open(tmp_resampled) as src25:
            vec_arr = src25.read(1)
            vec_transform = src25.transform
        
        try: os.remove(tmp_resampled)
        except: pass
    else:
        vec_arr = custom_arr
        vec_transform = transform
        del custom_arr; gc.collect()
    
    # Sieve filter: 900 pixels a 2.5m = 5625 m²
    nodata_mask = (vec_arr == -9999)
    vec_arr[nodata_mask] = 0
    # Sieve in-place para evitar duplicar o array
    sieved = sieve(vec_arr, size=900, connectivity=8)
    del vec_arr; gc.collect()
    vec_arr = sieved
    del sieved; gc.collect()
    vec_arr[nodata_mask] = -9999
    del nodata_mask; gc.collect()

    raw_c1_geoms = [shape(s) for s, v in shapes(vec_arr, mask=(vec_arr == 1), transform=vec_transform, connectivity=8)]
    raw_c2_geoms = [shape(s) for s, v in shapes(vec_arr, mask=(vec_arr == 2), transform=vec_transform, connectivity=8)]
    raw_c3_geoms = [shape(s) for s, v in shapes(vec_arr, mask=(vec_arr == 3), transform=vec_transform, connectivity=8)]
    
    del vec_arr; gc.collect()
    
    raw_c1 = unary_union(raw_c1_geoms) if raw_c1_geoms else Polygon()
    raw_c2 = unary_union(raw_c2_geoms) if raw_c2_geoms else Polygon()
    raw_c3 = unary_union(raw_c3_geoms) if raw_c3_geoms else Polygon()
    del raw_c1_geoms, raw_c2_geoms, raw_c3_geoms; gc.collect()
    
    # Suavizar geometrias (Douglas-Peucker + buffer smooth, conforme QGIS Model 06/07)
    raw_c1 = raw_c1.simplify(5.0, preserve_topology=True).buffer(2.5).buffer(-2.5) if not raw_c1.is_empty else raw_c1
    raw_c2 = raw_c2.simplify(5.0, preserve_topology=True).buffer(2.5).buffer(-2.5) if not raw_c2.is_empty else raw_c2
    raw_c3 = raw_c3.simplify(5.0, preserve_topology=True).buffer(2.5).buffer(-2.5) if not raw_c3.is_empty else raw_c3

    valid_relevo_geom = None
    if relevo_fornecido:
        try:
            rv_path = os.path.join(session_dir, "relevo_original.shp")
            if os.path.exists(rv_path):
                gdf_rv = gpd.read_file(rv_path)
                col_cod_relfei = next((c for c in gdf_rv.columns if 'cod_relfei' in c.lower()), None)
                col_cod_rel = next((c for c in gdf_rv.columns if c.lower() == 'cod_rel'), None) if not col_cod_relfei else None
                col_relevo = col_cod_relfei or col_cod_rel
                if col_relevo:
                    valid_geoms = []
                    for _, row in gdf_rv.iterrows():
                        cod_val = str(row[col_relevo])
                        peso = float(relevo_weights.get(cod_val, 0))
                        is_agua = 'agua' in cod_val.lower() or 'água' in cod_val.lower()
                        if peso > 0 and not is_agua:
                            valid_geoms.append(row.geometry)
                    if valid_geoms:
                        valid_relevo_geom = unary_union(valid_geoms).buffer(0)
        except:
            pass

    if valid_relevo_geom and not valid_relevo_geom.is_empty:
        poly_c1 = raw_c1.intersection(valid_relevo_geom)
        poly_c2 = raw_c2.intersection(valid_relevo_geom)
        poly_c3 = raw_c3.intersection(valid_relevo_geom)
    else:
        poly_c1 = raw_c1
        poly_c2 = raw_c2
        poly_c3 = raw_c3
    
    records = []
    def add_record(geom, gridcode, cls_name):
        if geom.is_empty: return
        if geom.geom_type == 'Polygon': g_list = [geom]
        elif geom.geom_type == 'MultiPolygon': g_list = geom.geoms
        else: return
        for g in g_list:
            if g.area > 25.0:  # mínimo 25 m² (4 pixels a 2.5m)
                records.append({
                    'gridcode': gridcode, 'Classe': cls_name, 'MUNICIPIO': '', 'UF': '',
                    'PROCESSO': 'Inundação', 'OBS': '', 'FONTE': '', 'EXECUCAO': '', 'PROJETO': '', 'geometry': g
                })
                
    add_record(poly_c1, 1, 'Baixa')
    add_record(poly_c2, 2, 'Média')
    add_record(poly_c3, 3, 'Alta')
    if not records:
        gdf_native = gpd.GeoDataFrame(columns=['gridcode', 'Classe', 'MUNICIPIO', 'UF', 'PROCESSO', 'OBS', 'FONTE', 'EXECUCAO', 'PROJETO', 'geometry'], geometry='geometry', crs=crs)
    else:
        gdf_native = gpd.GeoDataFrame(records, crs=crs)
        
    shp_base = os.path.join(session_dir, "inundacao_vetor")
    if not gdf_native.empty:
        gdf_native.to_file(shp_base + ".shp", driver='ESRI Shapefile', encoding='utf-8')
    else:
        pass
    
    zip_path = shp_base + ".zip"
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for ext in ['.shp', '.shx', '.dbf', '.prj', '.cpg']:
            f = shp_base + ext
            if os.path.exists(f): z.write(f, os.path.basename(f))
            
    try:
        gdf_wgs84 = gdf_native.to_crs(epsg=4326)
    except:
        gdf_wgs84 = gdf_native.copy()
        
    geojson_path = os.path.join(session_dir, "inundacao_vetor.geojson")
    gdf_wgs84.to_file(geojson_path, driver='GeoJSON')
    
    import time
    timestamp = int(time.time())
    rel_out = "/storage/outputs/" + os.path.basename(session_dir)
    return {
        "status": "success",
        "geojson_url": f"{rel_out}/inundacao_vetor.geojson?t={timestamp}",
        "shp_zip_url": f"{rel_out}/inundacao_vetor.zip?t={timestamp}"
    }
