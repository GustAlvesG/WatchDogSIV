# WatchDog LPR

Serviço que monitora a pasta onde o equipamento de LPR (leitura de placas) grava, via
FTP, as fotos dos veículos, insere os registros (placa, cor, data e portão) em um banco
MySQL e remove periodicamente as imagens com mais de `RETENTION_DAYS` dias.

## Estrutura

- `WatchDog.py` — ponto de entrada: loop de varredura da pasta e de limpeza periódica.
- `Scanner.py` — procura os arquivos pendentes, faz a inserção no banco e renomeia.
- `Utils.py` — parsing do caminho/SQL, logging (com rotação) e limpeza de arquivos antigos.
- `Database.py` — conexão com o MySQL.
- `tests/` — testes unitários.

## Como funciona

O equipamento de LPR envia as fotos por FTP para o próprio servidor onde o serviço roda,
dentro da pasta `FOLDER_PATH`, neste formato:

```
<FOLDER_PATH>/<PLACA>/<DATA>&<COR>&<PORTÃO>.vehicleBody.jpg
```

A cada `SCAN_INTERVAL_SECONDS` o serviço varre a pasta atrás de arquivos
`.vehicleBody.jpg`. Para cada um, insere o registro na tabela `parkings` e renomeia o
arquivo removendo o `.vehicleBody`. A coluna `file` guarda o caminho já com o nome
final (ex: `QXA4C30/2026-08-26T09-54-58&Prata&A.jpg`).

O sufixo `.vehicleBody` é, portanto, o controle de pendência:

- Um arquivo só é processado depois de ficar `FILE_SETTLE_SECONDS` sem ser modificado,
  para não pegar uma foto cujo upload ainda está em andamento.
- Se o banco estiver fora do ar, o arquivo continua pendente e é tentado de novo na
  próxima varredura (não é preciso reiniciar o serviço).
- Um arquivo com nome fora do padrão, ou recusado pelo banco, é registrado no log uma
  vez e ignorado até o serviço reiniciar.

Opcionalmente, um índice `UNIQUE` na coluna `file` da tabela `parkings` garante no
próprio banco que uma foto nunca seja registrada duas vezes; o serviço trata o erro de
duplicidade como "já inserido" e apenas finaliza o arquivo.

## Deploy em servidor Linux

1. Copie o projeto para o servidor (ex: `/opt/watchdog_lpr`).
2. Execute o instalador como root (instala e configura o servidor FTP, cria o venv,
   instala dependências, gera `.env` e o serviço systemd):

   ```bash
   sudo ./install.sh
   ```

   O script vai pedir (ou usar variáveis de ambiente já exportadas — veja abaixo) o
   usuário e a senha FTP que o equipamento vai usar e a pasta onde as imagens serão
   gravadas (ex: `/srv/lpr`). Ele instala o `vsftpd`, cria o usuário (sem acesso a
   shell e preso a essa pasta), libera o login por FTP somente para ele, coloca o
   usuário do serviço no grupo do usuário FTP (para poder renomear e apagar os arquivos)
   e já ajusta `FOLDER_PATH` no `.env`.

   Para automatizar sem prompts (ex: em provisionamento), exporte antes de rodar:

   ```bash
   export FTP_USER=lpr
   export FTP_PASSWORD=senha_ftp
   export FTP_DIR=/srv/lpr
   sudo -E ./install.sh
   ```

   Flags disponíveis:

   ```bash
   sudo ./install.sh --no-ftp        # pula o servidor FTP (já configurado por fora)
   sudo ./install.sh --no-service    # pula a instalação do serviço systemd
   ./install.sh --no-ftp --no-service   # só venv/deps/.env, sem precisar de root
   ```

3. Confira/edite `.env` com os demais dados reais (`DB_HOST`, `DB_DATABASE`,
   `DB_USERNAME`, `DB_PASSWORD`, etc. — veja `.env.example`).
4. Inicie o serviço:

   ```bash
   sudo systemctl start watchdog-lpr
   sudo systemctl status watchdog-lpr
   journalctl -u watchdog-lpr -f
   ```

5. Configure o equipamento de LPR para enviar as imagens por FTP para o servidor:
   porta 21, o usuário e a senha definidos na instalação e a pasta raiz (`/`), que
   corresponde a `FOLDER_PATH`. As pastas de placa devem ficar diretamente na raiz.

   Se houver firewall no servidor, libere a porta 21 e a faixa do modo passivo
   (40000-40100 por padrão; ajustável com `FTP_PASV_MIN_PORT`/`FTP_PASV_MAX_PORT`).

   O FTP trafega usuário, senha e imagens sem criptografia: mantenha o equipamento e o
   servidor na mesma rede interna.

## Limpeza automática

A cada `CLEANUP_INTERVAL_SECONDS` (padrão 1h) o serviço remove as imagens com mais de
`RETENTION_DAYS` dias (padrão 15) e as pastas que ficaram vazias. Só arquivos com as
extensões de `CLEANUP_EXTENSIONS` (padrão `.jpg,.jpeg`) são apagados; `RETENTION_DAYS=0`
desativa a limpeza.

## Testes

```bash
./venv/bin/python -m unittest discover -s tests -t .
```
