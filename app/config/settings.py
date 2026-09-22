# ============================================================================
# SETTINGS
# CONFIGURACIÓN MULTIEMPRESA / MULTICONECTOR
# ============================================================================
#
# Cada cliente utiliza su propio archivo en la raíz del proyecto:
#
#   .env.demo
#   .env.cliente1
#   .env.cliente2
#
# El archivo es seleccionado por run_main.sh / main.py según el nombre
# de cliente recibido.
#
# Parámetros de API (workers, timeouts, reintentos, rate limit...) se
# resuelven por conector, en este orden (gana el primero definido):
#
#   1. <CONECTOR>_<PARAM>   ej. BSALE_MAX_WORKERS=3
#   2. API_<PARAM>          ej. API_MAX_WORKERS=5   (aplica a todos)
#   3. default del conector (atributo `defaults` de su cliente)
#   4. default global       (DEFAULTS_API, más abajo)
#
# ============================================================================

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from app.connectors.registry import (
    CONECTORES_DISPONIBLES,
    obtener_clase_cliente,
)


BASE_DIR = Path(__file__).resolve().parent.parent.parent

MODULOS_VALIDOS = {
    "FINANCIERO",
    "COMERCIAL",
    "OPERACIONAL",
    "OTROS",
}

VALORES_VERDADEROS = {"1", "true", "si", "sí", "yes", "y", "on"}
VALORES_FALSOS = {"0", "false", "no", "n", "off"}


# ============================================================================
# DEFAULTS GLOBALES DE API
# ============================================================================

DEFAULTS_API = {
    # Peticiones HTTP simultáneas por endpoint.
    "MAX_WORKERS": 5,
    # Registros por página (se limita al máximo que acepte cada API).
    "PAGE_SIZE": 100,
    # Límite de peticiones por minuto (0 = sin límite). Se reparte de
    # forma uniforme entre todos los workers del conector.
    "MAX_REQUESTS_POR_MINUTO": 0,
    # Reintentos por request HTTP (errores de conexión, 429 y status
    # reintentables).
    "MAX_RETRY": 5,
    # Espera base entre reintentos (crece linealmente por intento).
    "RETRY_BACKOFF_SEGUNDOS": 5.0,
    # Espera ante 429 cuando la API no informa Retry-After.
    "RETRY_AFTER_DEFAULT_SEGUNDOS": 60.0,
    # Variación aleatoria extra (0.5 = hasta +50%) para que los workers
    # no reintenten todos en el mismo instante.
    "RETRY_JITTER": 0.5,
    # Timeouts de requests (conexión / lectura), en segundos.
    "TIMEOUT_CONEXION": 15.0,
    "TIMEOUT_LECTURA": 120.0,
    # Status HTTP que se reintentan con backoff (429 usa Retry-After).
    "STATUS_REINTENTABLES": "429,500,502,503,504",
    "VERIFY_SSL": True,
    # Tope de páginas por tramo: protege contra APIs que ignoran la
    # paginación y devolverían la misma página para siempre.
    "MAX_PAGINAS": 100000,
    # Reintentos de un endpoint completo (borrar + descargar + cargar).
    "ENDPOINT_MAX_RETRY": 3,
}


# ============================================================================
# DATACLASSES
# ============================================================================

@dataclass
class DatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    schema: str = "public"
    grant_user: str | None = None


@dataclass
class ProcesosConfig(DatabaseConfig):
    tabla: str = "procesos_dev"
    habilitado: bool = True


@dataclass
class ApiConfig:
    """Configuración resuelta de UN conector para UN cliente."""

    nombre: str
    prefijo_env: str
    url: str | None
    token: str | None
    usuario: str | None
    password: str | None
    max_workers: int
    page_size: int
    max_requests_por_minuto: int
    max_retry: int
    retry_backoff_segundos: float
    retry_after_default_segundos: float
    retry_jitter: float
    timeout_conexion: float
    timeout_lectura: float
    status_reintentables: frozenset
    verify_ssl: bool
    max_paginas: int
    endpoint_max_retry: int

    # Todas las variables <PREFIJO>_* del .env (sin el prefijo), para
    # parámetros propios de cada conector (ej. ODOO_DATABASE).
    extras: dict = field(default_factory=dict)

    def extra(self, nombre, default=None):
        valor = self.extras.get(nombre.upper())
        return default if valor in (None, "") else valor


@dataclass
class EjecucionConfig:
    meses_automatico: int
    zona_horaria: str
    stage_batch_size: int
    stage_insert_chunksize: int
    stage_insert_max_retry: int
    stage_insert_retry_espera_segundos: float
    dwh_habilitado: bool
    dwh_cargar_con_fallidos: bool
    dwh_read_chunksize: int
    dwh_insert_chunksize: int
    db_pool_recycle_segundos: int
    db_connect_timeout: int
    db_statement_timeout_ms: int
    log_nivel_consola: str
    log_buffer_capacidad: int
    log_archivo_ejecucion: bool
    log_retencion_dias: int


@dataclass
class EmpresaConfig:
    empresa: str
    estado: str
    proceso_nombre: str
    modulos: list
    modulo: str
    conectores: dict
    endpoints_habilitados: set
    endpoints_deshabilitados: set
    env_file: str
    stage: DatabaseConfig
    dwh: DatabaseConfig
    procesos: ProcesosConfig
    ejecucion: EjecucionConfig


# ============================================================================
# HELPERS DE LECTURA
# ============================================================================

def obtener_variable(nombre, obligatoria=True, default=None):
    valor = os.getenv(nombre)

    if valor is not None:
        valor = valor.strip()

    if not valor:
        if obligatoria:
            raise RuntimeError(
                f"Falta variable de entorno obligatoria: {nombre}"
            )

        return default

    return valor


def _convertir_int(valor, nombre, minimo=None):
    try:
        numero = int(str(valor).strip())
    except ValueError:
        raise ValueError(
            f"La variable {nombre} debe ser un entero. Valor: '{valor}'"
        ) from None

    if minimo is not None and numero < minimo:
        raise ValueError(
            f"La variable {nombre} debe ser >= {minimo}. Valor: {numero}"
        )

    return numero


def _convertir_float(valor, nombre, minimo=None):
    try:
        numero = float(str(valor).strip())
    except ValueError:
        raise ValueError(
            f"La variable {nombre} debe ser numérica. Valor: '{valor}'"
        ) from None

    if minimo is not None and numero < minimo:
        raise ValueError(
            f"La variable {nombre} debe ser >= {minimo}. Valor: {numero}"
        )

    return numero


def _convertir_bool(valor, nombre):
    if isinstance(valor, bool):
        return valor

    texto = str(valor).strip().lower()

    if texto in VALORES_VERDADEROS:
        return True

    if texto in VALORES_FALSOS:
        return False

    raise ValueError(
        f"La variable {nombre} debe ser booleana (1/0, true/false, "
        f"si/no). Valor: '{valor}'"
    )


def _convertir_status(valor, nombre):
    if isinstance(valor, (set, frozenset)):
        return frozenset(valor)

    codigos = set()

    for parte in str(valor).split(","):
        parte = parte.strip()

        if parte:
            codigos.add(_convertir_int(parte, nombre, minimo=100))

    return frozenset(codigos)


def obtener_int(nombre, default, minimo=None):
    valor = obtener_variable(nombre, obligatoria=False)
    return default if valor is None else _convertir_int(valor, nombre, minimo)


def obtener_float(nombre, default, minimo=None):
    valor = obtener_variable(nombre, obligatoria=False)
    return default if valor is None else _convertir_float(valor, nombre, minimo)


def obtener_bool(nombre, default):
    valor = obtener_variable(nombre, obligatoria=False)
    return default if valor is None else _convertir_bool(valor, nombre)


def obtener_lista(nombre, obligatoria=False, mayusculas=True):
    valor = obtener_variable(nombre, obligatoria=obligatoria) or ""

    elementos = [
        elemento.strip().upper() if mayusculas else elemento.strip()
        for elemento in valor.split(",")
        if elemento.strip()
    ]

    return list(dict.fromkeys(elementos))


# ============================================================================
# CARGA DEL .ENV DEL CLIENTE
# ============================================================================

def resolver_env_file(empresa):
    empresa = (empresa or "").strip()

    if not empresa:
        raise ValueError("No se especificó una empresa.")

    candidatos = [
        BASE_DIR / f".env.{empresa}",
        BASE_DIR / f".env.{empresa.lower()}",
    ]

    for candidato in candidatos:
        if candidato.exists():
            return candidato

    raise FileNotFoundError(
        f"No existe el archivo de configuración: {candidatos[0]}"
    )


def cargar_env_empresa(empresa):
    env_file = resolver_env_file(empresa)

    load_dotenv(dotenv_path=env_file, override=True)

    return str(env_file)


# ============================================================================
# CONSTRUCCIÓN DE CONFIGURACIONES
# ============================================================================

def _database(prefijo, obligatoria=True):
    return dict(
        host=obtener_variable(f"{prefijo}_HOST", obligatoria),
        port=_convertir_int(
            obtener_variable(f"{prefijo}_PORT", obligatoria=False) or "5432",
            f"{prefijo}_PORT",
            minimo=1,
        ),
        database=obtener_variable(f"{prefijo}_DB", obligatoria),
        user=obtener_variable(f"{prefijo}_USER", obligatoria),
        password=obtener_variable(f"{prefijo}_PASSWORD", obligatoria) or "",
        schema=obtener_variable(
            f"{prefijo}_SCHEMA",
            obligatoria=False,
            default="public",
        ),
    )


def _param_api(prefijo, nombre, defaults_conector, conversor):
    for clave in (f"{prefijo}_{nombre}", f"API_{nombre}"):
        valor = os.getenv(clave)

        if valor is not None and valor.strip() != "":
            return conversor(valor, clave)

    if nombre in defaults_conector:
        return conversor(defaults_conector[nombre], f"{prefijo}_{nombre}")

    return conversor(DEFAULTS_API[nombre], f"API_{nombre}")


def construir_api_config(nombre_conector):
    clase_cliente = obtener_clase_cliente(nombre_conector)

    prefijo = clase_cliente.prefijo_env
    defaults = dict(clase_cliente.defaults or {})

    extras = {
        clave[len(prefijo) + 1:]: valor.strip()
        for clave, valor in os.environ.items()
        if clave.upper().startswith(f"{prefijo}_")
    }

    def entero(nombre, minimo=0):
        return _param_api(
            prefijo,
            nombre,
            defaults,
            lambda v, c: _convertir_int(v, c, minimo=minimo),
        )

    def decimal(nombre, minimo=0):
        return _param_api(
            prefijo,
            nombre,
            defaults,
            lambda v, c: _convertir_float(v, c, minimo=minimo),
        )

    api = ApiConfig(
        nombre=nombre_conector,
        prefijo_env=prefijo,
        url=(
            obtener_variable(f"{prefijo}_URL", obligatoria=False)
            or clase_cliente.url_default
        ),
        token=obtener_variable(f"{prefijo}_TOKEN", obligatoria=False),
        usuario=obtener_variable(f"{prefijo}_USER", obligatoria=False),
        password=obtener_variable(f"{prefijo}_PASSWORD", obligatoria=False),
        max_workers=entero("MAX_WORKERS", minimo=1),
        page_size=entero("PAGE_SIZE", minimo=1),
        max_requests_por_minuto=entero("MAX_REQUESTS_POR_MINUTO"),
        max_retry=entero("MAX_RETRY", minimo=1),
        retry_backoff_segundos=decimal("RETRY_BACKOFF_SEGUNDOS"),
        retry_after_default_segundos=decimal("RETRY_AFTER_DEFAULT_SEGUNDOS"),
        retry_jitter=decimal("RETRY_JITTER"),
        timeout_conexion=decimal("TIMEOUT_CONEXION", minimo=1),
        timeout_lectura=decimal("TIMEOUT_LECTURA", minimo=1),
        status_reintentables=_param_api(
            prefijo,
            "STATUS_REINTENTABLES",
            defaults,
            _convertir_status,
        ),
        verify_ssl=_param_api(prefijo, "VERIFY_SSL", defaults, _convertir_bool),
        max_paginas=entero("MAX_PAGINAS", minimo=1),
        endpoint_max_retry=entero("ENDPOINT_MAX_RETRY", minimo=1),
        extras=extras,
    )

    faltantes = [
        f"{prefijo}_{requerido}"
        for requerido in clase_cliente.requiere
        if not getattr(api, requerido.lower(), None)
    ]

    if faltantes:
        raise RuntimeError(
            f"Conector '{nombre_conector}': faltan variables "
            f"obligatorias: {', '.join(faltantes)}"
        )

    return api


def construir_ejecucion_config():
    return EjecucionConfig(
        meses_automatico=obtener_int("MESES_AUTOMATICO", 2, minimo=1),
        zona_horaria=obtener_variable(
            "ZONA_HORARIA",
            obligatoria=False,
            default="America/Santiago",
        ),
        stage_batch_size=obtener_int("STAGE_BATCH_SIZE", 5000, minimo=1),
        stage_insert_chunksize=obtener_int(
            "STAGE_INSERT_CHUNKSIZE", 800, minimo=1
        ),
        stage_insert_max_retry=obtener_int(
            "STAGE_INSERT_MAX_RETRY", 3, minimo=1
        ),
        stage_insert_retry_espera_segundos=obtener_float(
            "STAGE_INSERT_RETRY_ESPERA_SEGUNDOS", 5.0, minimo=0
        ),
        dwh_habilitado=obtener_bool("DWH_HABILITADO", True),
        dwh_cargar_con_fallidos=obtener_bool("DWH_CARGAR_CON_FALLIDOS", False),
        dwh_read_chunksize=obtener_int("DWH_READ_CHUNKSIZE", 20000, minimo=1),
        dwh_insert_chunksize=obtener_int(
            "DWH_INSERT_CHUNKSIZE", 5000, minimo=1
        ),
        db_pool_recycle_segundos=obtener_int(
            "DB_POOL_RECYCLE_SEGUNDOS", 280, minimo=1
        ),
        db_connect_timeout=obtener_int("DB_CONNECT_TIMEOUT", 15, minimo=1),
        db_statement_timeout_ms=obtener_int(
            "DB_STATEMENT_TIMEOUT_MS", 0, minimo=0
        ),
        **leer_config_logging(),
    )


def leer_config_logging():
    """
    Parámetros de logging. Se leen aparte porque main.py configura el
    logging ANTES de validar el resto de la configuración (para que un
    error de configuración quede registrado en error/).
    """

    return dict(
        log_nivel_consola=(
            obtener_variable("LOG_NIVEL_CONSOLA", obligatoria=False)
            or "INFO"
        ).upper(),
        log_buffer_capacidad=obtener_int(
            "LOG_BUFFER_CAPACIDAD", 2000, minimo=100
        ),
        log_archivo_ejecucion=obtener_bool("LOG_ARCHIVO_EJECUCION", True),
        log_retencion_dias=obtener_int("LOG_RETENCION_DIAS", 30, minimo=0),
    )


def get_config(empresa, modulo):
    modulo = modulo.strip().upper()

    env_file = cargar_env_empresa(empresa)

    if modulo not in MODULOS_VALIDOS:
        raise ValueError(
            f"Módulo inválido: '{modulo}'. Valores permitidos: "
            f"{', '.join(sorted(MODULOS_VALIDOS))}"
        )

    empresa_env = obtener_variable(
        "EMPRESA",
        obligatoria=False,
        default=empresa,
    )

    modulos = obtener_lista("MODULOS", obligatoria=True)

    modulos_invalidos = [m for m in modulos if m not in MODULOS_VALIDOS]

    if modulos_invalidos:
        raise ValueError(
            f"Módulos inválidos configurados para empresa '{empresa}': "
            f"{', '.join(modulos_invalidos)}. Valores permitidos: "
            f"{', '.join(sorted(MODULOS_VALIDOS))}"
        )

    if modulo not in modulos:
        raise ValueError(
            f"El módulo '{modulo}' no está habilitado para la empresa "
            f"'{empresa}'. Módulos habilitados: {', '.join(modulos)}"
        )

    nombres_conectores = obtener_lista(
        "CONECTORES",
        obligatoria=True,
        mayusculas=False,
    )

    nombres_conectores = [nombre.lower() for nombre in nombres_conectores]

    desconocidos = [
        nombre
        for nombre in nombres_conectores
        if nombre not in CONECTORES_DISPONIBLES
    ]

    if desconocidos:
        raise ValueError(
            f"Conectores desconocidos en CONECTORES: "
            f"{', '.join(desconocidos)}. Disponibles: "
            f"{', '.join(sorted(CONECTORES_DISPONIBLES))}"
        )

    conectores = {
        nombre: construir_api_config(nombre)
        for nombre in nombres_conectores
    }

    stage = DatabaseConfig(**_database("STAGE"))

    ejecucion = construir_ejecucion_config()

    dwh = DatabaseConfig(
        **_database("DWH", obligatoria=ejecucion.dwh_habilitado),
        grant_user=obtener_variable("DWH_GRANT_USER", obligatoria=False),
    )

    procesos_habilitado = obtener_bool("PROCESOS_HABILITADO", True)

    procesos = ProcesosConfig(
        **_database("PS_PROCESOS", obligatoria=procesos_habilitado),
        tabla=obtener_variable(
            "PS_PROCESOS_TABLA",
            obligatoria=False,
            default="procesos_dev",
        ),
        habilitado=procesos_habilitado,
    )

    return EmpresaConfig(
        empresa=empresa_env,
        estado=obtener_variable("ESTADO", obligatoria=False, default="ACTIVA"),
        proceso_nombre=obtener_variable(
            "PROCESO_NOMBRE",
            obligatoria=False,
            default="BASE_INTEGRATION",
        ).upper(),
        modulos=modulos,
        modulo=modulo,
        conectores=conectores,
        endpoints_habilitados=set(obtener_lista("ENDPOINTS_HABILITADOS")),
        endpoints_deshabilitados=set(obtener_lista("ENDPOINTS_DESHABILITADOS")),
        env_file=env_file,
        stage=stage,
        dwh=dwh,
        procesos=procesos,
        ejecucion=ejecucion,
    )
