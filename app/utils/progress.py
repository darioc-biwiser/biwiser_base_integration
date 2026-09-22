import sys
import time


def _formatear_segundos(segundos):
    segundos = int(max(segundos, 0))
    horas, resto = divmod(segundos, 3600)
    minutos, segundos = divmod(resto, 60)

    if horas:
        return f"{horas:d}:{minutos:02d}:{segundos:02d}"

    return f"{minutos:02d}:{segundos:02d}"


class ProgressBar:
    """
    Barra de avance para procesos largos (páginas, tramos de fecha,
    registros hijos, endpoints de un módulo, traspaso a DWH...).

    - Si la salida estándar es una terminal interactiva, dibuja una
      barra que se actualiza en el lugar (usa \\r), con tiempo
      transcurrido y tiempo restante estimado.
    - Si la salida está redirigida (cron, archivo, pipe, Git Bash en
      Windows) o se pide interactivo=False, NO escribe \\r (se vería
      como basura en el log): emite una línea de log con la misma barra
      cada `paso_log` puntos porcentuales (y siempre al 100%).

    interactivo=False se usa en barras que avanzan entre líneas de log
    (ej. avance por endpoint): una barra con \\r quedaría cortada por
    los mensajes intermedios.

    Se actualiza siempre desde el hilo principal (los workers solo
    descargan; ver core/services.py), por lo que no necesita lock.
    """

    def __init__(
        self,
        total,
        prefix,
        unit="registros",
        width=30,
        log=None,
        paso_log=20,
        interactivo=None,
    ):
        self.total = max(total or 0, 0)
        self.prefix = prefix
        self.unit = unit
        self.width = width
        self.log = log
        self.paso_log = paso_log

        self._interactivo = (
            sys.stdout.isatty() if interactivo is None else interactivo
        )
        self._ultimo_pct_log = -1
        self._cerrado = False
        self._inicio = time.perf_counter()

    def _texto(self, actual):
        pct = int(actual * 100 / self.total)
        llenado = int(self.width * actual / self.total)
        barra = "█" * llenado + "░" * (self.width - llenado)

        transcurrido = time.perf_counter() - self._inicio

        if 0 < actual < self.total:
            restante = transcurrido * (self.total - actual) / actual
            eta = f" ⏳ {_formatear_segundos(restante)}"
        else:
            eta = ""

        return (
            f"{self.prefix} [{barra}] {pct:3d}% "
            f"({actual}/{self.total} {self.unit}) "
            f"⏱️ {_formatear_segundos(transcurrido)}{eta}"
        ), pct

    def update(self, actual):
        if self.total <= 0 or self._cerrado:
            return

        actual = min(actual, self.total)
        texto, pct = self._texto(actual)

        if self._interactivo:
            self._escribir(f"\r{texto}   ")

            if actual >= self.total:
                self._cerrar_linea()

        elif self.log is not None:
            alcanzo_paso = pct >= self._ultimo_pct_log + self.paso_log
            es_final = actual >= self.total

            if (alcanzo_paso or es_final) and pct != self._ultimo_pct_log:
                self._ultimo_pct_log = pct
                self.log.info("%s", texto)

    def _cerrar_linea(self):
        if self._cerrado:
            return

        if self._interactivo:
            self._escribir("\n")

        self._cerrado = True

    def _escribir(self, texto):
        # Algunas consolas (ej. cmd.exe en codepage legacy de Windows)
        # no soportan emojis y lanzan UnicodeEncodeError. En ese caso
        # se reintenta reemplazando los caracteres no soportados en
        # lugar de interrumpir el proceso.
        try:
            sys.stdout.write(texto)
        except UnicodeEncodeError:
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"

            texto_seguro = texto.encode(
                encoding,
                errors="replace",
            ).decode(encoding)

            sys.stdout.write(texto_seguro)

        sys.stdout.flush()

    def close(self):
        self._cerrar_linea()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
