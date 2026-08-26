# Importando as bibliotecas necessárias
import os
import signal
import time

from watchdog.observers.polling import PollingObserver
from WatchDogEvents import WatchDogEvents
from Utils import Utils

from dotenv import load_dotenv
load_dotenv(override=True)

# Configurações de limpeza periódica (substitui o antigo script Clear.py)
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "15"))
CLEANUP_INTERVAL_SECONDS = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "3600"))
# Intervalo do loop principal / verificação de encerramento
LOOP_INTERVAL_SECONDS = int(os.getenv("LOOP_INTERVAL_SECONDS", "30"))

_running = True


def _handle_stop_signal(signum, frame):
    # systemd envia SIGTERM por padrão ao parar o serviço; Ctrl+C envia SIGINT.
    # Tratamos ambos para garantir um encerramento limpo do observer.
    global _running
    Utils.log(f"Sinal de encerramento recebido ({signum}).")
    _running = False


# Função principal
def main():
    # Pasta que será monitorada
    folder = os.getenv("FOLDER_PATH")
    if not folder or not os.path.isdir(folder):
        Utils.log(f"FOLDER_PATH inválido ou indisponível: '{folder}'. Encerrando.", level="error")
        return

    signal.signal(signal.SIGTERM, _handle_stop_signal)
    signal.signal(signal.SIGINT, _handle_stop_signal)

    # Definições Biblioteca watchdog
    # Usamos PollingObserver (ao invés do Observer nativo baseado em inotify) porque
    # a pasta monitorada é um compartilhamento de rede (SMB/CIFS/NFS) — inotify não
    # detecta eventos de forma confiável (ou não funciona) em sistemas de arquivos de rede.
    observer = PollingObserver()
    watch_dog = WatchDogEvents()
    watch_dog.find_archives()
    observer.schedule(watch_dog, folder, recursive=True)
    Utils.log(f"Iniciando monitoramento da pasta {folder}.")
    observer.start()

    last_cleanup = 0.0
    try:
        while _running:
            time.sleep(LOOP_INTERVAL_SECONDS)

            if not observer.is_alive():
                Utils.log("Observer não está mais ativo. Encerrando serviço.", level="error")
                break

            now = time.time()
            if now - last_cleanup >= CLEANUP_INTERVAL_SECONDS:
                Utils.log("Iniciando limpeza periódica de arquivos antigos.")
                Utils.clear_old_files(folder, RETENTION_DAYS)
                last_cleanup = now
    finally:
        observer.stop()
        observer.join()
        Utils.log("WatchDog finalizado.")


if __name__ == '__main__':
    Utils.log("Iniciando WatchDog.")
    main()
