import logging
import time

from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError


log = logging.getLogger(__name__)

APPLICATION_NAME = "biwiser_base_integration"


def crear_engine(db, ejecucion, rol):
    """
    Engine Postgres con:
    - URL.create: usuarios/contraseñas con caracteres especiales
      (@, :, /) no rompen la cadena de conexión;
    - pool_pre_ping + pool_recycle: descarta conexiones cortadas por
      firewalls/balanceadores tras periodos largos sin actividad (un
      endpoint grande puede pasar minutos descargando de la API);
    - keepalives TCP y connect_timeout;
    - statement_timeout opcional (DB_STATEMENT_TIMEOUT_MS).
    """

    url = URL.create(
        "postgresql+psycopg2",
        username=db.user,
        password=db.password,
        host=db.host,
        port=db.port,
        database=db.database,
    )

    connect_args = {
        "connect_timeout": ejecucion.db_connect_timeout,
        "application_name": f"{APPLICATION_NAME}_{rol}"[:63],
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    }

    if ejecucion.db_statement_timeout_ms:
        connect_args["options"] = (
            f"-c statement_timeout={ejecucion.db_statement_timeout_ms}"
        )

    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=ejecucion.db_pool_recycle_segundos,
        connect_args=connect_args,
    )


def con_reintentos_db(func, max_retry, espera_segundos, descripcion):
    """
    Ejecuta `func()` reintentando ante errores de conexión transitorios
    (OperationalError, ej. "server closed the connection unexpectedly").
    `func` debe ser idempotente: normalmente abre su propia transacción
    (engine.begin()), así que si falla no queda nada a medio escribir.
    """

    for intento in range(1, max_retry + 1):
        try:
            return func()

        except OperationalError as exc:
            if intento >= max_retry:
                raise

            log.warning(
                "⏳ DB | ERROR CONEXIÓN | operacion=%s | intento=%s/%s | "
                "espera=%ss | error=%s",
                descripcion,
                intento,
                max_retry,
                espera_segundos,
                str(exc).splitlines()[0],
            )

            time.sleep(espera_segundos)
