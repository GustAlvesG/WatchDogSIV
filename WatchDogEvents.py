import os
import time

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from Utils import Utils
from Database import Database
from dotenv import load_dotenv
load_dotenv(override=True)

# Tempo (em segundos) durante o qual um mesmo caminho de arquivo é ignorado caso
# o watchdog dispare eventos duplicados para ele (comum em compartilhamentos de rede).
DEBOUNCE_SECONDS = 2


# Classe que trata os eventos de modificação
class WatchDogEvents(FileSystemEventHandler):

    # Método de inicialização da classe
    def __init__(self) -> None:
        super().__init__()
        # Instância de conexão com Banco de Dados
        self.db = Database()
        # Registro dos últimos caminhos processados, para evitar execução duplicada
        self._recent_events = {}

    # Verifica se o evento deve ser ignorado por ser um disparo duplicado recente.
    # Só marca o caminho como "tratado" quando o processamento é chamado, e limpa
    # entradas antigas para não crescer indefinidamente.
    def _is_duplicate(self, path: str) -> bool:
        now = time.time()
        # Limpa entradas expiradas
        expired = [p for p, ts in self._recent_events.items() if now - ts > DEBOUNCE_SECONDS]
        for p in expired:
            del self._recent_events[p]

        last = self._recent_events.get(path)
        if last is not None and now - last <= DEBOUNCE_SECONDS:
            return True

        self._recent_events[path] = now
        return False

    def find_archives(self):
        Utils.log("Procurando arquivos legados.")
        folder = os.getenv("FOLDER_PATH")
        if not folder or not os.path.isdir(folder):
            Utils.log(f"find_archives(): pasta inválida ou indisponível '{folder}'.", level="error")
            return
        try:
            for root, _, files in os.walk(folder):
                for file in files:
                    if ".vehicleBody.jpg" not in file:
                        continue
                    path = os.path.join(root, file)
                    self._process_file(path)
        except Exception as e:
            Utils.log(f"Erro find_archives(): {e}", level="error")

    # Processa um arquivo criado/legado: insere no banco e renomeia removendo o sufixo .vehicleBody
    def _process_file(self, src_path: str):
        sql_and_params = Utils.filter_sql_created_file(src_path)
        if not sql_and_params:
            return
        sql, params = sql_and_params
        if self.db.execute(sql, "insert", params):
            new_path = src_path.replace('.vehicleBody', '')
            if Utils.rename_file(src_path, new_path):
                Utils.log(fr"{new_path} finalizado com sucesso.")

    # Quando um arquivo é criado, essa função é chamada
    def on_created(self, event):
        try:
            # Verifica se o evento é um arquivo e se o arquivo é uma imagem
            if event.is_directory or ".jpg" not in event.src_path:
                return
            if self._is_duplicate(event.src_path):
                return

            Utils.log(f"Iniciando on_created() - {event.src_path}")
            self._process_file(event.src_path)
        except Exception as e:
            Utils.log(f"Erro on_created(): {e}", level="error")

    def on_any_event(self, event: FileSystemEvent) -> None:
        Utils.log(f"{event}", level="debug")
