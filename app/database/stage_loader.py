import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import inspect, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.utils import fechas


log = logging.getLogger(__name__)

# Postgres trunca identificadores a 63 bytes. Si no se trunca acá, el
# nombre de la columna en la tabla y en el DataFrame no coincidirían.
MAX_LARGO_IDENTIFICADOR = 63

# Postgres admite como máximo 65.535 parámetros por sentencia; cada fila
# aporta tantos parámetros como columnas.
MAX_PARAMETROS_SENTENCIA = 60000

BIGINT_MIN = -(2 ** 63)
BIGINT_MAX = 2 ** 63 - 1


# ============================================================================
# NORMALIZACIÓN
# ============================================================================

def normalizar_nombre_columna(nombre):
    nombre = str(nombre).strip().lower()
    nombre = re.sub(r"[^a-z0-9_]+", "_", nombre)
    nombre = nombre.strip("_") or "columna"

    if nombre[0].isdigit():
        nombre = f"c_{nombre}"

    return nombre[:MAX_LARGO_IDENTIFICADOR]


def normalizar_registro(registro):
    """
    Nombres de columna en minúscula/snake y valores compatibles con
    Postgres: listas y dicts se guardan como JSON (texto).
    """

    resultado = {}

    for campo, valor in registro.items():
        campo = normalizar_nombre_columna(campo)

        if isinstance(valor, (list, dict)):
            resultado[campo] = json.dumps(valor, ensure_ascii=False, default=str)
        else:
            resultado[campo] = valor

    return resultado


def inferir_tipo_postgres(valores):
    """
    Tipo Postgres a partir de TODOS los valores observados de una
    columna (no solo el primero): mezcla de enteros y decimales →
    NUMERIC; cualquier texto → TEXT; solo booleanos → BOOLEAN.
    """

    tipos = set()

    for valor in valores:
        if valor is None:
            continue

        if isinstance(valor, float) and np.isnan(valor):
            continue

        if isinstance(valor, (bool, np.bool_)):
            tipos.add("bool")

        elif isinstance(valor, (int, np.integer)):
            tipos.add("int" if BIGINT_MIN <= valor <= BIGINT_MAX else "num")

        elif isinstance(valor, (float, Decimal, np.floating)):
            tipos.add("num")

        elif isinstance(valor, datetime):
            tipos.add("timestamp")

        elif isinstance(valor, date):
            tipos.add("date")

        else:
            tipos.add("text")

    if not tipos or "text" in tipos:
        return "TEXT"

    if tipos == {"bool"}:
        return "BOOLEAN"

    if tipos == {"int"}:
        return "BIGINT"

    if tipos <= {"int", "num"}:
        return "NUMERIC"

    if tipos == {"date"}:
        return "DATE"

    if tipos <= {"date", "timestamp"}:
        return "TIMESTAMP"

    return "TEXT"


def _tipos_override_normalizados(tipos_override):
    return {
        normalizar_nombre_columna(campo): tipo
        for campo, tipo in (tipos_override or {}).items()
    }


def _detectar_columnas(registros, excluir, tipos_override):
    valores_por_columna = {}

    for registro in registros:
        for campo, valor in registro.items():
            if campo in excluir:
                continue

            valores_por_columna.setdefault(campo, []).append(valor)

    return {
        campo: tipos_override.get(campo) or inferir_tipo_postgres(valores)
        for campo, valores in valores_por_columna.items()
    }


# ============================================================================
# METADATA
# ============================================================================

def tabla_existe(conn, schema, tabla):
    return inspect(conn).has_table(tabla, schema=schema)


def obtener_columnas(conn, schema, tabla):
    return {
        columna["name"]
        for columna in inspect(conn).get_columns(tabla, schema=schema)
    }


def obtener_llave_primaria(conn, schema, tabla):
    restriccion = inspect(conn).get_pk_constraint(tabla, schema=schema)
    return list(restriccion.get("constrained_columns") or [])


def resolver_llave_primaria(endpoint, registros):
    """
    Columnas de la clave primaria (normalizadas) para crear la tabla:
    - endpoint.llave_primaria explícita → obligatoria en los datos;
    - si no, "id" cuando viene en los registros;
    - si no, tabla sin clave (inserción simple).
    """

    if endpoint.llave_primaria:
        columnas = [
            normalizar_nombre_columna(campo)
            for campo in endpoint.llave_primaria
        ]

        presentes = set().union(*(registro.keys() for registro in registros))
        faltantes = [c for c in columnas if c not in presentes]

        if faltantes:
            raise RuntimeError(
                f"Endpoint '{endpoint.nombre}': la llave_primaria incluye "
                f"columnas que no vienen en los datos: {', '.join(faltantes)}"
            )

        return columnas

    if any("id" in registro for registro in registros):
        return ["id"]

    return []


# ============================================================================
# DDL
# ============================================================================

def asegurar_tabla(conn, schema, tabla, registros, tipos_override, llave_primaria):
    """
    Crea la tabla si no existe y agrega las columnas nuevas que
    aparezcan en `registros` (ya normalizados). Devuelve la clave
    primaria REAL de la tabla (puede diferir si la tabla ya existía).
    """

    tipos_override = _tipos_override_normalizados(tipos_override)

    if not tabla_existe(conn, schema, tabla):
        if not registros:
            raise RuntimeError(
                f"No se puede crear {schema}.{tabla}: no hay registros."
            )

        columnas = _detectar_columnas(registros, set(), tipos_override)

        definiciones = [
            f'"{campo}" {tipo}'
            for campo, tipo in columnas.items()
        ]

        if llave_primaria:
            definiciones.append(
                "PRIMARY KEY ("
                + ", ".join(f'"{c}"' for c in llave_primaria)
                + ")"
            )

        conn.execute(
            text(
                f'CREATE TABLE "{schema}"."{tabla}" '
                f'({", ".join(definiciones)})'
            )
        )

        log.info(
            "🆕 STAGE TABLA CREADA | %s.%s | columnas=%s | pk=%s",
            schema,
            tabla,
            len(columnas),
            ",".join(llave_primaria) or "-",
        )

        return list(llave_primaria)

    existentes = obtener_columnas(conn, schema, tabla)
    nuevas = _detectar_columnas(registros, existentes, tipos_override)

    for campo, tipo in nuevas.items():
        conn.execute(
            text(f'ALTER TABLE "{schema}"."{tabla}" ADD COLUMN "{campo}" {tipo}')
        )

        log.info(
            "➕ STAGE NUEVA COLUMNA | %s.%s | tipo=%s",
            tabla,
            campo,
            tipo,
        )

    return obtener_llave_primaria(conn, schema, tabla)


# ============================================================================
# DML
# ============================================================================

def _metodo_upsert(llave_primaria):
    """
    Método para DataFrame.to_sql: INSERT ... ON CONFLICT (pk) DO UPDATE.

    La paginación offset/limit sobre colecciones que cambian durante la
    descarga puede traer el mismo registro en dos páginas: con upsert
    el duplicado actualiza la fila en vez de reventar el batch.
    """

    def _upsert(pd_table, conn, keys, data_iter):
        registros = [dict(zip(keys, fila)) for fila in data_iter]

        if not registros:
            return

        statement = pg_insert(pd_table.table).values(registros)

        if llave_primaria:
            columnas_actualizar = {
                columna: statement.excluded[columna]
                for columna in keys
                if columna not in llave_primaria
            }

            if columnas_actualizar:
                statement = statement.on_conflict_do_update(
                    index_elements=llave_primaria,
                    set_=columnas_actualizar,
                )
            else:
                statement = statement.on_conflict_do_nothing(
                    index_elements=llave_primaria,
                )

        conn.execute(statement)

    return _upsert


def chunksize_seguro(chunksize, cantidad_columnas):
    maximo = max(MAX_PARAMETROS_SENTENCIA // max(cantidad_columnas, 1), 1)
    return max(min(chunksize, maximo), 1)


def insertar_registros(
    conn,
    schema,
    tabla,
    registros,
    llave_primaria,
    chunksize,
    nombre_endpoint,
):
    """Inserta (upsert) registros ya normalizados. Devuelve filas insertadas."""

    # dtype=object conserva los tipos Python (evita que ids enteros con
    # nulos se conviertan a float y pierdan precisión).
    df = pd.DataFrame(registros, dtype=object)

    if df.empty:
        return 0

    # Textos vacíos → NULL (evita errores al castear fechas/números).
    df = df.replace(r"^\s*$", np.nan, regex=True)

    if llave_primaria:
        nulos = df[llave_primaria].isna().any(axis=1)

        if nulos.any():
            log.warning(
                "⚠️ STAGE | REGISTROS SIN LLAVE PRIMARIA DESCARTADOS | "
                "endpoint=%s | tabla=%s | pk=%s | cantidad=%s",
                nombre_endpoint,
                tabla,
                ",".join(llave_primaria),
                int(nulos.sum()),
            )
            df = df[~nulos]

        # Un mismo INSERT ... ON CONFLICT no puede tocar dos veces la
        # misma fila: se conserva la última versión dentro del batch.
        df = df.drop_duplicates(subset=llave_primaria, keep="last")

    if df.empty:
        return 0

    df.to_sql(
        tabla,
        conn,
        schema=schema,
        if_exists="append",
        index=False,
        method=_metodo_upsert(llave_primaria),
        chunksize=chunksize_seguro(chunksize, len(df.columns)),
    )

    return len(df)


def condicion_rango(campo_fecha):
    """
    Condición SQL (sin WHERE) para el rango [fecha_inicio, fecha_fin].
    El ::text previo permite usarla sobre columnas TEXT, DATE o
    TIMESTAMP por igual.
    """

    campo = normalizar_nombre_columna(campo_fecha)

    return (
        f"NULLIF(\"{campo}\"::text, '')::timestamp >= :fecha_inicio "
        f"AND NULLIF(\"{campo}\"::text, '')::timestamp < :fecha_fin"
    )


def parametros_rango(fecha_inicio, fecha_fin):
    return {
        "fecha_inicio": fechas.inicio_dia(fecha_inicio),
        "fecha_fin": fechas.fin_exclusivo(fecha_fin),
    }


def eliminar_rango(conn, schema, tabla, campo_fecha, fecha_inicio, fecha_fin):
    if not tabla_existe(conn, schema, tabla):
        return 0

    resultado = conn.execute(
        text(
            f'DELETE FROM "{schema}"."{tabla}" '
            f"WHERE {condicion_rango(campo_fecha)}"
        ),
        parametros_rango(fecha_inicio, fecha_fin),
    )

    return resultado.rowcount


def truncar(conn, schema, tabla):
    if not tabla_existe(conn, schema, tabla):
        return False

    conn.execute(text(f'TRUNCATE TABLE "{schema}"."{tabla}"'))
    return True
