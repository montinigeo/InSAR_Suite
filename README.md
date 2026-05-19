# InSAR Suite

**Plugin QGIS per l'analisi dei dati PSI / QGIS plugin for PSI data analysis**

---

## 🇮🇹 Italiano

### Descrizione

InSAR Suite è un plugin QGIS che raccoglie in un'unica toolbar dedicata gli strumenti per l'analisi dei dati PSI (Persistent Scatterer Interferometry). Il plugin nasce per semplificare il flusso di lavoro nell'analisi di dataset nazionali (es. EGMS Italia) e locali, coprendo tutte le fasi dall'acquisizione dati alla visualizzazione avanzata delle serie storiche.

La versione 3.0 ridisegna completamente il modulo TS: la verifica di normalità è sostituita da un modulo di qualità del dato più completo, vengono aggiunti tre nuovi strumenti (rilevamento anomalie temporali, confronto tra zone, selettore di trasformazione in tempo reale) e la geostatistica è spostata su script standalone.

La versione 3.1 introduce il fix del crash matplotlib alla chiusura di QGIS, aggiunge controlli di validità del layer attivo nei moduli TS, il controllo del CRS nel modulo VIS, aggiorna i preset satellitari e aggiunge RADARSAT-2.

La versione 3.2 aggiunge skewness e kurtosis all'analisi qualità TS, supporta il formato data YYYYMMDD (oltre a DYYYYMMDD), introduce il pulsante "Carica PS coerenti in QGIS" nei grafici TS, aggiunge celle esagonali nel modulo EWUD, consente il salvataggio permanente dell'output VIS, aggiorna i preset satellitari con azimut ASC in convenzione positiva ([0°, 360°]) e introduce il nuovo modulo **InSAR Polygons** per la delimitazione automatica delle aree di deformazione.

### Moduli

| Modulo | Descrizione |
|--------|-------------|
| **InSAR Load** | Caricamento layer PS da GeoPackage, Shapefile o GDB tramite un quadro di unione poligonale, con attivazione automatica al clic su mappa. Supporta anche il ricaricamento di un quadro già presente nel progetto. |
| **InSAR EWUD** | Ricostruzione del vettore velocità nel piano Est-Ovest / Up-Down dalle velocità LOS di coppie ascending/descending. Griglia di ricampionamento con celle quadrate o esagonali. Preset satellitari inclusi (Sentinel-1 EGMS - Italia centro-settentrionale, Sentinel-1 generico, ERS/Envisat, ALOS/ALOS-2, RADARSAT-2, COSMO-SkyMed, TerraSAR-X/TanDEM-X). Output con campi Na e Nd (numero PS per cella). |
| **InSAR VIS** | Calcolo della percentuale di movimento rilevabile (pc_mov) in funzione della geometria SAR e della morfologia del terreno (Aspect/Slope da DEM). Il DEM viene ritagliato alla risoluzione originale con snap to grid (targetAlignedPixels). Output salvabile come GeoPackage permanente. Preset satellitari inclusi (stessi di EWUD). Elaborazione tramite QgsTask (GUI non bloccante). |
| **InSAR TS** | Analisi serie storiche: qualità del dato (con skewness e kurtosis), analisi cinematica automatica, scomposizione STL, analisi non lineare piecewise (pwlf), rilevamento anomalie temporali, confronto tra zone. Supporto formati data DYYYYMMDD e YYYYMMDD. Pulsante "Carica PS coerenti in QGIS" disponibile nei grafici. |
| **InSAR Polygons** | Delimitazione automatica delle aree di deformazione: classificazione PS per soglia di velocità, buffer e dissolve geometrico per classe, validazione e smoothing morfologico. Output poligonale con attributi statistici (velocità media, n. PS, area km²). |

### Preset satellitari (moduli VIS e EWUD)

| Satellite | Banda | ASC az | ASC on | DESC az | DESC on |
|-----------|-------|--------|--------|---------|---------|
| Sentinel-1 (EGMS - Italia centro-settentrionale) | C | 349° | 42° | 191° | 38° |
| Sentinel-1 (generico) | C | 348° | 33° | 192° | 33° |
| ERS / Envisat | C | 347° | 23° | 193° | 23° |
| ALOS / ALOS-2 | L | 350° | 34° | 190° | 34° |
| RADARSAT-2 | C | 350° | 35° | 190° | 35° |
| COSMO-SkyMed | X | 345° | 30° | 195° | 30° |
| TerraSAR-X / TanDEM-X | X | 350° | 35° | 190° | 35° |

> Gli azimut ASC sono espressi nella convenzione [0°, 360°] (es. 349° = −11°). Il risultato delle formule è identico — sin(349°) = sin(−11°).

### Strumenti del modulo TS

| Strumento | Descrizione |
|-----------|-------------|
| **Qualità del dato** | Istogramma + curva normale N(μ,σ), Q-Q plot, boxplot con dati individuali, statistiche robuste (media, std, mediana, IQR, MAD, skewness, kurtosis, z-score robusto, Shapiro-Wilk). Selettore trasformazione in tempo reale. Pulsante "Carica PS coerenti in QGIS". |
| **Analisi automatica** | Serie storica media ±1σ, trend OLS con velocità e R², tooltip interattivo, pulsante per caricare la tabella in QGIS. Pulsante "Carica PS coerenti in QGIS". |
| **Scomposizione STL** | Scomposizione della serie media in trend T(t), stagionalità S(t) e residuo R(t). Pulsante "Carica PS coerenti in QGIS". |
| **Analisi non lineare** | Regressione piecewise (pwlf), ottimizzazione BIC, numero massimo di segmenti configurabile (2–5), tabella riepilogativa con periodo, velocità e R² per ogni segmento. Pulsante "Carica PS coerenti in QGIS". |
| **Anomalie temporali** | Rilevamento acquisizioni anomale su residui (soglia nσ) e variazioni consecutive (soglia Δmm). Tooltip ⚠ ANOMALIA sulle date anomale. |
| **Confronto tra zone** | Confronto serie medie tra 2–3 zone con pannello non modale, bande ±1σ e rette OLS. |

### Requisiti

- QGIS 3.16 o superiore
- Python 3 con librerie: `pandas`, `numpy`, `matplotlib`, `scipy`, `statsmodels`, `pyproj`, `mplcursors`, `pwlf`

> A partire dalla v3.0 la libreria `pykrige` non è più richiesta dalla toolbar principale. La geostatistica è disponibile come script standalone nella cartella `docs/`.

### Installazione

**Dal QGIS Plugin Repository (consigliato):**
1. In QGIS: *Plugin → Gestisci e installa plugin → Tutti*
2. Cerca **InSAR Suite** e clicca su *Installa plugin*

Le nuove versioni vengono pubblicate direttamente nel repository e sono immediatamente disponibili senza attese di revisione.

**Da ZIP:**
1. Scarica `InSAR_Suite_v3.2_QGIS.zip` dalla pagina [Releases](../../releases)
2. In QGIS: *Plugin → Gestisci e installa plugin → Installa da ZIP*
3. Abilita il plugin dall'elenco degli installati

**Installazione manuale:**

Copiare la cartella `InSAR_Suite/` nella directory dei plugin di QGIS:
- Windows: `C:\Users\<utente>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`
- Linux / macOS: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`

### Utilizzo rapido

1. **Load** — Carica il layer PS puntuale selezionando i poligoni del quadro di unione
2. **EWUD** — Crea la griglia (quadrata o esagonale) e ricostruisci il vettore velocità EW-UD da ascending/descending
3. **VIS** — Seleziona il layer PS e il DEM, definisci l'estensione di elaborazione e calcola pc_mov
4. **TS** — Imposta il layer PS come attivo, seleziona i punti sulla mappa, avvia le analisi nell'ordine: Qualità del dato → Analisi automatica → Scomposizione STL → Non lineare → Anomalie → Confronto zone
5. **Polygons** — Seleziona il layer PS, configura soglia di velocità e raggio buffer, avvia la delimitazione automatica delle aree di deformazione

> I moduli TS richiedono che il layer PS vettoriale sia attivo e che siano presenti punti selezionati. Se il layer attivo è un raster o non è presente alcuna selezione, il plugin mostra una finestra di avviso con le istruzioni per procedere.

### Formato dati atteso per il modulo TS

I layer PS devono contenere campi di spostamento nel formato `DYYYYMMDD` (es. `D20170101`) oppure `YYYYMMDD` (es. `20170101`), un campo per ogni data di acquisizione SAR. Il plugin riconosce automaticamente entrambi i formati.

### Segnalazione bug e contributi

Apri una [Issue](../../issues) su GitHub per segnalare problemi o proporre miglioramenti.

---

## 🇬🇧 English

### Description

InSAR Suite is a QGIS plugin that consolidates PSI (Persistent Scatterer Interferometry) analysis tools into a single dedicated toolbar. It is designed to streamline the analysis workflow for national (e.g. EGMS Italy) and local PSI datasets, covering all stages from data loading to advanced time series analysis.

Version 3.0 completely redesigns the TS module: the normality check is replaced by a more comprehensive data quality analysis, three new tools are added (temporal anomaly detection, multi-zone comparison, real-time transformation selector), and geostatistics is moved to a standalone script.

Version 3.1 introduces a fix for the matplotlib crash on QGIS exit, adds layer validity checks in TS modules, a CRS check in the VIS module, and updates satellite presets.

Version 3.2 adds skewness and kurtosis to the TS quality analysis, supports the YYYYMMDD date format (in addition to DYYYYMMDD), introduces a "Load coherent PS to QGIS" button in TS charts, adds hexagonal cell support in EWUD, allows permanent saving of VIS output, updates satellite presets to positive ASC azimuth convention ([0°, 360°]), and introduces the new **InSAR Polygons** module for automatic delineation of deformation areas.

### Modules

| Module | Description |
|--------|-------------|
| **InSAR Load** | Loads PSI point layers from GeoPackage, Shapefile or GDB using a polygon index layer, with automatic loading on map selection. Also supports reactivation of an index already loaded in the project. |
| **InSAR EWUD** | Reconstructs the velocity vector in the East-West / Up-Down plane from ascending/descending LOS velocities. Resampling grid with square or hexagonal cells. Includes satellite presets (Sentinel-1 EGMS - central-northern Italy, Sentinel-1 generic, ERS/Envisat, ALOS/ALOS-2, RADARSAT-2, COSMO-SkyMed, TerraSAR-X/TanDEM-X). Output includes Na and Nd fields (PS count per cell). |
| **InSAR VIS** | Calculates detectable movement percentage (pc_mov) based on SAR acquisition geometry and terrain morphology (Aspect/Slope from DEM). The DEM is clipped at its original resolution with snap to grid (targetAlignedPixels). Output can be saved as a permanent GeoPackage. Includes satellite presets (same as EWUD). Runs as a QgsTask (non-blocking GUI). |
| **InSAR TS** | Time series analysis: data quality check (with skewness and kurtosis), automatic mean series, STL seasonal decomposition, piecewise non-linear analysis (pwlf), temporal anomaly detection, multi-zone comparison. Supports DYYYYMMDD and YYYYMMDD date formats. "Load coherent PS to QGIS" button available in charts. |
| **InSAR Polygons** | Automatic delineation of deformation areas: PS classification by velocity threshold, buffer and geometric dissolve by velocity class, validation and morphological smoothing. Polygonal output with statistical attributes (mean velocity, PS count, area km²). |

### Satellite presets (VIS and EWUD modules)

| Satellite | Band | ASC az | ASC on | DESC az | DESC on |
|-----------|------|--------|--------|---------|---------|
| Sentinel-1 (EGMS - central-northern Italy) | C | 349° | 42° | 191° | 38° |
| Sentinel-1 (generic) | C | 348° | 33° | 192° | 33° |
| ERS / Envisat | C | 347° | 23° | 193° | 23° |
| ALOS / ALOS-2 | L | 350° | 34° | 190° | 34° |
| RADARSAT-2 | C | 350° | 35° | 190° | 35° |
| COSMO-SkyMed | X | 345° | 30° | 195° | 30° |
| TerraSAR-X / TanDEM-X | X | 350° | 35° | 190° | 35° |

> ASC azimuths are expressed in the [0°, 360°] convention (e.g. 349° = −11°). Formula results are identical — sin(349°) = sin(−11°).

### TS module tools

| Tool | Description |
|------|-------------|
| **Data quality** | Histogram + normal curve N(μ,σ), Q-Q plot, individual data boxplot, robust statistics (mean, std, median, IQR, MAD, skewness, kurtosis, robust z-score, Shapiro-Wilk). Real-time transformation selector. "Load coherent PS to QGIS" button. |
| **Automatic analysis** | Mean time series ±1σ, OLS trend with velocity and R², interactive tooltip, button to load the table into QGIS. "Load coherent PS to QGIS" button. |
| **STL decomposition** | Decomposition of the mean series into trend T(t), seasonality S(t) and residual R(t). "Load coherent PS to QGIS" button. |
| **Non-linear analysis** | Piecewise regression (pwlf), BIC optimisation, configurable maximum number of segments (2–5), summary table with period, velocity and R² per segment. "Load coherent PS to QGIS" button. |
| **Temporal anomalies** | Detection of anomalous acquisitions based on residual threshold (nσ) and consecutive variation threshold (Δmm). Interactive ⚠ ANOMALY tooltip. |
| **Zone comparison** | Comparison of mean time series between 2–3 zones with non-modal panel, ±1σ bands and OLS regression lines. |

### Requirements

- QGIS 3.16 or higher
- Python 3 with libraries: `pandas`, `numpy`, `matplotlib`, `scipy`, `statsmodels`, `pyproj`, `mplcursors`, `pwlf`

> From v3.0 onwards, the `pykrige` library is no longer required by the main toolbar. Geostatistics is available as a standalone script in the `docs/` folder.

### Installation

**From QGIS Plugin Repository (recommended):**
1. In QGIS: *Plugins → Manage and Install Plugins → All*
2. Search for **InSAR Suite** and click *Install Plugin*

New versions are published directly to the repository and are immediately available without review delays.

**From ZIP:**
1. Download `InSAR_Suite_v3.2_QGIS.zip` from the [Releases](../../releases) page
2. In QGIS: *Plugins → Manage and Install Plugins → Install from ZIP*
3. Enable the plugin from the installed list

**Manual installation:**

Copy the `InSAR_Suite/` folder to the QGIS plugins directory:
- Windows: `C:\Users\<user>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`
- Linux / macOS: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`

### Quick start

1. **Load** — Load the PS point layer by selecting polygons from the index layer
2. **EWUD** — Create the resampling grid (square or hexagonal) and reconstruct the EW-UD velocity vector from ascending/descending pairs
3. **VIS** — Select the PS layer and DEM, define the processing extent and calculate pc_mov
4. **TS** — Set the PS layer as active, select points on the map, run the analyses in order: Data quality → Automatic analysis → STL decomposition → Non-linear → Anomalies → Zone comparison
5. **Polygons** — Select the PS layer, configure velocity threshold and buffer radius, run the automatic delineation of deformation areas

> TS modules require the PS vector layer to be active and points to be selected. If the active layer is a raster or no selection is present, the plugin shows a dedicated warning dialog with instructions.

### Expected data format for the TS module

PS layers must contain displacement fields in the format `DYYYYMMDD` (e.g. `D20170101`) or `YYYYMMDD` (e.g. `20170101`), one field per SAR acquisition date. Both formats are recognised automatically.

### Bug reports and contributions

Please open an [Issue](../../issues) on GitHub to report bugs or suggest improvements.

---

## Disclaimer

InSAR Suite is an independent open-source QGIS plugin for post-processing analysis of PSI (Persistent Scatterer Interferometry) displacement data, developed independently from other InSAR-related QGIS plugins and organisations. It is not affiliated with any organisation involved in the development or commercialisation of PSI processing algorithms. The results produced by this plugin are intended as a support tool for hazard and risk analysis (landslide, subsidence) and must always be evaluated in conjunction with other base data and verified in the field by a qualified professional.

---

## License

This plugin is released under the [GNU General Public License v2 or later](https://www.gnu.org/licenses/old-licenses/gpl-2.0.html), in compliance with QGIS licensing requirements.

## Author

Giovanni Montini — [g.montini@appenninosettentrionale.it](mailto:g.montini@appenninosettentrionale.it)
