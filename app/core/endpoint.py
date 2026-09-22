from dataclasses import dataclass

from app.utils.fechas import PARTICIONES_VALIDAS


# ============================================================================
# MÓDULOS
# ============================================================================

MODULOS_VALIDOS = {
    "FINANCIERO",
    "COMERCIAL",
    "OPERACIONAL",
    "OTROS",
}

# Se ejecuta en cualquier módulo.
MODULO_TODOS = "TODOS"

# Definido pero apagado (no se ejecuta nunca).
MODULO_INACTIVO = "INACTIVO"


# ============================================================================
# PAGINACIÓN
# ============================================================================
#
# offset → parámetros offset/limit (nombres definidos por el cliente).
# page   → parámetros page/limit (página inicial definida por el cliente).
# none   → una sola llamada por tramo (la API devuelve todo).
#
# Si la API informa el total de registros (ruta_total / header), las
# páginas restantes se descargan EN PARALELO (MAX_WORKERS). Si no, se
# pagina de forma secuencial hasta recibir una página incompleta.
# ============================================================================

PAGINACIONES_VALIDAS = {
    "offset",
    "page",
    "none",
}


# ============================================================================
# FILTROS DE FECHA EN LA API
# ============================================================================
#
# range_unix  → {filtro: "[unix_inicio,unix_fin]"}        (ej. Bsale)
# unix        → {filtro: unix_inicio_del_tramo}           (ej. Bsale día exacto)
# desde_hasta → {filtro[0]: desde, filtro[1]: hasta}     (formato_fecha_api)
# ruta        → la ruta del endpoint usa {desde} {hasta} {anio} {mes}
# dominio     → filtro propio del conector (ej. dominio Odoo)
# ============================================================================

TIPOS_FILTRO_FECHA = {
    "range_unix",
    "unix",
    "desde_hasta",
    "ruta",
    "dominio",
}


@dataclass(frozen=True)
class EndpointConfig:

    # ------------------------------------------------------------------------
    # IDENTIFICACIÓN
    # ------------------------------------------------------------------------

    # Nombre único entre TODOS los conectores (se usa para ejecutar un
    # endpoint puntual: ./run_main.sh cliente_c NOMBRE).
    nombre: str

    # Conector al que pertenece (clave de connectors/registry.py).
    conector: str

    # Ruta relativa a la URL base del conector (o modelo, en Odoo).
    # Puede tener variables: {id} (endpoints hijo), {desde} {hasta}
    # {anio} {mes} (filtro "ruta").
    endpoint: str

    # Tabla destino (misma en STAGE y DWH).
    tabla: str

    # FINANCIERO / COMERCIAL / OPERACIONAL / OTROS / TODOS / INACTIVO.
    modulo: str

    # ------------------------------------------------------------------------
    # REQUEST
    # ------------------------------------------------------------------------

    parametros: dict | None = None
    paginacion: str = "offset"

    # Registros por página para este endpoint (si no, <CONECTOR>_PAGE_SIZE).
    page_size: int | None = None

    # Dónde vienen los registros y el total en la respuesta JSON
    # (ruta con puntos, ej. "data.items"). None = default del conector.
    ruta_items: str | None = None
    ruta_total: str | None = None

    # ------------------------------------------------------------------------
    # FECHAS
    # ------------------------------------------------------------------------

    # Columna (en STAGE/DWH) usada para borrar el rango antes de cargar.
    # Sin campo_fecha → carga completa (TRUNCATE + carga).
    campo_fecha: str | None = None

    filtro_fecha_api: str | tuple | None = None
    tipo_filtro_fecha_api: str | None = None
    formato_fecha_api: str = "%Y-%m-%d"

    # Divide el rango en tramos (dia/semana/mes/anio) y los descarga en
    # paralelo. Útil si la API solo acepta un día exacto o limita el
    # rango máximo por consulta.
    particion_fecha: str | None = None

    # Descarta localmente los registros cuyo campo_fecha quede fuera del
    # rango (para APIs que devuelven más de lo pedido, ej. un año entero).
    filtrar_rango_local: bool = False

    # Campos que vienen como timestamp Unix: se guarda <campo>Unix con el
    # valor original y <campo> como texto 'YYYY-MM-DD HH:MM:SS'.
    campos_fecha_unix: list | None = None

    # ------------------------------------------------------------------------
    # STAGE / DWH
    # ------------------------------------------------------------------------

    # Objetos anidados de un nivel se aplanan: {"client": {"id": 1}} →
    # client_id. Niveles más profundos y listas se guardan como JSON.
    aplanar_objetos: bool = True

    # Tipos Postgres explícitos (el resto se infiere de los datos).
    tipos_stage: dict | None = None

    # Clave primaria en STAGE (upsert). None → "id" si viene en los datos.
    llave_primaria: tuple | None = None

    # Columnas a traspasar a DWH (None → todas las de STAGE).
    campos_dwh: list | None = None

    # False → el endpoint solo se carga en STAGE (tablas de apoyo o
    # metadata que el BI no necesita).
    cargar_dwh: bool = True

    # ------------------------------------------------------------------------
    # ENDPOINT HIJO (encadenado a un padre, ej. documento → detalle)
    # ------------------------------------------------------------------------

    # Nombre del endpoint padre. Los ids se leen desde la tabla STAGE del
    # padre (dentro del rango de fechas si el padre tiene campo_fecha) y
    # se llama a `endpoint` con {id} reemplazado por cada uno.
    padre: str | None = None

    # Columna que se agrega a cada registro hijo con el id del padre.
    columna_fk: str | None = None

    # Columna del padre en STAGE que se usa como id.
    columna_id_padre: str = "id"

    # Columnas del padre (en STAGE) que se copian a cada hijo; si el
    # padre filtra por fecha, el campo_fecha del hijo debe venir de acá.
    campos_padre: list | None = None

    # ------------------------------------------------------------------------
    # OPCIONES PROPIAS DEL CONECTOR (ej. fields/domain de Odoo)
    # ------------------------------------------------------------------------

    opciones: dict | None = None

    def __post_init__(self):
        modulo = self.modulo.strip().upper()

        if modulo not in MODULOS_VALIDOS | {MODULO_TODOS, MODULO_INACTIVO}:
            raise ValueError(
                f"Endpoint '{self.nombre}' tiene un módulo inválido: "
                f"'{self.modulo}'."
            )

        object.__setattr__(self, "nombre", self.nombre.strip().upper())
        object.__setattr__(self, "conector", self.conector.strip().lower())
        object.__setattr__(self, "modulo", modulo)
        object.__setattr__(self, "parametros", dict(self.parametros or {}))
        object.__setattr__(self, "tipos_stage", dict(self.tipos_stage or {}))
        object.__setattr__(self, "campos_dwh", list(self.campos_dwh or []))
        object.__setattr__(self, "opciones", dict(self.opciones or {}))
        object.__setattr__(
            self, "campos_fecha_unix", list(self.campos_fecha_unix or [])
        )
        object.__setattr__(self, "campos_padre", list(self.campos_padre or []))

        if self.llave_primaria is not None:
            object.__setattr__(
                self, "llave_primaria", tuple(self.llave_primaria)
            )

        if self.paginacion not in PAGINACIONES_VALIDAS:
            raise ValueError(
                f"Endpoint '{self.nombre}': paginacion inválida "
                f"'{self.paginacion}'. Valores: "
                f"{', '.join(sorted(PAGINACIONES_VALIDAS))}"
            )

        if self.page_size is not None and self.page_size <= 0:
            raise ValueError(
                f"Endpoint '{self.nombre}': page_size debe ser > 0."
            )

        tipo = self.tipo_filtro_fecha_api

        if tipo is not None and tipo not in TIPOS_FILTRO_FECHA:
            raise ValueError(
                f"Endpoint '{self.nombre}': tipo_filtro_fecha_api "
                f"inválido '{tipo}'. Valores: "
                f"{', '.join(sorted(TIPOS_FILTRO_FECHA))}"
            )

        if tipo in {"range_unix", "unix"} and not isinstance(
            self.filtro_fecha_api, str
        ):
            raise ValueError(
                f"Endpoint '{self.nombre}': tipo '{tipo}' requiere "
                "filtro_fecha_api con el nombre del parámetro."
            )

        if tipo == "desde_hasta" and not (
            isinstance(self.filtro_fecha_api, (tuple, list))
            and len(self.filtro_fecha_api) == 2
        ):
            raise ValueError(
                f"Endpoint '{self.nombre}': tipo 'desde_hasta' requiere "
                "filtro_fecha_api=(param_desde, param_hasta)."
            )

        if tipo == "ruta" and not any(
            variable in self.endpoint
            for variable in ("{desde}", "{hasta}", "{anio}", "{mes}")
        ):
            raise ValueError(
                f"Endpoint '{self.nombre}': tipo 'ruta' requiere que la "
                "ruta use {desde}, {hasta}, {anio} o {mes}."
            )

        if self.particion_fecha is not None:
            if self.particion_fecha not in PARTICIONES_VALIDAS:
                raise ValueError(
                    f"Endpoint '{self.nombre}': particion_fecha inválida "
                    f"'{self.particion_fecha}'."
                )

            if tipo is None:
                raise ValueError(
                    f"Endpoint '{self.nombre}': particion_fecha requiere "
                    "tipo_filtro_fecha_api."
                )

        if tipo is not None and not self.campo_fecha:
            raise ValueError(
                f"Endpoint '{self.nombre}': filtra la API por fecha pero "
                "no define campo_fecha (necesario para borrar ese mismo "
                "rango en STAGE/DWH antes de cargar)."
            )

        if (
            self.campo_fecha
            and not self.padre
            and tipo is None
            and not self.filtrar_rango_local
        ):
            raise ValueError(
                f"Endpoint '{self.nombre}': tiene campo_fecha pero la API "
                "no filtra por fecha. Define tipo_filtro_fecha_api o "
                "filtrar_rango_local=True."
            )

        if self.padre:
            object.__setattr__(self, "padre", self.padre.strip().upper())

            if not self.columna_fk:
                raise ValueError(
                    f"Endpoint hijo '{self.nombre}': falta columna_fk."
                )

            if "{id}" not in self.endpoint:
                raise ValueError(
                    f"Endpoint hijo '{self.nombre}': la ruta debe incluir "
                    "{id} (se reemplaza por el id del padre)."
                )

            if tipo is not None:
                raise ValueError(
                    f"Endpoint hijo '{self.nombre}': no filtra por fecha "
                    "en la API; hereda el rango desde su padre."
                )

            if self.campo_fecha and self.campo_fecha not in self.campos_padre:
                raise ValueError(
                    f"Endpoint hijo '{self.nombre}': campo_fecha "
                    f"'{self.campo_fecha}' debe venir en campos_padre."
                )

    @property
    def es_hijo(self):
        return bool(self.padre)

    @property
    def activo(self):
        return self.modulo != MODULO_INACTIVO

    def aplica_a_modulo(self, modulo):
        return self.activo and self.modulo in {modulo, MODULO_TODOS}
