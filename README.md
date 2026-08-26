# WatchDog LPR

Serviço que monitora a pasta onde o equipamento de LPR (leitura de placas) grava as
fotos dos veículos, insere os registros (placa, cor, data e portão) em um banco MySQL
e remove periodicamente os arquivos com mais de `RETENTION_DAYS` dias.

## Estrutura

- `WatchDog.py` — ponto de entrada: inicia o monitoramento e o loop de limpeza periódica.
- `WatchDogEvents.py` — trata os eventos de criação de arquivo e faz a inserção no banco.
- `Utils.py` — parsing do caminho/SQL, logging (com rotação) e limpeza de arquivos antigos.
- `Database.py` — conexão com o MySQL.

## Deploy em servidor Linux

1. Copie o projeto para o servidor (ex: `/opt/watchdog_lpr`).
2. Execute o instalador como root (monta o compartilhamento Windows/SMB de origem,
   cria o venv, instala dependências, gera `.env` e o serviço systemd):

   ```bash
   sudo ./install.sh
   ```

   O script vai pedir (ou usar variáveis de ambiente já exportadas — veja abaixo) o
   caminho do compartilhamento Windows (ex: `//192.168.10.3/lpr`), o ponto de montagem
   local (ex: `/mnt/lpr`) e as credenciais SMB. Ele instala o `cifs-utils`, salva as
   credenciais em `/etc/samba/lpr-credentials` (permissão 600), adiciona a entrada em
   `/etc/fstab` (com `_netdev`, para montar automaticamente após o boot com rede
   disponível) e já ajusta `FOLDER_PATH` no `.env` para o ponto de montagem.

   Para automatizar sem prompts (ex: em provisionamento), exporte antes de rodar:

   ```bash
   export SMB_SHARE=//192.168.10.3/lpr
   export SMB_MOUNT_POINT=/mnt/lpr
   export SMB_USERNAME=usuario_smb
   export SMB_PASSWORD=senha_smb
   sudo -E ./install.sh
   ```

   Flags disponíveis:

   ```bash
   sudo ./install.sh --no-mount      # pula a montagem (você já montou manualmente)
   sudo ./install.sh --no-service    # pula a instalação do serviço systemd
   ./install.sh --no-mount --no-service   # só venv/deps/.env, sem precisar de root
   ```

3. Confira/edite `.env` com os demais dados reais (`DB_HOST`, `DB_DATABASE`,
   `DB_USERNAME`, `DB_PASSWORD`, etc. — veja `.env.example`).
4. Inicie o serviço:

   ```bash
   sudo systemctl start watchdog-lpr
   sudo systemctl status watchdog-lpr
   journalctl -u watchdog-lpr -f
   ```

## Por que `PollingObserver`?

A pasta monitorada é um compartilhamento de rede (SMB/CIFS/NFS). O observer padrão do
`watchdog` depende de `inotify` no Linux, que **não** recebe eventos de forma confiável
(ou não recebe nenhum) em sistemas de arquivos de rede. Por isso o serviço usa
`PollingObserver`, que varre a pasta periodicamente.

## Limpeza automática

Antes, a limpeza de arquivos antigos era um script separado (`Clear.py`), rodado por
fora. Agora ela roda dentro do próprio `WatchDog.py`, a cada `CLEANUP_INTERVAL_SECONDS`
(padrão 1h), removendo arquivos com mais de `RETENTION_DAYS` dias (padrão 15) e pastas
vazias.
