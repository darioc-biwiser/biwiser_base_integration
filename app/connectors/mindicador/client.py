from datetime import datetime

from app.core.http_client import BaseApiClient
from app.utils import fechas


class Cliente(BaseApiClient):
    """
    mindicador.cl — indicadores económicos de Chile (API libre, sin token).

    GET /api/{indicador}/{anio} →
        {"codigo": "uf", "nombre": "...", "unidad_medida": "Pesos",
         "serie": [{"fecha": "2026-09-22T03:00:00.000Z", "valor": 39000.1}]}

    Devuelve el año completo: el endpoint usa particion_fecha="anio" y
    filtrar_rango_local=True para quedarse solo con el rango pedido.
    """

    nombre = "mindicador"
    prefijo_env = "MINDICADOR"
    url_default = "https://mindicador.cl/api"

    defaults = {
        "MAX_WORKERS": 2,
        "MAX_REQUESTS_POR_MINUTO": 30,
        "TIMEOUT_LECTURA": 60,
    }

    ruta_items_default = "serie"

    def extraer_items(self, endpoint, respuesta):
        data = respuesta.data or {}
        serie = data.get("serie") or []

        return [
            {
                "codigo": data.get("codigo"),
                "nombre": data.get("nombre"),
                "unidad_medida": data.get("unidad_medida"),
                **item,
            }
            for item in serie
        ]

    def preparar_registro(self, endpoint, registro):
        registro = dict(registro)

        # "2026-09-22T03:00:00.000Z" (medianoche Chile en UTC) → fecha
        # calendario en la zona del cliente.
        texto = str(registro.get("fecha") or "")

        try:
            momento = datetime.fromisoformat(texto.replace("Z", "+00:00"))
            registro["fecha"] = momento.astimezone(fechas.zona_horaria()).date()
        except ValueError:
            registro["fecha"] = fechas.valor_a_fecha(texto)

        return registro
