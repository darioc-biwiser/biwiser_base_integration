from app.core.http_client import BaseApiClient


class Cliente(BaseApiClient):
    """
    Frankfurter — tipos de cambio del Banco Central Europeo (API libre).

    GET /v1/{desde}..{hasta}?base=USD&symbols=CLP,EUR →
        {"base": "USD", "start_date": "...", "end_date": "...",
         "rates": {"2026-09-01": {"CLP": 950.1, "EUR": 0.91}, ...}}

    `rates` es un dict fecha → monedas: se transforma a filas
    (fecha, base, moneda, valor). Sin datos en fines de semana/feriados.
    """

    nombre = "frankfurter"
    prefijo_env = "FRANKFURTER"
    url_default = "https://api.frankfurter.dev/v1"

    defaults = {
        "MAX_WORKERS": 3,
        "TIMEOUT_LECTURA": 60,
    }

    def extraer_items(self, endpoint, respuesta):
        data = respuesta.data or {}
        base = data.get("base")
        filas = []

        for fecha, monedas in (data.get("rates") or {}).items():
            for moneda, valor in (monedas or {}).items():
                filas.append({
                    "fecha": fecha,
                    "base": base,
                    "moneda": moneda,
                    "valor": valor,
                })

        return filas
