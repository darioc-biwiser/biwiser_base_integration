import logging

import pandas as pd
from sqlalchemy import text

from app.database.stage_loader import (
    chunksize_seguro,
    condicion_rango,
    normalizar_nombre_columna,
    parametros_rango,
    tabla_existe,
)
from app.utils import fechas
from app.utils.progress import ProgressBar


log = logging.getLogger(__name__)


# ============================================================================
# METADATA
# ============================================================================

def obtener_tipos_columnas(conn, schema, tabla):
    """Columnas en orden físico → definición de tipo Postgres."""

    filas = conn.execute(
        text(
            """
            SELECT
                column_name,
                data_type,
                character_maximum_length,
                numeric_precision,
                numeric_scale
            FROM information_schema.columns
            WHERE table_schema = :schema
              AND table_name = :tabla
            ORDER BY ordinal_position
            """
        ),
        {"schema": schema, "tabla": tabla},
    ).fetchall()

    return {
        fila[0]: construir_tipo_postgres(
            data_type=fila[1],
            longitud=fila[2],
            precision=fila[3],
            escala=fila[4],
        )
        for fila in filas
    }


def construir_tipo_postgres(data_type, longitud, precision, escala):
    if data_type == "character varying":
        return f"VARCHAR({longitud})" if longitud else "VARCHAR"

    if data_type == "character":
        return f"CHAR({longitud})" if longitud else "CHAR"

    if data_type == "numeric":
        if precision is not None:
            if escala is not None:
                return f"NUMERIC({precision},{escala})"
            return f"NUMERIC({precision})"

        return "NUMERIC"

    return data_type.upper()


def seleccionar_columnas(endpoint, tipos_stage):
    """Columnas a traspasar: campos_dwh (si se definieron) o todas."""

    if not endpoint.campos_dwh:
        return list(tipos_stage)

    columnas = [
        normalizar_nombre_columna(columna)
        for columna in endpoint.campos_dwh
    ]

    faltantes = [c for c in columnas if c not in tipos_stage]

    if faltantes:
        raise RuntimeError(
            f"Tabla {endpoint.tabla}: faltan columnas en STAGE: "
            f"{', '.join(faltantes)}"
        )

    return columnas


# ============================================================================
# DDL DWH
# ============================================================================

def asegurar_tabla_dwh(conn, schema, tabla, columnas, tipos_stage):
    """
    Crea la tabla DWH con los MISMOS tipos que STAGE (no los que
    inferiría pandas) o agrega las columnas que falten.
    """

    if not tabla_existe(conn, schema, tabla):
        definiciones = ", ".join(
            f'"{columna}" {tipos_stage[columna]}'
            for columna in columnas
        )

        conn.execute(text(f'CREATE TABLE "{schema}"."{tabla}" ({definiciones})'))

        log.info(
            "🆕 DWH TABLA CREADA | %s.%s | columnas=%s",
            schema,
            tabla,
            len(columnas),
        )

        return

    existentes = {
        fila[0]
        for fila in conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = :schema
                  AND table_name = :tabla
                """
            ),
            {"schema": schema, "tabla": tabla},
        ).fetchall()
    }

    for columna in columnas:
        if columna in existentes:
            continue

        conn.execute(
            text(
                f'ALTER TABLE "{schema}"."{tabla}" '
                f'ADD COLUMN "{columna}" {tipos_stage[columna]}'
            )
        )

        log.info(
            "➕ DWH NUEVA COLUMNA | %s.%s | tipo=%s",
            tabla,
            columna,
            tipos_stage[columna],
        )


def otorgar_permisos_dwh(conn, schema, tabla, usuario):
    if not usuario:
        return

    conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{usuario}"'))
    conn.execute(text(f'GRANT SELECT ON TABLE "{schema}"."{tabla}" TO "{usuario}"'))

    log.debug(
        "DWH | GRANT SELECT | %s.%s | usuario=%s",
        schema,
        tabla,
        usuario,
    )


# ============================================================================
# TRASPASO
# ============================================================================

def _resultado(endpoint, estado, filas=0, columnas=0, error=None):
    resultado = {
        "tabla": endpoint.tabla,
        "endpoint": endpoint.nombre,
        "modulo": endpoint.modulo,
        "estado": estado,
        "filas": filas,
        "columnas": columnas,
    }

    if error is not None:
        resultado["error"] = str(error)
        resultado["hora_error"] = fechas.ahora()

    return resultado


def traspasar_endpoint(
    engine_stage,
    engine_dwh,
    config,
    endpoint,
    fecha_inicio,
    fecha_fin,
):
    """
    STAGE → DWH de un endpoint, en UNA transacción DWH:

    1. Crea / ajusta la tabla DWH con los tipos de STAGE.
    2. Con campo_fecha: DELETE del rango en DWH. Sin campo_fecha:
       TRUNCATE (carga completa).
    3. Inserta leyendo STAGE por bloques (DWH_READ_CHUNKSIZE), para no
       cargar tablas grandes completas en memoria.
    4. GRANT SELECT a DWH_GRANT_USER (si está definido).

    Si algo falla, la transacción DWH hace rollback y la tabla queda
    como estaba antes del traspaso.
    """

    stage_schema = config.stage.schema
    dwh_schema = config.dwh.schema
    tabla = endpoint.tabla
    ejecucion = config.ejecucion

    with engine_stage.connect() as stage_conn:
        if not tabla_existe(stage_conn, stage_schema, tabla):
            log.warning(
                "DWH | STAGE NO EXISTE | %s.%s",
                stage_schema,
                tabla,
            )
            return _resultado(endpoint, "STAGE NO EXISTE")

        tipos_stage = obtener_tipos_columnas(stage_conn, stage_schema, tabla)
        columnas = seleccionar_columnas(endpoint, tipos_stage)

        columnas_sql = ", ".join(f'"{columna}"' for columna in columnas)
        consulta = f'SELECT {columnas_sql} FROM "{stage_schema}"."{tabla}"'
        conteo = f'SELECT COUNT(*) FROM "{stage_schema}"."{tabla}"'
        parametros = {}

        if endpoint.campo_fecha:
            condicion = condicion_rango(endpoint.campo_fecha)
            consulta += f" WHERE {condicion}"
            conteo += f" WHERE {condicion}"
            parametros = parametros_rango(fecha_inicio, fecha_fin)

        total_stage = stage_conn.execute(text(conteo), parametros).scalar()

    if not total_stage:
        # Igual que en los proyectos de origen: si STAGE no tiene datos
        # en el rango, NO se borra el DWH (protege ante una API que
        # respondió vacío por un problema puntual).
        log.warning(
            "DWH | SIN DATOS EN RANGO | endpoint=%s | tabla=%s | "
            "desde=%s | hasta=%s",
            endpoint.nombre,
            tabla,
            fecha_inicio,
            fecha_fin,
        )
        return _resultado(endpoint, "SIN DATOS", columnas=len(columnas))

    filas = 0
    chunk_insert = chunksize_seguro(ejecucion.dwh_insert_chunksize, len(columnas))

    with engine_dwh.begin() as dwh_conn:
        asegurar_tabla_dwh(dwh_conn, dwh_schema, tabla, columnas, tipos_stage)

        if endpoint.campo_fecha:
            eliminados = dwh_conn.execute(
                text(
                    f'DELETE FROM "{dwh_schema}"."{tabla}" '
                    f"WHERE {condicion_rango(endpoint.campo_fecha)}"
                ),
                parametros_rango(fecha_inicio, fecha_fin),
            ).rowcount

            log.info(
                "🗑️ DWH DELETE POR FECHA | tabla=%s | campo_fecha=%s | "
                "desde=%s | hasta=%s | eliminados=%s",
                tabla,
                normalizar_nombre_columna(endpoint.campo_fecha),
                fecha_inicio,
                fecha_fin,
                eliminados,
            )

        else:
            dwh_conn.execute(text(f'TRUNCATE TABLE "{dwh_schema}"."{tabla}"'))

            log.info(
                "🗑️ DWH TRUNCATE COMPLETO | tabla=%s | "
                "motivo=campo_fecha no configurado",
                tabla,
            )

        with engine_stage.connect() as stage_conn:
            stage_conn = stage_conn.execution_options(stream_results=True)

            for df in pd.read_sql(
                text(consulta),
                stage_conn,
                params=parametros,
                chunksize=ejecucion.dwh_read_chunksize,
                # NUMERIC llega como Decimal: sin esto pandas lo pasa a
                # float y el DWH recibe 49920.0 (y pierde precisión en
                # montos grandes).
                coerce_float=False,
            ):
                if df.empty:
                    continue

                df.to_sql(
                    tabla,
                    dwh_conn,
                    schema=dwh_schema,
                    if_exists="append",
                    index=False,
                    method="multi",
                    chunksize=chunk_insert,
                )

                filas += len(df)

                log.debug(
                    "DWH | BLOQUE INSERTADO | tabla=%s | filas=%s/%s",
                    tabla,
                    filas,
                    total_stage,
                )

        otorgar_permisos_dwh(dwh_conn, dwh_schema, tabla, config.dwh.grant_user)

    log.info(
        "✅ DWH OK | modulo=%s | endpoint=%s | tabla=%s | filas=%s | "
        "columnas=%s | modo=%s",
        endpoint.modulo,
        endpoint.nombre,
        tabla,
        filas,
        len(columnas),
        "RANGO" if endpoint.campo_fecha else "COMPLETO",
    )

    return _resultado(endpoint, "OK", filas=filas, columnas=len(columnas))


def stage_to_dwh(
    engine_stage,
    engine_dwh,
    config,
    endpoints,
    fecha_inicio,
    fecha_fin,
):
    log.info("=" * 70)
    log.info(
        "🏭 DWH INICIO TRASPASO STAGE → DWH | endpoints=%s | "
        "desde=%s | hasta=%s",
        len(endpoints),
        fecha_inicio,
        fecha_fin,
    )
    log.info("=" * 70)

    resultados = []

    barra = ProgressBar(
        total=len(endpoints),
        prefix="🏭 AVANCE DWH",
        unit="tablas",
        log=log,
        paso_log=1,
        interactivo=False,
    )

    for endpoint in endpoints:
        log.info(
            "🏭 DWH Procesando | endpoint=%s | tabla=%s | modulo=%s",
            endpoint.nombre,
            endpoint.tabla,
            endpoint.modulo,
        )

        try:
            resultados.append(
                traspasar_endpoint(
                    engine_stage=engine_stage,
                    engine_dwh=engine_dwh,
                    config=config,
                    endpoint=endpoint,
                    fecha_inicio=fecha_inicio,
                    fecha_fin=fecha_fin,
                )
            )

        except Exception as exc:
            log.exception(
                "💥 DWH ERROR traspasando | modulo=%s | endpoint=%s | tabla=%s",
                endpoint.modulo,
                endpoint.nombre,
                endpoint.tabla,
            )

            resultados.append(_resultado(endpoint, "ERROR", error=exc))

        barra.update(len(resultados))

    conteo = {}

    for resultado in resultados:
        conteo[resultado["estado"]] = conteo.get(resultado["estado"], 0) + 1

    log.info("=" * 70)
    log.info(
        "🏁 DWH FIN TRASPASO STAGE → DWH | endpoints=%s | OK=%s | "
        "ERROR=%s | SIN_DATOS=%s | STAGE_NO_EXISTE=%s",
        len(endpoints),
        conteo.get("OK", 0),
        conteo.get("ERROR", 0),
        conteo.get("SIN DATOS", 0),
        conteo.get("STAGE NO EXISTE", 0),
    )
    log.info("=" * 70)

    return resultados
