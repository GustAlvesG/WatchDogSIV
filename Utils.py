import logging
import os
import time
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv
load_dotenv(override=True)

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

    # Função que interpreta o caminho do arquivo criado e monta a query SQL parametrizada.
    # O caminho relativo é calculado a partir do FOLDER_PATH configurado (a raiz monitorada),
    # ao invés de procurar uma string fixa como "LPR" no caminho — isso evita quebrar por
    # diferença de maiúsculas/minúsculas ou pelo nome do ponto de montagem variar entre
    # ambientes (ex: //192.168.10.3/lpr no Windows vs /mnt/lpr no Linux).
    @staticmethod
    def filter_sql_created_file(file_path: str):
        try:
            folder = os.getenv("FOLDER_PATH")
            if not folder:
                raise Exception("FOLDER_PATH não configurado.")

            rel = os.path.relpath(file_path, folder)
            if rel.startswith(".."):
                raise Exception(f"Arquivo fora da pasta monitorada: {file_path}")

            subpath = rel.replace("\\", "/").split("/")
            if len(subpath) < 2:
                raise Exception("Erro no filtro do caminho.")

            plate = subpath[0]
            date_and_color = subpath[1].split('&')

            # Precisamos de 3 campos: data, cor e portão (gate)
            if len(date_and_color) < 3:
                raise Exception("Data, cor e/ou portão em formato incorreto.")

            date = date_and_color[0]
            color = date_and_color[1]
            gate = date_and_color[2].replace(".vehicleBody.jpg", "").replace(".jpg", "")

            keys = ["plate", "color", "entry_date", "file", "gate"]
            values = [plate, color, date, rel.replace("\\", "/"), gate]
            return Utils.mount_sql(keys, values)
        except Exception as e:
            Utils.log(f"Erro filter_sql_created_file(): {e}", level="error")
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

    # Função para renomear um arquivo, com pequenas tentativas de retry
    # (o arquivo pode ainda estar sendo escrito/travado pelo equipamento de LPR no instante do evento)
    @staticmethod
    def rename_file(old_name: str, new_name: str, retries: int = 5, delay: float = 1.0):
        for attempt in range(1, retries + 1):
            try:
                os.rename(old_name, new_name)
            except FileNotFoundError:
                Utils.log(f"Erro rename_file(): arquivo não encontrado - {old_name}", level="error")
                return False
            except Exception as e:
                if attempt == retries:
                    Utils.log(f"Erro rename_file(): {e}", level="error")
                    return False
                time.sleep(delay)
            else:
                return True
        return False

    # Remove arquivos com mais de `retention_days` dias e diretórios vazios dentro de `folder`.
    # Um erro em um arquivo/pasta específico não interrompe a limpeza dos demais.
    @staticmethod
    def clear_old_files(folder: str, retention_days: int):
        if not folder or not os.path.isdir(folder):
            Utils.log(f"clear_old_files(): pasta inválida ou indisponível '{folder}'.", level="error")
            return

        removed = 0
        cutoff = time.time() - retention_days * 24 * 60 * 60

        try:
            for root, dirs, files in os.walk(folder, topdown=False):
                for file in files:
                    path = os.path.join(root, file)
                    try:
                        # mtime (última modificação) é usado ao invés de ctime: no Linux o ctime
                        # também muda com qualquer alteração de metadado (ex: o rename feito após
                        # processar o arquivo), o que "zeraria" a idade do arquivo indevidamente.
                        if os.path.getmtime(path) < cutoff or file == 'Thumbs.db':
                            os.remove(path)
                            removed += 1
                    except FileNotFoundError:
                        continue
                    except Exception as e:
                        Utils.log(f"Erro ao remover arquivo '{path}': {e}", level="error")

                # Verifica se a pasta ficou vazia e remove (nunca remove a pasta raiz monitorada)
                try:
                    if root != folder and not os.listdir(root):
                        os.rmdir(root)
                except Exception as e:
                    Utils.log(f"Erro ao remover pasta '{root}': {e}", level="error")
        except Exception as e:
            Utils.log(f"Erro clear_old_files(): {e}", level="error")
        else:
            Utils.log(f"Limpeza concluída: {removed} arquivo(s) removido(s) (retenção de {retention_days} dias).")
