# -*- coding: utf-8 -*-
"""
qt_compat.py
============
Modulo di compatibilità per i TIPI DI CAMPO usati con QgsField, tra:
  - QGIS 3 / PyQt5  -> tipo espresso come QVariant.Int, QVariant.Double, ...
  - QGIS 4 / PyQt6  -> le costanti statiche di QVariant sono state rimosse;
                        il tipo va espresso con QMetaType (QMetaType.Type.Int
                        oppure QMetaType.Int a seconda della versione).

QUESTO MODULO È UNA BOZZA "difensiva": prova più strade in ordine e logga
ogni tentativo/esito su file, così che eseguendo il plugin su un QGIS 4
reale sia facile aprire il log e capire subito quale variante ha funzionato
(o dove fallisce), senza dover ricopiare a mano gli errori dalla console.

Uso nei moduli del plugin:
    from ..qt_compat import FIELD_INT, FIELD_DOUBLE, FIELD_STRING, ...
    fields.append(QgsField("velocita", FIELD_DOUBLE))

oppure, per un tipo occasionale non già precalcolato:
    from ..qt_compat import field_type
    fields.append(QgsField("nome_campo", field_type("LongLong")))

IMPORTANTE: questa è una bozza da verificare con QGIS 4 realmente installato.
Il file di log (percorso stampato al primo import, vedi anche get_log_path())
va controllato dopo il primo utilizzo del plugin su QGIS 4: se qualcosa non
torna, incollami il contenuto del log così sistemiamo il modulo.
"""
import os
import logging
import tempfile

from qgis.PyQt.QtCore import QVariant

try:
    from qgis.PyQt.QtCore import QMetaType
    _HAS_QMETATYPE = True
except ImportError:
    _HAS_QMETATYPE = False


# ---------------------------------------------------------------------------
# Setup logging su file dedicato (uno per sessione di QGIS)
# ---------------------------------------------------------------------------
def _build_log_path():
    try:
        base_dir = os.path.join(tempfile.gettempdir(), "insar_suite_logs")
        os.makedirs(base_dir, exist_ok=True)
    except OSError:
        base_dir = tempfile.gettempdir()
    return os.path.join(base_dir, "insar_suite_qt_compat.log")


_LOG_FILE = _build_log_path()

_logger = logging.getLogger("InSAR_Suite.qt_compat")
if not _logger.handlers:
    _logger.setLevel(logging.DEBUG)
    _handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    _handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    _logger.addHandler(_handler)


def get_log_path():
    """Restituisce il percorso del file di log, utile per mostrarlo all'utente
    (es. in un QMessageBox) o per aprirlo manualmente dopo un test su QGIS 4."""
    return _LOG_FILE


_logger.info("=" * 70)
_logger.info(
    "qt_compat caricato. QMetaType disponibile: %s | Log file: %s",
    _HAS_QMETATYPE, _LOG_FILE,
)


# ---------------------------------------------------------------------------
# Risoluzione del tipo campo, con più tentativi e log dettagliato
# ---------------------------------------------------------------------------
# Alcuni nomi di tipo differiscono tra le due nomenclature: QMetaType.Type
# usa il nome della classe Qt sottostante con il prefisso "Q" per i tipi
# non primitivi (rispecchia QString/QDate/QDateTime), mentre il vecchio
# QVariant.Type usava nomi semplificati senza prefisso. Se non mappato qui,
# il nome viene provato invariato (funziona per Int/Double/Bool/LongLong,
# che coincidono su entrambe le nomenclature).
_QMETATYPE_NAME_MAP = {
    "String": "QString",
    "Date": "QDate",
    "DateTime": "QDateTime",
    "Time": "QTime",
    "ByteArray": "QByteArray",
    "Char": "QChar",
    "Url": "QUrl",
}


def field_type(name):
    """
    Restituisce il valore da passare come 'type' a QgsField(...), dato un
    nome testuale del tipo QVariant/QMetaType classico, es:
    'Int', 'LongLong', 'Double', 'String', 'Date', 'DateTime', 'Bool'.

    Ordine dei tentativi (il primo che va a buon fine viene usato e loggato):
      1. QMetaType.Type.<nome mappato>   (QGIS 4 / Qt6, forma più recente;
         il nome viene tradotto tramite _QMETATYPE_NAME_MAP quando necessario,
         es. 'String' -> 'QString', perché QMetaType.Type non usa gli stessi
         nomi "semplificati" del vecchio QVariant.Type per i tipi non
         primitivi)
      2. QMetaType.<nome mappato>        (variante alternativa vista in alcune build)
      3. QVariant.<name>                 (QGIS 3 / Qt5, forma storica, nome originale)

    Se nessuno dei tre funziona, viene sollevata l'eccezione originale e
    l'errore viene scritto nel log con tutti i dettagli.
    """
    attempts = []
    qmeta_name = _QMETATYPE_NAME_MAP.get(name, name)

    if _HAS_QMETATYPE:
        try:
            value = getattr(QMetaType.Type, qmeta_name)
            _logger.debug("field_type(%r) risolto con QMetaType.Type.%s = %r", name, qmeta_name, value)
            return value
        except AttributeError as e:
            attempts.append(("QMetaType.Type.%s" % qmeta_name, e))

        try:
            value = getattr(QMetaType, qmeta_name)
            _logger.debug("field_type(%r) risolto con QMetaType.%s = %r", name, qmeta_name, value)
            return value
        except AttributeError as e:
            attempts.append(("QMetaType.%s" % qmeta_name, e))

    try:
        value = getattr(QVariant, name)
        _logger.debug("field_type(%r) risolto con QVariant.%s = %r", name, name, value)
        return value
    except AttributeError as e:
        attempts.append(("QVariant.%s" % name, e))

    _logger.error(
        "field_type(%r): NESSUN tentativo riuscito. Dettagli: %s",
        name,
        "; ".join("%s -> %s" % (label, err) for label, err in attempts),
    )
    # rilancia l'ultimo errore, così il traceback in QGIS resta comprensibile
    raise attempts[-1][1]


def log_exception(context, exc):
    """Helper per loggare eccezioni generiche incontrate durante il porting,
    da richiamare nei blocchi try/except dei moduli in fase di test su QGIS 4."""
    _logger.exception("Errore in '%s': %s", context, exc)


# ---------------------------------------------------------------------------
# Costanti pronte all'uso (calcolate una sola volta al caricamento del modulo)
# ---------------------------------------------------------------------------
FIELD_INT = field_type("Int")
FIELD_LONGLONG = field_type("LongLong")
FIELD_DOUBLE = field_type("Double")
FIELD_STRING = field_type("String")
FIELD_DATE = field_type("Date")
FIELD_DATETIME = field_type("DateTime")
FIELD_BOOL = field_type("Bool")


# ---------------------------------------------------------------------------
# setFilters() su QgsMapLayerComboBox / QgsMapLayerProxyModel: da QGIS 3.34
# la forma "int" (QgsMapLayerProxyModel.PointLayer, ecc.) è deprecata a
# favore di Qgis.LayerFilter (flag enum). La vecchia forma continua a
# funzionare, ma genera un DeprecationWarning ad ogni chiamata — con
# installazioni QGIS recenti (es. 3.44.13) il log può risultare rumoroso o,
# in alcuni casi, l'avviso viene mostrato come se fosse un errore.
# Questa funzione usa la forma nuova quando disponibile (QGIS >= 3.34),
# altrimenti ricade sulla vecchia per compatibilità con QGIS 3.16-3.33.
def set_layer_filters(combo, *names):
    """Imposta i filtri di tipo layer su un QgsMapLayerComboBox (o oggetto
    con lo stesso metodo setFilters), usando Qgis.LayerFilter se disponibile
    (QGIS >= 3.34), altrimenti QgsMapLayerProxyModel.<nome> (QGIS < 3.34).
    'names' sono stringhe come 'PointLayer', 'PolygonLayer', 'RasterLayer'."""
    try:
        from qgis.core import Qgis
        flags = None
        for n in names:
            v = getattr(Qgis.LayerFilter, n)
            flags = v if flags is None else (flags | v)
        # L'OR bit a bit tra membri di Qgis.LayerFilter non produce sempre
        # automaticamente un'istanza di Qgis.LayerFilters (il tipo "flags"
        # plurale atteso dalla firma moderna di setFilters()): incapsularlo
        # esplicitamente garantisce che venga selezionato l'overload
        # moderno e non quello deprecato basato su int, anche quando il
        # solo operatore | restituisse un intero semplice.
        try:
            flags = Qgis.LayerFilters(flags)
        except Exception as _e2:
            _logger.debug("set_layer_filters(%s): impossibile incapsulare in "
                           "Qgis.LayerFilters (%s), passo il valore così com'è.",
                           names, _e2)
        combo.setFilters(flags)
        _logger.debug("set_layer_filters(%s) risolto con Qgis.LayerFilter", names)
        return
    except (ImportError, AttributeError) as e:
        _logger.debug("set_layer_filters(%s): Qgis.LayerFilter non disponibile (%s), "
                       "uso QgsMapLayerProxyModel legacy.", names, e)

    from qgis.core import QgsMapLayerProxyModel
    flags = 0
    for n in names:
        flags |= getattr(QgsMapLayerProxyModel, n)
    combo.setFilters(flags)
