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
def field_type(name):
    """
    Restituisce il valore da passare come 'type' a QgsField(...), dato un
    nome testuale del tipo QVariant/QMetaType classico, es:
    'Int', 'LongLong', 'Double', 'String', 'Date', 'DateTime', 'Bool'.

    Ordine dei tentativi (il primo che va a buon fine viene usato e loggato):
      1. QMetaType.Type.<name>   (QGIS 4 / Qt6, forma più recente)
      2. QMetaType.<name>        (variante alternativa vista in alcune build)
      3. QVariant.<name>         (QGIS 3 / Qt5, forma storica)

    Se nessuno dei tre funziona, viene sollevata l'eccezione originale e
    l'errore viene scritto nel log con tutti i dettagli.
    """
    attempts = []

    if _HAS_QMETATYPE:
        try:
            value = getattr(QMetaType.Type, name)
            _logger.debug("field_type(%r) risolto con QMetaType.Type.%s = %r", name, name, value)
            return value
        except AttributeError as e:
            attempts.append(("QMetaType.Type.%s" % name, e))

        try:
            value = getattr(QMetaType, name)
            _logger.debug("field_type(%r) risolto con QMetaType.%s = %r", name, name, value)
            return value
        except AttributeError as e:
            attempts.append(("QMetaType.%s" % name, e))

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
