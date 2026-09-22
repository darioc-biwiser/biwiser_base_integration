#!/usr/bin/env python3

import logging
import sys

from app.config.settings import (
    MODULOS_VALIDOS,
    cargar_env_empresa,
    get_config,
    leer_config_logging,
    obtener_variable,
)
from app.core.procesos import RegistroProcesos
from app.core.services import ejecutar_integracion
from app.utils import fechas
from app.utils.logger import configurar_logging


log = logging.getLogger(__name__)

USO = (
    "Uso:\n"
    "  ./run_main.sh cliente modulo fecha_inicio fecha_fin [endpoint]\n\n"
    "Módulos: FINANCIERO, COMERCIAL, OPERACIONAL, OTROS\n\n"
    "Ejemplo automático (últimos MESES_AUTOMATICO meses):\n"
    "  python main.py demo FINANCIERO '' ''\n\n"
    "Ejemplo manual:\n"
    "  python main.py demo FINANCIERO 2026-06-01 2026-08-13\n\n"
    "Ejemplo endpoint único:\n"
    "  python main.py demo OPERACIONAL '' '' JSONPLACEHOLDER_POSTS\n"
    "  python main.py demo FINANCIERO 2026-06-01 2026-08-13 MINDICADOR_UF"
)


def main():
    if len(sys.argv) not in (5, 6):
        print(USO)
        sys.exit(1)

    empresa = sys.argv[1].strip()
    modulo = sys.argv[2].strip().upper()
    fecha_inicio_arg = sys.argv[3].strip() or None
    fecha_fin_arg = sys.argv[4].strip() or None
    endpoint_nombre = (sys.argv[5].strip() or None) if len(sys.argv) == 6 else None

    if modulo not in MODULOS_VALIDOS:
        print(
            f"ERROR: Módulo inválido: {modulo} | permitidos="
            f"{', '.join(sorted(MODULOS_VALIDOS))}"
        )
        sys.exit(1)

    if bool(fecha_inicio_arg) != bool(fecha_fin_arg):
        print("ERROR: Debes indicar fecha de inicio y fecha fin.")
        sys.exit(1)

    # El .env se carga antes de configurar el logging para respetar
    # LOG_* y ZONA_HORARIA del cliente. Si alguno es inválido, se usa
    # el default y el error real aparece al validar la configuración.
    try:
        cargar_env_empresa(empresa)
        opciones_log = leer_config_logging()
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    except Exception:
        opciones_log = {}

    try:
        fechas.configurar_zona_horaria(
            obtener_variable(
                "ZONA_HORARIA",
                obligatoria=False,
                default="America/Santiago",
            )
        )
    except Exception as exc:
        print(f"ERROR: ZONA_HORARIA inválida: {exc}")
        sys.exit(1)

    archivo_error, archivo_log = configurar_logging(
        empresa,
        modulo,
        nivel_consola=opciones_log.get("log_nivel_consola", "INFO"),
        buffer_capacidad=opciones_log.get("log_buffer_capacidad", 2000),
        archivo_ejecucion=opciones_log.get("log_archivo_ejecucion", True),
        retencion_dias=opciones_log.get("log_retencion_dias", 30),
    )

    log.info("📄 Archivo de errores (solo si hay fallas) | %s", archivo_error)

    if archivo_log is not None:
        log.info("📝 Log de ejecución | %s", archivo_log)

    try:
        config = get_config(empresa, modulo)
    except Exception:
        log.exception(
            "ERROR cargando configuración | empresa=%s | modulo=%s",
            empresa,
            modulo,
        )
        sys.exit(1)

    if config.estado.upper() != "ACTIVA":
        log.warning(
            "⏸️ Empresa '%s' no está activa | estado=%s",
            config.empresa,
            config.estado,
        )
        sys.exit(0)

    try:
        fecha_inicio, fecha_fin, modo = fechas.obtener_rango_fechas(
            fecha_inicio_arg,
            fecha_fin_arg,
            config.ejecucion.meses_automatico,
        )
    except Exception:
        log.exception("ERROR calculando rango de fechas")
        sys.exit(1)

    log.info(
        "📅 INICIO INTEGRACIÓN | proceso=%s | empresa=%s | modulo=%s | "
        "modo=%s | endpoint=%s | fecha_inicio=%s | fecha_fin=%s",
        config.proceso_nombre,
        config.empresa,
        modulo,
        modo,
        endpoint_nombre or "TODOS",
        fecha_inicio,
        fecha_fin,
    )

    try:
        procesos = RegistroProcesos(config)
    except Exception:
        log.exception("ERROR creando conexión a base de datos de procesos")
        sys.exit(1)

    run_iniciado = False
    errores = 0
    codigo_salida = 0

    try:
        accion = modo

        if endpoint_nombre:
            accion = f"{modo} | ENDPOINT={endpoint_nombre.upper()}"

        accion = f"{accion} | RANGO={fecha_inicio}..{fecha_fin}"

        procesos.iniciar(modulo, accion)
        run_iniciado = True

        resultado = ejecutar_integracion(
            config=config,
            modulo=modulo,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            procesos=procesos,
            endpoint_nombre=endpoint_nombre,
        )

        errores = resultado.get("errores", 0)

        if resultado.get("estado") == "SIN ENDPOINTS":
            estado = "SIN ENDPOINTS"
        elif errores > 0:
            estado = "FINALIZADO CON ERRORES"
            codigo_salida = 2
        else:
            estado = "FINALIZADO CORRECTAMENTE"

        procesos.finalizar(estado, errores=errores)

    except KeyboardInterrupt:
        log.error("🛑 EJECUCIÓN INTERRUMPIDA POR EL USUARIO")

        if run_iniciado:
            try:
                procesos.finalizar("INTERRUMPIDO", errores=errores + 1)
            except Exception:
                log.exception("ERROR actualizando estado del proceso")

        codigo_salida = 130

    except Exception:
        errores += 1

        log.exception(
            "ERROR CRÍTICO durante integración | empresa=%s | modulo=%s",
            config.empresa,
            modulo,
        )

        if run_iniciado:
            try:
                procesos.finalizar("ERROR_CRITICO", errores=errores)
            except Exception:
                log.exception(
                    "ERROR actualizando estado del proceso | run_id=%s",
                    procesos.run_id,
                )

        codigo_salida = 1

    finally:
        log.info(
            "🏁 FIN INTEGRACIÓN | empresa=%s | modulo=%s",
            config.empresa,
            modulo,
        )

        try:
            procesos.close()
        except Exception:
            log.exception("ERROR cerrando conexión a base de datos")

    sys.exit(codigo_salida)


if __name__ == "__main__":
    main()
