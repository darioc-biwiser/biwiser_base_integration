from app.core.endpoint import EndpointConfig


# ============================================================================
# OPEN-METEO (API libre)
# ============================================================================
#
# Ejemplo de: filtro desde/hasta por query params (start_date/end_date),
# partición mensual en paralelo, respuesta por columnas convertida a
# filas y rate limit propio del conector (MAX_REQUESTS_POR_MINUTO).
# ============================================================================

ENDPOINTS = [

    EndpointConfig(
        nombre="OPEN_METEO_CLIMA_DIARIO",
        conector="open_meteo",
        endpoint="archive",
        tabla="ope_api_open_meteo_clima_diario",
        modulo="OPERACIONAL",
        paginacion="none",
        parametros={
            "daily": (
                "temperature_2m_max,temperature_2m_min,"
                "precipitation_sum,wind_speed_10m_max"
            ),
        },

        campo_fecha="fecha",
        filtro_fecha_api=("start_date", "end_date"),
        tipo_filtro_fecha_api="desde_hasta",
        particion_fecha="mes",

        llave_primaria=("fecha", "latitud", "longitud"),
        tipos_stage={
            "fecha": "DATE",
            "latitud": "NUMERIC",
            "longitud": "NUMERIC",
            "temperature_2m_max": "NUMERIC",
            "temperature_2m_min": "NUMERIC",
            "precipitation_sum": "NUMERIC",
            "wind_speed_10m_max": "NUMERIC",
        },
    ),

]
