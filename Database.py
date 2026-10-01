# Importando as bibliotecas necessárias
import os
import mysql.connector as mysql
from mysql.connector import errorcode
from Utils import Utils

# Carregando as variáveis de ambiente
from dotenv import load_dotenv
load_dotenv(override=True)

# Tempo máximo (em segundos) para estabelecer a conexão. Sem isso, um banco inacessível
# poderia travar o serviço indefinidamente.
CONNECTION_TIMEOUT_SECONDS = 10


# Erro levantado quando o banco está inacessível (falha de conexão/rede). Diferente de um
# erro nos dados, a mesma operação pode ser repetida mais tarde.
class DatabaseUnavailable(Exception):
    pass


# Classe para gerenciar a conexão com o banco de dados
class Database():
    # Método de inicialização da classe
    def __init__(self) -> None:
        Utils.log("Iniciando conexão com o banco de dados.")
        # Carregando as informações de conexão do banco de dados das variáveis de ambiente
        self.host = os.getenv("DB_HOST")
        self.database = os.getenv("DB_DATABASE")
        self.user = os.getenv("DB_USERNAME")
        self.password = os.getenv("DB_PASSWORD")
        self.connection = None
        self.cursor = None

    # Método para conectar ao banco de dados
    def connect(self):
        # Estabelecendo a conexão
        self.connection = mysql.connect(
            host=self.host,
            user=self.user,
            password=self.password,
            database=self.database,
            connection_timeout=CONNECTION_TIMEOUT_SECONDS
        )
        # Criando um cursor para executar consultas SQL
        self.cursor = self.connection.cursor()

    # Método para executar uma consulta SQL. `params` permite queries parametrizadas
    # (evita SQL injection e problemas de escaping/quoting de valores string).
    # Levanta DatabaseUnavailable se o banco estiver inacessível.
    def execute(self, sql: str, type, params: tuple = None):
        if not sql:
            return None
        try:
            # Conectando ao banco de dados
            try:
                self.connect()
            except mysql.Error as e:
                raise DatabaseUnavailable(str(e)) from e
            # Executando a consulta
            self.cursor.execute(sql, params)
            if type == "select":
                # Retornando todos os resultados
                return self.fetchall()
            elif type == "insert":
                # Commitando as alterações
                self.connection.commit()
            elif type == "one":
                # Retornando o primeiro resultado
                return self.fetchone()
        except DatabaseUnavailable:
            raise
        except (mysql.InterfaceError, mysql.OperationalError) as e:
            # Conexão perdida no meio da operação
            raise DatabaseUnavailable(str(e)) from e
        except Exception as e:
            # Registro que já existe (tabela com índice UNIQUE): o insert já foi feito antes,
            # então é tratado como sucesso para o arquivo poder ser finalizado.
            if type == "insert" and getattr(e, "errno", None) == errorcode.ER_DUP_ENTRY:
                Utils.log(f"Registro já existente no banco, insert ignorado: {params}")
                return True
            Utils.log(f"Erro execute(): {e}", level="error")
            # Rollback em caso de erro (somente se a conexão chegou a ser estabelecida)
            if self.connection is not None:
                try:
                    self.connection.rollback()
                except Exception:
                    pass
            return False
        else:
            return True
        finally:
            # Desconecta do banco de dados
            self.disconnect()

    # Método para buscar todos os resultados de uma consulta
    def fetchall(self):
        # Retornando todos os resultados
        return self.cursor.fetchall()

    # Método para buscar o primeiro resultado de uma consulta
    def fetchone(self):
        # Retornando o primeiro resultado
        return self.cursor.fetchone()

    # Método para desconectar do banco de dados
    def disconnect(self):
        # Fechando cursor e conexão, se existirem
        try:
            if self.cursor is not None:
                self.cursor.close()
        except Exception:
            pass
        try:
            if self.connection is not None:
                self.connection.close()
        except Exception:
            pass
        finally:
            self.cursor = None
            self.connection = None
