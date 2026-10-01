import logging
import os
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv
load_dotenv(override=True)

# Sufixo que o equipamento de LPR usa nos arquivos ainda não processados. Ele é removido
# do nome do arquivo depois que o registro é inserido no banco.
PENDING_MARK = ".vehicleBody"

# Configuração central de logging (com rotação, para o serviço não crescer para sempre em log.log)
_logger = logging.getLogger("watchdog_lpr")
if not _logger.handlers:
    _logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())

    _formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    _file_handler = RotatingFileHandler(
        os.getenv("LOG_FILE", "log.log"),
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=5,
        encoding="utf-8",
    )
    _file_handler.setFormatter(_formatter)
    _logger.addHandler(_file_handler)

    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(_formatter)
    _logger.addHandler(_console_handler)


class Utils():

    # Função para registrar logs
    @staticmethod
    def log(msg, level="info"):
        getattr(_logger, level, _logger.info)(msg)

    # Função que interpreta o caminho do arquivo e monta a query SQL parametrizada.
    # `rel_path` é o caminho relativo à pasta raiz do FTP, que deve conter diretamente as
    # pastas de placa: <PLACA>/<DATA>&<COR>&<PORTÃO>.vehicleBody.jpg
    @staticmethod
    def filter_sql_created_file(rel_path: str):
        try:
            # O banco guarda o nome final do arquivo (sem o sufixo .vehicleBody), que é o
            # nome que ele terá depois de renomeado.
            rel = rel_path.replace("\\", "/").strip("/").replace(PENDING_MARK, "")

            subpath = rel.split("/")
            if len(subpath) != 2:
                raise Exception("Erro no filtro do caminho.")

            plate = subpath[0]
            date_and_color = subpath[1].split('&')

            # Precisamos de 3 campos: data, cor e portão (gate)
            if len(date_and_color) < 3:
                raise Exception("Data, cor e/ou portão em formato incorreto.")

            date = date_and_color[0]
            color = date_and_color[1]
            gate = date_and_color[2].replace(".jpg", "")

            keys = ["plate", "color", "entry_date", "file", "gate"]
            values = [plate, color, date, rel, gate]
            return Utils.mount_sql(keys, values)
        except Exception as e:
            Utils.log(f"Erro filter_sql_created_file(): {e} - {rel_path}", level="error")
            return None

    # Função que monta a query SQL de forma parametrizada (evita SQL injection e problemas de quoting)
    @staticmethod
    def mount_sql(keys: list, values: list):
        try:
            placeholders = ', '.join(['%s'] * len(values))
            sql = f"INSERT INTO parkings (" + ', '.join(keys) + f") VALUES ({placeholders});"
        except Exception as e:
            Utils.log(f"Erro mount_sql(): {e}", level="error")
            return None
        else:
            return sql, tuple(values)
