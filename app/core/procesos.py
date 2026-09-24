import logging

from sqlalchemy import text

from app.database.engines import crear_engine
from app.utils import fechas


log = logging.getLogger(__name__)


class RegistroProcesos:
    """
    Registro de ejecuciones en la tabla de control (por defecto
    public.procesos_dev, configurable con PS_PROCESOS_SCHEMA /
    PS_PROCESOS_TABLA). Estructura esperada: sql/procesos_dev.sql.

    Filas que se generan por ejecución:
    - 1 fila del proceso: EN EJECUCION → FINALIZADO CORRECTAMENTE /
      FINALIZADO CON ERRORES / SIN ENDPOINTS / ERROR_CRITICO, con
      fecha_fin y tiempo_ejecucion (minutos).
    - 1 fila "ERROR ENDPOINT" por cada endpoint que falló (API, STAGE o
      DWH) o que terminó con registros sin descargar.

    Con PROCESOS_HABILITADO=0 no se conecta a la base: todo queda solo en
    el log (útil para pruebas locales).
    """

    def __init__(self, config):
        self.config = config
        self.habilitado = config.procesos.habilitado
        self.engine = None
        self.run_id = None
        self.fecha_inicio = None

        if self.habilitado:
            self.engine = crear_engine(
                config.procesos,
                config.ejecucion,
                rol="procesos",
            )

        self.tabla_sql = (
            f'"{config.procesos.schema}"."{config.procesos.tabla}"'
        )

    def iniciar(self, modulo, accion):
        self.fecha_inicio = fechas.ahora()

        if not self.habilitado:
            log.info(
                "🚀 PROCESO INICIO | empresa=%s | modulo=%s | "
                "run_id=- (PROCESOS_HABILITADO=0)",
                self.config.empresa,
                modulo,
            )
            return None

        with self.engine.begin() as conn:
            self.run_id = conn.execute(
                text(
                    f"""
                    INSERT INTO {self.tabla_sql}
                        (proceso, accion, estado, fecha, cliente)
                    VALUES
                        (:proceso, :accion, 'EN EJECUCION', :fecha, :cliente)
                    RETURNING id
                    """
                ),
                {
                    "proceso": self.config.proceso_nombre,
                    "accion": f"{accion} | MODULO={modulo}",
                    "fecha": self.fecha_inicio,
                    "cliente": self.config.empresa.upper(),
                },
            ).scalar()

        log.info(
            "🚀 PROCESO INICIO | empresa=%s | modulo=%s | run_id=%s",
            self.config.empresa,
            modulo,
            self.run_id,
        )

        return self.run_id

    def finalizar(self, estado, errores=0, advertencias=0):
        fecha_fin = fechas.ahora()
        inicio = self.fecha_inicio or fecha_fin

        duracion = round((fecha_fin - inicio).total_seconds() / 60, 2)

        if self.habilitado and self.run_id is not None:
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        f"""
                        UPDATE {self.tabla_sql}
                        SET
                            estado = :estado,
                            fecha_fin = :fecha_fin,
                            tiempo_ejecucion = :duracion,
                            accion = accion ||
                                CASE
                                    WHEN :errores > 0
                                    THEN ' | ERRORES=' || :errores
                                    ELSE ''
                                END ||
                                CASE
                                    WHEN :advertencias > 0
                                    THEN ' | ADVERTENCIAS=' || :advertencias
                                    ELSE ''
                                END
                        WHERE id = :id
                        """
                    ),
                    {
                        "estado": estado,
                        "fecha_fin": fecha_fin,
                        "duracion": duracion,
                        "errores": errores,
                        "advertencias": advertencias,
                        "id": self.run_id,
                    },
                )

        log.info(
            "🏁 PROCESO FIN | run_id=%s | estado=%s | duracion=%s min | "
            "errores=%s | advertencias=%s",
            self.run_id if self.run_id is not None else "-",
            estado,
            duracion,
            errores,
            advertencias,
        )

    def registrar_advertencia_endpoint(self, endpoint, modulo, advertencias):
        """
        Una fila estado='ADVERTENCIA' por endpoint con problemas de datos
        que no impidieron la carga. Nunca lanza excepción.
        """

        hora = fechas.ahora()
        detalle = " || ".join(advertencias)

        accion = (
            f"ADVERTENCIA ENDPOINT | MODULO={modulo} | "
            f"CONECTOR={endpoint.conector} | "
            f"ENDPOINT={endpoint.nombre} | TABLA={endpoint.tabla} | "
            f"HORA={hora} | "
            f"RUN_ID={self.run_id if self.run_id is not None else '-'} | "
            f"CANTIDAD={len(advertencias)} | DETALLE={detalle[:2000]}"
        )

        if not self.habilitado:
            log.debug("PROCESOS | (deshabilitado) %s", accion)
            return

        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {self.tabla_sql}
                            (proceso, accion, estado, fecha, cliente)
                        VALUES
                            (:proceso, :accion, 'ADVERTENCIA', :fecha, :cliente)
                        """
                    ),
                    {
                        "proceso": self.config.proceso_nombre,
                        "accion": accion,
                        "fecha": hora,
                        "cliente": self.config.empresa.upper(),
                    },
                )

        except Exception:
            log.exception(
                "PROCESOS | ERROR REGISTRANDO ADVERTENCIA | endpoint=%s",
                endpoint.nombre,
            )

    def registrar_error_endpoint(self, endpoint, modulo, error, hora_error=None):
        """Nunca lanza excepción: un fallo acá no debe cortar la corrida."""

        hora_error = hora_error or fechas.ahora()

        accion = (
            f"ERROR ENDPOINT | MODULO={modulo} | "
            f"CONECTOR={endpoint.conector} | "
            f"ENDPOINT={endpoint.nombre} | RUTA={endpoint.endpoint} | "
            f"TABLA={endpoint.tabla} | HORA={hora_error} | "
            f"RUN_ID={self.run_id if self.run_id is not None else '-'} | "
            f"ERROR={str(error)[:2000]}"
        )

        if not self.habilitado:
            log.debug("PROCESOS | (deshabilitado) %s", accion)
            return

        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {self.tabla_sql}
                            (proceso, accion, estado, fecha, cliente)
                        VALUES
                            (:proceso, :accion, 'ERROR', :fecha, :cliente)
                        """
                    ),
                    {
                        "proceso": self.config.proceso_nombre,
                        "accion": accion,
                        "fecha": hora_error,
                        "cliente": self.config.empresa.upper(),
                    },
                )

            log.debug(
                "PROCESOS | ERROR ENDPOINT REGISTRADO | endpoint=%s",
                endpoint.nombre,
            )

        except Exception:
            log.exception(
                "PROCESOS | ERROR REGISTRANDO FALLA ENDPOINT | endpoint=%s",
                endpoint.nombre,
            )

    def close(self):
        if self.engine is not None:
            self.engine.dispose()
