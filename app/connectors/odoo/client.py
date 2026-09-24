import logging

from app.core.http_client import BaseApiClient, Pagina
from app.utils import fechas


log = logging.getLogger(__name__)


class Cliente(BaseApiClient):
    """
    Odoo External JSON-2 API (Odoo 19+): POST /json/2/<modelo>/<método>.

    - Autenticación: header `Authorization: bearer <api_key>`.
    - Base de datos opcional: ODOO_DATABASE → header X-Odoo-Database
      (necesario si el servidor aloja varias bases).
    - `search_read` con domain/fields/limit/offset/order.
    - Antes de la primera página se consulta `search_count`: con el
      total conocido, el motor descarga las páginas en paralelo (si
      falla, se pagina secuencialmente).
    - Si campo_fecha es una fecha de modificación (write_date), usar
      dwh_recarga_completa=True en el endpoint (ver core/endpoint.py).
    - Many2one ([id, "nombre"]) se linealiza: campo + campo_name.
    - Odoo devuelve `False` en campos vacíos (texto, fecha, relación):
      se convierten a NULL salvo en campos declarados BOOLEAN.
    """

    nombre = "odoo"
    prefijo_env = "ODOO"
    requiere = ("URL", "TOKEN")

    defaults = {
        "PAGE_SIZE": 1000,
        "MAX_WORKERS": 3,
    }

    def configurar_sesion(self, session):
        token = self.api.token

        if not token.lower().startswith("bearer "):
            token = f"bearer {token}"

        session.headers.update({
            "Content-Type": "application/json",
            "Authorization": token,
        })

        database = self.api.extra("DATABASE")

        if database:
            session.headers["X-Odoo-Database"] = database

    def construir_dominio(self, endpoint, desde, hasta):
        dominio = list(endpoint.opciones.get("domain", []))

        if endpoint.tipo_filtro_fecha_api == "dominio" and desde is not None:
            campo = endpoint.filtro_fecha_api or endpoint.campo_fecha

            dominio += [
                [campo, ">=", f"{desde} 00:00:00"],
                [campo, "<", f"{fechas.fin_exclusivo(hasta):%Y-%m-%d} 00:00:00"],
            ]

        return dominio

    def obtener_pagina(self, endpoint, contexto):
        modelo = endpoint.endpoint
        dominio = self.construir_dominio(endpoint, contexto.desde, contexto.hasta)

        total = None

        if contexto.offset == 0:
            # Sin el total el motor pagina de forma secuencial: un
            # search_count caído no debe hacer fallar todo el endpoint.
            try:
                total = self.request(
                    "POST",
                    f"{modelo}/search_count",
                    json={"domain": dominio},
                ).data
            except Exception as exc:
                log.warning(
                    "⚠️ %s | search_count no disponible, paginación "
                    "secuencial | error=%s",
                    endpoint.nombre,
                    exc,
                )

        payload = {
            "domain": dominio,
            "limit": contexto.limite,
            "offset": contexto.offset,
            "order": endpoint.opciones.get(
                "order",
                f"{endpoint.campo_fecha} asc, id asc"
                if endpoint.campo_fecha
                else "id asc",
            ),
        }

        campos = endpoint.opciones.get("fields")

        if campos:
            payload["fields"] = campos

        data = self.request("POST", f"{modelo}/search_read", json=payload).data

        if not isinstance(data, list):
            raise RuntimeError(
                f"Respuesta inesperada de Odoo para {modelo}: {type(data)}"
            )

        return Pagina(
            items=data,
            total=int(total) if isinstance(total, int) else None,
        )

    def preparar_registro(self, endpoint, registro):
        relacionales = set(endpoint.opciones.get("relacionales", []))
        booleanos = {
            campo
            for campo, tipo in endpoint.tipos_stage.items()
            if tipo.upper() == "BOOLEAN"
        }

        resultado = {}

        for campo, valor in registro.items():
            if campo in relacionales or (
                isinstance(valor, list)
                and len(valor) == 2
                and isinstance(valor[0], int)
                and isinstance(valor[1], str)
            ):
                if isinstance(valor, list) and len(valor) == 2:
                    resultado[campo] = valor[0]
                    resultado[f"{campo}_name"] = valor[1]
                else:
                    resultado[campo] = None
                    resultado[f"{campo}_name"] = None
                continue

            if valor is False and campo not in booleanos:
                valor = None

            resultado[campo] = valor

        return resultado
