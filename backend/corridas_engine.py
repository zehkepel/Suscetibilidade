import numpy as np
import rasterio
from rasterio.features import shapes
import rasterio.warp
import geopandas as gpd
from shapely.geometry import shape, Polygon, MultiPolygon
import heapq
from collections import deque
import math
import os
import zipfile
import json

__all__ = ['process_corridas_enxurradas', 'filter_and_export_corridas']

D8_OFFSETS = {
    1: (0, 1), 2: (1, 1), 4: (1, 0), 8: (1, -1),
    16: (0, -1), 32: (-1, -1), 64: (-1, 0), 128: (-1, 1)
}
D8_REVERSE = {1: 16, 2: 32, 4: 64, 8: 128, 16: 1, 32: 2, 64: 4, 128: 8}
D8_TUPLES = [
    (0, 1, 1, 1.0), (1, 1, 2, 1.41421356), (1, 0, 4, 1.0), (1, -1, 8, 1.41421356),
    (0, -1, 16, 1.0), (-1, -1, 32, 1.41421356), (-1, 0, 64, 1.0), (-1, 1, 128, 1.41421356)
]

def fill_depressions(dem, valid_mask):
    """
    Preenche as depressões do Modelo Digital de Elevação utilizando o algoritmo de Planchon-Darboux.
    Recebe valid_mask pré-calculado para evitar inconsistências de comparação float.
    Usa float64 para preservar gradientes mínimos em áreas planas.
    """
    rows, cols = dem.shape
    filled = np.full((rows, cols), np.inf, dtype=np.float64)
    pq = []
    
    for r in range(rows):
        for c in range(cols):
            if not valid_mask[r, c]:
                filled[r, c] = float(dem[r, c])
                continue
                
            is_border = (r == 0 or r == rows - 1 or c == 0 or c == cols - 1)
            if not is_border:
                for dr, dc, _, _ in D8_TUPLES:
                    nr, nc = r + dr, c + dc
                    if not (0 <= nr < rows and 0 <= nc < cols and valid_mask[nr, nc]):
                        is_border = True
                        break
            
            if is_border:
                filled[r, c] = float(dem[r, c])
                heapq.heappush(pq, (float(dem[r, c]), r, c))
                
    # Epsilon maior para garantir gradiente em float64
    epsilon = 1e-3
    
    while pq:
        elev, r, c = heapq.heappop(pq)
        
        if elev > filled[r, c]:
            continue
            
        for dr, dc, _, _ in D8_TUPLES:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and valid_mask[nr, nc]:
                next_elev = max(float(dem[nr, nc]), elev + epsilon)
                if next_elev < filled[nr, nc]:
                    filled[nr, nc] = next_elev
                    heapq.heappush(pq, (next_elev, nr, nc))
                    
    return filled

def compute_d8_flow_direction(filled_dem, valid_mask):
    """
    Calcula a direção de fluxo D8 com convenção do ArcGIS.
    Recebe valid_mask pré-calculado para consistência.
    """
    rows, cols = filled_dem.shape
    fdir = np.zeros((rows, cols), dtype=np.int32)
    
    for r in range(rows):
        for c in range(cols):
            if not valid_mask[r, c]:
                continue
                
            max_slope = 0
            best_dir = 0
            elev = float(filled_dem[r, c])
            
            for dr, dc, code, dist in D8_TUPLES:
                nr, nc = r + dr, c + dc
                if 0 <= nr < rows and 0 <= nc < cols and valid_mask[nr, nc]:
                    slope = (elev - float(filled_dem[nr, nc])) / dist
                    if slope > max_slope:
                        max_slope = slope
                        best_dir = code
                        
            fdir[r, c] = best_dir
            
    return fdir

def compute_d8_flow_accumulation(fdir, valid_mask):
    """
    Calcula a acumulação de fluxo (contagem de pixels a montante).
    """
    rows, cols = fdir.shape
    acc = np.zeros((rows, cols), dtype=np.int32)
    in_degree = np.zeros((rows, cols), dtype=np.int32)
    
    for r in range(rows):
        for c in range(cols):
            if not valid_mask[r, c] or fdir[r, c] == 0:
                continue
            dr, dc = D8_OFFSETS[fdir[r, c]]
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and valid_mask[nr, nc]:
                in_degree[nr, nc] += 1
                
    queue = deque()
    for r in range(rows):
        for c in range(cols):
            if valid_mask[r, c] and in_degree[r, c] == 0:
                queue.append((r, c))
                
    while queue:
        r, c = queue.popleft()
        if fdir[r, c] != 0:
            dr, dc = D8_OFFSETS[fdir[r, c]]
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and valid_mask[nr, nc]:
                acc[nr, nc] += acc[r, c] + 1
                in_degree[nr, nc] -= 1
                if in_degree[nr, nc] == 0:
                    queue.append((nr, nc))
                    
    return acc

def extract_stream_network(acc, valid_mask, threshold=500):
    """
    Extrai a rede de drenagem a partir de um limiar de acumulação.
    """
    return (acc >= threshold) & valid_mask

def compute_strahler_order(stream_raster, fdir, valid_mask):
    """
    Calcula a ordem de Strahler para a rede de drenagem.
    """
    rows, cols = fdir.shape
    order = np.zeros((rows, cols), dtype=np.int32)
    in_degree = np.zeros((rows, cols), dtype=np.int32)
    tributaries = [[[] for _ in range(cols)] for _ in range(rows)]
    
    for r in range(rows):
        for c in range(cols):
            if not stream_raster[r, c]:
                continue
            code = fdir[r, c]
            if code in D8_OFFSETS:
                dr, dc = D8_OFFSETS[code]
                nr, nc = r + dr, c + dc
                if 0 <= nr < rows and 0 <= nc < cols and stream_raster[nr, nc]:
                    in_degree[nr, nc] += 1
                    
    queue = deque()
    for r in range(rows):
        for c in range(cols):
            if stream_raster[r, c] and in_degree[r, c] == 0:
                queue.append((r, c))
                order[r, c] = 1
                
    while queue:
        r, c = queue.popleft()
        
        if len(tributaries[r][c]) > 0:
            max_ord = max(tributaries[r][c])
            count_max = tributaries[r][c].count(max_ord)
            if count_max >= 2:
                order[r, c] = max_ord + 1
            else:
                order[r, c] = max_ord
                
        code = fdir[r, c]
        if code in D8_OFFSETS:
            dr, dc = D8_OFFSETS[code]
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and stream_raster[nr, nc]:
                tributaries[nr][nc].append(order[r, c])
                in_degree[nr, nc] -= 1
                if in_degree[nr, nc] == 0:
                    queue.append((nr, nc))
                    
    return order

def compute_stream_links(stream_raster, fdir, valid_mask):
    """
    Identifica segmentos únicos da rede de drenagem (links).
    """
    rows, cols = fdir.shape
    links = np.zeros((rows, cols), dtype=np.int32)
    in_degree = np.zeros((rows, cols), dtype=np.int32)
    
    for r in range(rows):
        for c in range(cols):
            if not stream_raster[r, c]:
                continue
            code = fdir[r, c]
            if code in D8_OFFSETS:
                dr, dc = D8_OFFSETS[code]
                nr, nc = r + dr, c + dc
                if 0 <= nr < rows and 0 <= nc < cols and stream_raster[nr, nc]:
                    in_degree[nr, nc] += 1
                    
    link_id = 1
    
    for r in range(rows):
        for c in range(cols):
            if not stream_raster[r, c]:
                continue
            
            if in_degree[r, c] != 1:
                curr_r, curr_c = r, c
                
                if in_degree[r, c] == 0:
                    links[curr_r, curr_c] = link_id
                
                code = fdir[curr_r, curr_c]
                if code in D8_OFFSETS:
                    dr, dc = D8_OFFSETS[code]
                    next_r, next_c = curr_r + dr, curr_c + dc
                    
                    if 0 <= next_r < rows and 0 <= next_c < cols and stream_raster[next_r, next_c]:
                        curr_id = link_id if in_degree[r, c] == 0 else link_id
                        if in_degree[r, c] > 0:
                            curr_id = link_id
                        
                        curr_r, curr_c = next_r, next_c
                        while 0 <= curr_r < rows and 0 <= curr_c < cols and stream_raster[curr_r, curr_c]:
                            links[curr_r, curr_c] = curr_id
                            if in_degree[curr_r, curr_c] != 1:
                                break
                            
                            code2 = fdir[curr_r, curr_c]
                            if code2 in D8_OFFSETS:
                                dr2, dc2 = D8_OFFSETS[code2]
                                curr_r, curr_c = curr_r + dr2, curr_c + dc2
                            else:
                                break
                                
                link_id += 1
                
    return links

def find_tributary_pour_points(strahler_grid, stream_links, fdir, stream_raster, valid_mask):
    """
    Identifica exutórios de sub-bacias tributárias com base na ordem de Strahler.
    """
    rows, cols = fdir.shape
    pour_points = []
    pour_points_dict = {}
    pp_idx = 1
    
    unique_links = np.unique(stream_links)
    unique_links = unique_links[unique_links > 0]
    
    for link in unique_links:
        link_cells = np.argwhere(stream_links == link)
        if len(link_cells) == 0:
            continue
            
        order = strahler_grid[link_cells[0][0], link_cells[0][1]]
        
        most_downstream = None
        for r, c in link_cells:
            code = fdir[r, c]
            if code in D8_OFFSETS:
                dr, dc = D8_OFFSETS[code]
                nr, nc = r + dr, c + dc
                if not (0 <= nr < rows and 0 <= nc < cols and stream_links[nr, nc] == link):
                    most_downstream = (r, c)
                    break
            else:
                most_downstream = (r, c)
                
        if not most_downstream:
            most_downstream = (link_cells[-1][0], link_cells[-1][1])
            
        r_ds, c_ds = most_downstream
        code = fdir[r_ds, c_ds]
        if code in D8_OFFSETS:
            dr, dc = D8_OFFSETS[code]
            nr, nc = r_ds + dr, c_ds + dc
            
            if 0 <= nr < rows and 0 <= nc < cols and stream_raster[nr, nc]:
                downstream_order = strahler_grid[nr, nc]
                if downstream_order >= 3 and order < 4:
                    pour_points.append((r_ds, c_ds, order, 0))
                    pour_points_dict[pp_idx] = (r_ds, c_ds, order)
                    pp_idx += 1
                    
    return pour_points, pour_points_dict

def compute_watersheds(fdir, pour_points_dict, valid_mask):
    """
    Delineia as bacias hidrográficas a partir dos exutórios (pour points).
    """
    rows, cols = fdir.shape
    basins = np.zeros((rows, cols), dtype=np.int32)
    
    for pp_idx, (r, c, _) in pour_points_dict.items():
        if valid_mask[r, c]:
            basins[r, c] = pp_idx
            
    visited = np.zeros((rows, cols), dtype=bool)
    
    for r in range(rows):
        for c in range(cols):
            if not valid_mask[r, c] or basins[r, c] != 0 or visited[r, c]:
                continue
                
            path = []
            curr_r, curr_c = r, c
            found_basin = 0
            
            while 0 <= curr_r < rows and 0 <= curr_c < cols and valid_mask[curr_r, curr_c]:
                if basins[curr_r, curr_c] != 0:
                    found_basin = basins[curr_r, curr_c]
                    if found_basin == -1: found_basin = 0
                    break
                if visited[curr_r, curr_c]:
                    break
                    
                path.append((curr_r, curr_c))
                visited[curr_r, curr_c] = True
                
                code = fdir[curr_r, curr_c]
                if code in D8_OFFSETS:
                    dr, dc = D8_OFFSETS[code]
                    curr_r, curr_c = curr_r + dr, curr_c + dc
                else:
                    break
                    
            label = found_basin if found_basin > 0 else -1
            for pr, pc in path:
                basins[pr, pc] = label
                
    return basins

def compute_basin_morphometry(basin_raster, dem, transform, crs, pour_point_info):
    """
    Calcula a morfometria das bacias delineadas (área, amplitude altimétrica, Índice de Melton).
    """
    rows, cols = basin_raster.shape
    morph_data = {}
    
    is_geographic = crs.is_geographic if crs else True
    
    for r in range(rows):
        for c in range(cols):
            basin_id = basin_raster[r, c]
            if basin_id > 0:
                elev = dem[r, c]
                if basin_id not in morph_data:
                    morph_data[basin_id] = {'h_min': elev, 'h_max': elev, 'count': 0, 'r': r}
                else:
                    morph_data[basin_id]['h_min'] = min(morph_data[basin_id]['h_min'], elev)
                    morph_data[basin_id]['h_max'] = max(morph_data[basin_id]['h_max'], elev)
                morph_data[basin_id]['count'] += 1
                
    results = {}
    for basin_id, data in morph_data.items():
        if is_geographic:
            lat_center = transform.f + data['r'] * transform.e
            meters_per_deg_lat = 111320
            meters_per_deg_lon = 111320 * math.cos(math.radians(lat_center))
            pixel_area = abs(transform.a * meters_per_deg_lon) * abs(transform.e * meters_per_deg_lat)
        else:
            pixel_area = abs(transform.a * transform.e)
            
        area_m2 = data['count'] * pixel_area
        area_km2 = area_m2 / 1e6
        h_amp = data['h_max'] - data['h_min']
        melton = h_amp / math.sqrt(area_m2) if area_m2 > 0 else 0
        strahler = pour_point_info.get(basin_id, (0, 0, 0))[2]
        
        results[basin_id] = {
            'h_min': float(data['h_min']),
            'h_max': float(data['h_max']),
            'h_amp': float(h_amp),
            'area_m2': float(area_m2),
            'area_km2': float(area_km2),
            'melton': float(melton),
            'strahler': int(strahler)
        }
        
    return results

def chaikin_smooth(coords, iterations=2):
    """
    Aplica suavização Chaikin para as bordas dos polígonos.
    """
    if len(coords) < 3:
        return coords
        
    smoothed = list(coords)
    for _ in range(iterations):
        new_coords = []
        for i in range(len(smoothed) - 1):
            p0 = smoothed[i]
            p1 = smoothed[i + 1]
            q = (0.75 * p0[0] + 0.25 * p1[0], 0.75 * p0[1] + 0.25 * p1[1])
            r = (0.25 * p0[0] + 0.75 * p1[0], 0.25 * p0[1] + 0.75 * p1[1])
            new_coords.extend([q, r])
        new_coords.append(new_coords[0]) 
        smoothed = new_coords
    return smoothed

def vectorize_basins(basin_raster, dem_transform, crs, morphometry):
    """
    Vetoriza o raster de bacias e anexa atributos morfométricos.
    """
    mask = basin_raster > 0
    generator = shapes(basin_raster, mask=mask, transform=dem_transform)
    
    features = []
    for geom, value in generator:
        basin_id = int(value)
        if basin_id in morphometry:
            poly = shape(geom)
            
            smoothed_polys = []
            geoms = poly.geoms if isinstance(poly, MultiPolygon) else [poly]
            
            for p in geoms:
                ext = chaikin_smooth(list(p.exterior.coords), iterations=2)
                ints = [chaikin_smooth(list(ring.coords), iterations=2) for ring in p.interiors]
                smoothed_polys.append(Polygon(ext, ints))
                
            final_geom = MultiPolygon(smoothed_polys) if len(smoothed_polys) > 1 else smoothed_polys[0]
            
            props = {'basin_id': basin_id}
            props.update(morphometry[basin_id])
            
            features.append({
                'geometry': final_geom,
                'properties': props
            })
            
    if len(features) == 0:
        gdf = gpd.GeoDataFrame(
            columns=['basin_id', 'h_min', 'h_max', 'h_amp', 'area_m2', 'area_km2', 'melton', 'strahler', 'geometry'],
            geometry='geometry'
        )
        if crs:
            gdf.set_crs(crs, inplace=True)
        return gdf
        
    gdf = gpd.GeoDataFrame.from_features(features)
    if crs:
        gdf.set_crs(crs, inplace=True)
        
    return gdf

def resolve_crs(src):
    """
    Garante um CRS válido a partir da fonte raster.
    """
    if src.crs:
        return src.crs
    return "EPSG:4326"

def process_corridas_enxurradas(input_dem_path, output_dir, stream_threshold=500):
    """
    Pipeline principal para o pré-processamento hidrológico e delineamento das bacias hidrográficas.
    """
    os.makedirs(output_dir, exist_ok=True)
    
    with rasterio.open(input_dem_path) as src:
        dem = src.read(1)
        transform = src.transform
        crs = resolve_crs(src)
        nodata = src.nodata if src.nodata is not None else -9999
        bounds_wgs84 = rasterio.warp.transform_bounds(crs, 'EPSG:4326', *src.bounds)
        
    # Máscara de validade: calculada UMA VEZ com tolerância para evitar
    # problemas de precisão float32 vs float64 no valor de nodata
    if nodata is not None and not np.isnan(nodata):
        valid_mask = ~np.isclose(dem, nodata, atol=1e-4) & (~np.isnan(dem))
    else:
        valid_mask = ~np.isnan(dem)
    
    print(f'[corridas_engine] DEM shape: {dem.shape}, valid pixels: {np.sum(valid_mask)}, nodata: {nodata}')
    
    filled_dem = fill_depressions(dem, valid_mask)
    fdir = compute_d8_flow_direction(filled_dem, valid_mask)
    acc = compute_d8_flow_accumulation(fdir, valid_mask)
    print(f'[corridas_engine] Max accumulation: {np.max(acc)}')
    stream_raster = extract_stream_network(acc, valid_mask, stream_threshold)
    strahler_grid = compute_strahler_order(stream_raster, fdir, valid_mask)
    stream_links = compute_stream_links(stream_raster, fdir, valid_mask)
    
    pour_points, pour_points_dict = find_tributary_pour_points(strahler_grid, stream_links, fdir, stream_raster, valid_mask)
    basin_raster = compute_watersheds(fdir, pour_points_dict, valid_mask)
    
    morphometry = compute_basin_morphometry(basin_raster, dem, transform, crs, pour_points_dict)
    
    gdf = vectorize_basins(basin_raster, transform, crs, morphometry)
    
    if len(gdf) > 0 and 'geometry' in gdf.columns and gdf.geometry is not None:
        try:
            if gdf.crs and str(gdf.crs) != 'EPSG:4326':
                gdf = gdf.to_crs('EPSG:4326')
        except Exception:
            pass
        
    geojson_path = os.path.join(output_dir, 'bacias_morfometricas.geojson')
    gdf.to_file(geojson_path, driver='GeoJSON')
    
    if len(gdf) > 0:
        areas = gdf['area_km2'].values
        amps = gdf['h_amp'].values
        
        p90_area = np.percentile(areas, 90) if len(areas) > 0 else 10.0
        p75_amp = np.percentile(amps, 75) if len(amps) > 0 else 500
        p25_amp = np.percentile(amps, 25) if len(amps) > 0 else 250
        
        max_area_km2 = min(float(p90_area), 25.0)
        min_amp_corridas = max(float(p75_amp), 500.0)
        min_amp_enxurradas = max(float(p25_amp), 250.0)
    else:
        max_area_km2 = 10.0
        min_amp_corridas = 500.0
        min_amp_enxurradas = 250.0
        
    min_melton = 0.15
    
    bounds_dict = {
        'left': bounds_wgs84[0],
        'bottom': bounds_wgs84[1],
        'right': bounds_wgs84[2],
        'top': bounds_wgs84[3]
    }
    
    return {
        'total_basins': len(gdf),
        'bounds': bounds_dict,
        'geojson_path': geojson_path,
        'stats': {
            'area_mean': float(gdf['area_km2'].mean()) if len(gdf) > 0 else 0,
            'amp_mean': float(gdf['h_amp'].mean()) if len(gdf) > 0 else 0
        },
        'suggested_thresholds': {
            'max_area_km2': max_area_km2,
            'min_amp_corridas': min_amp_corridas,
            'min_amp_enxurradas': min_amp_enxurradas,
            'min_melton': min_melton
        }
    }

def filter_and_export_corridas(output_dir, max_area_km2, min_amp_corridas, min_amp_enxurradas, min_melton, municipio='ÁGUA DOCE', uf='SC'):
    """
    Filtra as bacias morfométricas com base nos limiares definidos e exporta para GeoJSON e Shapefile.
    """
    input_geojson = os.path.join(output_dir, 'bacias_morfometricas.geojson')
    gdf = gpd.read_file(input_geojson)
    
    def classify_basin(row):
        if row['area_km2'] <= max_area_km2 and row['h_amp'] >= min_amp_corridas and row['melton'] >= min_melton:
            return 3, 'Suscetível a Corridas de Massa', 'Corrida de Massa'
        elif row['area_km2'] <= max_area_km2 and min_amp_enxurradas <= row['h_amp'] < min_amp_corridas:
            return 2, 'Suscetível a Enxurradas', 'Enxurrada'
        else:
            return 0, 'Sem Suscetibilidade', 'N/A'
            
    classes = gdf.apply(classify_basin, axis=1)
    gdf['gridcode'] = [c[0] for c in classes]
    gdf['Classe'] = [c[1] for c in classes]
    gdf['PROCESSO'] = [c[2] for c in classes]
    
    gdf['MUNICIPIO'] = municipio
    gdf['UF'] = uf
    gdf['AREA_KM2'] = gdf['area_km2']
    gdf['AMP_M'] = gdf['h_amp']
    gdf['MELTON'] = gdf['melton']
    gdf['STRAHLER'] = gdf['strahler']
    gdf['OBS'] = ''
    gdf['FONTE'] = 'Produto obtido através de modelagem morfométrica de sub-bacias (Amplitude Altimétrica e Índice de Rugosidade de Melton) validado por trabalho de campo pela CPRM no ano de 2026.'
    gdf['EXECUCAO'] = 'CPRM (2026)'
    gdf['PROJETO'] = 'Cartas de Suscetibilidade a Movimentos Gravitacionais de Massa e Inundações. Tema: Corridas e Enxurradas.'
    
    gdf_filtered = gdf[gdf['gridcode'] > 0].copy()
    
    cols_to_keep = ['gridcode', 'Classe', 'MUNICIPIO', 'UF', 'PROCESSO', 'AREA_KM2', 'AMP_M', 'MELTON', 'STRAHLER', 'OBS', 'FONTE', 'EXECUCAO', 'PROJETO', 'geometry']
    gdf_export = gdf_filtered[cols_to_keep]
    
    out_geojson = os.path.join(output_dir, 'corridas_enxurradas_vetor.geojson')
    gdf_export.to_file(out_geojson, driver='GeoJSON')
    
    out_shp_dir = os.path.join(output_dir, 'shp_temp')
    os.makedirs(out_shp_dir, exist_ok=True)
    out_shp = os.path.join(out_shp_dir, 'corridas_enxurradas_vetor.shp')
    
    gdf_shp = gdf_export.copy()
    rename_dict = {
        'gridcode': 'gridcode',
        'Classe': 'Classe',
        'MUNICIPIO': 'MUNICIPIO',
        'UF': 'UF',
        'PROCESSO': 'PROCESSO',
        'AREA_KM2': 'AREA_KM2',
        'AMP_M': 'AMP_M',
        'MELTON': 'MELTON',
        'STRAHLER': 'STRAHLER',
        'OBS': 'OBS',
        'FONTE': 'FONTE',
        'EXECUCAO': 'EXECUCAO',
        'PROJETO': 'PROJETO'
    }
    
    short_rename = {k: k[:10] for k in rename_dict.keys()}
    gdf_shp.rename(columns=short_rename, inplace=True)
    gdf_shp.to_file(out_shp, driver='ESRI Shapefile')
    
    out_zip = os.path.join(output_dir, 'corridas_enxurradas_vetor.zip')
    with zipfile.ZipFile(out_zip, 'w') as zf:
        for ext in ['.shp', '.shx', '.dbf', '.prj', '.cpg']:
            fpath = out_shp.replace('.shp', ext)
            if os.path.exists(fpath):
                zf.write(fpath, os.path.basename(fpath))
                
    import shutil
    shutil.rmtree(out_shp_dir)
    
    corridas_count = sum(gdf['gridcode'] == 3)
    enxurradas_count = sum(gdf['gridcode'] == 2)
    sem_susc_count = sum(gdf['gridcode'] == 0)
    
    bounds = gdf_filtered.total_bounds if len(gdf_filtered) > 0 else (0, 0, 0, 0)
    
    return {
        'total_basins': len(gdf),
        'corridas_count': int(corridas_count),
        'enxurradas_count': int(enxurradas_count),
        'sem_susc_count': int(sem_susc_count),
        'geojson_path': out_geojson,
        'shp_zip_path': out_zip,
        'bounds': {
            'left': bounds[0],
            'bottom': bounds[1],
            'right': bounds[2],
            'top': bounds[3]
        }
    }
