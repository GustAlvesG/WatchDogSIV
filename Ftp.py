# Importando as bibliotecas necessárias
import calendar
import ftplib
import os
import posixpath
import time
from collections import namedtuple

from Utils import Utils

# Carregando as variáveis de ambiente
from dotenv import load_dotenv
load_dotenv(override=True)

# Item de uma listagem de pasta. `is_dir`, `size` e `mtime` ficam None quando o servidor
# não informa (servidores sem suporte a MLSD só devolvem os nomes).
Entry = namedtuple("Entry", ["name", "is_dir", "size", "mtime"])


# Erro levantado quando o servidor FTP está inacessível (falha de conexão/rede/login).
# A conexão é descartada e refeita na próxima operação.
class FtpUnavailable(Exception):
    pass


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "sim")


# Classe para gerenciar a conexão com o servidor FTP onde ficam as imagens.
# Todos os caminhos recebidos pelos métodos são relativos à pasta raiz (FTP_PATH).
class Ftp():
    # Método de inicialização da classe
    def __init__(self) -> None:
        # Carregando as informações de conexão das variáveis de ambiente
        self.host = os.getenv("FTP_HOST")
        self.port = int(os.getenv("FTP_PORT", "21"))
        self.user = os.getenv("FTP_USERNAME", "")
        self.password = os.getenv("FTP_PASSWORD", "")
        self.path = os.getenv("FTP_PATH", "/") or "/"
        self.passive = _env_bool("FTP_PASSIVE", "true")
        self.tls = _env_bool("FTP_TLS", "false")
        self.timeout = float(os.getenv("FTP_TIMEOUT_SECONDS", "30"))
        self.encoding = os.getenv("FTP_ENCODING", "utf-8")
        self._ftp = None
        self._root = None
        self._use_mlsd = True

    # Método para conectar ao servidor FTP (não faz nada se já estiver conectado)
    def connect(self):
        if self._ftp is not None:
            return
        ftp_class = ftplib.FTP_TLS if self.tls else ftplib.FTP
        ftp = ftp_class(timeout=self.timeout, encoding=self.encoding)
        try:
            ftp.connect(self.host, self.port)
            ftp.login(self.user, self.password)
            if self.tls:
                ftp.prot_p()
            ftp.set_pasv(self.passive)
            # Entra na pasta raiz e guarda o caminho absoluto dela
            ftp.cwd(self.path)
            self._root = ftp.pwd()
        except ftplib.all_errors as e:
            ftp.close()
            raise FtpUnavailable(str(e)) from e
        self._ftp = ftp
        Utils.log(f"Conectado ao FTP {self.host}:{self.port}, pasta {self._root}.")

    # Executa uma operação na conexão. Erros de resposta do servidor (5xx, ex: arquivo
    # não encontrado) são repassados; qualquer outro erro derruba a conexão.
    def _call(self, func):
        self.connect()
        try:
            return func(self._ftp)
        except ftplib.error_perm:
            raise
        except UnicodeError as e:
            self.close()
            raise FtpUnavailable(
                f"nome de arquivo fora da codificação '{self.encoding}' (ajuste FTP_ENCODING, ex: latin-1): {e}"
            ) from e
        except ftplib.all_errors as e:
            self.close()
            raise FtpUnavailable(str(e)) from e

    def _abs(self, rel: str) -> str:
        return posixpath.join(self._root, rel) if rel else self._root

    # Lista uma pasta. Retorna None se o caminho não for uma pasta (ou não existir).
    def list_dir(self, rel: str = ""):
        self.connect()
        path = self._abs(rel)
        try:
            self._call(lambda ftp: ftp.cwd(path))
        except ftplib.error_perm:
            return None

        # MLSD devolve tipo, tamanho e data de cada item em uma única listagem
        if self._use_mlsd:
            try:
                return self._call(self._mlsd)
            except ftplib.error_perm as e:
                if str(e)[:3] not in ("500", "502"):
                    Utils.log(f"Erro ao listar '{path}': {e}", level="error")
                    return []
                Utils.log("Servidor FTP sem suporte a MLSD, usando listagem simples (NLST).")
                self._use_mlsd = False

        try:
            names = self._call(lambda ftp: ftp.nlst())
        except ftplib.error_perm:
            # Alguns servidores respondem com erro quando a pasta está vazia
            return []
        entries = []
        for name in names:
            # Alguns servidores devolvem o caminho completo ao invés de só o nome
            name = posixpath.basename(name.rstrip("/"))
            if name and name not in (".", ".."):
                entries.append(Entry(name, None, None, None))
        return entries

    @staticmethod
    def _mlsd(ftp):
        entries = []
        for name, facts in ftp.mlsd():
            kind = facts.get("type", "").lower()
            if kind in ("cdir", "pdir") or name in (".", ".."):
                continue
            size = facts.get("size", "")
            entries.append(Entry(name, kind == "dir", int(size) if size.isdigit() else None,
                                 Ftp._parse_modify(facts.get("modify"))))
        return entries

    # Converte a data do MLSD (YYYYMMDDHHMMSS, em UTC) para timestamp
    @staticmethod
    def _parse_modify(value):
        try:
            return calendar.timegm(time.strptime(value[:14], "%Y%m%d%H%M%S"))
        except (TypeError, ValueError):
            return None

    # Tamanho de um arquivo em bytes, ou None se o servidor não informar
    def size(self, rel: str):
        self.connect()
        path = self._abs(rel)

        def _size(ftp):
            # Vários servidores só respondem ao SIZE em modo binário
            ftp.voidcmd("TYPE I")
            return ftp.size(path)

        try:
            return self._call(_size)
        except ftplib.error_perm:
            return None

    # Método para renomear um arquivo
    def rename(self, rel_from: str, rel_to: str) -> bool:
        self.connect()
        path_from, path_to = self._abs(rel_from), self._abs(rel_to)
        try:
            self._call(lambda ftp: ftp.rename(path_from, path_to))
        except ftplib.error_perm as e:
            Utils.log(f"Erro ao renomear '{path_from}': {e}", level="error")
            return False
        return True

    # Método para apagar um arquivo
    def delete(self, rel: str) -> bool:
        self.connect()
        path = self._abs(rel)
        try:
            self._call(lambda ftp: ftp.delete(path))
        except ftplib.error_perm as e:
            Utils.log(f"Erro ao remover arquivo '{path}': {e}", level="error")
            return False
        return True

    # Método para apagar uma pasta (o servidor recusa se ela não estiver vazia)
    def rmdir(self, rel: str) -> bool:
        self.connect()
        path = self._abs(rel)

        def _rmd(ftp):
            # Sai da pasta antes de removê-la
            ftp.cwd(self._root)
            ftp.rmd(path)

        try:
            self._call(_rmd)
        except ftplib.error_perm as e:
            Utils.log(f"Erro ao remover pasta '{path}': {e}", level="error")
            return False
        return True

    # Descarta a conexão atual (a próxima operação reconecta)
    def close(self):
        if self._ftp is not None:
            try:
                self._ftp.close()
            except Exception:
                pass
            self._ftp = None

    # Encerra a sessão avisando o servidor
    def quit(self):
        if self._ftp is not None:
            try:
                self._ftp.quit()
            except Exception:
                pass
        self.close()
