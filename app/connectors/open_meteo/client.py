from datetime import timedelta

from app.core.http_client import BaseApiClient, Pagina
from app.utils import fechas


class Cliente(BaseApiClient):
    """
    Open-Meteo Historical Weather API (libre, sin token).

    GET /v1/archive?latitude=..&longitude=..&start_date=..&end_date=..
        &daily=temperature_2m_max,...&timezone=America/Santiago →
        {"latitude": .., "longitude": .., "daily": {"time": [...],
         "temperature_2m_max": [...], ...}}

    `daily` viene por columnas (una lista por variable): se transforma a
    una fila por día. El archivo histórico tiene unos días de retraso:
    el rango se recorta a hoy - OPEN_METEO_RETRASO_DIAS (default 5).
    Coordenadas por cliente: OPEN_METEO_LATITUD / OPEN_METEO_LONGITUD.
    """

    nombre = "open_meteo"
    prefijo_env = "OPEN_METEO"
    url_default = "https://archive-api.open-meteo.com/v1"

    defaults = {
        "MAX_WORKERS": 2,
        "MAX_REQUESTS_POR_MINUTO": 60,
        "TIMEOUT_LECTURA": 60,
    }

    def _fecha_maxima(self):
        retraso = int(self.api.extra("RETRASO_DIAS", 5))
        return fechas.hoy() - timedelta(days=retraso)

    def construir_filtro_fecha(self, endpoint, desde, hasta):
        params = super().construir_filtro_fecha(
            endpoint,
            desde,
            min(hasta, self._fecha_maxima()),
        )

        params["latitude"] = self.api.extra("LATITUD", "-33.4489")
        params["longitude"] = self.api.extra("LONGITUD", "-70.6693")
        params.setdefault("timezone", fechas.zona_horaria().key)

        return params

    def obtener_pagina(self, endpoint, contexto):
        if contexto.desde is not None and contexto.desde > self._fecha_maxima():
            # Tramo completo aún no disponible en el archivo histórico.
            return Pagina(items=[], total=0)

        return super().obtener_pagina(endpoint, contexto)

    def extraer_items(self, endpoint, respuesta):
        data = respuesta.data or {}
        diario = data.get("daily") or {}
        dias = diario.get("time") or []

        filas = []

        for indice, dia in enumerate(dias):
            fila = {
                "fecha": dia,
                "latitud": data.get("latitude"),
                "longitud": data.get("longitude"),
            }

            for variable, valores in diario.items():
                if variable != "time":
                    fila[variable] = valores[indice] if indice < len(valores) else None

            filas.append(fila)

        return filas
