import logging
import random
import threading
import time
from dataclasses import dataclass, field

import requests
from requests.adapters import HTTPAdapter

from app.utils import fechas


log = logging.getLogger(__name__)


# ============================================================================
# ESTRUCTURAS
# ============================================================================

@dataclass
class RespuestaApi:
    data: object
    # CaseInsensitiveDict de requests: headers.get("x-total-count")
    # funciona sin importar mayúsculas.
    headers: object
    status: int


@dataclass
class Pagina:
    # Registros de la página (ya extraídos de la respuesta).
    items: list

    # Total de registros informado por la API (None si no lo informa).
    total: int | None = None


@dataclass
class ContextoPeticion:
    """Todo lo que el cliente necesita para pedir UNA página."""

    limite: int
    offset: int = 0
    pagina: int = 1
    desde: object = None
    hasta: object = None
    variables_ruta: dict = field(default_factory=dict)


# ============================================================================
# RATE LIMITER
# ============================================================================

class RateLimiter:
    """
    Limita las peticiones por minuto de un conector, compartido entre
    todos sus workers. Reparte las peticiones de forma uniforme (una
    cada 60/N segundos) en vez de permitir ráfagas, para no gatillar el
    rate limit de la API. 0 = sin límite.
    """

    def __init__(self, max_por_minuto):
        self.intervalo = 60.0 / max_por_minuto if max_por_minuto else 0.0
        self._lock = threading.Lock()
        self._proximo = 0.0

    def esperar(self):
        if not self.intervalo:
            return

        with self._lock:
            ahora = time.monotonic()
            turno = max(ahora, self._proximo)
            self._proximo = turno + self.intervalo

        espera = turno - ahora

        if espera > 0:
            time.sleep(espera)


# ============================================================================
# CLIENTE BASE
# ============================================================================

class BaseApiClient:
    """
    Cliente HTTP genérico. Cada conector hereda y ajusta:

    - atributos de clase (nombre, prefijo_env, url_default, requiere,
      defaults, nombres de parámetros de paginación, rutas de items /
      total, page_size_maximo);
    - `configurar_sesion` (autenticación / headers);
    - si la API no es REST paginada estándar: `obtener_pagina`,
      `extraer_items`, `extraer_total`, `construir_filtro_fecha`;
    - `preparar_registro` para transformar cada registro antes de STAGE.

    Reintentos por request (MAX_RETRY):
    - errores de conexión / timeout → backoff lineal + jitter;
    - 429 → espera Retry-After (body/header) o RETRY_AFTER_DEFAULT + jitter;
    - STATUS_REINTENTABLES → backoff lineal + jitter;
    - cualquier otro status no 2xx → error inmediato (sin reintento).
    """

    nombre = "base"
    prefijo_env = "API"
    url_default = None

    # Atributos de ApiConfig obligatorios (en minúscula: url, token,
    # usuario, password).
    requiere = ("URL",)

    # Defaults propios del conector (misma clave que DEFAULTS_API).
    defaults = {}

    param_offset = "offset"
    param_limit = "limit"
    param_page = "page"
    pagina_inicial = 1

    ruta_items_default = "items"
    ruta_total_default = None

    # Máximo registros por página que acepta la API (None = sin tope).
    page_size_maximo = None

    def __init__(self, api):
        self.api = api
        self.base_url = (api.url or "").rstrip("/")

        self.session = requests.Session()

        # El pool por defecto de requests es de 10 conexiones: con más
        # workers aparecen "Connection pool is full" y se pierden
        # conexiones keep-alive.
        adapter = HTTPAdapter(
            pool_connections=max(api.max_workers, 10),
            pool_maxsize=max(api.max_workers * 2, 10),
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.session.verify = api.verify_ssl
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": f"biwiser_base_integration/{self.nombre}",
        })

        self._limitador = RateLimiter(api.max_requests_por_minuto)

        # Advertencias de datos por endpoint (ver `advertir`). Se escriben
        # desde los workers, por eso el lock.
        self._advertencias = {}
        self._lock_advertencias = threading.Lock()

        self.configurar_sesion(self.session)

    # ------------------------------------------------------------------
    # HOOKS DEL CONECTOR
    # ------------------------------------------------------------------

    def configurar_sesion(self, session):
        """Autenticación y headers propios de la API."""

    def page_size(self, endpoint):
        tamano = endpoint.page_size or self.api.page_size

        if self.page_size_maximo:
            tamano = min(tamano, self.page_size_maximo)

        return max(int(tamano), 1)

    def construir_ruta(self, endpoint, variables_ruta):
        try:
            return endpoint.endpoint.format(**variables_ruta)
        except KeyError as exc:
            raise ValueError(
                f"Endpoint '{endpoint.nombre}': la ruta "
                f"'{endpoint.endpoint}' usa la variable {exc} que no "
                "está disponible."
            ) from None

    def construir_filtro_fecha(self, endpoint, desde, hasta):
        """Parámetros de query para filtrar por fecha el tramo [desde, hasta]."""

        tipo = endpoint.tipo_filtro_fecha_api

        if tipo is None or desde is None:
            return {}

        if tipo == "range_unix":
            return {
                endpoint.filtro_fecha_api: (
                    f"[{fechas.fecha_a_unix_inicio(desde)},"
                    f"{fechas.fecha_a_unix_fin(hasta)}]"
                )
            }

        if tipo == "unix":
            return {endpoint.filtro_fecha_api: fechas.fecha_a_unix_inicio(desde)}

        if tipo == "desde_hasta":
            param_desde, param_hasta = endpoint.filtro_fecha_api
            formato = endpoint.formato_fecha_api

            return {
                param_desde: desde.strftime(formato),
                param_hasta: hasta.strftime(formato),
            }

        if tipo == "ruta":
            return {}

        raise ValueError(
            f"Conector '{self.nombre}': tipo_filtro_fecha_api '{tipo}' no "
            f"soportado para el endpoint '{endpoint.nombre}'."
        )

    def obtener_pagina(self, endpoint, contexto):
        params = dict(endpoint.parametros)
        params.update(
            self.construir_filtro_fecha(endpoint, contexto.desde, contexto.hasta)
        )

        if endpoint.paginacion == "offset":
            params[self.param_offset] = contexto.offset
            params[self.param_limit] = contexto.limite

        elif endpoint.paginacion == "page":
            params[self.param_page] = contexto.pagina
            params[self.param_limit] = contexto.limite

        respuesta = self.request(
            "GET",
            self.construir_ruta(endpoint, contexto.variables_ruta),
            params=params,
        )

        return Pagina(
            items=self.extraer_items(endpoint, respuesta),
            total=self.extraer_total(endpoint, respuesta),
        )

    def extraer_items(self, endpoint, respuesta):
        data = respuesta.data

        if isinstance(data, list):
            return data

        ruta = endpoint.ruta_items or self.ruta_items_default
        items = _valor_por_ruta(data, ruta)

        if items is None:
            raise RuntimeError(
                f"Respuesta inválida de '{self.nombre}' para "
                f"'{endpoint.nombre}': no existe '{ruta}'."
            )

        if isinstance(items, dict):
            return [items]

        if not isinstance(items, list):
            raise RuntimeError(
                f"Respuesta inválida de '{self.nombre}' para "
                f"'{endpoint.nombre}': '{ruta}' no es una lista."
            )

        return items

    def extraer_total(self, endpoint, respuesta):
        ruta = endpoint.ruta_total or self.ruta_total_default

        if not ruta or not isinstance(respuesta.data, dict):
            return None

        return _a_entero(_valor_por_ruta(respuesta.data, ruta))

    # ------------------------------------------------------------------
    # ADVERTENCIAS DE DATOS
    # ------------------------------------------------------------------

    def advertir(self, endpoint, detalle):
        """
        Registra un problema de DATOS que no impide cargar el endpoint
        (ej. un texto en una columna numérica que se cargó como NULL, una
        columna nueva sin tipo declarado).

        Al terminar el endpoint, services.py las junta y deja UNA fila
        ADVERTENCIA en procesos con la cantidad y el detalle; la corrida
        termina FINALIZADO CON ADVERTENCIAS (si no hubo errores).

        Es thread-safe (se llama desde los workers de descarga). Detalles
        repetidos (ej. una página descargada dos veces por reintento) se
        cuentan una vez.
        """

        with self._lock_advertencias:
            self._advertencias.setdefault(endpoint.nombre, {})[str(detalle)] = None

    def tomar_advertencias(self, endpoint):
        """Devuelve y limpia las advertencias acumuladas del endpoint."""

        with self._lock_advertencias:
            return list(self._advertencias.pop(endpoint.nombre, {}))

    def preparar_registro(self, endpoint, registro):
        """
        Transforma un registro de la API antes de normalizarlo e
        insertarlo en STAGE. Por defecto:
        - convierte campos_fecha_unix (conserva <campo>Unix);
        - aplana objetos de un nivel ({"client": {"id": 1}} → client_id).
        """

        registro = dict(registro)

        for campo in endpoint.campos_fecha_unix:
            if campo in registro:
                valor_original = registro[campo]
                registro[f"{campo}Unix"] = valor_original
                registro[campo] = fechas.unix_a_texto(valor_original)

        if endpoint.aplanar_objetos:
            registro = aplanar_un_nivel(registro)

        return registro

    # ------------------------------------------------------------------
    # HTTP CON REINTENTOS
    # ------------------------------------------------------------------

    def url(self, ruta):
        if ruta.startswith(("http://", "https://")):
            return ruta

        return f"{self.base_url}/{ruta.lstrip('/')}"

    def _con_jitter(self, espera_base):
        return espera_base * (1 + random.uniform(0, self.api.retry_jitter))

    def _extraer_retry_after(self, response):
        try:
            cuerpo = response.json()

            if isinstance(cuerpo, dict) and "retry_after" in cuerpo:
                return float(cuerpo["retry_after"]), "body"
        except ValueError:
            pass

        header = response.headers.get("Retry-After")

        if header:
            try:
                return float(header), "header"
            except ValueError:
                pass

        return self.api.retry_after_default_segundos, "default"

    def request(self, metodo, ruta, params=None, json=None):
        url = self.url(ruta)
        max_retry = self.api.max_retry
        timeout = (self.api.timeout_conexion, self.api.timeout_lectura)
        etiqueta = f"{self.nombre.upper()} API"

        log.debug(
            "%s | %s | ruta=%s | params=%s",
            etiqueta,
            metodo,
            ruta,
            params,
        )

        response = None

        for intento in range(1, max_retry + 1):
            agotado = intento >= max_retry
            self._limitador.esperar()

            try:
                inicio = time.perf_counter()
                response = self.session.request(
                    metodo,
                    url,
                    params=params,
                    json=json,
                    timeout=timeout,
                )
                duracion = time.perf_counter() - inicio

            except requests.RequestException as exc:
                espera = self._con_jitter(
                    self.api.retry_backoff_segundos * intento
                )

                log.warning(
                    "⏳ %s | ERROR CONEXIÓN | ruta=%s | intento=%s/%s | "
                    "espera=%.1fs | error=%s",
                    etiqueta,
                    ruta,
                    intento,
                    max_retry,
                    espera,
                    exc,
                )

                if agotado:
                    log.error(
                        "%s | ERROR CONEXIÓN DEFINITIVO | ruta=%s | "
                        "intentos=%s | error=%s",
                        etiqueta,
                        ruta,
                        max_retry,
                        exc,
                    )
                    raise

                time.sleep(espera)
                continue

            log.debug(
                "%s | RESPUESTA | ruta=%s | status=%s | segundos=%.2f",
                etiqueta,
                ruta,
                response.status_code,
                duracion,
            )

            if response.status_code == 429:
                retry_after, fuente = self._extraer_retry_after(response)
                espera = self._con_jitter(retry_after)

                log.warning(
                    "⏳ %s | RATE LIMIT | ruta=%s | intento=%s/%s | "
                    "retry_after=%.1fs (%s) | espera_con_jitter=%.1fs",
                    etiqueta,
                    ruta,
                    intento,
                    max_retry,
                    retry_after,
                    fuente,
                    espera,
                )

                if agotado:
                    raise requests.HTTPError(
                        f"HTTP 429 (rate limit) para {ruta} tras "
                        f"{max_retry} intentos",
                        response=response,
                    )

                time.sleep(espera)
                continue

            if response.status_code in self.api.status_reintentables:
                espera = self._con_jitter(
                    self.api.retry_backoff_segundos * intento
                )

                log.warning(
                    "⏳ %s | ERROR TRANSITORIO | ruta=%s | status=%s | "
                    "intento=%s/%s | espera=%.1fs | respuesta=%s",
                    etiqueta,
                    ruta,
                    response.status_code,
                    intento,
                    max_retry,
                    espera,
                    response.text[:500],
                )

                if not agotado:
                    time.sleep(espera)
                    continue

            break

        if not 200 <= response.status_code < 300:
            log.error(
                "%s | ERROR HTTP | ruta=%s | status=%s | respuesta=%s",
                etiqueta,
                ruta,
                response.status_code,
                response.text[:2000],
            )

            raise requests.HTTPError(
                f"HTTP {response.status_code} para {ruta}",
                response=response,
            )

        if not response.content:
            data = None
        else:
            try:
                data = response.json()
            except ValueError as exc:
                log.error(
                    "%s | ERROR JSON | ruta=%s | respuesta=%s",
                    etiqueta,
                    ruta,
                    response.text[:2000],
                )

                raise RuntimeError(
                    f"Respuesta no JSON de '{self.nombre}' para '{ruta}'."
                ) from exc

        return RespuestaApi(
            data=data,
            headers=response.headers,
            status=response.status_code,
        )

    def close(self):
        self.session.close()


# ============================================================================
# HELPERS
# ============================================================================

def _valor_por_ruta(data, ruta):
    """Navega un dict con una ruta con puntos: 'data.items'."""

    valor = data

    for parte in ruta.split("."):
        if not isinstance(valor, dict) or parte not in valor:
            return None

        valor = valor[parte]

    return valor


def _a_entero(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def aplanar_un_nivel(registro):
    """
    {"id": 1, "client": {"id": 7, "href": "..."}} →
    {"id": 1, "client_id": 7, "client_href": "..."}

    Los valores anidados más profundos (dict/list) se mantienen tal
    cual; stage_loader.normalizar_registro los guarda como JSON.
    """

    resultado = {}

    for campo, valor in registro.items():
        if isinstance(valor, dict) and valor:
            for subcampo, subvalor in valor.items():
                resultado[f"{campo}_{subcampo}"] = subvalor
        else:
            resultado[campo] = valor

    return resultado
