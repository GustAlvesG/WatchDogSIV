# Importando as bibliotecas necessárias
import os
import signal
import sys
import threading
import time

from Scanner import Scanner
from Utils import Utils

from dotenv import load_dotenv
load_dotenv(override=True)

# Intervalo entre as varreduras da pasta em busca de arquivos pendentes
SCAN_INTERVAL_SECONDS = float(os.getenv("SCAN_INTERVAL_SECONDS", "5"))
# Tempo sem modificação para considerar que o upload do arquivo terminou
FILE_SETTLE_SECONDS = float(os.getenv("FILE_SETTLE_SECONDS", "3"))
# Configurações de limpeza periódica (substitui o antigo script Clear.py)
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "15"))
CLEANUP_INTERVAL_SECONDS = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "3600"))
CLEANUP_EXTENSIONS = tuple(
    ext.strip().lower() for ext in os.getenv("CLEANUP_EXTENSIONS", ".jpg,.jpeg").split(",") if ext.strip()
)

_stop = threading.Event()


def _handle_stop_signal(signum, frame):
    # systemd envia SIGTERM por padrão ao parar o serviço; Ctrl+C envia SIGINT.
    # Tratamos ambos para garantir um encerramento limpo.
    Utils.log(f"Sinal de encerramento recebido ({signum}).")
    _stop.set()


# Função principal. Retorna o código de saída do processo: diferente de zero em caso de
# erro, para o systemd (Restart=on-failure) reiniciar o serviço.
def main() -> int:
    # Pasta que será monitorada
    folder = os.getenv("FOLDER_PATH")
    if not folder or not os.path.isdir(folder):
        Utils.log(f"FOLDER_PATH inválido ou indisponível: '{folder}'. Encerrando.", level="error")
        return 1

    signal.signal(signal.SIGTERM, _handle_stop_signal)
    signal.signal(signal.SIGINT, _handle_stop_signal)

    # A pasta é varrida periodicamente em busca de arquivos .vehicleBody.jpg, ao invés de
    # depender de eventos do sistema de arquivos: assim um arquivo que falhou (banco fora
    # do ar, upload ainda em andamento) é simplesmente tentado de novo na próxima varredura.
    scanner = Scanner(folder, settle_seconds=FILE_SETTLE_SECONDS)
    Utils.log(f"Iniciando monitoramento da pasta {folder}.")
    if RETENTION_DAYS <= 0 or not CLEANUP_EXTENSIONS:
        Utils.log("Limpeza automática desativada (RETENTION_DAYS <= 0 ou CLEANUP_EXTENSIONS vazio).")

    exit_code = 0
    last_cleanup = None
    while not _stop.is_set():
        if not os.path.isdir(folder):
            Utils.log(f"Pasta monitorada indisponível: '{folder}'. Encerrando.", level="error")
            exit_code = 1
            break

        try:
            scanner.scan()
        except Exception as e:
            Utils.log(f"Erro scan(): {e}", level="error")

        now = time.monotonic()
        if RETENTION_DAYS > 0 and CLEANUP_EXTENSIONS and (
            last_cleanup is None or now - last_cleanup >= CLEANUP_INTERVAL_SECONDS
        ):
            Utils.log("Iniciando limpeza periódica de arquivos antigos.")
            Utils.clear_old_files(folder, RETENTION_DAYS, CLEANUP_EXTENSIONS)
            last_cleanup = now

        # Diferente de time.sleep(), acorda imediatamente quando chega o sinal de encerramento
        _stop.wait(SCAN_INTERVAL_SECONDS)

    Utils.log("WatchDog finalizado.")
    return exit_code


if __name__ == '__main__':
    Utils.log("Iniciando WatchDog.")
    sys.exit(main())
