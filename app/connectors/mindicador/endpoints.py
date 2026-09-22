from app.core.endpoint import EndpointConfig


# ============================================================================
# MINDICADOR.CL (API libre)
# ============================================================================
#
# Ejemplo de: filtro de fecha en la RUTA ({anio}), partición por año
# (un request por año del rango, en paralelo), descarte local de
# registros fuera de rango y clave primaria compuesta.
# ============================================================================

TIPOS_INDICADOR = {
    "codigo": "TEXT",
    "nombre": "TEXT",
    "unidad_medida": "TEXT",
    "fecha": "DATE",
    "valor": "NUMERIC",
}


def _indicador(codigo, modulo="FINANCIERO"):
    return EndpointConfig(
        nombre=f"MINDICADOR_{codigo.upper()}",
        conector="mindicador",
        endpoint=f"{codigo}/{{anio}}",
        tabla=f"fin_api_mindicador_{codigo}",
        modulo=modulo,
        paginacion="none",

        campo_fecha="fecha",
        tipo_filtro_fecha_api="ruta",
        particion_fecha="anio",
        filtrar_rango_local=True,

        llave_primaria=("codigo", "fecha"),
        tipos_stage=TIPOS_INDICADOR,
    )


ENDPOINTS = [
    _indicador("uf"),
    _indicador("dolar"),
    _indicador("utm", modulo="INACTIVO"),
]
