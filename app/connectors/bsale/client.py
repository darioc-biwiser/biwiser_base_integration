from app.core.http_client import BaseApiClient


class Cliente(BaseApiClient):
    """
    Bsale API v1 (https://docs.bsale.dev).

    - Autenticación: header `access_token`.
    - Respuesta de colecciones: {"count": N, "limit": .., "offset": ..,
      "items": [...]} → como informa `count`, las páginas se descargan
      en paralelo.
    - Máximo 50 registros por página.
    - Ante 429 la API informa `retry_after` en el body (lo respeta el
      cliente base).
    """

    nombre = "bsale"
    prefijo_env = "BSALE"
    url_default = "https://api.bsale.cl/v1"
    requiere = ("URL", "TOKEN")

    defaults = {
        "PAGE_SIZE": 50,
        "MAX_WORKERS": 5,
        # 401 se reintenta: en producción se observaron rechazos
        # puntuales ("request can not be authenticated") bajo
        # concurrencia, con el mismo token que funcionaba en las
        # requests vecinas.
        "STATUS_REINTENTABLES": "401,429,500,502,503,504",
    }

    param_offset = "offset"
    param_limit = "limit"
    ruta_items_default = "items"
    ruta_total_default = "count"
    page_size_maximo = 50

    def configurar_sesion(self, session):
        session.headers.update({
            "Content-Type": "application/json",
            "access_token": self.api.token,
        })
