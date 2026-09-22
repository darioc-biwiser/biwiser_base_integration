import collections
import logging
import sys
import time
from pathlib import Path

from app.utils import fechas


BASE_DIR = Path(__file__).resolve().parent.parent.parent

# error/: detalle reciente (DEBUG+) SOLO si hubo una falla real, y los
#         archivos api_fallida_*.log con las URLs que no se pudieron
#         descargar.
# logs/:  log completo de cada ejecución (INFO+), opcional
#         (LOG_ARCHIVO_EJECUCION=1 en el .env del cliente).
ERROR_DIR = BASE_DIR / "error"
LOGS_DIR = BASE_DIR / "logs"

# Ícono automático según severidad del log.
ICONOS_NIVEL = {
    "DEBUG": "🔍",
    "INFO": "ℹ️ ",
    "WARNING": "⚠️ ",
    "ERROR": "❌",
    "CRITICAL": "🔥",
}

FORMATO_LOG = (
    "%(asctime)s | "
    "%(icono)s %(levelname)-8s | "
    "%(name)s | "
    "%(message)s"
)

FORMATO_FECHA_LOG = "%Y-%m-%d %H:%M:%S"


class IconFormatter(logging.Formatter):
    def format(self, record):
        record.icono = ICONOS_NIVEL.get(record.levelname, "  ")
        return super().format(record)


class RingBufferHandler(logging.Handler):
    """
    Buffer circular de logging: conserva solo las últimas `capacity`
    líneas en memoria (descarta las más antiguas al superar el límite)
    y únicamente las escribe al handler `target` cuando llega un
    registro de nivel >= `flush_level`.

    A diferencia de logging.handlers.MemoryHandler, NUNCA vuelca a
    disco por haber llenado el buffer: el archivo de error solo se
    crea ante un error real y queda acotado al contexto reciente (las
    últimas `capacity` líneas), no a todo el historial de la ejecución.
    """

    def __init__(self, capacity, flush_level, target):
        super().__init__()

        self.capacity = capacity
        self.flush_level = flush_level
        self.target = target
        self.buffer = collections.deque(maxlen=capacity)

    def emit(self, record):
        self.buffer.append(record)

        if record.levelno >= self.flush_level:
            self._volcar_buffer()

    def _volcar_buffer(self):
        if self.target is None or not self.buffer:
            return

        for registro_pendiente in self.buffer:
            self.target.handle(registro_pendiente)

        self.buffer.clear()

    def flush(self):
        # No-op intencional. logging.shutdown() (vía atexit) llama
        # flush() en TODOS los handlers al terminar el proceso, haya o
        # no errores. Si este método volcara el buffer, el archivo de
        # error se crearía en corridas 100% exitosas. El volcado real
        # solo ocurre en emit(), ante un registro >= flush_level.
        pass

    def close(self):
        if self.target is not None:
            self.target.close()

        super().close()


def configurar_encoding_consola():
    # En consolas Windows con codepage legacy (cp1252), imprimir emojis
    # puede lanzar UnicodeEncodeError y detener el proceso. Se fuerza
    # UTF-8 (con reemplazo) para que los íconos nunca interrumpan.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def limpiar_archivos_antiguos(directorio, dias_retencion):
    """
    Elimina archivos *.log más antiguos que `dias_retencion` días.
    0 (o negativo) = no se elimina nada.
    """

    if dias_retencion <= 0 or not directorio.exists():
        return 0

    limite = time.time() - dias_retencion * 86400
    eliminados = 0

    for archivo in directorio.glob("*.log"):
        try:
            if archivo.stat().st_mtime < limite:
                archivo.unlink()
                eliminados += 1
        except OSError:
            continue

    return eliminados


def configurar_logging(
    empresa,
    modulo,
    nivel_consola="INFO",
    buffer_capacidad=2000,
    archivo_ejecucion=False,
    retencion_dias=0,
):
    """
    Configura el logging de la ejecución y devuelve
    (archivo_error, archivo_ejecucion | None):

    - Consola: nivel `nivel_consola` (por defecto INFO) con íconos.
    - error/<empresa>_<modulo>_<ts>.log: detalle DEBUG+ reciente, que
      solo se escribe si ocurre un ERROR (ver RingBufferHandler).
    - logs/<empresa>_<modulo>_<ts>.log (opcional): log INFO+ completo.
    """

    configurar_encoding_consola()

    ERROR_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = fechas.timestamp_archivo()
    nombre_base = f"{empresa}_{modulo}_{timestamp}.log"

    archivo_error = ERROR_DIR / nombre_base
    archivo_log = LOGS_DIR / nombre_base if archivo_ejecucion else None

    formatter = IconFormatter(fmt=FORMATO_LOG, datefmt=FORMATO_FECHA_LOG)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(
        getattr(logging, str(nivel_consola).upper(), logging.INFO)
    )
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    detalle_handler = logging.FileHandler(
        archivo_error,
        encoding="utf-8",
        delay=True,
    )
    detalle_handler.setLevel(logging.DEBUG)
    detalle_handler.setFormatter(formatter)

    buffer_handler = RingBufferHandler(
        capacity=buffer_capacidad,
        flush_level=logging.ERROR,
        target=detalle_handler,
    )
    buffer_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(buffer_handler)

    if archivo_log is not None:
        ejecucion_handler = logging.FileHandler(
            archivo_log,
            encoding="utf-8",
            delay=True,
        )
        ejecucion_handler.setLevel(logging.INFO)
        ejecucion_handler.setFormatter(formatter)
        root_logger.addHandler(ejecucion_handler)

    # urllib3 registra una línea DEBUG por cada request HTTP
    # (duplicando lo que ya registra core/http_client). En ejecuciones
    # con miles de llamadas es la principal fuente de peso del log.
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    limpiar_archivos_antiguos(ERROR_DIR, retencion_dias)
    limpiar_archivos_antiguos(LOGS_DIR, retencion_dias)

    return archivo_error, archivo_log
