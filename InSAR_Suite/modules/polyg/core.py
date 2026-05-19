# -*- coding: utf-8 -*-
"""
core.py — Logica di elaborazione InSAR, indipendente da QGIS.

IMPORTANTE: questo modulo non chiama mai pyproj né geopandas.to_crs().
Tutte le coordinate in ingresso sono già in EPSG:3857 (metri), proiettate
nel thread principale prima di avviare il task. I risultati vengono restituiti
come GeoDataFrame in EPSG:3857 e riproiettati nel thread principale.

Flusso:
  1. Classificazione PS in classi di velocità
  2. Per ogni PS instabile: buffer di raggio R con la sua classe
  3. Validazione dell'intorno via KDTree
  4. Dissolve per classe in ordine di priorità + clip gerarchico
  5. Validazione finale per ogni poligono
  6. Smoothing morfologico opzionale
"""

import numpy as np
import geopandas as gpd
from scipy.spatial import cKDTree
from shapely.ops import unary_union
from shapely.geometry import Point, Polygon, MultiPolygon


# ─────────────────────────────────────────────────────────────────────────────
# Eccezione dedicata per condizioni attese (non bug)
# ─────────────────────────────────────────────────────────────────────────────

class AnalysisWarning(Exception):
    """
    Sollevata quando l'analisi non può proseguire per ragioni legate ai dati
    o ai parametri, non per errori di programmazione.
    Il dialog la mostra come finestra di avviso informativa, non come errore.
    """
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Classi di velocità
# ─────────────────────────────────────────────────────────────────────────────

VELOCITY_CLASSES = [
    ("< -10 mm/yr",      None,   -10.0,  1),
    ("> +10 mm/yr",      10.0,    None,  2),
    ("-10 ÷ -5 mm/yr",  -10.0,   -5.0,  3),
    ("+5 ÷ +10 mm/yr",   5.0,    10.0,  4),
    ("-5 ÷ -2 mm/yr",   -5.0,   -2.0,  5),
    ("+2 ÷ +5 mm/yr",    2.0,    5.0,  6),
]


def classify_velocity(vel, threshold):
    for label, lower, upper, priority in VELOCITY_CLASSES:
        lo = lower if lower is not None else float("-inf")
        hi = upper if upper is not None else float("+inf")
        if lo <= vel < hi:
            return label, priority
    return "-2 ÷ +2 mm/yr", 99


# ─────────────────────────────────────────────────────────────────────────────
# 1. Separazione stabili / instabili (su array numpy)
# ─────────────────────────────────────────────────────────────────────────────

def split_stable_unstable(vel_all, threshold):
    """
    vel_all: numpy array di velocità (tutti i PS)
    Restituisce maschere booleane per instabili e stabili.
    """
    mask_unst = np.abs(vel_all) >= threshold
    return mask_unst, ~mask_unst


# ─────────────────────────────────────────────────────────────────────────────
# 2 + 3. Buffer + validazione intorno
# ─────────────────────────────────────────────────────────────────────────────

def select_valid_buffers(coords_all, vel_all, coords_unst, vel_unst,
                         threshold, radius_m, min_ps, min_ratio,
                         progress_callback=None):
    """
    Tutti gli array di coordinate sono già in EPSG:3857 (metri).
    coords_all:   (N, 2) — tutti i PS
    vel_all:      (N,)   — velocità tutti i PS
    coords_unst:  (M, 2) — solo PS instabili
    vel_unst:     (M,)   — velocità PS instabili

    Restituisce liste parallele:
      buf_geoms  — geometrie Shapely (buffer circolari in EPSG:3857)
      buf_class  — label classe
      buf_priority — priorità
      buf_vel    — velocità del PS sorgente
    """
    tree  = cKDTree(coords_all)
    total = len(coords_unst)

    buf_geoms    = []
    buf_class    = []
    buf_priority = []
    buf_vel      = []

    for i in range(total):
        if progress_callback and i % 10 == 0:
            progress_callback(8 + int(i / total * 52))  # 8-60%

        neighbors = tree.query_ball_point(coords_unst[i], r=radius_m)
        n_tot     = len(neighbors)
        if n_tot == 0:
            continue

        vel_n  = vel_all[neighbors]
        n_unst = int(np.sum(np.abs(vel_n) >= threshold))

        if n_unst < min_ps:
            continue
        if n_unst / n_tot < min_ratio:
            continue

        vel            = float(vel_unst[i])
        label, priority = classify_velocity(vel, threshold)
        buf            = Point(coords_unst[i][0], coords_unst[i][1]).buffer(radius_m)

        buf_geoms.append(buf)
        buf_class.append(label)
        buf_priority.append(priority)
        buf_vel.append(vel)

    return buf_geoms, buf_class, buf_priority, buf_vel


# ─────────────────────────────────────────────────────────────────────────────
# 4 + 5. Dissolve per classe + clip gerarchico
# ─────────────────────────────────────────────────────────────────────────────

def dissolve_and_clip_by_class(buf_geoms, buf_class, buf_priority,
                                progress_callback=None):
    """
    Tutte le geometrie sono in EPSG:3857.
    Restituisce lista di dict {geometry, vel_class, priority}.
    """
    # Raggruppa buffer per priorità
    groups = {}
    for geom, cls, pri in zip(buf_geoms, buf_class, buf_priority):
        if pri not in groups:
            groups[pri] = {"geoms": [], "label": cls}
        groups[pri]["geoms"].append(geom)

    priorities = sorted(groups.keys())
    claimed    = None
    results    = []
    n_pri      = len(priorities)

    for k, pri in enumerate(priorities):
        if progress_callback:
            progress_callback(62 + int(k / n_pri * 12))  # 62-74%

        label  = groups[pri]["label"]
        merged = unary_union(groups[pri]["geoms"])
        if merged.is_empty:
            continue

        if claimed is not None and not claimed.is_empty:
            try:
                merged = merged.difference(claimed)
            except Exception:
                pass

        if merged is None or merged.is_empty:
            continue

        claimed = merged if claimed is None else unary_union([claimed, merged])

        if isinstance(merged, Polygon):
            polys = [merged]
        elif isinstance(merged, MultiPolygon):
            polys = list(merged.geoms)
        else:
            polys = [g for g in getattr(merged, "geoms", [])
                     if isinstance(g, Polygon)]

        for poly in polys:
            if not poly.is_empty and poly.area > 0:
                results.append({
                    "geometry":  poly,
                    "vel_class": label,
                    "priority":  pri,
                })

    return results


# ─────────────────────────────────────────────────────────────────────────────
# 6. Validazione finale + attributi (sjoin in EPSG:3857 con numpy)
# ─────────────────────────────────────────────────────────────────────────────

def validate_and_attribute(poly_records, coords_all_proj, vel_all,
                            threshold, min_ps_poly, min_ratio,
                            progress_callback=None):
    """
    coords_all_proj: (N,2) coordinate in EPSG:3857
    vel_all:         (N,)  velocità

    Usa KDTree per assegnare ogni punto al suo poligono — nessuna chiamata
    geopandas/pyproj. Restituisce lista di dict con attributi.
    """
    if not poly_records:
        return []

    if progress_callback:
        progress_callback(76)

    polys    = [r["geometry"] for r in poly_records]
    n_polys  = len(polys)
    records  = []
    cluster_id = 0

    # Per ogni poligono conta i PS che vi ricadono usando contains
    from shapely.geometry import MultiPoint
    for k, (poly, rec) in enumerate(zip(polys, poly_records)):
        if progress_callback and k % 5 == 0:
            progress_callback(76 + int(k / n_polys * 16))  # 76-92%

        # Espandi leggermente per includere punti sul bordo
        poly_buf = poly.buffer(0.5)

        # Candidati: punti nel bounding box (pre-filtro rapido)
        minx, miny, maxx, maxy = poly_buf.bounds
        mask_bb = (
            (coords_all_proj[:, 0] >= minx) &
            (coords_all_proj[:, 0] <= maxx) &
            (coords_all_proj[:, 1] >= miny) &
            (coords_all_proj[:, 1] <= maxy)
        )
        idx_candidates = np.where(mask_bb)[0]

        if len(idx_candidates) == 0:
            continue

        # Verifica esatta con contains
        inside = [i for i in idx_candidates
                  if poly_buf.contains(
                      Point(float(coords_all_proj[i, 0]),
                            float(coords_all_proj[i, 1])))]

        n_tot  = len(inside)
        if n_tot < min_ps_poly:
            continue

        vel_inside = vel_all[inside]
        n_unst     = int(np.sum(np.abs(vel_inside) >= threshold))

        if n_tot == 0 or (n_unst / n_tot) < min_ratio:
            continue

        records.append({
            "cluster_id": cluster_id,
            "vel_class":  rec["vel_class"],
            "priority":   rec["priority"],
            "n_ps":       n_tot,
            "n_unstable": n_unst,
            "vel_mean":   round(float(vel_inside.mean()), 3),
            "vel_std":    round(float(vel_inside.std()),  3),
            "vel_min":    round(float(vel_inside.min()),  3),
            "vel_max":    round(float(vel_inside.max()),  3),
            "area_km2":   round(poly.area / 1e6, 6),
            "geometry":   poly,
        })
        cluster_id += 1

    return records


# ─────────────────────────────────────────────────────────────────────────────
# 7. Smoothing morfologico (tutto in EPSG:3857, nessun to_crs)
# ─────────────────────────────────────────────────────────────────────────────

def smooth_polygons(records, radius_m):
    """
    records: lista di dict con 'geometry' (Shapely, EPSG:3857) e 'priority'.
    Restituisce lista di dict aggiornata.
    """
    r         = radius_m * 0.3
    priorities = sorted(set(rec["priority"] for rec in records))
    claimed    = None
    smoothed   = []

    for pri in priorities:
        subset = [rec for rec in records if rec["priority"] == pri]
        for rec in subset:
            geom = rec["geometry"].buffer(r).buffer(-r)
            if claimed is not None and not claimed.is_empty:
                try:
                    geom = geom.difference(claimed)
                except Exception:
                    pass
            if geom is None or geom.is_empty:
                continue
            new_rec = dict(rec)
            new_rec["geometry"] = geom
            smoothed.append(new_rec)

        class_geoms = [s["geometry"] for s in smoothed
                       if s["priority"] == pri and not s["geometry"].is_empty]
        if class_geoms:
            cu = unary_union(class_geoms)
            claimed = cu if claimed is None else unary_union([claimed, cu])

    # Rinumera cluster_id
    for i, rec in enumerate(smoothed):
        rec["cluster_id"] = i

    return smoothed


# ─────────────────────────────────────────────────────────────────────────────
# Funzione principale — nessuna chiamata pyproj
# ─────────────────────────────────────────────────────────────────────────────

def run_analysis(coords_all_proj, vel_all,
                 coords_unst_proj, vel_unst,
                 velocity_col_unused,
                 threshold, radius_m,
                 min_ps, min_ratio, min_ps_poly,
                 smooth,
                 progress_callback=None, log_callback=None):
    """
    Parametri (tutti array numpy, coordinate già in EPSG:3857):
      coords_all_proj:  (N,2) — tutti i PS proiettati
      vel_all:          (N,)  — velocità tutti i PS
      coords_unst_proj: (M,2) — PS instabili proiettati
      vel_unst:         (M,)  — velocità PS instabili
    """
    def log(msg):
        if log_callback:
            log_callback(msg)

    def prog(v):
        if progress_callback:
            progress_callback(v)

    n_all  = len(coords_all_proj)
    n_unst = len(coords_unst_proj)
    log(f"Punti nel sottoinsieme: {n_all:,}")
    log(f"PS instabili (|vel| >= {threshold} mm/yr): {n_unst:,}")
    prog(5)

    if n_unst < 3:
        raise AnalysisWarning(
            "Nessun PS instabile trovato nell'area selezionata.\n\n"
            "Cause possibili:\n"
            f"  • La soglia di velocità è troppo alta (attuale: {threshold} mm/anno)\n"
            "  • L'area selezionata contiene solo PS stabili\n"
            "  • Il campo velocità selezionato non è corretto\n\n"
            "Soluzione: abbassa la soglia di velocità o seleziona un'area con zone in deformazione.")

    # Distribuzione per classe
    class_counts = {}
    for vel in vel_unst:
        label, _ = classify_velocity(float(vel), threshold)
        class_counts[label] = class_counts.get(label, 0) + 1
    for label, cnt in sorted(class_counts.items(), key=lambda x: x[1], reverse=True):
        log(f"  {label}: {cnt:,} PS")

    # 2+3. Buffer + validazione
    log(f"Validazione buffer (raggio={radius_m} m, "
        f"min_ps={min_ps}, min_ratio={min_ratio})...")
    prog(8)
    buf_geoms, buf_class, buf_priority, buf_vel = select_valid_buffers(
        coords_all_proj, vel_all, coords_unst_proj, vel_unst,
        threshold, radius_m, min_ps, min_ratio, prog)

    if not buf_geoms:
        raise AnalysisWarning(
            "Nessun buffer supera i criteri di validazione.\n\n"
            "Cause possibili:\n"
            f"  • PS instabili minimi troppo alti (attuale: {min_ps}): prova a ridurli\n"
            f"  • Rapporto instabili/totali troppo alto (attuale: {min_ratio}): prova 0.65\n"
            f"  • Raggio di ricerca troppo piccolo (attuale: {radius_m} m): aumentalo\n"
            "  • I PS instabili sono troppo isolati nell'area selezionata\n\n"
            "Soluzione: modifica uno o più dei parametri indicati.")

    log(f"Buffer validi: {len(buf_geoms):,} su {n_unst:,} PS instabili")
    buf_counts = {}
    for cls in buf_class:
        buf_counts[cls] = buf_counts.get(cls, 0) + 1
    for label, cnt in buf_counts.items():
        log(f"  {label}: {cnt:,} buffer")
    prog(60)

    # 4+5. Dissolve + clip gerarchico
    log("Dissolve per classe e clip gerarchico...")
    poly_records = dissolve_and_clip_by_class(
        buf_geoms, buf_class, buf_priority, prog)
    log(f"Poligoni dopo dissolve: {len(poly_records)}")
    prog(74)

    # 6. Validazione finale
    log("Validazione finale e calcolo attributi...")
    prog(76)
    records = validate_and_attribute(
        poly_records, coords_all_proj, vel_all,
        threshold, min_ps_poly, min_ratio, prog)

    if not records:
        raise AnalysisWarning(
            "Nessun poligono supera la validazione finale.\n\n"
            "Cause possibili:\n"
            f"  • PS minimi nel poligono finale troppo alti (attuale: {min_ps_poly})\n"
            f"  • Rapporto instabili/totali troppo alto (attuale: {min_ratio})\n"
            "  • I poligoni generati sono troppo piccoli o marginali\n\n"
            "Soluzione: riduci 'PS minimi nel poligono' o abbassa il rapporto minimo.")

    log(f"Poligoni validi: {len(records)}")
    prog(92)

    # 7. Smoothing
    if smooth:
        log("Smoothing morfologico...")
        prog(94)
        records = smooth_polygons(records, radius_m)
        prog(98)

    prog(100)
    return records
