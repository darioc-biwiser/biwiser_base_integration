# ============================================================================
# REGISTRO DE CONECTORES
# ============================================================================
#
# Cada conector es un paquete app/connectors/<nombre>/ con:
#
#   client.py     → clase `Cliente` (hereda de core.http_client.BaseApiClient)
#   endpoints.py  → lista `ENDPOINTS` (core.endpoint.EndpointConfig)
#
# Para agregar un conector nuevo: crear el paquete y registrarlo en
# CONECTORES_DISPONIBLES. Un cliente lo habilita con CONECTORES=... en
# su .env.
#
# ============================================================================

import importlib
from functools import lru_cache


CONECTORES_DISPONIBLES = {
    "bsale": "app.connectors.bsale",
    "odoo": "app.connectors.odoo",
    "mindicador": "app.connectors.mindicador",
    "frankfurter": "app.connectors.frankfurter",
    "jsonplaceholder": "app.connectors.jsonplaceholder",
    "open_meteo": "app.connectors.open_meteo",
    "smartsheet": "app.connectors.smartsheet",
}


def _paquete(nombre):
    nombre = nombre.strip().lower()

    if nombre not in CONECTORES_DISPONIBLES:
        raise ValueError(
            f"Conector desconocido: '{nombre}'. Disponibles: "
            f"{', '.join(sorted(CONECTORES_DISPONIBLES))}"
        )

    return CONECTORES_DISPONIBLES[nombre]


@lru_cache(maxsize=None)
def obtener_clase_cliente(nombre):
    modulo = importlib.import_module(f"{_paquete(nombre)}.client")
    return modulo.Cliente


@lru_cache(maxsize=None)
def obtener_endpoints_conector(nombre):
    modulo = importlib.import_module(f"{_paquete(nombre)}.endpoints")
    endpoints = list(modulo.ENDPOINTS)

    for endpoint in endpoints:
        if endpoint.conector != nombre:
            raise ValueError(
                f"Endpoint '{endpoint.nombre}' declarado en el conector "
                f"'{nombre}' tiene conector='{endpoint.conector}'."
            )

    return endpoints


def obtener_endpoints(nombres_conectores):
    """
    Devuelve todos los endpoints de los conectores indicados,
    validando que los nombres sean únicos y que cada endpoint hijo
    apunte a un padre existente y coherente.
    """

    endpoints = []

    for nombre in nombres_conectores:
        endpoints.extend(obtener_endpoints_conector(nombre))

    por_nombre = {}

    for endpoint in endpoints:
        if endpoint.nombre in por_nombre:
            raise ValueError(
                f"Nombre de endpoint duplicado: '{endpoint.nombre}'. "
                "Los nombres deben ser únicos entre todos los conectores."
            )

        por_nombre[endpoint.nombre] = endpoint

    for endpoint in endpoints:
        if not endpoint.padre:
            continue

        padre = por_nombre.get(endpoint.padre)

        if padre is None:
            raise ValueError(
                f"Endpoint '{endpoint.nombre}': el padre "
                f"'{endpoint.padre}' no existe en los conectores "
                "habilitados."
            )

        if padre.conector != endpoint.conector:
            raise ValueError(
                f"Endpoint '{endpoint.nombre}': el padre "
                f"'{endpoint.padre}' pertenece a otro conector."
            )

        if padre.campo_fecha and not endpoint.campo_fecha:
            raise ValueError(
                f"Endpoint '{endpoint.nombre}': su padre filtra por "
                f"fecha ('{padre.campo_fecha}'), así que el hijo debe "
                "heredar ese campo en campos_padre y declararlo como "
                "campo_fecha (si no, no se podría limpiar su rango en "
                "STAGE/DWH)."
            )

    return endpoints
