# Importando as bibliotecas necessárias
import os
import signal
import sys
import threading
import time

from Ftp import Ftp
from Scanner import Scanner
from Utils import Utils

from dotenv import load_dotenv
load_dotenv(override=True)

# Intervalo entre as varreduras do FTP em busca de arquivos pendentes
SCAN_INTERVAL_SECONDS = float(os.getenv("SCAN_INTERVAL_SECONDS", "5"))
# Configurações de limpeza periódica (substitui o antigo script Clear.py)
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "15"))
CLEANUP_INTERVAL_SECONDS = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "3600"))
CLEANUP_EXTENSIONS = tuple(
    ext.strip().lower() for ext in os.getenv("CLEANUP_EXTENSIONS", ".jpg,.jpeg").split(",") if ext.strip()
)

# Código de saída para erro de configuração. A unidade systemd não reinicia o serviço
# nesse caso (RestartPreventExitStatus), já que reiniciar não resolveria.
EXIT_CONFIG_ERROR = 2

_stop = threading.Event()


def _handle_stop_signal(signum, frame):
    # systemd envia SIGTERM por padrão ao parar o serviço; Ctrl+C envia SIGINT.
    # Tratamos ambos para garantir um encerramento limpo.
    Utils.log(f"Sinal de encerramento recebido ({signum}).")
    _stop.set()


# Função principal. Retorna o código de saída do processo.
def main() -> int:
    if not os.getenv("FTP_HOST"):
        Utils.log("FTP_HOST não configurado no .env. Encerrando.", level="error")
        return EXIT_CONFIG_ERROR

    signal.signal(signal.SIGTERM, _handle_stop_signal)
    signal.signal(signal.SIGINT, _handle_stop_signal)

    # O FTP é varrido periodicamente em busca de arquivos .vehicleBody.jpg: um arquivo que
    # falhou (FTP ou banco fora do ar, envio ainda em andamento) é simplesmente tentado de
    # novo na próxima varredura.
    ftp = Ftp()
    scanner = Scanner(ftp)
    Utils.log(f"Iniciando monitoramento do FTP {ftp.host}:{ftp.port}, pasta {ftp.path}.")
    cleanup_enabled = RETENTION_DAYS > 0 and bool(CLEANUP_EXTENSIONS)
    if not cleanup_enabled:
        Utils.log("Limpeza automática desativada (RETENTION_DAYS <= 0 ou CLEANUP_EXTENSIONS vazio).")

    last_cleanup = None
    while not _stop.is_set():
        try:
            scanner.scan()

            now = time.monotonic()
            if cleanup_enabled and (last_cleanup is None or now - last_cleanup >= CLEANUP_INTERVAL_SECONDS):
                # Se o FTP estiver fora do ar a limpeza é tentada de novo no próximo ciclo
                if scanner.cleanup(RETENTION_DAYS, CLEANUP_EXTENSIONS):
                    last_cleanup = now
        except Exception as e:
            Utils.log(f"Erro no ciclo de varredura: {e}", level="error")

        # Diferente de time.sleep(), acorda imediatamente quando chega o sinal de encerramento
        _stop.wait(SCAN_INTERVAL_SECONDS)

    ftp.quit()
    Utils.log("WatchDog finalizado.")
    return 0


if __name__ == '__main__':
    Utils.log("Iniciando WatchDog.")
    sys.exit(main())
