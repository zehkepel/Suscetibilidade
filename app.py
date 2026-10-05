"""
Servidor Web FastAPI para a Aplicação de Suscetibilidade a Movimentos de Massa (SUSC_Global_sem_Lin)
e Modelo de Corridas de Massa e Enxurradas (Suscetibilidade_Corridas_Enxurradas).
"""

import os
import sys

# Configura caminhos do GDAL/PROJ
try:
    import site
    possible_paths = site.getsitepackages() + [site.getusersitepackages()]
    for p in possible_paths:
        r_proj = os.path.join(p, 'rasterio', 'proj_data')
        r_gdal = os.path.join(p, 'rasterio', 'gdal_data')
        if os.path.exists(r_proj):
            os.environ['PROJ_DATA'] = r_proj
            os.environ['PROJ_LIB'] = r_proj
            os.environ['GDAL_DATA'] = r_gdal
            break
except Exception:
    pass

import json
import shutil
import zipfile
import uuid
from typing import List, Optional
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.warp import reproject, calculate_default_transform, Resampling
import matplotlib.pyplot as plt
from PIL import Image

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backend.susc_engine import (
    process_susceptibility,
    vectorize_susceptibility,
    resolve_crs,
    DEFAULT_SLOPE_ISD,
    DEFAULT_CURVATURE_ISD
)
from backend.corridas_engine import (
    process_corridas_enxurradas,
    filter_and_export_corridas
)


app = FastAPI(title="GeoSuscetibilidade Web App", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
UPLOAD_DIR = os.path.join(BASE_DIR, "storage", "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "storage", "outputs")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
app.mount("/storage", StaticFiles(directory=os.path.join(BASE_DIR, "storage")), name="storage")


def raster_to_png_overlay(tif_path, png_path, cmap_name="turbo", is_jenks=False):
    """
    Converte um GeoTIFF em PNG transparente com projeção WGS84 (EPSG:4326) para alinhamento perfeito no Leaflet.
    """
    dst_crs = CRS.from_epsg(4326)

    with rasterio.open(tif_path) as src:
        src_crs = resolve_crs(src)
        nodata = src.nodata if src.nodata is not None else -9999.0

        if src_crs != dst_crs:
            transform_wgs, width_wgs, height_wgs = calculate_default_transform(
                src_crs, dst_crs, src.width, src.height, *src.bounds
            )
            data = np.full((height_wgs, width_wgs), nodata, dtype=np.float32)
            reproject(
                source=rasterio.band(src, 1),
                destination=data,
                src_transform=src.transform,
                src_crs=src_crs,
                dst_transform=transform_wgs,
                dst_crs=dst_crs,
                resampling=Resampling.nearest if is_jenks else Resampling.bilinear
            )
        else:
            data = src.read(1)

    valid_mask = ~np.isnan(data) & (data != nodata)
    height, width = data.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)

    if is_jenks:
        color_dict = {
            1: (46, 204, 113, 210),  # Verde (#2ecc71)
            2: (241, 196, 15, 210),  # Amarelo (#f1c40f)
            3: (231, 76, 60, 210)    # Vermelho (#e74c3c)
        }
        for code, color in color_dict.items():
            mask = valid_mask & (data == code)
            rgba[mask] = color
    else:
        valid_vals = data[valid_mask]
        if len(valid_vals) > 0:
            vmin, vmax = np.percentile(valid_vals, [1, 99])
            if vmin == vmax:
                vmax = vmin + 1.0
            norm_data = np.clip((data - vmin) / (vmax - vmin), 0.0, 1.0)
            
            cmap = plt.get_cmap(cmap_name)
            mapped = cmap(norm_data)
            
            rgba[valid_mask, :3] = (mapped[valid_mask, :3] * 255).astype(np.uint8)
            rgba[valid_mask, 3] = 200

    img = Image.fromarray(rgba, mode="RGBA")
    img.save(png_path, format="PNG")


def raster_to_raw_png(tif_path, raw_png_path):
    """
    Gera um PNG escala de cinza normalizada (0-255) em WGS84 para reclassificação em tempo real no cliente (JS Canvas).
    """
    dst_crs = CRS.from_epsg(4326)

    with rasterio.open(tif_path) as src:
        src_crs = resolve_crs(src)
        nodata = src.nodata if src.nodata is not None else -9999.0

        if src_crs != dst_crs:
            transform_wgs, width_wgs, height_wgs = calculate_default_transform(
                src_crs, dst_crs, src.width, src.height, *src.bounds
            )
            data = np.full((height_wgs, width_wgs), nodata, dtype=np.float32)
            reproject(
                source=rasterio.band(src, 1),
                destination=data,
                src_transform=src.transform,
                src_crs=src_crs,
                dst_transform=transform_wgs,
                dst_crs=dst_crs,
                resampling=Resampling.bilinear
            )
        else:
            data = src.read(1)

    valid_mask = ~np.isnan(data) & (data != nodata)
    height, width = data.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)

    valid_vals = data[valid_mask]
    if len(valid_vals) > 0:
        min_v = float(np.min(valid_vals))
        max_v = float(np.max(valid_vals))
        if min_v == max_v:
            max_v = min_v + 1.0
        
        norm_data = np.clip((data - min_v) / (max_v - min_v) * 255.0, 0, 255).astype(np.uint8)
        
        rgba[valid_mask, 0] = norm_data[valid_mask]
        rgba[valid_mask, 1] = norm_data[valid_mask]
        rgba[valid_mask, 2] = norm_data[valid_mask]
        rgba[valid_mask, 3] = 255

    img = Image.fromarray(rgba, mode="RGBA")
    img.save(raw_png_path, format="PNG")

def raster_to_inundacao_raw_png(alt_path, hand_path, rel_path, raw_png_path, alt_stats, hand_stats):
    """
    Gera um PNG RGB para Inundação em WGS84 para reclassificação ao vivo:
    R = Altimetria (normalizada)
    G = HAND (normalizada)
    B = Relevo ID (1 a N, mapeamento direto)
    Retorna os bounds WGS84 para posicionamento no mapa.
    """
    dst_crs = CRS.from_epsg(4326)
    wgs84_bounds = None

    def load_and_reproject(path, nodata_override=None, is_int=False):
        nonlocal wgs84_bounds
        with rasterio.open(path) as src:
            src_crs = resolve_crs(src)
            nodata = nodata_override if nodata_override is not None else src.nodata
            if nodata is None: nodata = -9999.0 if not is_int else 0
            
            if src_crs != dst_crs:
                transform_wgs, width_wgs, height_wgs = calculate_default_transform(
                    src_crs, dst_crs, src.width, src.height, *src.bounds
                )
                if wgs84_bounds is None:
                    # Calculate bounds from the transform
                    left = transform_wgs.c
                    top = transform_wgs.f
                    right = left + transform_wgs.a * width_wgs
                    bottom = top + transform_wgs.e * height_wgs
                    wgs84_bounds = {"left": left, "bottom": bottom, "right": right, "top": top}
                dtype = np.int32 if is_int else np.float32
                data = np.full((height_wgs, width_wgs), nodata, dtype=dtype)
                reproject(
                    source=rasterio.band(src, 1),
                    destination=data,
                    src_transform=src.transform,
                    src_crs=src_crs,
                    dst_transform=transform_wgs,
                    dst_crs=dst_crs,
                    resampling=Resampling.nearest if is_int else Resampling.bilinear
                )
            else:
                data = src.read(1)
                if wgs84_bounds is None:
                    b = src.bounds
                    wgs84_bounds = {"left": b.left, "bottom": b.bottom, "right": b.right, "top": b.top}
            return data, nodata

    alt_data, alt_nd = load_and_reproject(alt_path)
    hand_data, hand_nd = load_and_reproject(hand_path)
    rel_data, rel_nd = load_and_reproject(rel_path, is_int=True)

    height, width = alt_data.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)

    alt_valid = ~np.isnan(alt_data) & (alt_data != alt_nd)
    hand_valid = ~np.isnan(hand_data) & (hand_data != hand_nd)
    
    alt_min, alt_max = alt_stats['min'], alt_stats['max']
    if alt_min == alt_max: alt_max = alt_min + 1.0
    hand_min, hand_max = hand_stats['min'], hand_stats['max']
    if hand_min == hand_max: hand_max = hand_min + 1.0
    
    valid_mask = ~np.isnan(alt_data) & ~np.isnan(hand_data)
    
    r_chan = np.zeros_like(alt_data, dtype=np.uint8)
    g_chan = np.zeros_like(hand_data, dtype=np.uint8)
    
    r_chan[valid_mask] = np.clip((alt_data[valid_mask] - alt_min) / (alt_max - alt_min) * 255.0, 0, 255).astype(np.uint8)
    g_chan[valid_mask] = np.clip((hand_data[valid_mask] - hand_min) / (hand_max - hand_min) * 255.0, 0, 255).astype(np.uint8)
    
    b_chan = rel_data.astype(np.uint8)
    
    rgba[valid_mask, 0] = r_chan[valid_mask]
    rgba[valid_mask, 1] = g_chan[valid_mask]
    rgba[valid_mask, 2] = b_chan[valid_mask]
    rgba[valid_mask, 3] = 255

    img = Image.fromarray(rgba, mode="RGBA")
    img.save(raw_png_path, format="PNG")
    
    return wgs84_bounds

@app.get("/")
def read_root():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/api/default-params")
def get_default_params():
    return {
        "slope_isd": DEFAULT_SLOPE_ISD,
        "curvature_isd": DEFAULT_CURVATURE_ISD
    }


# ==============================================================================
# ENDPOINTS MODELO: SUSCETIBILIDADE A MOVIMENTOS DE MASSA (SUSC_Global_sem_Lin)
# ==============================================================================

@app.post("/api/process")
async def process_dem(
    file: Optional[UploadFile] = File(None),
    use_sample: bool = Form(False),
    slope_isd_json: Optional[str] = Form(None),
    curvature_isd_json: Optional[str] = Form(None)
):
    try:
        session_id = str(uuid.uuid4())[:8]
        session_out = os.path.join(OUTPUT_DIR, session_id)
        os.makedirs(session_out, exist_ok=True)

        if use_sample or file is None:
            dem_path = os.path.join(BASE_DIR, "tests", "MDE.tif")
            if not os.path.exists(dem_path):
                dem_path = os.path.join(BASE_DIR, "tests", "mde_teste.tif")
        else:
            dem_path = os.path.join(UPLOAD_DIR, f"{session_id}_{file.filename}")
            with open(dem_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

        slope_rules = json.loads(slope_isd_json) if slope_isd_json else DEFAULT_SLOPE_ISD
        curv_rules = json.loads(curvature_isd_json) if curvature_isd_json else DEFAULT_CURVATURE_ISD

        res = process_susceptibility(
            input_dem_path=dem_path,
            output_dir=session_out,
            slope_isd_rules=slope_rules,
            curvature_isd_rules=curv_rules
        )

        slope_png = os.path.join(session_out, "declividade.png")
        curv_png = os.path.join(session_out, "curvatura.png")
        susc_png = os.path.join(session_out, "suscetibilidade_isd.png")
        jenks_png = os.path.join(session_out, "suscetibilidade_jenks3.png")
        raw_png = os.path.join(session_out, "suscetibilidade_raw.png")

        raster_to_png_overlay(res["slope_path"], slope_png, cmap_name="magma")
        raster_to_png_overlay(res["curv_path"], curv_png, cmap_name="coolwarm")
        raster_to_png_overlay(res["susc_path"], susc_png, cmap_name="turbo")
        raster_to_png_overlay(res["jenks_path"], jenks_png, is_jenks=True)
        raster_to_raw_png(res["susc_path"], raw_png)

        rel_out = f"/storage/outputs/{session_id}"
        
        return {
            "status": "success",
            "session_id": session_id,
            "bounds": res["bounds"],
            "crs": res["crs"],
            "stats": res["stats"],
            "overlays": {
                "slope_png": f"{rel_out}/declividade.png",
                "curv_png": f"{rel_out}/curvatura.png",
                "susc_png": f"{rel_out}/suscetibilidade_isd.png",
                "jenks_png": f"{rel_out}/suscetibilidade_jenks3.png",
                "raw_png": f"{rel_out}/suscetibilidade_raw.png"
            },
            "downloads": {
                "slope_tif": f"{rel_out}/declividade.tif",
                "curv_tif": f"{rel_out}/curvatura.tif",
                "susc_tif": f"{rel_out}/suscetibilidade_isd.tif",
                "jenks_tif": f"{rel_out}/suscetibilidade_jenks3.tif"
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/reclassify-vectorize")
async def reclassify_and_vectorize_endpoint(
    session_id: str = Form(...),
    break1: float = Form(...),
    break2: float = Form(...),
    municipio: Optional[str] = Form("ÁGUA DOCE"),
    uf: Optional[str] = Form("SC")
):
    try:
        session_out = os.path.join(OUTPUT_DIR, session_id)
        if not os.path.exists(session_out):
            raise HTTPException(status_code=404, detail="Sessão não encontrada.")

        res = vectorize_susceptibility(
            output_dir=session_out,
            break1=break1,
            break2=break2,
            municipio=municipio,
            uf=uf
        )

        custom_png = os.path.join(session_out, "suscetibilidade_custom3.png")
        raster_to_png_overlay(res["custom_raster_path"], custom_png, is_jenks=True)

        rel_out = f"/storage/outputs/{session_id}"

        return {
            "status": "success",
            "session_id": session_id,
            "bounds": res["bounds"],
            "stats": res["stats"],
            "overlays": {
                "custom_png": f"{rel_out}/suscetibilidade_custom3.png"
            },
            "geojson_url": f"{rel_out}/suscetibilidade_vetor.geojson",
            "shp_zip_url": f"{rel_out}/suscetibilidade_vetor.zip",
            "custom_tif_url": f"{rel_out}/suscetibilidade_custom3.tif"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==============================================================================
# ENDPOINTS MODELO: SUSCETIBILIDADE A CORRIDAS DE MASSA E ENXURRADAS
# ==============================================================================

@app.post("/api/corridas/process")
async def process_corridas(
    mde: Optional[UploadFile] = File(None),
    use_sample: bool = Form(False),
    drain_threshold: int = Form(500)
):
    """Processa o MDE para delimitação de sub-bacias hidrográficas morfométricas."""
    try:
        session_id = str(uuid.uuid4())[:8]
        session_out = os.path.join(OUTPUT_DIR, session_id)
        os.makedirs(session_out, exist_ok=True)

        if use_sample or mde is None:
            dem_path = os.path.join(BASE_DIR, "tests", "MDE.tif")
            if not os.path.exists(dem_path):
                dem_path = os.path.join(BASE_DIR, "tests", "mde_teste.tif")
        else:
            dem_path = os.path.join(UPLOAD_DIR, f"{session_id}_{mde.filename}")
            with open(dem_path, "wb") as buffer:
                shutil.copyfileobj(mde.file, buffer)

        res = process_corridas_enxurradas(
            input_dem_path=dem_path,
            output_dir=session_out,
            stream_threshold=drain_threshold
        )

        rel_out = f"/storage/outputs/{session_id}"

        return {
            "status": "success",
            "session_id": session_id,
            "bounds": res["bounds"],
            "total_basins": res["total_basins"],
            "geojson_url": f"{rel_out}/bacias_morfometricas.geojson",
            "stats": res["stats"],
            "suggested_thresholds": res["suggested_thresholds"]
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

from backend.inundacao_engine import process_inundacao_insumos, reclassify_vectorize_inundacao

@app.post("/api/inundacao/process_insumos")
async def process_inundacao_insumos_endpoint(
    mde: UploadFile = File(...),
    relevo_zip: UploadFile = File(None),
    drain_threshold: float = Form(2000.0)
):
    if not mde.filename.endswith(('.tif', '.tiff')):
        raise HTTPException(status_code=400, detail="Formato de arquivo MDE inválido. Use .tif")

    mde_path = os.path.join(UPLOAD_DIR, mde.filename)
    with open(mde_path, "wb") as buffer:
        shutil.copyfileobj(mde.file, buffer)
        
    relevo_path = None
    if relevo_zip and relevo_zip.filename:
        if not relevo_zip.filename.endswith('.zip'):
             raise HTTPException(status_code=400, detail="O arquivo de relevo deve ser um .zip contendo o shapefile.")
        relevo_path = os.path.join(UPLOAD_DIR, relevo_zip.filename)
        with open(relevo_path, "wb") as buffer:
            shutil.copyfileobj(relevo_zip.file, buffer)

    try:
        session_id = str(uuid.uuid4())[:8]
        session_out = os.path.join(OUTPUT_DIR, session_id)
        os.makedirs(session_out, exist_ok=True)

        res = process_inundacao_insumos(mde_path, relevo_path, drain_threshold, session_out)
        res["session_id"] = session_id
        
        raw_png = os.path.join(session_out, "inundacao_raw.png")
        wgs84_bounds = raster_to_inundacao_raw_png(
            os.path.join(session_out, "altimetria.tif"),
            os.path.join(session_out, "hand.tif"),
            os.path.join(session_out, "relevo.tif"),
            raw_png,
            res["altimetria_stats"],
            res["hand_stats"]
        )
        
        rel_out = f"/storage/outputs/{session_id}"
        res["overlays"] = {
            "raw_png": f"{rel_out}/inundacao_raw.png"
        }
        if wgs84_bounds:
            res["bounds"] = wgs84_bounds
        
        return res
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/inundacao/reclassify-vectorize")
async def reclassify_vectorize_inundacao_endpoint(
    session_id: str = Form(...),
    b_alt_1: float = Form(...),
    b_alt_2: float = Form(...),
    b_hand_1: float = Form(...),
    b_hand_2: float = Form(...),
    relevo_weights: str = Form(...)
):
    try:
        session_out = os.path.join(OUTPUT_DIR, session_id)
        weights_dict = json.loads(relevo_weights)
        b_alt = [b_alt_1, b_alt_2]
        b_hand = [b_hand_1, b_hand_2]
        
        res = reclassify_vectorize_inundacao(session_out, b_alt, b_hand, weights_dict)
        return res
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/corridas/filter-export")
async def filter_export_corridas(
    session_id: str = Form(...),
    max_area_km2: float = Form(10.0),
    min_amp_corridas: float = Form(500.0),
    min_amp_enxurradas: float = Form(250.0),
    min_melton: float = Form(0.15),
    municipio: Optional[str] = Form("ÁGUA DOCE"),
    uf: Optional[str] = Form("SC")
):
    """Filtra bacias e exporta Corridas de Massa e Enxurradas como GeoJSON + Shapefile."""
    try:
        session_out = os.path.join(OUTPUT_DIR, session_id)
        if not os.path.exists(session_out):
            raise HTTPException(status_code=404, detail="Sessão não encontrada.")

        res = filter_and_export_corridas(
            output_dir=session_out,
            max_area_km2=max_area_km2,
            min_amp_corridas=min_amp_corridas,
            min_amp_enxurradas=min_amp_enxurradas,
            min_melton=min_melton,
            municipio=municipio,
            uf=uf
        )

        rel_out = f"/storage/outputs/{session_id}"

        return {
            "status": "success",
            "stats": {
                "total": res["total_basins"],
                "corridas": res["corridas_count"],
                "enxurradas": res["enxurradas_count"],
                "sem_susc": res["sem_susc_count"]
            },
            "geojson_url": f"{rel_out}/corridas_enxurradas_vetor.geojson",
            "shp_zip_url": f"{rel_out}/corridas_enxurradas_vetor.zip",
            "bounds": res["bounds"]
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
