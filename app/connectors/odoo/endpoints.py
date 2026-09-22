from app.core.endpoint import EndpointConfig


# ============================================================================
# ODOO
# ============================================================================
#
# `endpoint` es el modelo Odoo. En `opciones`:
#   fields       → campos a solicitar (search_read)
#   relacionales → campos Many2one a linealizar (campo + campo_name)
#   domain       → filtro fijo adicional (se suma al filtro de fecha)
#   order        → orden (default: campo_fecha asc, id asc)
# ============================================================================

ENDPOINTS = [

    EndpointConfig(
        nombre="ODOO_ACCOUNT_MOVE",
        conector="odoo",
        endpoint="account.move",
        tabla="fin_api_odoo_account_move",
        modulo="FINANCIERO",

        campo_fecha="date",
        tipo_filtro_fecha_api="dominio",

        opciones={
            "domain": [
                ["state", "=", "posted"],
            ],
            "fields": [
                "id",
                "name",
                "ref",
                "date",
                "invoice_date",
                "invoice_date_due",
                "state",
                "move_type",
                "payment_state",
                "partner_id",
                "journal_id",
                "company_id",
                "currency_id",
                "amount_untaxed",
                "amount_tax",
                "amount_total",
                "amount_residual",
                "create_date",
                "write_date",
            ],
            "relacionales": [
                "partner_id",
                "journal_id",
                "company_id",
                "currency_id",
            ],
        },

        tipos_stage={
            "id": "INTEGER",
            "name": "TEXT",
            "ref": "TEXT",
            "date": "DATE",
            "invoice_date": "DATE",
            "invoice_date_due": "DATE",
            "state": "TEXT",
            "move_type": "TEXT",
            "payment_state": "TEXT",
            "partner_id": "INTEGER",
            "partner_id_name": "TEXT",
            "journal_id": "INTEGER",
            "journal_id_name": "TEXT",
            "company_id": "INTEGER",
            "company_id_name": "TEXT",
            "currency_id": "INTEGER",
            "currency_id_name": "TEXT",
            "amount_untaxed": "NUMERIC",
            "amount_tax": "NUMERIC",
            "amount_total": "NUMERIC",
            "amount_residual": "NUMERIC",
            "create_date": "TIMESTAMP",
            "write_date": "TIMESTAMP",
        },
    ),

]
