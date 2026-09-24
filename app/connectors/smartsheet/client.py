import logging
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.core.http_client import BaseApiClient, Pagina
from app.database.stage_loader import normalizar_nombre_columna


log = logging.getLogger(__name__)


# Columnas de metadata que se agregan a cada fila. Llevan prefijo para
# no chocar con columnas de la hoja (ej. una columna llamada "ID").
COLUMNAS_METADATA = (
    "row_id",
    "row_number",
    "row_parent_id",
    "row_sibling_id",
    "row_expanded",
    "row_locked",
    "row_created_at",
    "row_modified_at",
    "sheet_id",
    "sheet_name",
    "sheet_permalink",
    "sheet_version",
    "sheet_total_row_count",
    "sheet_created_at",
    "sheet_modified_at",
    "workspace_id",
    "workspace_name",
)

TIPOS_FECHA = {"DATE"}
TIPOS_FECHA_HORA = {"DATETIME", "ABSTRACT_DATETIME"}
TIPOS_BOOLEANOS = {"CHECKBOX"}
TIPOS_CONTACTO = {"CONTACT_LIST", "MULTI_CONTACT_LIST"}

# "13.124.300" / "1.234,5" (formato es-CL) o "1234.5" / "-12".
_NUMERO_ES_CL = re.compile(r"^-?\d{1,3}(\.\d{3})+(,\d+)?$|^-?\d+,\d+$")
_NUMERO_SIMPLE = re.compile(r"^-?\d+(\.\d+)?$")


def nombre_columna(titulo):
    """
    Título de columna Smartsheet → nombre de columna en STAGE/DWH:
    sin tildes, "#" como "num_", minúsculas y snake_case.
      "Descripción" → descripcion | "#OC" → num_oc | "Fecha P." → fecha_p
    """

    texto = unicodedata.normalize("NFKD", str(titulo))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.replace("#", " num ")

    return normalizar_nombre_columna(texto)


class Cliente(BaseApiClient):
    """
    Smartsheet API 2.0 (https://smartsheet.redoc.ly).

    - Autenticación: header `Authorization: Bearer <token>`.
    - Cada endpoint es una hoja: GET /sheets/{sheetId}. El sheetId puede
      ser el id numérico o el token del permalink
      (app.smartsheet.com/sheets/<token>).
    - Paginación por `page` / `pageSize`; la respuesta informa
      `totalRowCount`, así que las páginas se descargan en paralelo.
    - Las filas vienen como celdas por columnId: se aplanan a un registro
      {titulo_columna: valor} usando `columns` de la misma respuesta, más
      la metadata de la fila, la hoja y el workspace (COLUMNAS_METADATA).
      Las columnas se llaman como el título de la hoja (sin renombrar).
      Contactos agregan <columna>_nombre y celdas con link <columna>_url.
    - opciones["recurso"] = "columnas": diccionario de columnas de la hoja
      (GET /sheets/{id}/columns) con descripción, tipo y fórmula.
    - Se usa `value` (valor crudo, sin el formato regional de
      `displayValue`). Tipos por columna:
        DATE              → date
        DATETIME / ABSTRACT_DATETIME → timestamp (UTC)
        CHECKBOX          → boolean (celda vacía = False)
        números           → Decimal exacto (125.0 → 125): una columna
                            solo numérica queda NUMERIC y una mixta
                            (ej. "#R") queda TEXT sin ".0".
    - opciones["numericos"]: columnas donde además se convierte el texto
      con formato numérico ("13.124.300" → 13124300).
    - Advertencias (quedan en procesos con estado ADVERTENCIA, ver
      BaseApiClient.advertir):
        * texto que no es número en una columna de `numericos` → NULL;
        * texto que no es fecha en una columna DATE / DATETIME → NULL;
        * columna de la hoja sin tipo declarado en `tipos` (el tipo se
          infiere y la carga podría fallar el día que cambien los datos).
    - Rate limit de Smartsheet: 300 requests/minuto por token.
    """

    nombre = "smartsheet"
    prefijo_env = "SMARTSHEET"
    url_default = "https://api.smartsheet.com/2.0"
    requiere = ("URL", "TOKEN")

    defaults = {
        # Con 5000 la mayoría de las hojas se baja en una sola request
        # (una foto consistente de la hoja).
        "PAGE_SIZE": 5000,
        "MAX_WORKERS": 3,
        "MAX_REQUESTS_POR_MINUTO": 250,
    }

    param_page = "page"
    param_limit = "pageSize"
    pagina_inicial = 1
    ruta_items_default = "rows"
    ruta_total_default = "totalRowCount"

    def configurar_sesion(self, session):
        token = self.api.token

        if not token.lower().startswith("bearer "):
            token = f"Bearer {token}"

        session.headers.update({"Authorization": token})

    def obtener_pagina(self, endpoint, contexto):
        if endpoint.opciones.get("recurso") == "columnas":
            return self._obtener_columnas(endpoint)

        params = dict(endpoint.parametros)
        params[self.param_page] = contexto.pagina
        params[self.param_limit] = contexto.limite

        respuesta = self.request(
            "GET",
            self.construir_ruta(endpoint, contexto.variables_ruta),
            params=params,
        )

        hoja = respuesta.data

        if not isinstance(hoja, dict) or "columns" not in hoja:
            raise RuntimeError(
                f"Respuesta inválida de Smartsheet para '{endpoint.nombre}': "
                "no es una hoja (falta 'columns')."
            )

        columnas = mapa_columnas(hoja["columns"])
        numericos = {
            nombre_columna(titulo)
            for titulo in endpoint.opciones.get("numericos", [])
        }
        metadata_hoja = _metadata_hoja(hoja)

        self._advertir_columnas_sin_tipo(endpoint, hoja["columns"], columnas)

        items = [
            self._fila_a_registro(endpoint, fila, columnas, metadata_hoja, numericos)
            for fila in hoja.get("rows") or []
        ]

        return Pagina(
            items=items,
            total=self.extraer_total(endpoint, respuesta),
        )

    def _advertir_columnas_sin_tipo(self, endpoint, definiciones, columnas):
        """
        Columnas de la hoja que no están en `tipos`: su tipo se infiere de
        los datos de la primera carga. Checkbox se omite (siempre boolean).
        """

        declarados = set(endpoint.tipos_stage)
        titulos = {c["id"]: c.get("title") for c in definiciones}

        for column_id, (nombre, tipo, _contacto) in columnas.items():
            if nombre in declarados or tipo in TIPOS_BOOLEANOS:
                continue

            self.advertir(
                endpoint,
                f"COLUMNA SIN TIPO DECLARADO | columna={nombre} | "
                f"titulo={titulos.get(column_id)!r} | tipo_smartsheet={tipo} | "
                "declararla en tipos de endpoints.py",
            )

    # ------------------------------------------------------------------
    # DICCIONARIO DE COLUMNAS
    # ------------------------------------------------------------------

    def _obtener_columnas(self, endpoint):
        """
        Una fila por columna de la hoja: título, nombre en BD, descripción,
        tipo, fórmula, opciones... Sirve para entender títulos cortos
        ("F", "CC", "#R") sin abrir Smartsheet.

        Se lee de GET /sheets/{id} con pageSize=1 (trae la definición
        completa de columnas): /sheets/{id}/columns no acepta el token del
        permalink, solo el id numérico.
        """

        hoja = self.request(
            "GET",
            endpoint.endpoint,
            params={"page": 1, "pageSize": 1},
        ).data

        if not isinstance(hoja, dict) or "columns" not in hoja:
            raise RuntimeError(
                f"Respuesta inválida de Smartsheet para '{endpoint.nombre}': "
                "no es una hoja (falta 'columns')."
            )

        columnas = hoja["columns"]
        sheet_id = hoja.get("id")
        sheet_name = hoja.get("name")
        mapa = mapa_columnas(columnas)
        items = []

        for columna in columnas:
            nombre, tipo, nombre_contacto = mapa[columna["id"]]

            items.append({
                "column_id": columna.get("id"),
                "sheet_id": sheet_id,
                "sheet_name": sheet_name,
                "column_index": columna.get("index"),
                "column_title": columna.get("title"),
                "columna_bd": nombre,
                "columna_bd_nombre_contacto": nombre_contacto,
                "column_description": columna.get("description"),
                "column_type": tipo,
                "column_formula": columna.get("formula"),
                "column_options": columna.get("options"),
                "column_primary": bool(columna.get("primary")),
                "column_validation": bool(columna.get("validation")),
                "column_locked": bool(columna.get("locked")),
                "column_hidden": bool(columna.get("hidden")),
                "column_system_type": columna.get("systemColumnType"),
                "column_symbol": columna.get("symbol"),
                "column_width": columna.get("width"),
                "column_version": columna.get("version"),
            })

        return Pagina(items=items, total=len(items))

    # ------------------------------------------------------------------
    # CONVERSIÓN HOJA → REGISTROS
    # ------------------------------------------------------------------

    def _fila_a_registro(self, endpoint, fila, columnas, metadata_hoja, numericos):
        registro = {
            "row_id": fila.get("id"),
            "row_number": fila.get("rowNumber"),
            "row_parent_id": fila.get("parentId"),
            "row_sibling_id": fila.get("siblingId"),
            "row_expanded": fila.get("expanded"),
            "row_locked": bool(fila.get("locked")),
            "row_created_at": _a_fecha_hora(fila.get("createdAt")),
            "row_modified_at": _a_fecha_hora(fila.get("modifiedAt")),
            **metadata_hoja,
        }

        # Todas las columnas quedan en el registro aunque la celda venga
        # vacía, para que la tabla tenga siempre el mismo esquema.
        for nombre, tipo, nombre_contacto in columnas.values():
            registro[nombre] = False if tipo in TIPOS_BOOLEANOS else None

            if nombre_contacto:
                registro[nombre_contacto] = None

        for celda in fila.get("cells") or []:
            columna = columnas.get(celda.get("columnId"))

            if columna is None:
                continue

            nombre, tipo, nombre_contacto = columna
            valor = _convertir_valor(celda.get("value"), tipo)

            if nombre in numericos and isinstance(valor, str):
                numero = _texto_a_numero(valor)

                if numero is None:
                    self.advertir(
                        endpoint,
                        f"VALOR NO NUMÉRICO → NULL | fila={fila.get('rowNumber')} | "
                        f"columna={nombre} | valor={valor!r}",
                    )

                valor = numero

            elif tipo in TIPOS_FECHA | TIPOS_FECHA_HORA and isinstance(valor, str):
                # _convertir_valor devuelve el texto original si no es una
                # fecha válida: insertarlo haría fallar toda la hoja.
                self.advertir(
                    endpoint,
                    f"VALOR NO ES FECHA → NULL | fila={fila.get('rowNumber')} | "
                    f"columna={nombre} | valor={valor!r}",
                )
                valor = None

            registro[nombre] = valor

            # Contactos: value es el email; displayValue, el nombre.
            if nombre_contacto:
                registro[nombre_contacto] = celda.get("displayValue")

            # Celdas con link (a una URL u otra hoja/reporte).
            hyperlink = celda.get("hyperlink")

            if isinstance(hyperlink, dict):
                registro[f"{nombre}_url"] = (
                    hyperlink.get("url")
                    or hyperlink.get("sheetId")
                    or hyperlink.get("reportId")
                )

        return registro

    def preparar_registro(self, endpoint, registro):
        # Los registros ya vienen planos desde obtener_pagina.
        return registro


def mapa_columnas(columnas):
    """
    {columnId: (nombre, tipo, nombre_contacto)}. El nombre es el título de
    la columna en Smartsheet normalizado (`nombre_columna`): la tabla usa
    los mismos nombres que se ven en la hoja. Títulos que colisionan tras
    normalizar reciben sufijo _2, _3... Las columnas de contacto agregan
    <nombre>_nombre con el nombre visible del contacto.
    """

    usados = set(COLUMNAS_METADATA)
    mapa = {}

    def reservar(nombre):
        base, sufijo = nombre, 2

        while nombre in usados:
            nombre = f"{base}_{sufijo}"
            sufijo += 1

        usados.add(nombre)
        return nombre

    for columna in sorted(columnas, key=lambda c: c.get("index", 0)):
        titulo = (columna.get("title") or "").strip()
        tipo = (columna.get("type") or "").upper()
        nombre = reservar(nombre_columna(titulo or f"col_{columna['id']}"))
        nombre_contacto = (
            reservar(f"{nombre}_nombre") if tipo in TIPOS_CONTACTO else None
        )
        mapa[columna["id"]] = (nombre, tipo, nombre_contacto)

    return mapa


def _metadata_hoja(hoja):
    workspace = hoja.get("workspace") or {}

    return {
        "sheet_id": hoja.get("id"),
        "sheet_name": hoja.get("name"),
        "sheet_permalink": hoja.get("permalink"),
        "sheet_version": hoja.get("version"),
        "sheet_total_row_count": hoja.get("totalRowCount"),
        "sheet_created_at": _a_fecha_hora(hoja.get("createdAt")),
        "sheet_modified_at": _a_fecha_hora(hoja.get("modifiedAt")),
        "workspace_id": workspace.get("id"),
        "workspace_name": workspace.get("name"),
    }


# ============================================================================
# HELPERS
# ============================================================================

def _convertir_valor(valor, tipo):
    if valor is None:
        return False if tipo in TIPOS_BOOLEANOS else None

    if tipo in TIPOS_BOOLEANOS:
        return bool(valor)

    if tipo in TIPOS_FECHA and isinstance(valor, str):
        try:
            return date.fromisoformat(valor[:10])
        except ValueError:
            return valor

    if tipo in TIPOS_FECHA_HORA and isinstance(valor, str):
        return _a_fecha_hora(valor) or valor

    # Smartsheet entrega todos los números como float: se pasan a Decimal
    # (repr corto: 346071.52, no 346071.519999...) para guardarlos exactos
    # y para que 125.0 quede como 125.
    if isinstance(valor, float):
        if valor != valor or valor in (float("inf"), float("-inf")):
            return None

        numero = Decimal(repr(valor))
        return numero.to_integral_value() if numero == numero.to_integral_value() else numero

    if isinstance(valor, int) and not isinstance(valor, bool):
        return Decimal(valor)

    return valor


def _texto_a_numero(texto):
    limpio = texto.strip().replace("$", "").replace(" ", "")

    if _NUMERO_ES_CL.match(limpio):
        limpio = limpio.replace(".", "").replace(",", ".")
    elif not _NUMERO_SIMPLE.match(limpio):
        return None

    try:
        return Decimal(limpio)
    except InvalidOperation:
        return None


def _a_fecha_hora(valor):
    """'2025-02-11T16:27:21Z' → datetime UTC sin zona (None si no aplica)."""

    if not isinstance(valor, str) or not valor:
        return None

    try:
        return (
            datetime.fromisoformat(valor.replace("Z", "+00:00"))
            .replace(tzinfo=None)
        )
    except ValueError:
        return None
