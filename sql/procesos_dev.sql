-- ============================================================
-- Tabla de control de ejecuciones (base "procesos").
-- Misma estructura que usan biwiser_bsale_integration y
-- biwiser_odoo_integration. Nombre/esquema configurables con
-- PS_PROCESOS_SCHEMA / PS_PROCESOS_TABLA.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.procesos_dev
(
    id               SERIAL PRIMARY KEY,
    proceso          TEXT        NOT NULL,   -- PROCESO_NOMBRE (ej. BSALE, BASE_INTEGRATION)
    accion           TEXT,                   -- modo | endpoint | rango | módulo | errores / detalle del error
    estado           TEXT        NOT NULL,   -- EN EJECUCION, FINALIZADO CORRECTAMENTE, FINALIZADO CON ERRORES,
                                             -- FINALIZADO CON ADVERTENCIAS, SIN ENDPOINTS, ERROR_CRITICO,
                                             -- INTERRUMPIDO, ERROR / ADVERTENCIA (fila por endpoint)
    fecha            TIMESTAMP   NOT NULL,   -- inicio (hora local del cliente)
    cliente          TEXT,                   -- EMPRESA en mayúsculas
    fecha_fin        TIMESTAMP,
    tiempo_ejecucion NUMERIC(12, 2)          -- minutos
);

CREATE INDEX IF NOT EXISTS ix_procesos_dev_cliente_fecha
    ON public.procesos_dev (cliente, fecha DESC);

-- Últimas ejecuciones de un cliente:
-- SELECT id, proceso, estado, fecha, fecha_fin, tiempo_ejecucion, accion
-- FROM public.procesos_dev
-- WHERE cliente = 'DEMO'
-- ORDER BY id DESC
-- LIMIT 50;
