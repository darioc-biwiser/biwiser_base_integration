from app.core.endpoint import EndpointConfig


# ============================================================================
# BSALE
# ============================================================================
#
# Ejemplo de cadena padre → hijo (igual que en biwiser_bsale_integration):
#
#   BSALE_DOCUMENTS          documents.json, filtrado por emissiondaterange
#     └─ BSALE_DOCUMENT_DETAILS  documents/{id}/details.json por cada
#                                documento cargado en STAGE en el rango
#
# El hijo hereda emissionDate del padre para poder borrar/traspasar su
# mismo rango de fechas en STAGE y DWH.
# ============================================================================

ENDPOINTS = [

    EndpointConfig(
        nombre="BSALE_DOCUMENTS",
        conector="bsale",
        endpoint="documents.json",
        tabla="com_api_bsale_documents",
        modulo="COMERCIAL",
        campo_fecha="emissionDate",
        filtro_fecha_api="emissiondaterange",
        tipo_filtro_fecha_api="range_unix",

        # emissionDate/expirationDate vienen como Unix: se guarda
        # emissionDateUnix (original) y emissionDate como texto.
        campos_fecha_unix=[
            "emissionDate",
            "expirationDate",
        ],

        tipos_stage={
            "id": "BIGINT",
            "href": "TEXT",
            "emissionDateUnix": "BIGINT",
            "emissionDate": "TEXT",
            "expirationDateUnix": "BIGINT",
            "expirationDate": "TEXT",
            "generationDate": "TEXT",
            "number": "BIGINT",
            "serialNumber": "TEXT",
            "totalAmount": "NUMERIC",
            "netAmount": "NUMERIC",
            "taxAmount": "NUMERIC",
            "exemptAmount": "NUMERIC",
            "state": "INTEGER",
            "document_type_id": "BIGINT",
            "client_id": "BIGINT",
            "office_id": "BIGINT",
            "user_id": "BIGINT",
        },
    ),

    EndpointConfig(
        nombre="BSALE_DOCUMENT_DETAILS",
        conector="bsale",
        endpoint="documents/{id}/details.json",
        tabla="com_api_bsale_document_details",
        modulo="COMERCIAL",
        page_size=25,

        padre="BSALE_DOCUMENTS",
        columna_fk="document_id",
        campos_padre=[
            "emissionDate",
            "emissionDateUnix",
        ],
        campo_fecha="emissionDate",

        tipos_stage={
            "id": "BIGINT",
            "document_id": "BIGINT",
            "emissionDate": "TEXT",
            "emissionDateUnix": "BIGINT",
            "lineNumber": "INTEGER",
            "quantity": "NUMERIC",
            "netUnitValue": "NUMERIC",
            "totalUnitValue": "NUMERIC",
            "netAmount": "NUMERIC",
            "taxAmount": "NUMERIC",
            "totalAmount": "NUMERIC",
            "netDiscount": "NUMERIC",
            "totalDiscount": "NUMERIC",
            "variant_id": "BIGINT",
        },
    ),

]
