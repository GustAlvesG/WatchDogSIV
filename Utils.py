import logging
import os
import time
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv
load_dotenv(override=True)

# Sufixo que o equipamento de LPR usa nos arquivos ainda não processados. Ele é removido
# do nome do arquivo depois que o registro é inserido no banco.
PENDING_MARK = ".vehicleBody"

# Idade mínima (em segundos) para remover uma pasta vazia na limpeza. Evita apagar a pasta
# de uma placa que o equipamento acabou de criar e ainda não recebeu a foto.
EMPTY_DIR_MIN_AGE_SECONDS = 3600

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
    # O caminho relativo é calculado a partir da raiz monitorada (`folder`, por padrão o
    # FOLDER_PATH configurado), que deve conter diretamente as pastas de placa:
    #   <folder>/<PLACA>/<DATA>&<COR>&<PORTÃO>.vehicleBody.jpg
    @staticmethod
    def filter_sql_created_file(file_path: str, folder: str = None):
        try:
            folder = folder or os.getenv("FOLDER_PATH")
            if not folder:
                raise Exception("FOLDER_PATH não configurado.")

            rel = os.path.relpath(file_path, folder)
            if rel.startswith(".."):
                raise Exception(f"Arquivo fora da pasta monitorada: {file_path}")

            # O banco guarda o nome final do arquivo (sem o sufixo .vehicleBody), que é o
            # nome que ele terá depois de renomeado.
            rel = rel.replace("\\", "/").replace(PENDING_MARK, "")

            subpath = rel.split("/")
            if len(subpath) < 2:
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
            Utils.log(f"Erro filter_sql_created_file(): {e} - {file_path}", level="error")
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

    # Remove imagens com mais de `retention_days` dias e diretórios vazios dentro de `folder`.
    # Só arquivos com as extensões informadas são removidos, para que um FOLDER_PATH apontando
    # para a pasta errada não apague outros tipos de arquivo.
    # Um erro em um arquivo/pasta específico não interrompe a limpeza dos demais.
    @staticmethod
    def clear_old_files(folder: str, retention_days: int, extensions: tuple = (".jpg", ".jpeg")):
        if not folder or not os.path.isdir(folder):
            Utils.log(f"clear_old_files(): pasta inválida ou indisponível '{folder}'.", level="error")
            return

        removed = 0
        now = time.time()
        cutoff = now - retention_days * 24 * 60 * 60
        dir_cutoff = now - EMPTY_DIR_MIN_AGE_SECONDS

        try:
            for root, dirs, files in os.walk(folder, topdown=False):
                for file in files:
                    if not file.lower().endswith(extensions):
                        continue
                    path = os.path.join(root, file)
                    try:
                        # mtime (última modificação) é usado ao invés de ctime: no Linux o ctime
                        # também muda com qualquer alteração de metadado (ex: o rename feito após
                        # processar o arquivo), o que "zeraria" a idade do arquivo indevidamente.
                        if os.path.getmtime(path) < cutoff:
                            os.remove(path)
                            removed += 1
                    except FileNotFoundError:
                        continue
                    except Exception as e:
                        Utils.log(f"Erro ao remover arquivo '{path}': {e}", level="error")

                # Verifica se a pasta ficou vazia e remove (nunca remove a pasta raiz monitorada)
                try:
                    if root != folder and not os.listdir(root) and os.path.getmtime(root) < dir_cutoff:
                        os.rmdir(root)
                except FileNotFoundError:
                    continue
                except Exception as e:
                    Utils.log(f"Erro ao remover pasta '{root}': {e}", level="error")
        except Exception as e:
            Utils.log(f"Erro clear_old_files(): {e}", level="error")
        else:
            Utils.log(f"Limpeza concluída: {removed} arquivo(s) removido(s) (retenção de {retention_days} dias).")
