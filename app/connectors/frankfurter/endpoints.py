from app.core.endpoint import EndpointConfig


# ============================================================================
# FRANKFURTER (API libre)
# ============================================================================
#
# Ejemplo de: rango de fechas en la RUTA ({desde}..{hasta}), partición
# mensual (un request por mes en paralelo), respuesta anidada que el
# cliente transforma en filas, clave primaria compuesta.
# ============================================================================

ENDPOINTS = [

    EndpointConfig(
        nombre="FRANKFURTER_USD",
        conector="frankfurter",
        endpoint="{desde}..{hasta}",
        tabla="fin_api_frankfurter_usd",
        modulo="FINANCIERO",
        paginacion="none",
        parametros={
            "base": "USD",
            "symbols": "CLP,EUR,BRL,PEN,MXN",
        },

        campo_fecha="fecha",
        tipo_filtro_fecha_api="ruta",
        particion_fecha="mes",
        filtrar_rango_local=True,

        llave_primaria=("fecha", "base", "moneda"),
        tipos_stage={
            "fecha": "DATE",
            "base": "TEXT",
            "moneda": "TEXT",
            "valor": "NUMERIC",
        },
    ),

]
