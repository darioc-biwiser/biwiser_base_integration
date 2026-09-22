from app.connectors.smartsheet.client import nombre_columna
from app.core.endpoint import EndpointConfig


# ============================================================================
# SMARTSHEET
# ============================================================================
#
# Cada hoja genera DOS endpoints:
#
#   <NOMBRE>            → filas de la hoja      → tabla <tabla>
#   <NOMBRE>_COLUMNAS   → diccionario de columnas (título, descripción,
#                         tipo, fórmula, opciones) → tabla <tabla>_columnas
#                         SOLO en STAGE (cargar_dwh=False)
#
# sheet_id es el id numérico o el token del link de la hoja
# (https://app.smartsheet.com/sheets/<token>).
#
# Las hojas no se filtran por fecha: cada ejecución hace carga completa
# (TRUNCATE + carga en STAGE y DWH). El rango de fechas del run se ignora.
#
# - Clave primaria: row_id (filas) / column_id (diccionario).
# - Nombres de columna = título de la columna en Smartsheet, normalizado
#   (sin tildes, "#" → "num_", minúsculas, snake_case): "#R" → num_r,
#   "Fact." → fact, "Fecha P." → fecha_p. No se renombran: la tabla usa
#   los mismos nombres que se ven en la hoja, y el significado de cada
#   sigla queda en la tabla <tabla>_columnas (column_description).
# - tipos: tipo Postgres por título de columna. Conviene declararlos: la
#   tabla se crea con los tipos de la primera carga, y una columna
#   TEXT_NUMBER que hoy solo tiene números fallaría el día que alguien
#   escriba un texto (ej. "N/A") si se hubiera creado NUMERIC.
# - numericos: títulos de columnas de montos donde un número escrito
#   como texto ("13.124.300") se convierte a número; otro texto queda
#   NULL (con advertencia en el log).
#
# Implementación de referencia (hojas reales de un cliente):
# biwiser_smartsheet_integration.
# ============================================================================

TIPOS_METADATA_FILA = {
    "row_id": "BIGINT",
    "row_number": "INTEGER",
    "row_parent_id": "BIGINT",
    "row_sibling_id": "BIGINT",
    "row_expanded": "BOOLEAN",
    "row_locked": "BOOLEAN",
    "row_created_at": "TIMESTAMP",
    "row_modified_at": "TIMESTAMP",
    "sheet_id": "BIGINT",
    "sheet_name": "TEXT",
    "sheet_permalink": "TEXT",
    "sheet_version": "INTEGER",
    "sheet_total_row_count": "INTEGER",
    "sheet_created_at": "TIMESTAMP",
    "sheet_modified_at": "TIMESTAMP",
    "workspace_id": "BIGINT",
    "workspace_name": "TEXT",
}

TIPOS_DICCIONARIO = {
    "column_id": "BIGINT",
    "sheet_id": "BIGINT",
    "sheet_name": "TEXT",
    "column_index": "INTEGER",
    "column_title": "TEXT",
    "columna_bd": "TEXT",
    "columna_bd_nombre_contacto": "TEXT",
    "column_description": "TEXT",
    "column_type": "TEXT",
    "column_formula": "TEXT",
    "column_options": "TEXT",
    "column_primary": "BOOLEAN",
    "column_validation": "BOOLEAN",
    "column_locked": "BOOLEAN",
    "column_hidden": "BOOLEAN",
    "column_system_type": "TEXT",
    "column_symbol": "TEXT",
    "column_width": "INTEGER",
    "column_version": "INTEGER",
}


def hoja(nombre, sheet_id, tabla, modulo, tipos=None, numericos=None):
    """Devuelve [endpoint de filas, endpoint del diccionario de columnas]."""

    filas = EndpointConfig(
        nombre=nombre,
        conector="smartsheet",
        endpoint=f"sheets/{sheet_id}",
        tabla=tabla,
        modulo=modulo,
        paginacion="page",
        llave_primaria=("row_id",),
        tipos_stage={
            **TIPOS_METADATA_FILA,
            **{nombre_columna(titulo): tipo for titulo, tipo in (tipos or {}).items()},
        },
        opciones={"numericos": list(numericos or [])},
    )

    diccionario = EndpointConfig(
        nombre=f"{nombre}_COLUMNAS",
        conector="smartsheet",
        endpoint=f"sheets/{sheet_id}",
        tabla=f"{tabla}_columnas",
        modulo=modulo,
        paginacion="none",
        llave_primaria=("column_id",),
        tipos_stage=TIPOS_DICCIONARIO,
        opciones={"recurso": "columnas"},
        # Solo STAGE: es documentación de la hoja, el BI no la necesita.
        cargar_dwh=False,
    )

    return [filas, diccionario]


ENDPOINTS = [

    # Ejemplo (INACTIVO: definido pero nunca se ejecuta). Para usarlo,
    # reemplazar sheet_id por el token de una hoja real y cambiar
    # modulo a COMERCIAL / FINANCIERO / OPERACIONAL / OTROS.
    *hoja(
        nombre="SMARTSHEET_EJEMPLO_FACTURACION",
        sheet_id="REEMPLAZAR_TOKEN_DE_LA_HOJA",
        tabla="fin_api_smartsheet_ejemplo_facturacion",
        modulo="INACTIVO",
        numericos=["Neto", "Bruto"],
        tipos={
            "Cliente": "TEXT",
            "#R": "TEXT",           # num_r  | Número de referencia
            "Fecha": "DATE",
            "D": "TEXT",            # d      | Divisa
            "Neto": "NUMERIC",
            "Bruto": "NUMERIC",
            "Pago": "BOOLEAN",
        },
    ),

]
