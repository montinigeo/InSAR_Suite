# InSAR Suite

**Plugin QGIS per l'analisi dei dati PS-InSAR / QGIS plugin for PS-InSAR data analysis**

---

## 🇮🇹 Italiano

### Descrizione

InSAR Suite è un plugin QGIS che raccoglie in un'unica toolbar dedicata quattro strumenti per l'analisi dei dati PS-InSAR (Persistent Scatterer Interferometric SAR). Il plugin nasce per semplificare il flusso di lavoro nell'analisi di dataset nazionali (es. EGMS Italia) e locali, coprendo tutte le fasi dall'acquisizione dati alla visualizzazione avanzata delle serie storiche.

### Moduli

| Modulo | Descrizione |
|--------|-------------|
| **InSAR Load** | Caricamento layer PS da GeoPackage, Shapefile o GDB tramite un quadro di unione poligonale, con attivazione automatica al clic su mappa. Supporta anche il ricaricamento di un quadro già presente nel progetto. |
| **InSAR EWUD** | Decomposizione East-West / Up-Down delle velocità LOS da coppie ascending/descending. Preset satellitari inclusi (Sentinel-1 EGMS, ERS/Envisat, COSMO-SkyMed, TerraSAR-X, ALOS). Output con campi Na e Nd (numero PS per cella). |
| **PSInSAR VIS** | Calcolo della percentuale di movimento rilevabile (pc_mov) in funzione della geometria SAR e della morfologia del terreno (Aspect/Slope da DEM). Elaborazione tramite QgsTask (GUI non bloccante). |
| **PSInSAR TS** | Analisi serie storiche: verifica normalità, serie media automatica (con layer temporaneo in QGIS), scomposizione STL, analisi non lineare piecewise (pwlf), geostatistica e kriging. |

### Requisiti

- QGIS 3.16 o superiore
- Python 3 con librerie: `pandas`, `numpy`, `matplotlib`, `scipy`, `statsmodels`, `pyproj`, `pykrige`, `mplcursors`, `pwlf`

### Installazione

**Da ZIP (consigliato):**
1. Scarica l'ultima release da [Releases](../../releases)
2. In QGIS: *Plugin → Gestisci e installa plugin → Installa da ZIP*
3. Abilita il plugin dall'elenco degli installati

**Dal QGIS Plugin Repository:**
1. In QGIS: *Plugin → Gestisci e installa plugin*
2. Spunta "Mostra plugin sperimentali"
3. Cerca "PSInSAR Suite" e installa

### Utilizzo rapido

1. **Load** — Carica il layer PS puntuale selezionando i poligoni del quadro di unione
2. **EWUD** — Crea la griglia e decomponi le velocità ascending/descending
3. **VIS** — Calcola pc_mov sul layer PS con un DEM
4. **TS** — Imposta il layer PS come attivo, seleziona i punti sulla mappa, avvia l'analisi

> I moduli TS richiedono che il layer PS sia attivo e che siano presenti punti selezionati. In assenza di selezione il plugin mostra una finestra di avviso dedicata.

### Formato dati atteso per il modulo TS

I layer PS devono contenere campi di spostamento nel formato `DYYYYMMDD` (es. `D20170101`, `D20170213`, …), un campo per ogni data di acquisizione SAR.

### Segnalazione bug e contributi

Apri una [Issue](../../issues) su GitHub per segnalare problemi o proporre miglioramenti.

---

## 🇬🇧 English

### Description

PSInSAR Suite is a QGIS plugin that consolidates four PS-InSAR (Persistent Scatterer Interferometric SAR) analysis tools into a single dedicated toolbar. It is designed to streamline the analysis workflow for national (e.g. EGMS Italy) and local PS-InSAR datasets, covering all stages from data loading to advanced time series analysis.

### Modules

| Module | Description |
|--------|-------------|
| **PSInSAR Load** | Loads PS-InSAR point layers from GeoPackage, Shapefile or GDB using a polygon index layer, with automatic loading on map selection. Also supports reactivation of an index already loaded in the project. |
| **PSInSAR EWUD** | East-West / Up-Down decomposition of LOS velocities from ascending/descending pairs. Includes satellite presets (Sentinel-1 EGMS, ERS/Envisat, COSMO-SkyMed, TerraSAR-X, ALOS). Output includes Na and Nd fields (PS count per cell). |
| **PSInSAR VIS** | Calculates detectable movement percentage (pc_mov) based on SAR acquisition geometry and terrain morphology (Aspect/Slope from DEM). Runs as a QgsTask (non-blocking GUI). |
| **PSInSAR TS** | Time series analysis: normality check, automatic mean series (with temporary QGIS layer output), STL seasonal decomposition, piecewise non-linear analysis (pwlf), geostatistics and ordinary kriging. |

### Requirements

- QGIS 3.16 or higher
- Python 3 with libraries: `pandas`, `numpy`, `matplotlib`, `scipy`, `statsmodels`, `pyproj`, `pykrige`, `mplcursors`, `pwlf`

### Installation

**From ZIP (recommended):**
1. Download the latest release from [Releases](../../releases)
2. In QGIS: *Plugins → Manage and Install Plugins → Install from ZIP*
3. Enable the plugin from the installed list

**From QGIS Plugin Repository:**
1. In QGIS: *Plugins → Manage and Install Plugins*
2. Check "Show experimental plugins"
3. Search for "PSInSAR Suite" and install

### Quick start

1. **Load** — Load the PS point layer by selecting polygons from the index layer
2. **EWUD** — Create the resampling grid and decompose ascending/descending LOS velocities
3. **VIS** — Calculate pc_mov on the PS layer using a DEM
4. **TS** — Set the PS layer as active, select points on the map, run the analysis

> TS modules require the PS layer to be active and points to be selected. If no selection is present, the plugin shows a dedicated warning dialog.

### Expected data format for TS module

PS layers must contain displacement fields in the format `DYYYYMMDD` (e.g. `D20170101`, `D20170213`, …), one field per SAR acquisition date.

### Bug reports and contributions

Please open an [Issue](../../issues) on GitHub to report bugs or suggest improvements.

---

## Disclaimer

PSInSAR Suite is an independent open-source QGIS plugin developed for post-processing analysis of PS-InSAR displacement data. It is **not affiliated with, endorsed by, or related to** TRE ALTAMIRA S.r.l., the Politecnico di Milano, or any other organisation involved in the development or commercialisation of PS-InSAR processing algorithms. The term "PS-InSAR" (Persistent Scatterer Interferometric SAR) is used here exclusively in its generic scientific meaning, as widely adopted in the open literature.

The results produced by this plugin are intended as a support tool for hazard and risk analysis (landslide, subsidence) and must always be evaluated in conjunction with other base data and verified in the field by a qualified professional.

---

## License

This plugin is released under the [GNU General Public License v2 or later](https://www.gnu.org/licenses/old-licenses/gpl-2.0.html), in compliance with QGIS licensing requirements.

## Author

Giovanni Montini — [g.montini@appenninosettentrionale.it](mailto:g.montini@appenninosettentrionale.it)
