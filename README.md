# WatchDog LPR

Serviço que se conecta ao servidor FTP onde o equipamento de LPR (leitura de placas)
grava as fotos dos veículos, insere os registros (placa, cor, data e portão) em um banco
MySQL e remove periodicamente do FTP as imagens com mais de `RETENTION_DAYS` dias.

Os arquivos nunca são baixados: tudo é feito no próprio servidor FTP (listar, renomear
e apagar).

## Estrutura

- `WatchDog.py` — ponto de entrada: loop de varredura do FTP e de limpeza periódica.
- `Scanner.py` — procura os arquivos pendentes, faz a inserção no banco, renomeia e limpa.
- `Ftp.py` — conexão com o servidor FTP.
- `Database.py` — conexão com o MySQL.
- `Utils.py` — parsing do caminho/SQL e logging (com rotação).
- `tests/` — testes unitários.

## Como funciona

Dentro da pasta `FTP_PATH` do servidor FTP, as fotos devem estar neste formato:

```
<PLACA>/<DATA>&<COR>&<PORTÃO>.vehicleBody.jpg
```

A cada `SCAN_INTERVAL_SECONDS` o serviço lista o FTP atrás de arquivos
`.vehicleBody.jpg`. Para cada um, insere o registro na tabela `parkings` e renomeia o
arquivo no FTP removendo o `.vehicleBody`. A coluna `file` guarda o caminho já com o
nome final (ex: `QXA4C30/2026-08-26T09-54-58&Prata&A.jpg`).

O sufixo `.vehicleBody` é, portanto, o controle de pendência:

- Um arquivo só é processado quando o tamanho dele é o mesmo em duas varreduras
  seguidas, para não pegar uma foto cujo envio ainda está em andamento.
- Se o FTP ou o banco estiverem fora do ar, o arquivo continua pendente e é tentado de
  novo na próxima varredura (não é preciso reiniciar o serviço).
- Um arquivo com nome fora do padrão, ou recusado pelo banco, é registrado no log uma
  vez e ignorado até o serviço reiniciar.

Quando o servidor FTP informa a data de modificação das pastas (comando MLSD), só as
pastas de placa que mudaram são listadas a cada varredura, com uma varredura completa a
cada 5 minutos. Em servidores sem MLSD todas as pastas são listadas a cada varredura.

Opcionalmente, um índice `UNIQUE` na coluna `file` da tabela `parkings` garante no
próprio banco que uma foto nunca seja registrada duas vezes; o serviço trata o erro de
duplicidade como "já inserido" e apenas finaliza o arquivo.

## Configuração (`.env`)

| Variável | Descrição |
|---|---|
| `FTP_HOST` | Endereço do servidor FTP. |
| `FTP_PORT` | Porta do servidor FTP (padrão 21). |
| `FTP_USERNAME` / `FTP_PASSWORD` | Usuário e senha. Precisa de permissão para listar, renomear e apagar. |
| `FTP_PATH` | Pasta, dentro do servidor, que contém diretamente as pastas de placa (padrão `/`). |
| `FTP_PASSIVE` | Modo passivo (padrão `true`). |
| `FTP_TLS` | `true` para FTP sobre TLS (FTPS explícito). Padrão `false`. |
| `FTP_TIMEOUT_SECONDS` | Tempo máximo de espera por resposta do servidor (padrão 30). |
| `FTP_ENCODING` | Codificação dos nomes de arquivo (padrão `utf-8`; use `latin-1` se o servidor não trabalhar em UTF-8). |
| `DB_HOST`, `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD` | Conexão com o MySQL. |
| `SCAN_INTERVAL_SECONDS` | Intervalo entre as varreduras (padrão 5). |
| `RETENTION_DAYS`, `CLEANUP_INTERVAL_SECONDS`, `CLEANUP_EXTENSIONS` | Limpeza automática (veja abaixo). |
| `LOG_LEVEL`, `LOG_FILE` | Logging. |

## Deploy em servidor Linux

1. Copie o projeto para o servidor (ex: `/opt/watchdog_lpr`).
2. Execute o instalador como root (cria o venv, instala dependências, gera `.env` e o
   serviço systemd):

   ```bash
   sudo ./install.sh
   ```

   Para instalar só o venv/dependências/`.env`, sem o serviço e sem precisar de root:

   ```bash
   ./install.sh --no-service
   ```

3. Edite o `.env` com os dados do servidor FTP e do banco (veja a tabela acima).
4. Inicie o serviço:

   ```bash
   sudo systemctl start watchdog-lpr
   sudo systemctl status watchdog-lpr
   journalctl -u watchdog-lpr -f
   ```

## Limpeza automática

A cada `CLEANUP_INTERVAL_SECONDS` (padrão 1h) o serviço remove do FTP as imagens com
mais de `RETENTION_DAYS` dias (padrão 15). A idade é calculada pela data que está no
nome do arquivo; se o nome não seguir o padrão, pela data de modificação informada pelo
servidor. Só arquivos com as extensões de `CLEANUP_EXTENSIONS` (padrão `.jpg,.jpeg`) são
apagados; `RETENTION_DAYS=0` desativa a limpeza.

Uma pasta de placa vazia só é removida se continuar vazia em duas limpezas seguidas,
para não apagar a pasta de uma placa recém-criada que ainda não recebeu a foto.

## Testes

```bash
./venv/bin/python -m unittest discover -s tests -t .
```

Os testes de `Ftp.py` sobem um servidor FTP local e só rodam com o `pyftpdlib`
instalado (`./venv/bin/pip install pyftpdlib`); sem ele, são pulados.
