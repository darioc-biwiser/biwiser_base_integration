from app.core.endpoint import EndpointConfig


# ============================================================================
# JSONPLACEHOLDER (API libre)
# ============================================================================
#
# Ejemplo de:
# - JSONPLACEHOLDER_USERS: una sola llamada, objetos anidados aplanados
#   (address → address_street, address_city, ...; company → company_name).
# - JSONPLACEHOLDER_POSTS: paginación offset concurrente con total en
#   header (100 registros / 20 por página = 5 páginas en paralelo).
# - JSONPLACEHOLDER_COMMENTS: endpoint HIJO (posts/{id}/comments), un
#   request por cada post cargado en STAGE, con barra de avance.
# ============================================================================

ENDPOINTS = [

    EndpointConfig(
        nombre="JSONPLACEHOLDER_USERS",
        conector="jsonplaceholder",
        endpoint="users",
        tabla="com_api_jsonplaceholder_users",
        modulo="COMERCIAL",
        paginacion="none",
        tipos_stage={
            "id": "BIGINT",
        },
    ),

    EndpointConfig(
        nombre="JSONPLACEHOLDER_POSTS",
        conector="jsonplaceholder",
        endpoint="posts",
        tabla="ope_api_jsonplaceholder_posts",
        modulo="OPERACIONAL",
        paginacion="offset",
        tipos_stage={
            "id": "BIGINT",
            "userId": "BIGINT",
            "title": "TEXT",
            "body": "TEXT",
        },
    ),

    EndpointConfig(
        nombre="JSONPLACEHOLDER_COMMENTS",
        conector="jsonplaceholder",
        endpoint="posts/{id}/comments",
        tabla="ope_api_jsonplaceholder_comments",
        modulo="OPERACIONAL",
        paginacion="none",

        padre="JSONPLACEHOLDER_POSTS",
        columna_fk="post_id",

        tipos_stage={
            "id": "BIGINT",
            "postId": "BIGINT",
            "post_id": "BIGINT",
        },
    ),

]
