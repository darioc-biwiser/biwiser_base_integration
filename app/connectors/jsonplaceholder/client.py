from app.core.http_client import BaseApiClient


class Cliente(BaseApiClient):
    """
    JSONPlaceholder — API REST falsa para pruebas (libre, sin token).

    - Paginación offset con `_start` / `_limit`.
    - El total viene en el header `X-Total-Count` → páginas en paralelo.
    - Respuestas: lista JSON directa.
    - Datos fijos (sin fechas): cargas completas (TRUNCATE + carga).
    """

    nombre = "jsonplaceholder"
    prefijo_env = "JSONPLACEHOLDER"
    url_default = "https://jsonplaceholder.typicode.com"

    defaults = {
        "PAGE_SIZE": 20,
        "MAX_WORKERS": 4,
    }

    param_offset = "_start"
    param_limit = "_limit"

    def extraer_total(self, endpoint, respuesta):
        try:
            return int(respuesta.headers.get("x-total-count"))
        except (TypeError, ValueError):
            return None
