import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta


# Zona horaria de la ejecución. Se ajusta una sola vez al inicio
# (main.py → configurar_zona_horaria) con el valor ZONA_HORARIA del
# .env del cliente; por defecto Chile.
_ZONA_HORARIA = ZoneInfo("America/Santiago")

PARTICIONES_VALIDAS = {
    "dia",
    "semana",
    "mes",
    "anio",
}


def configurar_zona_horaria(nombre):
    global _ZONA_HORARIA

    _ZONA_HORARIA = ZoneInfo(nombre)


def zona_horaria():
    return _ZONA_HORARIA


def ahora():
    """Fecha/hora actual en la zona del cliente, sin tzinfo (naive)."""
    return datetime.now(_ZONA_HORARIA).replace(tzinfo=None)


def hoy():
    return ahora().date()


def timestamp_archivo():
    return ahora().strftime("%Y%m%d_%H%M%S")


def parsear_fecha(valor):
    return datetime.strptime(valor.strip(), "%Y-%m-%d").date()


def obtener_rango_fechas(fecha_inicio_arg, fecha_fin_arg, meses_automatico):
    """
    Devuelve (fecha_inicio, fecha_fin, modo).

    - MANUAL:     ambas fechas vienen por parámetro (YYYY-MM-DD).
    - AUTOMATICO: hoy - `meses_automatico` meses → hoy.
    """

    if fecha_inicio_arg and fecha_fin_arg:
        fecha_inicio = parsear_fecha(fecha_inicio_arg)
        fecha_fin = parsear_fecha(fecha_fin_arg)

        if fecha_inicio > fecha_fin:
            raise ValueError(
                "La fecha de inicio no puede ser mayor que la fecha fin."
            )

        return fecha_inicio, fecha_fin, "MANUAL"

    fecha_fin = hoy()
    fecha_inicio = fecha_fin - relativedelta(months=meses_automatico)

    return fecha_inicio, fecha_fin, "AUTOMATICO"


def dividir_rango(fecha_inicio, fecha_fin, particion):
    """
    Divide [fecha_inicio, fecha_fin] (ambos inclusive) en tramos
    consecutivos según `particion` (dia/semana/mes/anio). Útil para
    APIs que solo aceptan un día exacto, que limitan el rango máximo
    por consulta, o para paralelizar la descarga por tramo.

    Los tramos de mes/año respetan el calendario: el primer y el
    último tramo pueden ser parciales.
    """

    if particion not in PARTICIONES_VALIDAS:
        raise ValueError(
            f"Partición de fecha inválida: '{particion}'. "
            f"Valores permitidos: {', '.join(sorted(PARTICIONES_VALIDAS))}"
        )

    tramos = []
    desde = fecha_inicio

    while desde <= fecha_fin:
        if particion == "dia":
            hasta = desde

        elif particion == "semana":
            hasta = desde + timedelta(days=6)

        elif particion == "mes":
            ultimo_dia = calendar.monthrange(desde.year, desde.month)[1]
            hasta = desde.replace(day=ultimo_dia)

        else:
            hasta = date(desde.year, 12, 31)

        hasta = min(hasta, fecha_fin)
        tramos.append((desde, hasta))
        desde = hasta + timedelta(days=1)

    return tramos


def inicio_dia(fecha):
    return datetime.combine(fecha, datetime.min.time())


def fin_exclusivo(fecha):
    """Medianoche del día siguiente: para filtros `campo < fin`."""
    return datetime.combine(fecha + timedelta(days=1), datetime.min.time())


def fecha_a_unix_inicio(fecha):
    return calendar.timegm(inicio_dia(fecha).timetuple())


def fecha_a_unix_fin(fecha):
    fin = datetime.combine(fecha, datetime.max.time()).replace(microsecond=0)
    return calendar.timegm(fin.timetuple())


def unix_a_texto(valor):
    """
    Convierte un timestamp Unix (segundos o milisegundos) a texto
    'YYYY-MM-DD HH:MM:SS' en UTC (sin aplicar zona horaria: APIs como
    Bsale entregan la fecha calendario ya "desplazada" a UTC).
    Devuelve None si el valor no es convertible.
    """

    if valor is None or valor == "":
        return None

    try:
        timestamp = float(valor)

        if timestamp > 100_000_000_000:
            timestamp = timestamp / 1000

        return datetime.utcfromtimestamp(timestamp).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    except (TypeError, ValueError, OverflowError, OSError):
        return None


def valor_a_fecha(valor):
    """
    Interpreta un valor de fecha "cualquiera" (date, datetime, texto
    ISO 'YYYY-MM-DD[ HH:MM:SS]' / 'YYYY-MM-DDTHH:MM:SS[.fff][Z]') y
    devuelve un `date`, o None si no se puede interpretar. Se usa para
    descartar registros fuera de rango (filtrar_rango_local).
    """

    if valor is None or valor == "":
        return None

    if isinstance(valor, datetime):
        return valor.date()

    if isinstance(valor, date):
        return valor

    texto = str(valor).strip()

    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).date()
    except ValueError:
        pass

    try:
        return datetime.strptime(texto[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
