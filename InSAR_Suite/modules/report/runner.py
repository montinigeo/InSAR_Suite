# -*- coding: utf-8 -*-
"""
Runner per il Report PSInSAR.
Esegue tutti i calcoli (N PS, densità, copertura areale, coerenza
cinematica, velocità medie, visibilità, vettore EWUD) in un QgsTask
separato per non bloccare la GUI.
"""
import re
import traceback

import numpy as np
import pandas as pd
import pwlf
import matplotlib.dates as mdates
from statsmodels.tsa.seasonal import seasonal_decompose

from qgis.core import (
    QgsTask, QgsFeatureRequest, QgsGeometry, QgsCoordinateTransform,
    QgsProject, QgsDistanceArea, QgsUnitTypes, QgsMessageLog, Qgis
)
import processing


def _qv(v):
    """Converte QVariant/NULL a float; restituisce None se NULL."""
    if v is None:
        return None
    try:
        from qgis.PyQt.QtCore import QVariant as _QVT
        if isinstance(v, _QVT):
            return None if v.isNull() else float(v.value())
    except Exception:
        v = v  # nessuna azione: si prova comunque la conversione a float sotto
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _transform(geom, src_crs, dst_crs):
    if src_crs == dst_crs:
        return geom
    xform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
    g2 = QgsGeometry(geom)
    g2.transform(xform)
    return g2


def _features_in_area(layer, area_geom_proj_crs, proj_crs):
    """Restituisce le feature di 'layer' che ricadono dentro area_geom
    (quest'ultima nel CRS del progetto). Gestisce la riproiezione."""
    area_geom_layer_crs = _transform(area_geom_proj_crs, proj_crs, layer.crs())
    bbox = area_geom_layer_crs.boundingBox()
    req = QgsFeatureRequest().setFilterRect(bbox)
    out = []
    for f in layer.getFeatures(req):
        g = f.geometry()
        if g is None or g.isEmpty():
            continue
        if area_geom_layer_crs.intersects(g):
            out.append(f)
    return out


def _circular_stats(angles_deg, r_min=0.1):
    """Media e deviazione standard circolare di un insieme di angoli (gradi).
    Restituisce (media, dev_std, R) dove R è la lunghezza risultante (0-1,
    indicatore di coerenza direzionale). Se R < r_min le direzioni sono
    troppo disperse per una deviazione standard circolare significativa
    (la formula tenderebbe a infinito): in tal caso dev_std è None."""
    if not angles_deg:
        return None, None, None
    rad = np.radians(angles_deg)
    C = np.mean(np.cos(rad))
    S = np.mean(np.sin(rad))
    R = float(np.sqrt(C ** 2 + S ** 2))
    mean_angle = float(np.degrees(np.arctan2(S, C)) % 360)
    if R < r_min:
        std_deg = None
    else:
        std_deg = float(np.degrees(np.sqrt(-2.0 * np.log(R))))
    return mean_angle, std_deg, R


def _utm_crs_for_lonlat(lon, lat):
    """Restituisce il CRS UTM (metrico) appropriato per una coppia lon/lat."""
    from qgis.core import QgsCoordinateReferenceSystem
    zone = int((lon + 180) / 6) + 1
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    return QgsCoordinateReferenceSystem(f'EPSG:{epsg}')


def _metric_crs_for(area_geom_proj, proj_crs, layer_crs):
    """Sceglie un CRS metrico (proiettato) da usare per il buffer:
    - se il CRS del layer PS è già proiettato, usa quello;
    - altrimenti, se il CRS del progetto è proiettato, usa quello;
    - altrimenti (entrambi geografici, es. dati in WGS84) calcola
      automaticamente il fuso UTM appropriato dal centroide dell'area."""
    if not layer_crs.isGeographic():
        return layer_crs
    if not proj_crs.isGeographic():
        return proj_crs
    from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject as _QP
    centroid = area_geom_proj.centroid().asPoint()
    if proj_crs != QgsCoordinateReferenceSystem('EPSG:4326'):
        xform = QgsCoordinateTransform(proj_crs, QgsCoordinateReferenceSystem('EPSG:4326'), _QP.instance())
        centroid = xform.transform(centroid)
    return _utm_crs_for_lonlat(centroid.x(), centroid.y())


class ReportTask(QgsTask):
    def __init__(self, params, bridge):
        super().__init__('Generazione Report PSInSAR', QgsTask.Flag.CanCancel)
        self.params = params
        self.bridge = bridge
        self.results = None
        self.error_msg = None

    def _log(self, msg):
        self.bridge.log_signal.emit(msg)

    # ── Coerenza cinematica (stessa logica del modulo TS — qualita_dato.py) ──
    @staticmethod
    def _coerenza(feats, campi_d, soglia):
        if not feats or not campi_d:
            return 0, 0, 0.0
        records = [[_qv(f[c]) for c in campi_d] for f in feats]
        vals = pd.DataFrame(records, columns=campi_d).apply(pd.to_numeric, errors='coerce')
        n_tot = len(vals)
        if n_tot <= 1:
            return n_tot, n_tot, 100.0 if n_tot else 0.0
        arr = vals.to_numpy(dtype=float)
        arr = np.where(np.isnan(arr), 0.0, arr)
        std_r = np.std(arr, axis=1, ddof=1)
        valid_r = std_r > 0
        if np.sum(valid_r) > 1:
            corr_m = np.corrcoef(arr)
            corr_m[~valid_r, :] = np.nan
            corr_m[:, ~valid_r] = np.nan
        else:
            corr_m = np.full((n_tot, n_tot), np.nan)
        coerente = (np.ones(n_tot, dtype=bool) if soglia <= 0
                    else (np.nansum(corr_m >= soglia, axis=1) >= n_tot / 2))
        n_coe = int(np.sum(coerente))
        pct = (n_coe / n_tot * 100.0) if n_tot else 0.0
        return n_tot, n_coe, pct, coerente

    @staticmethod
    def _date_fields(layer):
        campi = [f.name() for f in layer.fields()
                 if re.match(r'^D\d{8}$', f.name()) or re.match(r'^\d{8}$', f.name())]
        return ['D' + c if re.match(r'^\d{8}$', c) else c for c in campi]

    def _copertura_pct(self, feats, layer_crs, area_geom_proj, proj_crs, raggio_m, ctx, feedback):
        """% dell'area di studio coperta da un buffer di raggio 'raggio_m'
        (in METRI) attorno ai punti PS in 'feats' (metodo buffer + dissolve
        + clip). Il buffer viene sempre calcolato in un CRS metrico, con
        riproiezione automatica se il layer PS e/o il progetto sono in
        coordinate geografiche (es. dati distribuiti in WGS84)."""
        if not feats:
            return 0.0
        from qgis.core import QgsVectorLayer, QgsField, QgsFeature
        from ..qt_compat import FIELD_INT

        metric_crs = _metric_crs_for(area_geom_proj, proj_crs, layer_crs)
        self._log(f'  Copertura areale: buffer calcolato in {metric_crs.authid()}')

        mem = QgsVectorLayer(f"Point?crs={layer_crs.authid()}", "ps_tmp", "memory")
        pr = mem.dataProvider()
        pr.addAttributes([QgsField("id", FIELD_INT)])
        mem.updateFields()
        new_feats = []
        for i, f in enumerate(feats):
            nf = QgsFeature(mem.fields())
            nf.setGeometry(f.geometry())
            nf.setAttributes([i])
            new_feats.append(nf)
        pr.addFeatures(new_feats)
        mem.updateExtents()

        if metric_crs != layer_crs:
            mem = processing.run('native:reprojectlayer', {
                'INPUT': mem, 'TARGET_CRS': metric_crs, 'OUTPUT': 'memory:',
            }, context=ctx, feedback=feedback, is_child_algorithm=False)['OUTPUT']

        buf = processing.run('native:buffer', {
            'INPUT': mem, 'DISTANCE': raggio_m, 'SEGMENTS': 12,
            'DISSOLVE': True, 'END_CAP_STYLE': 0, 'JOIN_STYLE': 0,
            'MITER_LIMIT': 2, 'OUTPUT': 'memory:',
        }, context=ctx, feedback=feedback, is_child_algorithm=False)['OUTPUT']

        buf_feats = list(buf.getFeatures())
        if not buf_feats:
            return 0.0
        buf_geom = buf_feats[0].geometry()
        for bf in buf_feats[1:]:
            buf_geom = buf_geom.combine(bf.geometry())

        area_geom_metric = _transform(area_geom_proj, proj_crs, metric_crs)
        clipped = buf_geom.intersection(area_geom_metric)
        if clipped is None or clipped.isEmpty():
            return 0.0

        # CRS metrico: l'area planare (.area()) è già in mq, senza bisogno
        # di QgsDistanceArea/ellissoide.
        area_clip_m2 = abs(clipped.area())
        area_studio_m2 = abs(area_geom_metric.area())
        if area_studio_m2 <= 0:
            return 0.0
        return min(100.0, area_clip_m2 / area_studio_m2 * 100.0)

    @staticmethod
    def _accelerazione(feats, campi_d, coerente_mask):
        """Analizza la serie storica media dei PS coerenti con una
        regressione piecewise a 2 segmenti fissi (esattamente 1 breakpoint,
        la cui posizione ottimale viene comunque cercata dall'ottimizzatore
        pwlf). Il numero di segmenti non viene scelto tramite BIC: per un
        rapporto tra due velocità significativo servono sempre e solo due
        velocità da confrontare — lasciare il numero di segmenti variabile
        toglierebbe significato al rapporto stesso, oltre a risentire della
        tendenza del BIC a preferire troppi segmenti sui modelli piecewise
        "a nodi liberi" (vedi modulo TS). Restituisce un dict con rapporto,
        velocità dei due segmenti, data del breakpoint ed eventuale
        inversione di direzione (cambio di segno tra i due segmenti)."""
        vuoto = {'rapporto': None, 'v_ultimo': None, 'v_precedente': None,
                 'data_breakpoint': None, 'inversione': False}
        if not campi_d or coerente_mask is None:
            return vuoto
        idx_coerenti = [i for i, c in enumerate(coerente_mask) if c]
        if len(idx_coerenti) < 2:
            return vuoto

        date = [pd.to_datetime(c[1:], format='%Y%m%d') for c in campi_d]
        records = [[_qv(feats[i][c]) for c in campi_d] for i in idx_coerenti]
        arr = np.array(records, dtype=float)
        serie_media = np.nanmean(arr, axis=0)
        df_media = pd.DataFrame({'data': date, 'y': serie_media}).dropna().reset_index(drop=True)
        if len(df_media) < 6:
            return vuoto

        x = mdates.date2num(df_media['data'].values)

        # Rimuove la componente stagionale prima del fit (stessa logica del
        # modulo TS "Analisi non lineare piecewise"): senza questo passaggio
        # il breakpoint potrebbe cadere su un massimo/minimo stagionale
        # invece che su una vera variazione del tasso di deformazione.
        y = df_media['y'].values
        try:
            giorni = df_media['data'].diff().dt.days.dropna()
            intervallo_medio = float(giorni.median()) if len(giorni) > 0 else 30.0
            period = max(2, round(365.25 / max(intervallo_medio, 1.0)))
            if len(df_media) >= 2 * period:
                decomp = seasonal_decompose(
                    df_media['y'], period=period,
                    model='additive', extrapolate_trend='freq')
                y = (df_media['y'] - decomp.seasonal).values
        except Exception as _e:
            QgsMessageLog.logMessage(
                f"InSAR Report: rimozione stagionalità non riuscita ({_e}), "
                f"uso la serie originale per il calcolo dell'accelerazione.",
                "InSAR Report", Qgis.MessageLevel.Warning)

        try:
            model = pwlf.PiecewiseLinFit(x, y, seed=42)
            model.fit(2)  # sempre e solo 2 segmenti (1 breakpoint fisso)
            slopes = model.slopes
            v_ultimo = float(slopes[-1]) * 365.25
            v_precedente = float(slopes[-2]) * 365.25
            data_breakpoint = mdates.num2date(model.fit_breaks[1]).strftime('%Y-%m-%d')
        except Exception:
            return vuoto

        inversione = (v_ultimo * v_precedente < 0)
        if abs(v_precedente) < 0.05:
            # velocità del segmento precedente troppo vicina a zero: il
            # rapporto sarebbe numericamente instabile e poco significativo
            return {'rapporto': None, 'v_ultimo': v_ultimo, 'v_precedente': v_precedente,
                    'data_breakpoint': data_breakpoint, 'inversione': inversione}
        rapporto = abs(v_ultimo) / abs(v_precedente)
        return {'rapporto': rapporto, 'v_ultimo': v_ultimo, 'v_precedente': v_precedente,
                'data_breakpoint': data_breakpoint, 'inversione': inversione}

    def _lato(self, side_label, ps_layer, vel_field, vis_layer, area_geom_proj,
              proj_crs, raggio_m, soglia, area_mq, ctx, feedback):
        self._log(f'--- Lato {side_label} ---')
        if ps_layer is None:
            return None

        feats = _features_in_area(ps_layer, area_geom_proj, proj_crs)
        n_ps = len(feats)
        self._log(f'  PS nell\'area: {n_ps}')

        densita = (n_ps / area_mq * 1_000_000) if area_mq else 0.0  # PS/kmq

        campi_d = self._date_fields(ps_layer)
        if campi_d and n_ps > 0:
            coe_res = self._coerenza(feats, campi_d, soglia)
            _, _, pct_coe, coerente_mask = (
                coe_res if len(coe_res) == 4 else (coe_res[0], coe_res[1], coe_res[2], None)
            )
        else:
            pct_coe, coerente_mask = 0.0, None

        # Velocità media totale e su soli coerenti
        vel_vals = []
        vel_vals_coe = []
        for i, f in enumerate(feats):
            v = _qv(f[vel_field]) if vel_field and vel_field in f.fields().names() else None
            if v is not None:
                vel_vals.append(v)
                if coerente_mask is not None and i < len(coerente_mask) and coerente_mask[i]:
                    vel_vals_coe.append(v)
        vel_media_tot = float(np.mean(vel_vals)) if vel_vals else None
        vel_std_tot = float(np.std(vel_vals, ddof=1)) if len(vel_vals) > 1 else None
        vel_media_coe = float(np.mean(vel_vals_coe)) if vel_vals_coe else (
            vel_media_tot if coerente_mask is None else None)
        vel_std_coe = (float(np.std(vel_vals_coe, ddof=1)) if len(vel_vals_coe) > 1
                        else (vel_std_tot if coerente_mask is None else None))

        # Copertura areale (% copertura buffer)
        try:
            copertura = self._copertura_pct(feats, ps_layer.crs(), area_geom_proj,
                                             proj_crs, raggio_m, ctx, feedback)
        except Exception as _e:
            self._log(f'  ⚠ Copertura non calcolabile: {_e}')
            copertura = None

        # Visibilità media (pc_mov) dal layer VIS corrispondente
        visibilita = None
        visibilita_std = None
        if vis_layer is not None:
            vis_feats = _features_in_area(vis_layer, area_geom_proj, proj_crs)
            pcs = [_qv(f['pc_mov']) for f in vis_feats
                   if 'pc_mov' in f.fields().names()]
            pcs = [p for p in pcs if p is not None]
            visibilita = float(np.mean(pcs)) if pcs else None
            visibilita_std = float(np.std(pcs, ddof=1)) if len(pcs) > 1 else None

        # Accelerazione: rapporto tra velocità dell'ultimo segmento e del
        # precedente, da regressione piecewise sulla serie media coerente
        try:
            accel = self._accelerazione(feats, campi_d, coerente_mask)
        except Exception as _e:
            self._log(f'  ⚠ Accelerazione non calcolabile: {_e}')
            accel = {'rapporto': None, 'v_ultimo': None, 'v_precedente': None,
                      'n_segmenti': None, 'inversione': False}

        return {
            'n_ps': n_ps,
            'densita': densita,
            'copertura_pct': copertura,
            'coerenza_pct': pct_coe,
            'coerenza_soglia': soglia,
            'vel_media_tot': vel_media_tot,
            'vel_std_tot': vel_std_tot,
            'vel_media_coe': vel_media_coe,
            'vel_std_coe': vel_std_coe,
            'visibilita_media': visibilita,
            'visibilita_std': visibilita_std,
            'accelerazione': accel,
        }

    def run(self):
        try:
            from qgis.core import QgsProcessingContext, QgsProcessingFeedback
            ctx = QgsProcessingContext()
            feedback = QgsProcessingFeedback()

            p = self.params
            proj_crs = QgsProject.instance().crs()
            area_geom = p['area_geom']

            da = QgsDistanceArea()
            da.setSourceCrs(proj_crs, QgsProject.instance().transformContext())
            da.setEllipsoid(QgsProject.instance().ellipsoid() or 'WGS84')
            area_mq_raw = abs(da.measureArea(area_geom))
            area_mq = da.convertAreaMeasurement(area_mq_raw, QgsUnitTypes.AreaUnit.AreaSquareMeters)

            asc = self._lato('ASC', p.get('ps_asc'), p.get('ps_asc_field'),
                              p.get('vis_asc'), area_geom, proj_crs,
                              p['raggio_m'], p['soglia'], area_mq, ctx, feedback)
            if self.isCanceled():
                return False
            desc = self._lato('DESC', p.get('ps_desc'), p.get('ps_desc_field'),
                               p.get('vis_desc'), area_geom, proj_crs,
                               p['raggio_m'], p['soglia'], area_mq, ctx, feedback)
            if self.isCanceled():
                return False

            # Blocco EWUD (media su area di studio)
            ewud = None
            ewud_layer = p.get('ewud')
            if ewud_layer is not None:
                ewud_feats = _features_in_area(ewud_layer, area_geom, proj_crs)
                names = [f.name() for f in ewud_layer.fields()]

                def _field_vals(fname):
                    if fname not in names:
                        return []
                    vals = [_qv(f[fname]) for f in ewud_feats]
                    return [v for v in vals if v is not None]

                vel_vals_ewud = _field_vals('vel')
                vel_mean_ewud = float(np.mean(vel_vals_ewud)) if vel_vals_ewud else None
                vel_std_ewud = (float(np.std(vel_vals_ewud, ddof=1))
                                 if len(vel_vals_ewud) > 1 else None)

                # L'angolo EWUD è una grandezza circolare: la media aritmetica
                # sarebbe scorretta vicino allo 0°/360° (es. 359° e 1° darebbero
                # una media di 180°, l'opposto del valore corretto). Si usa
                # quindi la media vettoriale, che restituisce anche la
                # lunghezza risultante R (0-1): R vicino a 1 indica direzioni
                # coerenti, R vicino a 0 indica direzioni sparse in tutti i
                # sensi (media poco significativa).
                ang_vals = _field_vals('ang2')
                ang_mean, ang_std, ang_R = _circular_stats(ang_vals)

                e_vals = _field_vals('E')
                u_vals = _field_vals('U')

                ewud = {
                    'n_celle': len(ewud_feats),
                    'ang2': ang_mean,
                    'ang2_std': ang_std,
                    'ang2_R': ang_R,
                    'vel': vel_mean_ewud,
                    'vel_std': vel_std_ewud,
                    'E': float(np.mean(e_vals)) if e_vals else None,
                    'E_std': float(np.std(e_vals, ddof=1)) if len(e_vals) > 1 else None,
                    'U': float(np.mean(u_vals)) if u_vals else None,
                    'U_std': float(np.std(u_vals, ddof=1)) if len(u_vals) > 1 else None,
                }

            self.results = {
                'area_mq': area_mq,
                'asc': asc,
                'desc': desc,
                'ewud': ewud,
                'soglia_accelerazione': p.get('soglia_accel', 1.5),
            }
            return True
        except Exception:
            self.error_msg = traceback.format_exc()
            return False

    def finished(self, success):
        if success and self.results is not None:
            self.bridge.finished_signal.emit(self.results)
        else:
            self.bridge.error_signal.emit(self.error_msg or 'Errore sconosciuto.')

    def cancel(self):
        self.bridge.log_signal.emit('Interruzione richiesta — attendi...')
        super().cancel()
