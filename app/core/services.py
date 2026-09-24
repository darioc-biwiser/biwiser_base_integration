import json
import logging
import math
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from sqlalchemy import text

from app.connectors.registry import obtener_clase_cliente, obtener_endpoints
from app.core.http_client import ContextoPeticion
from app.database.engines import con_reintentos_db, crear_engine
from app.database.stage_loader import (
    asegurar_tabla,
    condicion_rango,
    eliminar_rango,
    insertar_registros,
    normalizar_nombre_columna,
    normalizar_registro,
    parametros_rango,
    resolver_llave_primaria,
    tabla_existe,
    truncar,
)
from app.database.stage_to_dwh import stage_to_dwh
from app.utils import fechas
from app.utils.logger import ERROR_DIR
from app.utils.progress import ProgressBar


log = logging.getLogger(__name__)


# ============================================================================
# CONTEXTO DE EJECUCIÓN
# ============================================================================

@dataclass
class ContextoEjecucion:
    config: object
    modulo: str
    fecha_inicio: object
    fecha_fin: object
    procesos: object
    engine_stage: object
    clientes: dict
    endpoints_por_nombre: dict
    resultados: list = field(default_factory=list)
    errores: int = 0
    advertencias: int = 0
    barra: object = None


# ============================================================================
# SELECCIÓN DE ENDPOINTS
# ============================================================================

def seleccionar_endpoints(config, modulo, endpoint_nombre=None):
    todos = obtener_endpoints(list(config.conectores))

    if endpoint_nombre:
        nombre = endpoint_nombre.strip().upper()
        endpoint = next((e for e in todos if e.nombre == nombre), None)

        if endpoint is None:
            raise ValueError(
                f"El endpoint '{endpoint_nombre}' no existe en los "
                f"conectores habilitados ({', '.join(config.conectores)}). "
                f"Disponibles: {', '.join(e.nombre for e in todos)}"
            )

        if not endpoint.aplica_a_modulo(modulo):
            raise ValueError(
                f"El endpoint '{nombre}' pertenece al módulo "
                f"'{endpoint.modulo}', no a '{modulo}'."
            )

        seleccionados = [endpoint]

    else:
        seleccionados = []

        for endpoint in todos:
            if not endpoint.aplica_a_modulo(modulo):
                continue

            if (
                config.endpoints_habilitados
                and endpoint.nombre not in config.endpoints_habilitados
            ):
                continue

            if endpoint.nombre in config.endpoints_deshabilitados:
                continue

            seleccionados.append(endpoint)

    for endpoint in seleccionados:
        log.info(
            "📋 ENDPOINT SELECCIONADO | %s | conector=%s | endpoint=%s | "
            "tabla=%s | modulo=%s%s",
            endpoint.nombre,
            endpoint.conector,
            endpoint.endpoint,
            endpoint.tabla,
            endpoint.modulo,
            f" | padre={endpoint.padre}" if endpoint.padre else "",
        )

    return seleccionados, {e.nombre: e for e in todos}


# ============================================================================
# CARGA INCREMENTAL A STAGE
# ============================================================================

class CargadorStage:
    """
    Acumula registros descargados y los inserta en STAGE por lotes de
    STAGE_BATCH_SIZE, apenas se completan (no espera a terminar la
    descarga): si más adelante falla una página, lo ya descargado no
    se pierde. Se usa SOLO desde el hilo principal.
    """

    def __init__(self, ctx, endpoint, client):
        self.ctx = ctx
        self.endpoint = endpoint
        self.client = client
        self.ejecucion = ctx.config.ejecucion
        self.schema = ctx.config.stage.schema

        self.buffer = []
        self.recibidos = 0
        self.insertados = 0
        self.descartados = 0
        self.batches = 0

        self._campo_fecha = (
            normalizar_nombre_columna(endpoint.campo_fecha)
            if endpoint.campo_fecha
            else None
        )

    def agregar(self, registros, extra=None):
        for registro in registros:
            self.recibidos += 1

            registro = self.client.preparar_registro(self.endpoint, registro)

            if extra:
                registro.update(extra)

            registro = normalizar_registro(registro)

            if self.endpoint.filtrar_rango_local and self._fuera_de_rango(
                registro
            ):
                self.descartados += 1
                continue

            self.buffer.append(registro)

        tamano = self.ejecucion.stage_batch_size

        while len(self.buffer) >= tamano:
            lote = self.buffer[:tamano]
            self.buffer = self.buffer[tamano:]
            self._insertar(lote)

    def _fuera_de_rango(self, registro):
        fecha = fechas.valor_a_fecha(registro.get(self._campo_fecha))

        if fecha is None:
            return False

        return not (self.ctx.fecha_inicio <= fecha <= self.ctx.fecha_fin)

    def _insertar(self, lote):
        endpoint = self.endpoint

        def _operacion():
            with self.ctx.engine_stage.begin() as conn:
                llave = asegurar_tabla(
                    conn=conn,
                    schema=self.schema,
                    tabla=endpoint.tabla,
                    registros=lote,
                    tipos_override=endpoint.tipos_stage,
                    llave_primaria=resolver_llave_primaria(endpoint, lote),
                )

                return insertar_registros(
                    conn=conn,
                    schema=self.schema,
                    tabla=endpoint.tabla,
                    registros=lote,
                    llave_primaria=llave,
                    chunksize=self.ejecucion.stage_insert_chunksize,
                    nombre_endpoint=endpoint.nombre,
                )

        insertados = con_reintentos_db(
            _operacion,
            max_retry=self.ejecucion.stage_insert_max_retry,
            espera_segundos=self.ejecucion.stage_insert_retry_espera_segundos,
            descripcion=f"INSERT STAGE {endpoint.tabla}",
        )

        self.batches += 1
        self.insertados += insertados

        log.debug(
            "STAGE | BATCH INSERTADO | endpoint=%s | batch=%s | filas=%s",
            endpoint.nombre,
            self.batches,
            insertados,
        )

    def finalizar(self):
        if self.buffer:
            lote, self.buffer = self.buffer, []
            self._insertar(lote)


# ============================================================================
# LIMPIEZA DE STAGE ANTES DE CARGAR
# ============================================================================

def limpiar_stage(ctx, endpoint):
    config = ctx.config
    schema = config.stage.schema

    def _operacion():
        with ctx.engine_stage.begin() as conn:
            if endpoint.campo_fecha:
                eliminados = eliminar_rango(
                    conn,
                    schema,
                    endpoint.tabla,
                    endpoint.campo_fecha,
                    ctx.fecha_inicio,
                    ctx.fecha_fin,
                )

                log.info(
                    "🗑️ STAGE DELETE POR FECHA | endpoint=%s | tabla=%s | "
                    "campo_fecha=%s | desde=%s | hasta=%s | eliminados=%s",
                    endpoint.nombre,
                    endpoint.tabla,
                    normalizar_nombre_columna(endpoint.campo_fecha),
                    ctx.fecha_inicio,
                    ctx.fecha_fin,
                    eliminados,
                )

            elif truncar(conn, schema, endpoint.tabla):
                log.info(
                    "🗑️ STAGE TRUNCATE | endpoint=%s | tabla=%s | "
                    "motivo=campo_fecha no configurado",
                    endpoint.nombre,
                    endpoint.tabla,
                )

    con_reintentos_db(
        _operacion,
        max_retry=config.ejecucion.stage_insert_max_retry,
        espera_segundos=config.ejecucion.stage_insert_retry_espera_segundos,
        descripcion=f"LIMPIEZA STAGE {endpoint.tabla}",
    )


# ============================================================================
# DESCARGA CONCURRENTE
# ============================================================================

def _descargar_lote_paralelo(items, obtener_uno, on_exito, on_progreso, max_workers):
    """
    Descarga en paralelo `items` (páginas, tramos, ids de padres...)
    con `obtener_uno(item)`. Un item que falla (tras agotar los
    reintentos HTTP del cliente) NO aborta el lote: se aparta y se
    devuelve como pendiente.

    `on_exito` / `on_progreso` se ejecutan en el hilo principal (acá
    se inserta en STAGE y se actualiza la barra).
    """

    pendientes = []

    if not items:
        return pendientes

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futuros = {executor.submit(obtener_uno, item): item for item in items}

        try:
            for futuro in as_completed(futuros):
                item = futuros[futuro]

                try:
                    resultado = futuro.result()
                except Exception as exc:
                    pendientes.append((item, exc))

                    log.debug(
                        "DESCARGA | ITEM FALLIDO | item=%s | error=%s",
                        item,
                        exc,
                    )

                    if on_progreso:
                        on_progreso(item, None)

                    continue

                on_exito(item, resultado)

                if on_progreso:
                    on_progreso(item, resultado)

        except BaseException:
            # Falla al insertar (on_exito) o Ctrl+C: no seguir
            # descargando lo que queda en cola.
            for futuro in futuros:
                futuro.cancel()

            raise

    return pendientes


def _descargar_con_reintento(
    ctx,
    endpoint,
    items,
    obtener_uno,
    on_exito,
    on_progreso,
    descripcion,
    max_workers,
):
    """
    Corre `_descargar_lote_paralelo` y, si quedan pendientes, hace UNA
    segunda pasada solo sobre esos (pensado para rate limits o cortes
    transitorios: para cuando termina el lote, la API suele haberse
    recuperado).
    """

    fallidos = _descargar_lote_paralelo(
        items,
        obtener_uno,
        on_exito,
        on_progreso,
        max_workers,
    )

    if fallidos:
        log.warning(
            "🔁 %s REINTENTO DE FALLIDOS | empresa=%s | endpoint=%s | "
            "cantidad=%s",
            descripcion,
            ctx.config.empresa,
            endpoint.nombre,
            len(fallidos),
        )

        fallidos = _descargar_lote_paralelo(
            [item for item, _error in fallidos],
            obtener_uno,
            on_exito,
            None,
            max_workers,
        )

    return fallidos


def _huella_pagina(items):
    if not items:
        return None

    try:
        return (
            len(items),
            json.dumps(items[0], sort_keys=True, default=str),
            json.dumps(items[-1], sort_keys=True, default=str),
        )
    except (TypeError, ValueError):
        return None


def _paginar_secuencial(client, endpoint, base, limite, on_pagina, inicio=None):
    """
    Pagina un tramo de forma secuencial hasta que la API devuelva una
    página incompleta/vacía o se alcance el total informado.

    `inicio` = (offset, pagina, acumulado, huella) para continuar después
    de una primera página ya descargada.

    Protecciones: tope MAX_PAGINAS y detección de página repetida (APIs
    que ignoran offset/page devolverían lo mismo para siempre).
    """

    offset, pagina, acumulado, huella_anterior = inicio or (
        0,
        client.pagina_inicial,
        0,
        None,
    )

    paginas = 0
    max_paginas = client.api.max_paginas

    while True:
        paginas += 1

        if paginas > max_paginas:
            raise RuntimeError(
                f"Endpoint '{endpoint.nombre}': se superó MAX_PAGINAS "
                f"({max_paginas}). Revisa la paginación de la API."
            )

        contexto = ContextoPeticion(
            limite=limite,
            offset=offset,
            pagina=pagina,
            desde=base.desde,
            hasta=base.hasta,
            variables_ruta=base.variables_ruta,
        )

        resultado = client.obtener_pagina(endpoint, contexto)
        items = resultado.items

        huella = _huella_pagina(items)

        if huella is not None and huella == huella_anterior:
            log.warning(
                "⚠️ %s | PÁGINA REPETIDA | la API parece ignorar la "
                "paginación | offset=%s | pagina=%s",
                endpoint.nombre,
                offset,
                pagina,
            )
            break

        huella_anterior = huella

        if items:
            on_pagina(items)

        acumulado += len(items)

        if endpoint.paginacion == "none" or not items:
            break

        if resultado.total is not None and acumulado >= resultado.total:
            break

        if len(items) < limite:
            break

        offset += len(items)
        pagina += 1

    return acumulado


def _variables_ruta(endpoint, desde, hasta):
    if desde is None:
        return {}

    formato = endpoint.formato_fecha_api

    return {
        "desde": desde.strftime(formato),
        "hasta": hasta.strftime(formato),
        "anio": desde.year,
        "mes": f"{desde.month:02d}",
    }


def _tramos(ctx, endpoint):
    if not endpoint.campo_fecha:
        return [(None, None)]

    if endpoint.particion_fecha:
        return fechas.dividir_rango(
            ctx.fecha_inicio,
            ctx.fecha_fin,
            endpoint.particion_fecha,
        )

    return [(ctx.fecha_inicio, ctx.fecha_fin)]


def _descargar_tramo_unico(ctx, endpoint, client, cargador, desde, hasta):
    """
    Un solo tramo: se pide la primera página; si la API informa el
    total, el resto de páginas se descarga EN PARALELO (con barra de
    avance por registros). Si no, se sigue secuencialmente.
    """

    limite = client.page_size(endpoint)
    ruta = endpoint.endpoint
    base = ContextoPeticion(
        limite=limite,
        desde=desde,
        hasta=hasta,
        variables_ruta=_variables_ruta(endpoint, desde, hasta),
    )

    primera = client.obtener_pagina(
        endpoint,
        ContextoPeticion(
            limite=limite,
            offset=0,
            pagina=client.pagina_inicial,
            desde=desde,
            hasta=hasta,
            variables_ruta=base.variables_ruta,
        ),
    )

    cargador.agregar(primera.items)
    recibidos = len(primera.items)

    log.debug(
        "%s | PRIMERA PÁGINA | registros=%s | total_api=%s | limit=%s",
        endpoint.nombre,
        recibidos,
        primera.total,
        limite,
    )

    total = primera.total

    if (
        endpoint.paginacion == "none"
        or not primera.items
        or (total is not None and recibidos >= total)
        or (total is None and recibidos < limite)
    ):
        # Todo llegó en la primera página: igual se muestra la barra
        # (100%) para que se vea el avance endpoint por endpoint.
        _barra_completa(endpoint, recibidos)
        return []

    if total is None:

        log.debug(
            "%s | PAGINACIÓN SECUENCIAL | motivo=la API no informa total",
            endpoint.nombre,
        )

        _paginar_secuencial(
            client,
            endpoint,
            base,
            limite,
            on_pagina=cargador.agregar,
            inicio=(
                recibidos,
                client.pagina_inicial + 1,
                recibidos,
                _huella_pagina(primera.items),
            ),
        )

        return []

    # Tamaño real de página (la API puede devolver menos que lo pedido).
    paso = recibidos

    if endpoint.paginacion == "offset":
        items = list(range(paso, total, paso))
    else:
        cantidad_paginas = math.ceil(total / paso)
        items = list(
            range(
                client.pagina_inicial + 1,
                client.pagina_inicial + cantidad_paginas,
            )
        )

    log.debug(
        "%s | PAGINACIÓN CONCURRENTE | total=%s | paso=%s | paginas=%s | "
        "workers=%s",
        endpoint.nombre,
        total,
        paso,
        len(items),
        client.api.max_workers,
    )

    def _obtener(item):
        contexto = ContextoPeticion(
            limite=paso,
            offset=item if endpoint.paginacion == "offset" else 0,
            pagina=item if endpoint.paginacion == "page" else 1,
            desde=desde,
            hasta=hasta,
            variables_ruta=base.variables_ruta,
        )
        return client.obtener_pagina(endpoint, contexto).items

    barra = ProgressBar(
        total=total,
        prefix=f"📥 {endpoint.nombre}",
        unit="registros",
        log=log,
    )

    procesados = recibidos
    barra.update(procesados)

    def _avanzar(_item, registros):
        nonlocal procesados

        if registros is None:
            return

        procesados += len(registros)
        barra.update(procesados)

    try:
        fallidos = _descargar_con_reintento(
            ctx,
            endpoint,
            items,
            _obtener,
            on_exito=lambda _item, registros: cargador.agregar(registros),
            on_progreso=_avanzar,
            descripcion="PÁGINAS",
            max_workers=client.api.max_workers,
        )
    finally:
        barra.close()

    parametro = (
        client.param_offset if endpoint.paginacion == "offset" else client.param_page
    )

    return [
        (f"{ruta}?{parametro}={item}&{client.param_limit}={paso}", error)
        for item, error in fallidos
    ]


def _barra_completa(endpoint, registros):
    if not registros:
        return

    barra = ProgressBar(
        total=registros,
        prefix=f"📥 {endpoint.nombre}",
        unit="registros",
        log=log,
    )
    barra.update(registros)
    barra.close()


def _descargar_por_tramos(ctx, endpoint, client, cargador, tramos):
    """Varios tramos de fecha: cada worker pagina un tramo completo."""

    limite = client.page_size(endpoint)

    def _obtener(tramo):
        desde, hasta = tramo
        registros = []

        _paginar_secuencial(
            client,
            endpoint,
            ContextoPeticion(
                limite=limite,
                desde=desde,
                hasta=hasta,
                variables_ruta=_variables_ruta(endpoint, desde, hasta),
            ),
            limite,
            on_pagina=registros.extend,
        )

        return registros

    barra = ProgressBar(
        total=len(tramos),
        prefix=f"📥 {endpoint.nombre} (por {endpoint.particion_fecha})",
        unit="tramos",
        log=log,
    )

    procesados = 0

    def _avanzar(_tramo, _registros):
        nonlocal procesados
        procesados += 1
        barra.update(procesados)

    try:
        fallidos = _descargar_con_reintento(
            ctx,
            endpoint,
            tramos,
            _obtener,
            on_exito=lambda _tramo, registros: cargador.agregar(registros),
            on_progreso=_avanzar,
            descripcion="TRAMOS",
            max_workers=client.api.max_workers,
        )
    finally:
        barra.close()

    return [
        (f"{endpoint.endpoint} [{desde} → {hasta}]", error)
        for (desde, hasta), error in fallidos
    ]


# ============================================================================
# ENDPOINTS HIJO
# ============================================================================

def leer_padres(ctx, padre, hijo):
    """
    Lee desde STAGE los registros del padre (dentro del rango si el
    padre filtra por fecha) con las columnas que el hijo necesita.
    Devuelve [(id_padre, {columna_fk: id, campo_padre: valor, ...})].
    """

    schema = ctx.config.stage.schema
    columna_id = normalizar_nombre_columna(hijo.columna_id_padre)
    campos = [(c, normalizar_nombre_columna(c)) for c in hijo.campos_padre]

    columnas_sql = ", ".join(
        [f'"{columna_id}"'] + [f'"{normalizado}"' for _, normalizado in campos]
    )

    consulta = (
        f'SELECT DISTINCT {columnas_sql} FROM "{schema}"."{padre.tabla}" '
        f'WHERE "{columna_id}" IS NOT NULL'
    )
    parametros = {}

    if padre.campo_fecha:
        consulta += f" AND {condicion_rango(padre.campo_fecha)}"
        parametros = parametros_rango(ctx.fecha_inicio, ctx.fecha_fin)

    with ctx.engine_stage.connect() as conn:
        if not tabla_existe(conn, schema, padre.tabla):
            log.warning(
                "⚠️ %s | TABLA PADRE NO EXISTE EN STAGE | padre=%s | tabla=%s",
                hijo.nombre,
                padre.nombre,
                padre.tabla,
            )
            return []

        filas = conn.execute(text(consulta), parametros).fetchall()

    resultado = []

    for fila in filas:
        id_padre = fila[0]
        extra = {hijo.columna_fk: id_padre}

        for indice, (original, _normalizado) in enumerate(campos, start=1):
            extra[original] = fila[indice]

        resultado.append((id_padre, extra))

    return resultado


def _descargar_hijos(ctx, endpoint, client, cargador):
    padre = ctx.endpoints_por_nombre[endpoint.padre]
    padres = leer_padres(ctx, padre, endpoint)
    limite = client.page_size(endpoint)

    log.info(
        "🔗 %s | REGISTROS PADRE A RECORRER | padre=%s | cantidad=%s",
        endpoint.nombre,
        padre.nombre,
        len(padres),
    )

    def _obtener(item):
        id_padre, _extra = item
        registros = []

        _paginar_secuencial(
            client,
            endpoint,
            ContextoPeticion(limite=limite, variables_ruta={"id": id_padre}),
            limite,
            on_pagina=registros.extend,
        )

        return registros

    barra = ProgressBar(
        total=len(padres),
        prefix=f"📥 {endpoint.nombre}",
        unit="registros padre",
        log=log,
    )

    procesados = 0

    def _avanzar(_item, _registros):
        nonlocal procesados
        procesados += 1
        barra.update(procesados)

    try:
        fallidos = _descargar_con_reintento(
            ctx,
            endpoint,
            padres,
            _obtener,
            on_exito=lambda item, registros: cargador.agregar(
                registros,
                extra=item[1],
            ),
            on_progreso=_avanzar,
            descripcion=endpoint.nombre,
            max_workers=client.api.max_workers,
        )
    finally:
        barra.close()

    return [
        (endpoint.endpoint.format(id=id_padre), error)
        for (id_padre, _extra), error in fallidos
    ]


# ============================================================================
# REGISTRO DE FALLIDOS
# ============================================================================

def registrar_urls_fallidas(ctx, endpoint, fallidos):
    """
    Deja en error/api_fallida_<empresa>_<modulo>_<endpoint>_<ts>.log las
    URLs que NO se pudieron descargar tras agotar todos los reintentos,
    con el motivo de cada una. Se genera aunque el resto del endpoint
    haya terminado OK.
    """

    if not fallidos:
        return None

    ERROR_DIR.mkdir(parents=True, exist_ok=True)

    archivo = ERROR_DIR / (
        f"api_fallida_{ctx.config.empresa}_{ctx.modulo}_"
        f"{endpoint.nombre}_{fechas.timestamp_archivo()}.log"
    )

    with open(archivo, "w", encoding="utf-8") as salida:
        for descripcion, error in fallidos:
            salida.write(
                f"{fechas.ahora():%Y-%m-%d %H:%M:%S} | "
                f"endpoint={descripcion} | error={error}\n"
            )

    log.error(
        "🧾 API FALLIDA | empresa=%s | endpoint=%s | pendientes=%s | "
        "archivo=%s",
        ctx.config.empresa,
        endpoint.nombre,
        len(fallidos),
        archivo,
    )

    return archivo


# ============================================================================
# EJECUCIÓN DE UN ENDPOINT
# ============================================================================

def _procesar_endpoint(ctx, endpoint, intento):
    inicio = time.perf_counter()
    client = ctx.clientes[endpoint.conector]

    log.debug(
        "ENDPOINT | INTENTO | endpoint=%s | intento=%s/%s",
        endpoint.nombre,
        intento,
        client.api.endpoint_max_retry,
    )

    if endpoint.campo_fecha and not endpoint.es_hijo:
        log.info(
            "📅 %s FILTRO FECHA | tipo=%s | particion=%s | desde=%s | "
            "hasta=%s",
            endpoint.nombre,
            endpoint.tipo_filtro_fecha_api or "local",
            endpoint.particion_fecha or "-",
            ctx.fecha_inicio,
            ctx.fecha_fin,
        )

    limpiar_stage(ctx, endpoint)

    # Un intento anterior fallido pudo dejar advertencias: se descartan
    # para contar solo las de la carga que queda.
    client.tomar_advertencias(endpoint)

    cargador = CargadorStage(ctx, endpoint, client)

    if endpoint.es_hijo:
        fallidos = _descargar_hijos(ctx, endpoint, client, cargador)

    else:
        tramos = _tramos(ctx, endpoint)

        if len(tramos) == 1:
            desde, hasta = tramos[0]
            fallidos = _descargar_tramo_unico(
                ctx,
                endpoint,
                client,
                cargador,
                desde,
                hasta,
            )
        else:
            fallidos = _descargar_por_tramos(
                ctx,
                endpoint,
                client,
                cargador,
                tramos,
            )

    cargador.finalizar()

    advertencias = client.tomar_advertencias(endpoint)

    if advertencias:
        log.warning(
            "⚠️ %s | ADVERTENCIAS DE DATOS | cantidad=%s | %s%s",
            endpoint.nombre,
            len(advertencias),
            " || ".join(advertencias[:10]),
            " || ..." if len(advertencias) > 10 else "",
        )

    archivo_fallidos = registrar_urls_fallidas(ctx, endpoint, fallidos)
    segundos = time.perf_counter() - inicio

    if cargador.descartados:
        log.info(
            "✂️ %s | DESCARTADOS FUERA DE RANGO | cantidad=%s",
            endpoint.nombre,
            cargador.descartados,
        )

    log.info(
        "%s ENDPOINT FIN | nombre=%s | recibidos=%s | stage=%s | "
        "batches=%s | fallidos=%s | segundos=%.2f",
        "⚠️" if fallidos else "✅",
        endpoint.nombre,
        cargador.recibidos,
        cargador.insertados,
        cargador.batches,
        len(fallidos),
        segundos,
    )

    return _resultado_endpoint(
        endpoint,
        estado="OK",
        registros=cargador.insertados,
        recibidos=cargador.recibidos,
        fallidos=len(fallidos),
        archivo_fallidos=str(archivo_fallidos) if archivo_fallidos else None,
        advertencias=advertencias,
        segundos=segundos,
    )


def _resultado_endpoint(endpoint, estado, **datos):
    resultado = {
        "nombre": endpoint.nombre,
        "conector": endpoint.conector,
        "endpoint": endpoint.endpoint,
        "tabla": endpoint.tabla,
        "modulo": endpoint.modulo,
        "estado": estado,
        "registros": 0,
        "recibidos": 0,
        "fallidos": 0,
        "archivo_fallidos": None,
        "advertencias": [],
        "segundos": 0.0,
        "dwh": "-",
    }
    resultado.update(datos)
    return resultado


def ejecutar_endpoint(ctx, endpoint):
    """
    Reintenta el endpoint completo (limpiar + descargar + cargar) hasta
    ENDPOINT_MAX_RETRY veces. Si se agotan, registra el error en la
    tabla de procesos y devuelve estado ERROR (no propaga): una falla
    en un endpoint no interrumpe a los demás.
    """

    max_retry = ctx.clientes[endpoint.conector].api.endpoint_max_retry

    log.info("=" * 70)
    log.info(
        "🚀 %s INICIO | empresa=%s | conector=%s | endpoint=%s | tabla=%s",
        endpoint.nombre,
        ctx.config.empresa,
        endpoint.conector,
        endpoint.endpoint,
        endpoint.tabla,
    )

    ultimo_error = None

    for intento in range(1, max_retry + 1):
        try:
            return _procesar_endpoint(ctx, endpoint, intento)

        except Exception as exc:
            ultimo_error = exc

            log.exception(
                "💥 %s ERROR | intento=%s/%s",
                endpoint.nombre,
                intento,
                max_retry,
            )

            if intento < max_retry:
                log.warning(
                    "🔁 %s REINTENTANDO | intento_siguiente=%s/%s",
                    endpoint.nombre,
                    intento + 1,
                    max_retry,
                )

    hora_error = fechas.ahora()

    log.error(
        "💥 %s FALLA DEFINITIVA | intentos=%s | hora=%s | error=%s",
        endpoint.nombre,
        max_retry,
        hora_error,
        ultimo_error,
    )

    ctx.procesos.registrar_error_endpoint(
        endpoint=endpoint,
        modulo=ctx.modulo,
        error=ultimo_error or "Error desconocido",
        hora_error=hora_error,
    )

    return _resultado_endpoint(
        endpoint,
        estado="ERROR",
        error=str(ultimo_error or "Error desconocido"),
    )


def _evaluar_resultado(ctx, endpoint, resultado):
    """
    True si el resultado suma a los errores del proceso: el endpoint
    falló (ya registrado en procesos) o terminó OK pero con ítems que
    no se pudieron descargar (se registra acá, para que la corrida no
    quede como FINALIZADO CORRECTAMENTE con datos faltantes).
    """

    if resultado["estado"] != "OK":
        return True

    if not resultado["fallidos"]:
        return False

    ctx.procesos.registrar_error_endpoint(
        endpoint=endpoint,
        modulo=ctx.modulo,
        error=(
            f"{resultado['fallidos']} request(s) no se pudieron descargar "
            f"tras agotar los reintentos. Detalle: "
            f"{resultado['archivo_fallidos'] or 'sin archivo'}"
        ),
    )

    return True


def _avanzar_modulo(ctx):
    if ctx.barra is not None:
        ctx.barra.update(len(ctx.resultados))


def _registrar_advertencias(ctx, endpoint, resultado):
    """
    Endpoint OK con problemas de datos (ej. textos cargados como NULL):
    una fila ADVERTENCIA en procesos. No cuenta como error.
    """

    if resultado["estado"] != "OK" or not resultado["advertencias"]:
        return

    ctx.procesos.registrar_advertencia_endpoint(
        endpoint=endpoint,
        modulo=ctx.modulo,
        advertencias=resultado["advertencias"],
    )
    ctx.advertencias += 1


def _ejecutar_arbol(ctx, endpoint, hijos_de):
    resultado = ejecutar_endpoint(ctx, endpoint)
    ctx.resultados.append((endpoint, resultado))
    _avanzar_modulo(ctx)

    if _evaluar_resultado(ctx, endpoint, resultado):
        ctx.errores += 1

    _registrar_advertencias(ctx, endpoint, resultado)

    for hijo in hijos_de.get(endpoint.nombre, []):
        if resultado["estado"] == "OK":
            _ejecutar_arbol(ctx, hijo, hijos_de)
        else:
            _omitir_arbol(ctx, hijo, hijos_de, endpoint)


def _omitir_arbol(ctx, endpoint, hijos_de, padre):
    motivo = f"Omitido: el endpoint padre '{padre.nombre}' terminó con error."

    log.warning("⏭️ %s OMITIDO | %s", endpoint.nombre, motivo)

    ctx.procesos.registrar_error_endpoint(
        endpoint=endpoint,
        modulo=ctx.modulo,
        error=motivo,
    )

    ctx.resultados.append(
        (endpoint, _resultado_endpoint(endpoint, "OMITIDO", error=motivo))
    )
    _avanzar_modulo(ctx)
    ctx.errores += 1

    for nieto in hijos_de.get(endpoint.nombre, []):
        _omitir_arbol(ctx, nieto, hijos_de, endpoint)


def _organizar_arbol(endpoints):
    """
    Raíces (sin padre, o cuyo padre no está en esta selección: se
    ejecutan leyendo el padre ya existente en STAGE) y mapa padre → hijos
    en el orden de declaración.
    """

    nombres = {endpoint.nombre for endpoint in endpoints}
    hijos_de = defaultdict(list)
    raices = []

    for endpoint in endpoints:
        if endpoint.padre and endpoint.padre in nombres:
            hijos_de[endpoint.padre].append(endpoint)
        else:
            if endpoint.padre:
                log.info(
                    "🔗 %s | su padre %s no está en esta ejecución: se "
                    "usarán los registros del padre ya cargados en STAGE",
                    endpoint.nombre,
                    endpoint.padre,
                )

            raices.append(endpoint)

    return raices, hijos_de


# ============================================================================
# RESUMEN
# ============================================================================

ICONOS_ESTADO = {
    "OK": "✅",
    "ERROR": "❌",
    "OMITIDO": "⏭️",
}


def log_resumen(ctx):
    log.info("=" * 70)
    log.info("📊 RESUMEN | empresa=%s | modulo=%s", ctx.config.empresa, ctx.modulo)

    for endpoint, resultado in ctx.resultados:
        icono = ICONOS_ESTADO.get(resultado["estado"], "❔")

        if resultado["estado"] == "OK" and (
            resultado["fallidos"] or resultado["advertencias"]
        ):
            icono = "⚠️"

        log.info(
            "%s %-28s | estado=%-8s | stage=%-8s | fallidos=%-4s | "
            "advertencias=%-4s | dwh=%-15s | %.1fs",
            icono,
            endpoint.nombre,
            resultado["estado"],
            resultado["registros"],
            resultado["fallidos"],
            len(resultado["advertencias"]),
            resultado["dwh"],
            resultado["segundos"],
        )

    log.info("=" * 70)


# ============================================================================
# ORQUESTACIÓN
# ============================================================================

def ejecutar_integracion(
    config,
    modulo,
    fecha_inicio,
    fecha_fin,
    procesos,
    endpoint_nombre=None,
):
    modulo = modulo.strip().upper()

    if modulo not in config.modulos:
        raise ValueError(
            f"El módulo '{modulo}' no está habilitado para la empresa "
            f"'{config.empresa}'."
        )

    log.info(
        "🔎 MÓDULO VALIDADO | empresa=%s | modulo=%s | conectores=%s",
        config.empresa,
        modulo,
        ", ".join(config.conectores),
    )

    endpoints, endpoints_por_nombre = seleccionar_endpoints(
        config,
        modulo,
        endpoint_nombre,
    )

    if not endpoints:
        log.warning(
            "SIN ENDPOINTS | empresa=%s | modulo=%s",
            config.empresa,
            modulo,
        )

        return {
            "empresa": config.empresa,
            "modulo": modulo,
            "stage": 0,
            "dwh": [],
            "errores": 0,
            "endpoints": [],
            "advertencias": 0,
            "estado": "SIN ENDPOINTS",
        }

    clientes = {
        conector: obtener_clase_cliente(conector)(config.conectores[conector])
        for conector in sorted({endpoint.conector for endpoint in endpoints})
    }

    for conector, cliente in clientes.items():
        api = cliente.api
        log.info(
            "🔌 CONECTOR %s | url=%s | workers=%s | page_size=%s | "
            "max_req_min=%s | max_retry=%s | timeout=%s/%ss",
            conector.upper(),
            cliente.base_url,
            api.max_workers,
            api.page_size,
            api.max_requests_por_minuto or "∞",
            api.max_retry,
            api.timeout_conexion,
            api.timeout_lectura,
        )

    engine_stage = crear_engine(config.stage, config.ejecucion, rol="stage")
    engine_dwh = (
        crear_engine(config.dwh, config.ejecucion, rol="dwh")
        if config.ejecucion.dwh_habilitado
        else None
    )

    ctx = ContextoEjecucion(
        config=config,
        modulo=modulo,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        procesos=procesos,
        engine_stage=engine_stage,
        clientes=clientes,
        endpoints_por_nombre=endpoints_por_nombre,
        barra=ProgressBar(
            total=len(endpoints),
            prefix=f"📦 AVANCE {modulo}",
            unit="endpoints",
            log=log,
            paso_log=1,
            interactivo=False,
        ),
    )

    try:
        raices, hijos_de = _organizar_arbol(endpoints)

        for endpoint in raices:
            _ejecutar_arbol(ctx, endpoint, hijos_de)

        errores_api_stage = ctx.errores
        resultados_dwh = []

        para_dwh = []

        for endpoint, resultado in ctx.resultados:
            if resultado["estado"] != "OK":
                continue

            if not endpoint.cargar_dwh:
                resultado["dwh"] = "NO APLICA"
                log.info(
                    "⏭️ DWH NO APLICA | endpoint=%s | tabla=%s | "
                    "motivo=cargar_dwh=False (solo STAGE)",
                    endpoint.nombre,
                    endpoint.tabla,
                )
                continue

            if resultado["fallidos"] and not config.ejecucion.dwh_cargar_con_fallidos:
                resultado["dwh"] = "OMITIDO"
                log.warning(
                    "⏭️ DWH OMITIDO | endpoint=%s | motivo=%s request(s) "
                    "fallidos (DWH_CARGAR_CON_FALLIDOS=0)",
                    endpoint.nombre,
                    resultado["fallidos"],
                )
                continue

            para_dwh.append(endpoint)

        if engine_dwh is None:
            log.info("⏭️ DWH DESHABILITADO | DWH_HABILITADO=0")

        elif para_dwh:
            resultados_dwh = stage_to_dwh(
                engine_stage=engine_stage,
                engine_dwh=engine_dwh,
                config=config,
                endpoints=para_dwh,
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
            )

        resultados_por_nombre = {
            resultado["nombre"]: resultado for _e, resultado in ctx.resultados
        }

        for resultado_dwh in resultados_dwh:
            resultados_por_nombre[resultado_dwh["endpoint"]]["dwh"] = (
                resultado_dwh["estado"]
            )

            if resultado_dwh["estado"] != "ERROR":
                continue

            ctx.errores += 1

            ctx.procesos.registrar_error_endpoint(
                endpoint=ctx.endpoints_por_nombre[resultado_dwh["endpoint"]],
                modulo=modulo,
                error=f"DWH: {resultado_dwh.get('error', 'Error desconocido')}",
                hora_error=resultado_dwh.get("hora_error"),
            )

        log_resumen(ctx)

        total_stage = sum(r["registros"] for _e, r in ctx.resultados)

        log.info(
            "🏁 INTEGRACIÓN FIN | empresa=%s | modulo=%s | stage=%s | "
            "endpoints=%s | errores_api_stage=%s | errores_dwh=%s | "
            "errores_total=%s | endpoints_con_advertencias=%s",
            config.empresa,
            modulo,
            total_stage,
            len(ctx.resultados),
            errores_api_stage,
            ctx.errores - errores_api_stage,
            ctx.errores,
            ctx.advertencias,
        )

        return {
            "empresa": config.empresa,
            "modulo": modulo,
            "stage": total_stage,
            "dwh": resultados_dwh,
            "errores": ctx.errores,
            "advertencias": ctx.advertencias,
            "endpoints": [resultado for _e, resultado in ctx.resultados],
        }

    finally:
        engine_stage.dispose()

        if engine_dwh is not None:
            engine_dwh.dispose()

        for cliente in clientes.values():
            cliente.close()
