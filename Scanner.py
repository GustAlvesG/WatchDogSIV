import os
import time

from Utils import Utils, PENDING_MARK
from Database import Database, DatabaseUnavailable

# Arquivos com este sufixo ainda não foram processados. O próprio nome do arquivo é o
# controle de pendência: depois do insert no banco o sufixo .vehicleBody é removido.
PENDING_SUFFIX = PENDING_MARK + ".jpg"

# Enquanto o banco estiver fora, o erro é logado de novo só a cada este intervalo (segundos)
DB_DOWN_LOG_INTERVAL_SECONDS = 300


# Classe que procura e processa os arquivos pendentes na pasta monitorada
class Scanner():

    # Método de inicialização da classe
    def __init__(self, folder: str, db=None, settle_seconds: float = 3.0) -> None:
        self.folder = folder
        # Instância de conexão com Banco de Dados
        self.db = db if db is not None else Database()
        # Um arquivo só é processado depois de ficar este tempo sem ser modificado, para
        # não pegar uma foto cujo upload via FTP ainda está em andamento.
        self.settle_seconds = settle_seconds
        # Arquivos já inseridos no banco cujo rename falhou: só o rename é tentado de novo,
        # para não duplicar o registro.
        self._inserted = set()
        # Arquivos com nome fora do padrão ou rejeitados pelo banco: o erro é logado uma
        # vez e eles são ignorados até o serviço reiniciar (a limpeza periódica os remove).
        self._rejected = set()
        self._db_down_logged_at = None

    # Lista os arquivos pendentes. Retorna (todos, prontos): `prontos` são os que já
    # terminaram de ser gravados.
    def _find_pending(self):
        pending = set()
        ready = []
        limit = time.time() - self.settle_seconds
        for root, _, files in os.walk(self.folder):
            for file in files:
                if not file.endswith(PENDING_SUFFIX):
                    continue
                path = os.path.join(root, file)
                pending.add(path)
                try:
                    if os.path.getmtime(path) <= limit:
                        ready.append(path)
                except OSError:
                    continue
        return pending, sorted(ready)

    # Varre a pasta e processa os arquivos pendentes. Retorna quantos foram finalizados.
    # Se o banco estiver inacessível a varredura é interrompida e os arquivos continuam
    # pendentes para a próxima.
    def scan(self) -> int:
        pending, ready = self._find_pending()
        # Esquece os arquivos que não existem mais, para os conjuntos não crescerem indefinidamente
        self._inserted &= pending
        self._rejected &= pending

        finished = 0
        for path in ready:
            if path in self._rejected:
                continue
            try:
                if self._process_file(path):
                    finished += 1
            except DatabaseUnavailable as e:
                self._log_db_down(e, len(ready) - finished)
                break
        return finished

    def _log_db_down(self, error, remaining: int):
        now = time.monotonic()
        if self._db_down_logged_at is None or now - self._db_down_logged_at >= DB_DOWN_LOG_INTERVAL_SECONDS:
            Utils.log(
                f"Banco de dados indisponível: {error}. {remaining} arquivo(s) aguardando nova tentativa.",
                level="error",
            )
            self._db_down_logged_at = now

    # Processa um arquivo pendente: insere no banco e renomeia removendo o sufixo .vehicleBody
    def _process_file(self, src_path: str) -> bool:
        if src_path not in self._inserted:
            sql_and_params = Utils.filter_sql_created_file(src_path, self.folder)
            if not sql_and_params:
                self._reject(src_path)
                return False
            sql, params = sql_and_params
            if not self.db.execute(sql, "insert", params):
                self._reject(src_path)
                return False
            self._inserted.add(src_path)
            if self._db_down_logged_at is not None:
                Utils.log("Conexão com o banco de dados restabelecida.")
                self._db_down_logged_at = None

        new_path = src_path[:-len(PENDING_SUFFIX)] + ".jpg"
        # Sem retries aqui: se falhar, a próxima varredura tenta o rename de novo
        if not Utils.rename_file(src_path, new_path, retries=1):
            return False
        self._inserted.discard(src_path)
        Utils.log(f"{new_path} finalizado com sucesso.")
        return True

    def _reject(self, src_path: str):
        self._rejected.add(src_path)
        Utils.log(f"{src_path} não pôde ser processado e será ignorado até o serviço reiniciar.", level="error")
