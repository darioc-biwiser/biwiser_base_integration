# Biwiser Base Integration: API → STAGE → DWH

Proyecto base para construir integraciones de APIs con la misma arquitectura que
`biwiser_bsale_integration` y `biwiser_odoo_integration`: se descargan datos desde una o más
APIs, se dejan en una base **STAGE**, se traspasan a una base **DWH** y cada corrida queda
registrada en la tabla de control **`procesos_dev`**.

La diferencia con los proyectos de origen es que el motor es **genérico**. Descarga, paginación,
concurrencia, reintentos, carga a STAGE, traspaso a DWH, logs y registro de procesos viven en
`app/core` y `app/database`. Cada API es un **conector**: un cliente pequeño más una lista de
endpoints declarativos. Los parámetros de rendimiento (workers, timeouts, reintentos, rate
limit, tamaño de página...) se configuran **por cliente y por conector** en el `.env`.

**Accesos rápidos:**
- ▶️ [Cómo ejecutar](#5-ejecución-run_mainsh) (`run_main.sh`)
- 🏢 [Agregar un cliente nuevo](#agregar-un-cliente-nuevo)
- 🔌 [Agregar un endpoint](#agregar-un-endpoint-a-un-conector-existente)
- 🧩 [Agregar un conector (API nueva)](#agregar-un-conector-api-nueva)
- 🐳 [Probar en local con Docker](#12-probar-en-local-con-docker)

## Índice

1. [Flujo de datos](#1-flujo-de-datos)
2. [Estructura del repositorio](#2-estructura-del-repositorio)
3. [Conectores y endpoints de ejemplo](#3-conectores-y-endpoints-de-ejemplo)
4. [Configuración por cliente: `.env.<cliente>`](#4-configuración-por-cliente-envcliente)
5. [Ejecución: `run_main.sh`](#5-ejecución-run_mainsh)
6. [Endpoints: `EndpointConfig`](#6-endpoints-endpointconfig)
7. [Concurrencia, paginación y reintentos](#7-concurrencia-paginación-y-reintentos)
8. [Ciclo de vida en STAGE y DWH](#8-ciclo-de-vida-en-stage-y-dwh)
9. [Logs y carpetas](#9-logs-y-carpetas)
10. [Tabla de control `procesos_dev`](#10-tabla-de-control-procesos_dev)
11. [Extender el proyecto](#11-extender-el-proyecto)
12. [Probar en local con Docker](#12-probar-en-local-con-docker)
13. [Diferencias con los proyectos de origen](#13-diferencias-con-los-proyectos-de-origen)

---

## 1. Flujo de datos

```
 APIs (Bsale, Odoo, mindicador, ...)  →  STAGE (Postgres)  →  DWH (Postgres)
                                              ↑
                        procesos_dev (Postgres, control de ejecuciones)
```

1. **`run_main.sh`** interpreta cliente, módulo, fechas y endpoint, y llama a `main.py` una vez
   por módulo.
2. **`main.py`** carga `.env.<cliente>`, configura los logs, calcula el rango de fechas, registra
   el inicio en `procesos_dev` y llama a `ejecutar_integracion(...)` (`app/core/services.py`).
3. Para cada endpoint del módulo:
   borrar el rango en STAGE (o TRUNCATE) → descargar (en paralelo si se puede) → insertar en
   STAGE por lotes → ejecutar sus endpoints **hijo** si los tiene.
4. Los endpoints que terminaron OK se traspasan **STAGE → DWH** (`app/database/stage_to_dwh.py`).
5. `main.py` cierra el registro en `procesos_dev` con el estado final, la duración y la cantidad
   de errores.

---

## 2. Estructura del repositorio

```
main.py                         Punto de entrada: argumentos, logs, procesos, rango de fechas.
run_main.sh                     Wrapper: cliente/módulo/fechas/endpoint → main.py por módulo.
Pipfile                         Dependencias (mismas versiones que bsale/odoo: CentOS 7 / py3.10).
.env.example                    Plantilla completa y comentada de TODAS las variables.
.env.demo                       Cliente de ejemplo que usa solo APIs libres (sin tokens).
sql/procesos_dev.sql            DDL de la tabla de control.
error/                          Logs de error (solo cuando hay fallas) y URLs fallidas.
logs/                           Log completo de cada ejecución (LOG_ARCHIVO_EJECUCION=1).
app/
  config/settings.py            Lectura y validación del .env (EmpresaConfig, ApiConfig, ...).
  core/
    endpoint.py                 EndpointConfig: definición declarativa de un endpoint.
    http_client.py              BaseApiClient: HTTP, reintentos, 429, jitter, rate limit, paginación.
    services.py                 Motor: selección, limpieza STAGE, descarga concurrente, hijos, DWH.
    procesos.py                 RegistroProcesos: inicio/fin/errores en procesos_dev.
  connectors/
    registry.py                 Registro de conectores disponibles.
    bsale/        client.py + endpoints.py
    odoo/         client.py + endpoints.py
    smartsheet/   client.py + endpoints.py     (hojas → tablas, diccionario de columnas)
    mindicador/   client.py + endpoints.py     (API libre)
    frankfurter/  client.py + endpoints.py     (API libre)
    jsonplaceholder/ client.py + endpoints.py  (API libre)
    open_meteo/   client.py + endpoints.py     (API libre)
  database/
    engines.py                  Engines Postgres (keepalive, pool_recycle, reintentos).
    stage_loader.py             Normalización, DDL automático, upsert, borrado por rango.
    stage_to_dwh.py             Traspaso por bloques con los tipos de STAGE.
  utils/
    fechas.py                   Zona horaria, rango automático, tramos, Unix.
    logger.py                   Íconos, buffer circular de errores, archivos de log.
    progress.py                 Barra de avance con tiempo transcurrido y restante.
```

---

## 3. Conectores y endpoints de ejemplo

| Conector | Endpoint | Módulo | Qué demuestra |
|---|---|---|---|
| `bsale` | `BSALE_DOCUMENTS` | COMERCIAL | Token por header, filtro `emissiondaterange` Unix, páginas en paralelo usando `count`, fechas Unix → texto, objetos aplanados (`client_id`). |
| `bsale` | `BSALE_DOCUMENT_DETAILS` | COMERCIAL | **Endpoint hijo**: `documents/{id}/details.json` por cada documento del rango. Hereda `emissionDate` del padre. |
| `odoo` | `ODOO_ACCOUNT_MOVE` | FINANCIERO | JSON-2 API (POST), filtro por *domain*, `search_count` para paralelizar, Many2one → `campo` + `campo_name`, `False` → NULL. |
| `smartsheet` | `SMARTSHEET_EJEMPLO_FACTURACION` (+ `_COLUMNAS`) | INACTIVO | Hoja completa sin filtro de fecha, paginación `page`/`pageSize` con `totalRowCount`, celdas por `columnId` → columnas con el título de la hoja, JSON aplanado (fila, hoja, workspace, contactos, links), números exactos (Decimal), montos escritos como texto (`"13.124.300"`), diccionario de columnas solo en STAGE (`cargar_dwh=False`). Ver sección 3.1. |
| `mindicador` | `MINDICADOR_UF`, `MINDICADOR_DOLAR` | FINANCIERO | Fecha en la ruta (`uf/{anio}`), un tramo por año, descarte local fuera de rango, clave primaria compuesta. `MINDICADOR_UTM` viene `INACTIVO`. |
| `frankfurter` | `FRANKFURTER_USD` | FINANCIERO | Rango en la ruta (`{desde}..{hasta}`), tramos mensuales en paralelo, respuesta anidada convertida a filas. |
| `jsonplaceholder` | `JSONPLACEHOLDER_USERS` | COMERCIAL | Una sola llamada, aplanado de objetos (`address_city`, `company_name`). |
| `jsonplaceholder` | `JSONPLACEHOLDER_POSTS` | OPERACIONAL | Paginación `_start`/`_limit` con total en el header `X-Total-Count` (5 páginas en paralelo). |
| `jsonplaceholder` | `JSONPLACEHOLDER_COMMENTS` | OPERACIONAL | Endpoint hijo sin fecha (`posts/{id}/comments`) con barra de avance por padre. |
| `open_meteo` | `OPEN_METEO_CLIMA_DIARIO` | OPERACIONAL | Filtro `start_date`/`end_date`, tramos mensuales, respuesta por columnas → filas, rate limit propio. |

Las tablas siguen la convención `<mod>_api_<conector>_<recurso>` (`com_`, `fin_`, `ope_`,
`otr_` para OTROS).

### 3.1 Smartsheet

Implementación de referencia con hojas reales: `biwiser_smartsheet_integration`.

Cada hoja se declara con `hoja(...)` en `app/connectors/smartsheet/endpoints.py` y genera dos
endpoints:

| Endpoint | Tabla | DWH | Contenido |
|---|---|---|---|
| `<NOMBRE>` | `<tabla>` | Sí | Una fila por fila de la hoja (PK `row_id`). |
| `<NOMBRE>_COLUMNAS` | `<tabla>_columnas` | No (solo STAGE) | Una fila por columna: título, `columna_bd`, descripción, tipo, fórmula, opciones. |

```python
*hoja(
    nombre="SMARTSHEET_FACTURACION",
    sheet_id="<token de https://app.smartsheet.com/sheets/<token>>",
    tabla="fin_api_smartsheet_facturacion",
    modulo="FINANCIERO",
    numericos=["Neto"],                   # "13.124.300" → 13124300; otro texto → NULL + warning
    tipos={"#R": "TEXT", "Fecha": "DATE", "Neto": "NUMERIC", "Pago": "BOOLEAN"},
),
```

- **Nombres de columna** = título en Smartsheet normalizado (sin tildes, `#` → `num_`,
  snake_case): `#R` → `num_r`, `Fecha P.` → `fecha_p`. No se renombran; el significado queda en
  `<tabla>_columnas.column_description`.
- **JSON aplanado**: metadata de fila (`row_id`, `row_number`, `row_parent_id`,
  `row_sibling_id`, `row_expanded`, `row_locked`, `row_created_at`, `row_modified_at`), de hoja
  (`sheet_id`, `sheet_name`, `sheet_permalink`, `sheet_version`, `sheet_total_row_count`,
  `sheet_created_at`, `sheet_modified_at`) y de workspace (`workspace_id`, `workspace_name`).
  Contactos agregan `<col>_nombre`; celdas con link, `<col>_url`.
- **Valores**: se usa `value` (no `displayValue`, que trae formato regional). `DATE` → date,
  `DATETIME` → timestamp UTC, `CHECKBOX` → boolean (vacío = false), números → Decimal exacto
  (`125.0` → `125`). Las fórmulas se cargan con su valor calculado.
- **Tipos**: conviene declararlos todos; la tabla se crea con los de la primera carga.
- Variables: `SMARTSHEET_TOKEN`, `SMARTSHEET_PAGE_SIZE` (5000), `SMARTSHEET_MAX_WORKERS` (3),
  `SMARTSHEET_MAX_REQUESTS_POR_MINUTO` (250; el límite de Smartsheet es 300).

---

## 4. Configuración por cliente: `.env.<cliente>`

Cada cliente tiene su archivo `.env.<cliente>` en la raíz. `.env.example` documenta **todas**
las variables; `.env.demo` es un cliente listo para usar con APIs libres.

Variables principales:

| Variable | Descripción |
|---|---|
| `EMPRESA`, `ESTADO` | Nombre del cliente (columna `cliente` de procesos). Si `ESTADO` no es `ACTIVA`, se omite sin error. |
| `PROCESO_NOMBRE` | Valor de la columna `proceso` (ej. `BSALE`, `ODOO`, `BASE_INTEGRATION`). |
| `MODULOS` | Módulos habilitados: `FINANCIERO,COMERCIAL,OPERACIONAL,OTROS`. Un módulo sin endpoints termina como `SIN ENDPOINTS` (no es error). |
| `CONECTORES` | Conectores habilitados: `bsale,odoo,mindicador,...`. |
| `MESES_AUTOMATICO` | Meses hacia atrás en modo automático (default **2**). |
| `ZONA_HORARIA` | Zona de las fechas de procesos, del rango automático y de los logs (default `America/Santiago`). |
| `ENDPOINTS_HABILITADOS` / `ENDPOINTS_DESHABILITADOS` | Lista blanca / negra de endpoints para ese cliente. |
| `STAGE_*`, `DWH_*`, `PS_PROCESOS_*` | Conexiones a las tres bases (mismos nombres que bsale/odoo). |
| `PROCESOS_HABILITADO`, `DWH_HABILITADO` | `0` para pruebas: no registra procesos / no traspasa a DWH. |

### Parámetros de API por conector

Cada parámetro se resuelve en este orden (gana el primero definido):

```
<CONECTOR>_<PARAM>   →   API_<PARAM>   →   default del conector   →   default global
BSALE_MAX_WORKERS=3      API_MAX_WORKERS=5  (Cliente.defaults)        (settings.DEFAULTS_API)
```

| Parámetro | Default global | Uso |
|---|---|---|
| `MAX_WORKERS` | 5 | Peticiones simultáneas por endpoint. |
| `PAGE_SIZE` | 100 | Registros por página (se limita al máximo de la API; Bsale 50). |
| `MAX_REQUESTS_POR_MINUTO` | 0 (sin límite) | Rate limit compartido por todos los workers del conector. |
| `MAX_RETRY` | 5 | Reintentos por request HTTP. |
| `RETRY_BACKOFF_SEGUNDOS` | 5 | Espera base entre reintentos (5s, 10s, 15s...). |
| `RETRY_AFTER_DEFAULT_SEGUNDOS` | 60 | Espera ante 429 si la API no informa `Retry-After`. |
| `RETRY_JITTER` | 0.5 | Hasta +50% aleatorio de espera para que los workers no reintenten a la vez. |
| `TIMEOUT_CONEXION` / `TIMEOUT_LECTURA` | 15 / 120 | Timeouts HTTP en segundos. |
| `STATUS_REINTENTABLES` | `429,500,502,503,504` | Status que se reintentan (Bsale agrega 401). |
| `VERIFY_SSL` | 1 | Validación de certificado. |
| `MAX_PAGINAS` | 100000 | Tope de páginas por tramo (protege contra APIs que ignoran la paginación). |
| `ENDPOINT_MAX_RETRY` | 3 | Reintentos del endpoint completo (borrar + descargar + cargar). |

Credenciales y URL: `<CONECTOR>_URL`, `<CONECTOR>_TOKEN`, `<CONECTOR>_USER`,
`<CONECTOR>_PASSWORD`. Cualquier otra variable `<CONECTOR>_*` queda disponible para el cliente
del conector con `self.api.extra("NOMBRE")` (ej. `ODOO_DATABASE`, `OPEN_METEO_LATITUD`).

Parámetros de base de datos y carga (todos opcionales): `STAGE_BATCH_SIZE`,
`STAGE_INSERT_CHUNKSIZE`, `STAGE_INSERT_MAX_RETRY`, `STAGE_INSERT_RETRY_ESPERA_SEGUNDOS`,
`DWH_READ_CHUNKSIZE`, `DWH_INSERT_CHUNKSIZE`, `DWH_CARGAR_CON_FALLIDOS`,
`DB_POOL_RECYCLE_SEGUNDOS`, `DB_CONNECT_TIMEOUT`, `DB_STATEMENT_TIMEOUT_MS`. Logs:
`LOG_NIVEL_CONSOLA`, `LOG_BUFFER_CAPACIDAD`, `LOG_ARCHIVO_EJECUCION`, `LOG_RETENCION_DIAS`.

Toda la configuración se valida al inicio: un valor inválido (ej. `BSALE_MAX_WORKERS=abc`) o una
variable obligatoria faltante corta la ejecución con un mensaje claro, antes de tocar las bases.

### Agregar un cliente nuevo

1. `cp .env.example .env.<cliente>` y completar credenciales, `MODULOS` y `CONECTORES`.
2. Ajustar los parámetros de API de sus conectores si la API del cliente lo requiere.
3. Crear las bases STAGE/DWH (las tablas se crean solas en la primera ejecución).
4. `./run_main.sh <cliente>`.

Los `.env.<cliente>` están en `.gitignore`; solo se versionan `.env.example` y `.env.demo`.

---

## 5. Ejecución: `run_main.sh`

En Linux, la primera vez: `chmod +x run_main.sh` (o ejecutarlo con `bash run_main.sh ...`).

```bash
# Automático: últimos MESES_AUTOMATICO meses (default 2) hasta hoy
./run_main.sh demo            # todos los módulos de MODULOS
./run_main.sh demo_f          # FINANCIERO
./run_main.sh demo_c          # COMERCIAL
./run_main.sh demo_o          # OPERACIONAL
./run_main.sh demo_ot         # OTROS

# Manual: rango de fechas
./run_main.sh demo 2026-06-01 2026-08-13
./run_main.sh demo_f 2026-06-01 2026-08-13

# Un único endpoint (requiere sufijo de módulo)
./run_main.sh demo_f MINDICADOR_UF
./run_main.sh demo_f 2026-06-01 2026-08-13 MINDICADOR_UF
./run_main.sh demo_o JSONPLACEHOLDER_COMMENTS    # hijo: usa los padres ya cargados en STAGE
```

| Sufijo | Módulo |
|---|---|
| (ninguno) | Todos los habilitados en `MODULOS` del `.env`, en orden COMERCIAL, FINANCIERO, OPERACIONAL, OTROS |
| `_f` | FINANCIERO |
| `_c` | COMERCIAL |
| `_o` | OPERACIONAL |
| `_ot` | OTROS |

- Valida formato de fechas, que `inicio <= fin`, que exista el `.env` y que un endpoint puntual
  lleve sufijo de módulo.
- Un módulo con error **no detiene** a los siguientes.
- Intérprete: `PYTHON_BIN` si está definido; si no, `pipenv run python`; si no, `.venv`; si no,
  `python3`/`python`.
- `RUN_MAIN_PAUSE=0 ./run_main.sh demo` evita el "Presiona ENTER" al final (cron / tareas
  programadas).

Contrato interno (igual que Bsale): `python main.py <cliente> <MODULO> <inicio|''> <fin|''> [ENDPOINT]`.

Códigos de salida de `main.py`: `0` correcto (o sin endpoints / cliente inactivo), `1` error
crítico o de configuración, `2` finalizado con errores, `130` interrumpido con Ctrl+C.

---

## 6. Endpoints: `EndpointConfig`

Cada endpoint se declara en `app/connectors/<conector>/endpoints.py`. Los campos más usados:

```python
EndpointConfig(
    nombre="BSALE_DOCUMENTS",          # único entre todos los conectores
    conector="bsale",
    endpoint="documents.json",         # ruta (o modelo en Odoo); admite {id} {desde} {hasta} {anio} {mes}
    tabla="com_api_bsale_documents",   # misma tabla en STAGE y DWH
    modulo="COMERCIAL",                # FINANCIERO / COMERCIAL / OPERACIONAL / OTROS / TODOS / INACTIVO
    parametros={"state": 0},           # query params fijos
    paginacion="offset",               # offset | page | none
    page_size=None,                    # override de <CONECTOR>_PAGE_SIZE

    campo_fecha="emissionDate",        # columna para borrar/traspasar el rango (sin él → carga completa)
    filtro_fecha_api="emissiondaterange",
    tipo_filtro_fecha_api="range_unix",   # range_unix | unix | desde_hasta | ruta | dominio
    particion_fecha=None,              # dia | semana | mes | anio → tramos en paralelo
    filtrar_rango_local=False,         # descarta registros fuera de rango (APIs que devuelven de más)
    campos_fecha_unix=["emissionDate"],   # guarda <campo>Unix + <campo> como texto

    tipos_stage={"id": "BIGINT"},      # tipos explícitos (el resto se infiere)
    llave_primaria=None,               # None → "id" si viene; o tupla compuesta
    campos_dwh=None,                   # subconjunto de columnas para DWH (None → todas)
    cargar_dwh=True,                   # False → solo STAGE (tablas de apoyo que el BI no usa)

    padre=None, columna_fk=None, campos_padre=None,   # endpoints hijo
    opciones={},                       # parámetros propios del conector (fields/domain de Odoo)
)
```

La configuración se valida al importar. Por ejemplo, un filtro de fecha sin `campo_fecha`, un
hijo sin `{id}` en la ruta o un hijo que no hereda la fecha de un padre fechado fallan con un
mensaje explícito.

### Tipos de filtro de fecha

| `tipo_filtro_fecha_api` | Ejemplo | Resultado |
|---|---|---|
| `range_unix` | Bsale | `?emissiondaterange=[1751328000,1754006399]` |
| `unix` | Bsale día exacto (con `particion_fecha="dia"`) | `?shippingdate=1751328000` |
| `desde_hasta` | Open-Meteo, `filtro_fecha_api=("start_date","end_date")` | `?start_date=2026-07-01&end_date=2026-07-31` |
| `ruta` | Frankfurter `"{desde}..{hasta}"`, mindicador `"uf/{anio}"` | fechas en la URL |
| `dominio` | Odoo | `[["date", ">=", "..."], ["date", "<", "..."]]` |

### Endpoints hijo (encadenados)

Igual que `DOCUMENTS → DOCUMENT_DETAILS` en Bsale, pero genérico:

```python
EndpointConfig(
    nombre="BSALE_DOCUMENT_DETAILS",
    endpoint="documents/{id}/details.json",
    padre="BSALE_DOCUMENTS",           # se leen sus ids desde STAGE (en el rango, si tiene fecha)
    columna_fk="document_id",          # se agrega a cada detalle
    campos_padre=["emissionDate", "emissionDateUnix"],   # se copian del padre
    campo_fecha="emissionDate",        # heredado: permite borrar/traspasar el mismo rango
    ...
)
```

- En una corrida completa, el hijo se ejecuta **justo después** de su padre y solo si el padre
  terminó OK. Si el padre falla, el hijo queda `OMITIDO` y se registra en procesos.
- Se admiten varios niveles (hijo de un hijo).
- Si se ejecuta el hijo solo (`./run_main.sh cliente_c BSALE_DOCUMENT_DETAILS`), usa los padres ya
  cargados en STAGE.

---

## 7. Concurrencia, paginación y reintentos

### Cómo se descarga un endpoint

| Caso | Estrategia |
|---|---|
| La API informa el total (`count`, `X-Total-Count`, `search_count`) | 1ª página → resto de páginas **en paralelo** (`MAX_WORKERS`) con barra por registros. |
| La API no informa total | Paginación secuencial hasta recibir una página incompleta o vacía. |
| `particion_fecha` definida | Un tramo por día/semana/mes/año **en paralelo**, con barra por tramos. |
| Endpoint hijo | Un request (paginado) por cada padre **en paralelo**, con barra por padre. |

Los workers solo descargan. La inserción en STAGE y la barra se hacen en el hilo principal, por
lo que no hay escrituras concurrentes a la base. El paso de paginación usa el tamaño **real** de
la primera página, por si la API devuelve menos de lo pedido.

### Tres niveles de reintento

1. **Request HTTP** (`MAX_RETRY`): errores de conexión/timeout y `STATUS_REINTENTABLES` con
   backoff lineal + jitter; `429` respetando `retry_after` (body) o `Retry-After` (header).
2. **Items fallidos** (páginas, tramos, padres): tras terminar el lote se hace **una segunda
   pasada** solo sobre los pendientes. Lo que siga fallando se escribe en
   `error/api_fallida_<cliente>_<modulo>_<endpoint>_<ts>.log`, el endpoint queda OK con
   `fallidos > 0`, se registra en procesos y **no se traspasa a DWH**
   (salvo `DWH_CARGAR_CON_FALLIDOS=1`), para no dejar el DWH con un rango incompleto.
3. **Endpoint completo** (`ENDPOINT_MAX_RETRY`): si algo lanza excepción (incluida la base), se
   repite borrar + descargar + cargar. Si se agota, queda `ERROR` en procesos y la corrida sigue
   con los demás endpoints.

Protecciones adicionales: `MAX_REQUESTS_POR_MINUTO` (rate limit uniforme entre workers),
`MAX_PAGINAS` y detección de página repetida (API que ignora offset), pool HTTP dimensionado según
los workers y cancelación de la cola si falla la inserción o se presiona Ctrl+C.


### Barras de avance

| Barra | Cuándo | Ejemplo |
|---|---|---|
| `📥 <ENDPOINT>` | Cada endpoint: por registros (también cuando todo llega en una sola página, al 100%), por tramos o por registros padre | `📥 SMARTSHEET_LATAM [██████████] 100% (141/141 registros)` |
| `📦 AVANCE <MODULO>` | Avance del módulo, una línea por endpoint terminado | `📦 AVANCE FINANCIERO [█████░░░░░]  50% (3/6 endpoints) ⏱️ 00:04 ⏳ 00:04` |
| `🏭 AVANCE DWH` | Traspaso STAGE → DWH, una línea por tabla | `🏭 AVANCE DWH [██████░░░░]  66% (2/3 tablas)` |

En una terminal interactiva la barra por endpoint se redibuja en el lugar (`\r`). Si la salida
está redirigida (cron, archivo, Git Bash en Windows) se escribe como línea de log con la misma
barra. Las barras de módulo y DWH siempre se escriben como línea de log, para no quedar cortadas
por los mensajes entre endpoints.

---

## 8. Ciclo de vida en STAGE y DWH

**STAGE**, antes de descargar cada endpoint:
- con `campo_fecha` → `DELETE` del rango `[inicio 00:00:00, fin+1 00:00:00)`;
- sin `campo_fecha` → `TRUNCATE` (carga completa).

La carga es incremental: se inserta cada `STAGE_BATCH_SIZE` registros, cada lote en su propia
transacción y con reintento ante cortes de conexión. Además:
- la tabla se crea sola con los tipos de `tipos_stage` y el resto inferido de **todos** los
  valores del lote (enteros + decimales → `NUMERIC`, texto → `TEXT`, etc.);
- las columnas nuevas que aparezcan en la API se agregan con `ALTER TABLE`;
- el insert es un **upsert** por clave primaria (sin duplicados si la API repite registros entre
  páginas). Los duplicados dentro de un mismo lote se resuelven conservando el último;
- los nombres de columna se normalizan a `snake_case` en minúscula, con un máximo de 63
  caracteres (límite de Postgres);
- las filas por INSERT se ajustan solas al límite de 65.535 parámetros de Postgres.

**DWH**, por cada endpoint OK con `cargar_dwh=True` (en una sola transacción, con rollback si
falla):
1. crea o ajusta la tabla **con los mismos tipos de STAGE**;
2. `DELETE` del rango (o `TRUNCATE` si no tiene `campo_fecha`);
3. inserta leyendo STAGE por bloques de `DWH_READ_CHUNKSIZE` (no carga todo en memoria); los
   `NUMERIC` se copian como Decimal, sin pasar por float (sin `49920.0` ni pérdida de precisión);
4. `GRANT SELECT` a `DWH_GRANT_USER`.

Si STAGE no tiene datos en el rango, el DWH **no se borra** (`SIN DATOS`), igual que en los
proyectos de origen.

Las conexiones usan `pool_pre_ping`, `pool_recycle` (`DB_POOL_RECYCLE_SEGUNDOS`), keepalives TCP y
`URL.create` (contraseñas con `@`, `:` o `/` no rompen la conexión).

---

## 9. Logs y carpetas

| Destino | Contenido |
|---|---|
| Consola | Nivel `LOG_NIVEL_CONSOLA` (INFO): íconos por nivel (ℹ️ ⚠️ ❌ 🔥), eventos con emoji (🚀 📋 🗑️ 📥 ✅ 💥 🔁 🏭 🏁 📊) y barra de avance `[████░░░░] 60% (150/250 registros) ⏱️ 00:12 ⏳ 00:08`. |
| `error/<cliente>_<modulo>_<ts>.log` | Se crea **solo si hay un ERROR**. Contiene las últimas `LOG_BUFFER_CAPACIDAD` líneas DEBUG (buffer circular), con el detalle previo a la falla. |
| `error/api_fallida_*.log` | Una línea por URL que no se pudo descargar tras todos los reintentos, con el motivo. |
| `logs/<cliente>_<modulo>_<ts>.log` | Log completo INFO+ de la ejecución (`LOG_ARCHIVO_EJECUCION=1`). |

- Si la salida no es una terminal (cron, archivo, Git Bash), la barra se escribe como línea de
  log (ver "Barras de avance" en la sección 7).
- Los `.log` de más de `LOG_RETENCION_DIAS` días se borran al iniciar.
- La consola se fuerza a UTF-8 para que los emojis no rompan la ejecución en Windows.
- Al final de cada módulo se imprime un **resumen** por endpoint (estado, registros, fallidos,
  resultado DWH y segundos).

---

## 10. Tabla de control `procesos_dev`

DDL en `sql/procesos_dev.sql`. Se configura con `PS_PROCESOS_SCHEMA` y `PS_PROCESOS_TABLA`.

| Fila | `estado` | `accion` |
|---|---|---|
| Una por ejecución (cliente + módulo) | `EN EJECUCION` → `FINALIZADO CORRECTAMENTE` / `FINALIZADO CON ERRORES` / `SIN ENDPOINTS` / `ERROR_CRITICO` / `INTERRUMPIDO` | `AUTOMATICO\|MANUAL [\| ENDPOINT=X] \| RANGO=inicio..fin \| MODULO=M [\| ERRORES=n]` |
| Una por endpoint con problemas | `ERROR` | `ERROR ENDPOINT \| MODULO \| CONECTOR \| ENDPOINT \| RUTA \| TABLA \| HORA \| RUN_ID \| ERROR=...` |

Además se registran `fecha`, `fecha_fin` y `tiempo_ejecucion` (minutos) en la zona horaria del
cliente. Cuentan como error: un endpoint que agotó sus reintentos, uno con requests fallidos, un
hijo omitido por falla del padre y un error en el traspaso a DWH.

---

## 11. Extender el proyecto

### Agregar un endpoint a un conector existente

Agregar un `EndpointConfig` a `app/connectors/<conector>/endpoints.py`:

```python
EndpointConfig(
    nombre="BSALE_CLIENTS",
    conector="bsale",
    endpoint="clients.json",
    tabla="com_api_bsale_clients",
    modulo="COMERCIAL",
    tipos_stage={"id": "BIGINT", "maxCredit": "NUMERIC"},
),
```

Sin `campo_fecha` es una carga completa. Para cargar por rango, se agrega `campo_fecha` y el
filtro de la API.

### Agregar un conector (API nueva)

1. Crear `app/connectors/<nombre>/__init__.py`, `client.py` y `endpoints.py`.
2. En `client.py`, heredar de `BaseApiClient`:

```python
from app.core.http_client import BaseApiClient

class Cliente(BaseApiClient):
    nombre = "miapi"
    prefijo_env = "MIAPI"                 # MIAPI_URL, MIAPI_TOKEN, MIAPI_MAX_WORKERS...
    url_default = "https://api.miapi.com/v2"
    requiere = ("URL", "TOKEN")
    defaults = {"PAGE_SIZE": 200, "MAX_REQUESTS_POR_MINUTO": 120}

    param_offset = "skip"                 # nombres de los parámetros de paginación
    param_limit = "take"
    ruta_items_default = "data.results"   # dónde vienen los registros
    ruta_total_default = "data.total"     # dónde viene el total (None si no hay)
    page_size_maximo = 500

    def configurar_sesion(self, session):
        session.headers["Authorization"] = f"Bearer {self.api.token}"
```

   Si la API no sigue el patrón REST paginado, se sobrescriben solo los hooks necesarios:
   `obtener_pagina` (ej. Odoo usa POST), `extraer_items` (Frankfurter, Open-Meteo),
   `extraer_total` (JSONPlaceholder, header), `construir_filtro_fecha` o `preparar_registro`.
3. Registrar el conector en `app/connectors/registry.py` → `CONECTORES_DISPONIBLES`.
4. Habilitarlo en el `.env` del cliente: `CONECTORES=...,miapi` + `MIAPI_URL`, `MIAPI_TOKEN`.

Autenticaciones que requieren login previo (usuario/clave → token) se resuelven en
`configurar_sesion`, usando `self.api.usuario` y `self.api.password` (`MIAPI_USER` /
`MIAPI_PASSWORD`).

---

## 12. Probar en local con Docker

```bash
# 1. Dependencias
pipenv install

# 2. Postgres de prueba (puerto 55432 para no chocar con uno local)
docker run -d --name biwiser_pg -e POSTGRES_PASSWORD=postgres -p 55432:5432 postgres:16-alpine
docker exec biwiser_pg psql -U postgres -c "CREATE DATABASE demo_stage" \
    -c "CREATE DATABASE demo_dwh" -c "CREATE DATABASE procesos"
docker exec -i biwiser_pg psql -U postgres -d procesos < sql/procesos_dev.sql

# 3. En .env.demo cambiar los *_PORT=5432 por 55432 y ejecutar
./run_main.sh demo

# 4. Revisar
docker exec biwiser_pg psql -U postgres -d procesos \
    -c "SELECT id, estado, tiempo_ejecucion, accion FROM procesos_dev ORDER BY id DESC LIMIT 10"
```

Sin bases de datos se pueden probar solo los conectores con `PROCESOS_HABILITADO=0` y
`DWH_HABILITADO=0`, pero STAGE sigue siendo obligatorio.

---

## 13. Diferencias con los proyectos de origen

| Tema | bsale / odoo | base |
|---|---|---|
| APIs | Una API por proyecto | Varios conectores por cliente (`CONECTORES`) |
| Parámetros de rendimiento | Constantes en el código | `.env` por cliente y por conector |
| Endpoints encadenados | Código específico por cadena | Genérico (`padre`, `columna_fk`, `campos_padre`) |
| Módulos en `run_main.sh` sin sufijo | Siempre los 3 | Solo los de `MODULOS` del `.env` |
| Tipos DWH | Inferidos por pandas | Copiados de STAGE |
| Lectura STAGE → DWH | Tabla completa en memoria | Por bloques |
| Endpoint con requests fallidos | Se traspasa a DWH igual | No se traspasa (configurable) |
| Tabla de procesos | `public.procesos_dev` fija | Configurable (`PS_PROCESOS_SCHEMA/TABLA`) |
| `GRANT` DWH | Usuario fijo `biwiser` | `DWH_GRANT_USER` |
| Salida con errores | exit 0 | exit 2 (detectable en cron) |
