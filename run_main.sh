#!/usr/bin/env bash

set -uo pipefail


# ============================================================
# RAÍZ DEL PROYECTO
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"


# ============================================================
# DEJAR VENTANA ABIERTA AL TERMINAR
# ============================================================
#
# RUN_MAIN_PAUSE=1 (default) -> queda abierta esperando Enter.
# RUN_MAIN_PAUSE=0            -> se cierra sola (cron / tareas
#                                programadas, sin nadie que apriete
#                                Enter).
#
# ============================================================

trap '
echo
echo "============================================================"
echo "PROCESO FINALIZADO"
echo "============================================================"

if [[ "${RUN_MAIN_PAUSE:-1}" != "0" ]]; then
    echo "Presiona ENTER para cerrar..."

    # Si el script se abre con doble clic, stdin puede no ser la
    # consola y "read" recibiría EOF de inmediato: se lee desde
    # /dev/tty y, como último recurso en Windows, con "pause".
    if [ -t 0 ]; then
        read -r
    elif [ -e /dev/tty ] && read -r < /dev/tty 2>/dev/null; then
        :
    elif command -v cmd.exe > /dev/null 2>&1; then
        cmd.exe //c pause > /dev/null 2>&1 || true
    fi
fi
' EXIT


# ============================================================
# AYUDA
# ============================================================

mostrar_uso() {
    echo
    echo "Uso:"
    echo
    echo "  Ejecución automática (últimos MESES_AUTOMATICO meses, default 2):"
    echo "    $0 demo              (todos los módulos habilitados en MODULOS)"
    echo "    $0 demo_f            (FINANCIERO)"
    echo "    $0 demo_c            (COMERCIAL)"
    echo "    $0 demo_o            (OPERACIONAL)"
    echo "    $0 demo_ot           (OTROS)"
    echo
    echo "  Ejecución manual (rango de fechas YYYY-MM-DD):"
    echo "    $0 demo 2026-06-01 2026-08-13"
    echo "    $0 demo_f 2026-06-01 2026-08-13"
    echo
    echo "  Ejecución de un único endpoint (requiere sufijo _f/_c/_o/_ot):"
    echo "    $0 demo_f MINDICADOR_UF"
    echo "    $0 demo_f 2026-06-01 2026-08-13 MINDICADOR_UF"
    echo "    $0 demo_o JSONPLACEHOLDER_COMMENTS"
    echo
    echo "  Variables opcionales:"
    echo "    RUN_MAIN_PAUSE=0 $0 demo    (no esperar ENTER al terminar)"
    echo "    PYTHON_BIN=/ruta/python $0 demo    (forzar intérprete)"
    echo
}


# ============================================================
# VALIDAR PARÁMETROS
# ============================================================

if [[ $# -lt 1 || $# -gt 4 ]]; then
    echo "ERROR: Parámetros incorrectos."
    mostrar_uso
    exit 1
fi


# ============================================================
# DETERMINAR CLIENTE Y MÓDULO
# ============================================================
#
# demo     -> cliente=demo  modulo=TODOS
# demo_f   -> cliente=demo  modulo=FINANCIERO
# demo_c   -> cliente=demo  modulo=COMERCIAL
# demo_o   -> cliente=demo  modulo=OPERACIONAL
# demo_ot  -> cliente=demo  modulo=OTROS
#
# ============================================================

EJECUCION="$1"

case "$EJECUCION" in
    *_ot)
        EMPRESA="${EJECUCION%_ot}"
        MODULO="OTROS"
        ;;
    *_f)
        EMPRESA="${EJECUCION%_f}"
        MODULO="FINANCIERO"
        ;;
    *_c)
        EMPRESA="${EJECUCION%_c}"
        MODULO="COMERCIAL"
        ;;
    *_o)
        EMPRESA="${EJECUCION%_o}"
        MODULO="OPERACIONAL"
        ;;
    *)
        EMPRESA="$EJECUCION"
        MODULO="TODOS"
        ;;
esac


# ============================================================
# ARCHIVO DE CONFIGURACIÓN DEL CLIENTE
# ============================================================
#
# Se busca .env.<cliente> tal cual y en minúsculas
# (Demo -> .env.demo), igual que app/config/settings.py.
#
# ============================================================

ENV_FILE=".env.${EMPRESA}"

if [[ ! -f "$ENV_FILE" ]]; then
    ENV_FILE=".env.$(echo "$EMPRESA" | tr '[:upper:]' '[:lower:]')"
fi

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: No existe el archivo de configuración:"
    echo
    echo "  .env.${EMPRESA}"
    echo
    echo "Debes crearlo a partir de la plantilla:"
    echo
    echo "  cp .env.example $SCRIPT_DIR/.env.${EMPRESA}"
    echo
    exit 1
fi


# ============================================================
# FECHAS / ENDPOINT
# ============================================================

FECHA_INICIO=""
FECHA_FIN=""
ENDPOINT=""

es_fecha() {
    [[ "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]
}

case $# in
    1)
        ;;
    2)
        ENDPOINT="$2"
        ;;
    3)
        FECHA_INICIO="$2"
        FECHA_FIN="$3"
        ;;
    4)
        FECHA_INICIO="$2"
        FECHA_FIN="$3"
        ENDPOINT="$4"
        ;;
esac

if [[ -n "$ENDPOINT" ]] && es_fecha "$ENDPOINT"; then
    echo "ERROR: '$ENDPOINT' parece una fecha. Para un rango manual"
    echo "debes indicar fecha de inicio Y fecha fin."
    mostrar_uso
    exit 1
fi

if [[ -n "$FECHA_INICIO" ]]; then
    if ! es_fecha "$FECHA_INICIO" || ! es_fecha "$FECHA_FIN"; then
        echo "ERROR: Las fechas deben tener formato YYYY-MM-DD."
        echo "  fecha_inicio=$FECHA_INICIO | fecha_fin=$FECHA_FIN"
        exit 1
    fi

    if [[ "$FECHA_INICIO" > "$FECHA_FIN" ]]; then
        echo "ERROR: La fecha de inicio ($FECHA_INICIO) es mayor que"
        echo "la fecha fin ($FECHA_FIN)."
        exit 1
    fi
fi

if [[ -n "$ENDPOINT" && "$MODULO" == "TODOS" ]]; then
    echo "ERROR: Para ejecutar un endpoint específico debes indicar"
    echo "el módulo con el sufijo _f, _c, _o u _ot."
    echo
    echo "Ejemplo:"
    echo
    echo "  $0 ${EMPRESA}_f $ENDPOINT"
    echo
    exit 1
fi


# ============================================================
# MÓDULOS A EJECUTAR
# ============================================================
#
# Con MODULO=TODOS se ejecutan solo los módulos habilitados en la
# variable MODULOS del .env del cliente (en el orden
# COMERCIAL, FINANCIERO, OPERACIONAL, OTROS).
#
# ============================================================

leer_variable_env() {
    grep -E "^[[:space:]]*$1[[:space:]]*=" "$ENV_FILE" \
        | tail -n 1 \
        | cut -d '=' -f 2- \
        | sed -e 's/[[:space:]]*#.*$//' -e 's/\r$//' -e 's/^[[:space:]]*//' \
              -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

if [[ "$MODULO" == "TODOS" ]]; then
    MODULOS_ENV="$(leer_variable_env MODULOS | tr '[:lower:]' '[:upper:]')"
    MODULOS_A_EJECUTAR=()

    for MOD in COMERCIAL FINANCIERO OPERACIONAL OTROS; do
        if [[ ",${MODULOS_ENV// /}," == *",$MOD,"* ]]; then
            MODULOS_A_EJECUTAR+=("$MOD")
        fi
    done

    if [[ ${#MODULOS_A_EJECUTAR[@]} -eq 0 ]]; then
        echo "ERROR: $ENV_FILE no tiene módulos válidos en MODULOS."
        echo "  MODULOS=$MODULOS_ENV"
        exit 1
    fi
else
    MODULOS_A_EJECUTAR=("$MODULO")
fi

MESES_AUTOMATICO="$(leer_variable_env MESES_AUTOMATICO)"
MESES_AUTOMATICO="${MESES_AUTOMATICO:-2}"


# ============================================================
# INTÉRPRETE PYTHON
# ============================================================
#
# 0. PYTHON_BIN, si se define (ej. PYTHON_BIN=/opt/py310/bin/python)
# 1. pipenv (igual que los demás proyectos; requiere pipenv install)
# 2. .venv del proyecto (Linux o Windows)
# 3. python3 / python del sistema
#
# ============================================================

export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export PYTHONIOENCODING="utf-8"
export PYTHONUTF8="1"

if [[ -n "${PYTHON_BIN:-}" ]]; then
    PYTHON_CMD=("$PYTHON_BIN")
elif command -v pipenv > /dev/null 2>&1 && [[ -f "Pipfile" ]]; then
    PYTHON_CMD=(pipenv run python)
elif [[ -x ".venv/bin/python" ]]; then
    PYTHON_CMD=(".venv/bin/python")
elif [[ -x ".venv/Scripts/python.exe" ]]; then
    PYTHON_CMD=(".venv/Scripts/python.exe")
elif command -v python3 > /dev/null 2>&1; then
    PYTHON_CMD=(python3)
else
    PYTHON_CMD=(python)
fi


# ============================================================
# INICIO
# ============================================================

echo
echo "============================================================"
echo "🔄 BIWISER BASE INTEGRATION"
echo "============================================================"
echo "Ejecución    : $EJECUCION"
echo "Cliente      : $EMPRESA"
echo "Configuración: $ENV_FILE"
echo "Módulos      : ${MODULOS_A_EJECUTAR[*]}"
echo "Python       : ${PYTHON_CMD[*]}"

if [[ -n "$ENDPOINT" ]]; then
    echo "Endpoint     : $ENDPOINT"
fi

if [[ -n "$FECHA_INICIO" && -n "$FECHA_FIN" ]]; then
    echo "Fecha inicio : $FECHA_INICIO"
    echo "Fecha fin    : $FECHA_FIN"
    echo "Modo         : MANUAL"
else
    echo "Fecha inicio : AUTOMÁTICA (-${MESES_AUTOMATICO} meses)"
    echo "Fecha fin    : AUTOMÁTICA (hoy)"
    echo "Modo         : AUTOMÁTICO"
fi

echo "============================================================"
echo


# ============================================================
# EJECUTAR PYTHON POR MÓDULO
# ============================================================
#
# Códigos de salida de main.py:
#   0   -> FINALIZADO CORRECTAMENTE / SIN ENDPOINTS / empresa inactiva
#   1   -> error crítico o de configuración
#   2   -> FINALIZADO CON ERRORES (algún endpoint falló)
#   3   -> FINALIZADO CON ADVERTENCIAS (datos cargados, con valores
#          descartados o columnas sin tipo; ver procesos estado=ADVERTENCIA)
#   130 -> interrumpido (Ctrl+C)
#
# Un módulo con error NO detiene a los siguientes.
#
# ============================================================

RESULTADO_GENERAL=0
HUBO_ADVERTENCIAS=0
INICIO_TOTAL=$(date +%s)

for MOD in "${MODULOS_A_EJECUTAR[@]}"; do
    echo
    echo "------------------------------------------------------------"
    echo "Cliente       : $EMPRESA"
    echo "Configuración : $ENV_FILE"
    echo "Módulo        : $MOD"
    echo "------------------------------------------------------------"

    "${PYTHON_CMD[@]}" main.py \
        "$EMPRESA" \
        "$MOD" \
        "$FECHA_INICIO" \
        "$FECHA_FIN" \
        "$ENDPOINT"

    CODIGO=$?

    case $CODIGO in
        0)
            echo "✅ Módulo $MOD finalizado."
            ;;
        3)
            HUBO_ADVERTENCIAS=1
            echo "⚠️  Módulo $MOD finalizado CON ADVERTENCIAS de datos (revisar procesos estado=ADVERTENCIA)."
            ;;
        2)
            RESULTADO_GENERAL=1
            echo "⚠️  Módulo $MOD finalizado CON ERRORES (revisar error/ y procesos)."
            ;;
        130)
            RESULTADO_GENERAL=1
            echo "🛑 Módulo $MOD interrumpido. Se detiene la ejecución."
            break
            ;;
        *)
            RESULTADO_GENERAL=1
            echo "❌ ERROR: El módulo $MOD terminó con error (código $CODIGO)."
            echo "Se continuará con el siguiente módulo."
            ;;
    esac

    echo
done

DURACION_TOTAL=$(( $(date +%s) - INICIO_TOTAL ))


# ============================================================
# FIN
# ============================================================

echo "============================================================"

if [[ "$RESULTADO_GENERAL" -eq 0 && "$HUBO_ADVERTENCIAS" -eq 1 ]]; then
    echo "⚠️  MÓDULOS FINALIZADOS CON ADVERTENCIAS DE DATOS (la carga se completó)"
elif [[ "$RESULTADO_GENERAL" -eq 0 ]]; then
    echo "✅ TODOS LOS MÓDULOS FINALIZADOS CORRECTAMENTE"
else
    echo "⚠️  PROCESO FINALIZADO CON ERRORES"
fi

printf "⏱️  Duración total: %02d:%02d:%02d\n" \
    $((DURACION_TOTAL / 3600)) $(((DURACION_TOTAL % 3600) / 60)) $((DURACION_TOTAL % 60))

echo "============================================================"

exit "$RESULTADO_GENERAL"
