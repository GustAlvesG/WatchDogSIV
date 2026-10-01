import time
from datetime import datetime

from Utils import Utils, PENDING_MARK
from Database import Database, DatabaseUnavailable
from Ftp import Ftp, FtpUnavailable

# Arquivos com este sufixo ainda não foram processados. O próprio nome do arquivo é o
# controle de pendência: depois do insert no banco o sufixo .vehicleBody é removido.
PENDING_SUFFIX = PENDING_MARK + ".jpg"

# Enquanto o FTP ou o banco estiverem fora, o erro é logado de novo só a cada este intervalo (segundos)
OUTAGE_LOG_INTERVAL_SECONDS = 300

# Entre as varreduras completas, só são listadas as pastas de placa cuja data de modificação
# mudou (quando o servidor informa essa data). A varredura completa periódica é a garantia
# caso alguma mudança passe despercebida.
FULL_SCAN_INTERVAL_SECONDS = 300


# Classe que procura e processa os arquivos pendentes no servidor FTP.
# A estrutura esperada na pasta raiz é <PLACA>/<DATA>&<COR>&<PORTÃO>.vehicleBody.jpg
class Scanner():

    # Método de inicialização da classe
    def __init__(self, ftp=None, db=None) -> None:
        # Instâncias de conexão com o FTP e com o Banco de Dados
        self.ftp = ftp if ftp is not None else Ftp()
        self.db = db if db is not None else Database()
        # Último tamanho visto de cada arquivo pendente. Um arquivo só é processado quando
        # o tamanho se repete em duas varreduras seguidas, para não pegar uma foto cujo
        # envio ao FTP ainda está em andamento.
        self._sizes = {}
        # Arquivos já inseridos no banco cujo rename falhou: só o rename é tentado de novo,
        # para não duplicar o registro.
        self._inserted = set()
        # Arquivos com nome fora do padrão ou rejeitados pelo banco: o erro é logado uma
        # vez e eles são ignorados até o serviço reiniciar (a limpeza periódica os remove).
        self._rejected = set()
        # Estado de cada pasta de placa na última listagem: (data de modificação, tem pendentes)
        self._dirs = {}
        self._last_full_scan = None
        # Pastas que estavam vazias na limpeza anterior
        self._empty_dirs = set()
        self._outages = {}

    # Loga uma indisponibilidade (FTP ou banco) sem repetir o erro a cada varredura
    def _log_outage(self, key: str, message: str):
        now = time.monotonic()
        last = self._outages.get(key)
        if last is None or now - last >= OUTAGE_LOG_INTERVAL_SECONDS:
            Utils.log(message, level="error")
            self._outages[key] = now

    def _recovered(self, key: str, message: str):
        if self._outages.pop(key, None) is not None:
            Utils.log(message)

    # Varre o FTP e processa os arquivos pendentes. Retorna quantos foram finalizados.
    # Se o FTP ou o banco estiverem inacessíveis a varredura é interrompida e os arquivos
    # continuam pendentes para a próxima.
    def scan(self) -> int:
        finished = []
        try:
            self._scan(finished)
        except FtpUnavailable as e:
            self._log_outage("ftp", f"Servidor FTP indisponível: {e}")
        except DatabaseUnavailable as e:
            self._log_outage("db", f"Banco de dados indisponível: {e}. Os arquivos pendentes serão tentados de novo.")
        return len(finished)

    def _scan(self, finished: list):
        now = time.monotonic()
        full = self._last_full_scan is None or now - self._last_full_scan >= FULL_SCAN_INTERVAL_SECONDS

        root = self.ftp.list_dir("")
        if root is None:
            raise FtpUnavailable("pasta raiz (FTP_PATH) não encontrada")
        self._recovered("ftp", "Conexão com o servidor FTP restabelecida.")

        plates = [entry for entry in root if entry.is_dir is not False]
        seen = set()
        for entry in sorted(plates, key=lambda e: e.name):
            plate = entry.name
            state = self._dirs.get(plate)
            if not full and entry.mtime is not None and state == (entry.mtime, False):
                continue

            files = self.ftp.list_dir(plate)
            if files is None:
                continue
            has_pending = False
            for file in sorted(files, key=lambda f: f.name):
                if file.is_dir or not file.name.endswith(PENDING_SUFFIX):
                    continue
                rel = f"{plate}/{file.name}"
                seen.add(rel)
                if rel in self._rejected:
                    continue
                if not self._is_ready(rel, file):
                    has_pending = True
                elif self._process_file(rel):
                    finished.append(rel)
                elif rel not in self._rejected:
                    has_pending = True
            self._dirs[plate] = (entry.mtime, has_pending)

        # Esquece o que não existe mais, para os controles não crescerem indefinidamente
        names = {entry.name for entry in plates}
        self._dirs = {plate: state for plate, state in self._dirs.items() if plate in names}
        if full:
            self._sizes = {rel: size for rel, size in self._sizes.items() if rel in seen}
            self._inserted &= seen
            self._rejected &= seen
            self._last_full_scan = now

    # Um arquivo está pronto quando o tamanho é o mesmo da varredura anterior
    def _is_ready(self, rel: str, file) -> bool:
        if rel in self._inserted:
            return True
        size = file.size if file.size is not None else self.ftp.size(rel)
        if rel in self._sizes and self._sizes[rel] == size:
            return True
        self._sizes[rel] = size
        return False

    # Processa um arquivo pendente: insere no banco e renomeia removendo o sufixo .vehicleBody
    def _process_file(self, rel: str) -> bool:
        if rel not in self._inserted:
            sql_and_params = Utils.filter_sql_created_file(rel)
            if not sql_and_params:
                self._reject(rel)
                return False
            sql, params = sql_and_params
            if not self.db.execute(sql, "insert", params):
                self._reject(rel)
                return False
            self._inserted.add(rel)
            self._recovered("db", "Conexão com o banco de dados restabelecida.")

        new_rel = rel[:-len(PENDING_SUFFIX)] + ".jpg"
        # Se o rename falhar, a próxima varredura tenta de novo (sem repetir o insert)
        if not self.ftp.rename(rel, new_rel):
            return False
        self._inserted.discard(rel)
        self._sizes.pop(rel, None)
        Utils.log(f"{new_rel} finalizado com sucesso.")
        return True

    def _reject(self, rel: str):
        self._rejected.add(rel)
        self._sizes.pop(rel, None)
        Utils.log(f"{rel} não pôde ser processado e será ignorado até o serviço reiniciar.", level="error")

    # Data de um arquivo para a retenção: a data do nome do arquivo (momento da captura)
    # ou, se o nome não seguir o padrão, a data de modificação informada pelo servidor.
    @staticmethod
    def _file_timestamp(file):
        date = file.name.split('&')[0]
        for size, date_format in ((19, "%Y-%m-%dT%H-%M-%S"), (10, "%Y-%m-%d")):
            try:
                return datetime.strptime(date[:size], date_format).timestamp()
            except ValueError:
                continue
        return file.mtime

    def _is_old(self, file, cutoff: float, extensions: tuple) -> bool:
        if not file.name.lower().endswith(extensions):
            return False
        timestamp = self._file_timestamp(file)
        return timestamp is not None and timestamp < cutoff

    # Remove do FTP as imagens com mais de `retention_days` dias e as pastas de placa vazias.
    # Só arquivos com as extensões informadas são removidos. Retorna False se o FTP estiver
    # inacessível (a limpeza deve ser tentada de novo).
    def cleanup(self, retention_days: int, extensions: tuple = (".jpg", ".jpeg")) -> bool:
        removed = 0
        empty_dirs = set()
        cutoff = time.time() - retention_days * 24 * 60 * 60
        try:
            root = self.ftp.list_dir("")
            if root is None:
                raise FtpUnavailable("pasta raiz (FTP_PATH) não encontrada")

            for entry in root:
                files = None if entry.is_dir is False else self.ftp.list_dir(entry.name)
                if files is None:
                    # Arquivo solto na pasta raiz
                    if self._is_old(entry, cutoff, extensions) and self.ftp.delete(entry.name):
                        removed += 1
                    continue

                remaining = 0
                for file in files:
                    if not file.is_dir and self._is_old(file, cutoff, extensions) \
                            and self.ftp.delete(f"{entry.name}/{file.name}"):
                        removed += 1
                    else:
                        remaining += 1

                if remaining == 0:
                    # Só remove a pasta se ela já estava vazia na limpeza anterior, para não
                    # apagar a pasta de uma placa recém-criada que ainda não recebeu a foto.
                    if entry.name in self._empty_dirs:
                        self.ftp.rmdir(entry.name)
                    else:
                        empty_dirs.add(entry.name)
        except FtpUnavailable as e:
            self._log_outage("ftp", f"Servidor FTP indisponível: {e}")
            return False

        self._empty_dirs = empty_dirs
        Utils.log(f"Limpeza concluída: {removed} arquivo(s) removido(s) (retenção de {retention_days} dias).")
        return True
