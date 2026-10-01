#!/usr/bin/env bash
#
# Instala o WatchDog LPR em um servidor Linux:
#   - cria o ambiente virtual Python e instala as dependências
#   - cria o arquivo .env a partir do .env.example (se ainda não existir)
#   - instala e habilita o serviço systemd
#
# Uso:
#   sudo ./install.sh                 # instala tudo: venv/deps + serviço systemd
#   ./install.sh --no-service         # só venv/deps/.env, sem privilégios de root
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

INSTALL_SERVICE=true
for arg in "$@"; do
    case "$arg" in
        --no-service) INSTALL_SERVICE=false ;;
        *) echo "Argumento desconhecido: $arg" >&2; exit 1 ;;
    esac
done

log() { echo "[install] $*"; }
err() { echo "[install] ERRO: $*" >&2; }

# --- 0. Precisa de root para instalar o serviço -----------------------------
if [[ "$INSTALL_SERVICE" == true ]] && [[ $EUID -ne 0 ]]; then
    err "instalar o serviço systemd requer root."
    err "Rode novamente com: sudo ./install.sh"
    err "(ou use --no-service para pular essa etapa sem precisar de root)"
    exit 1
fi

# Usuário/grupo que vai rodar o serviço
SERVICE_USER="${SUDO_USER:-$(whoami)}"
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"

# --- 1. Verifica dependências do sistema -----------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    err "python3 não encontrado. Instale o Python 3 antes de continuar."
    exit 1
fi

PY_VERSION="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
log "Python detectado: $PY_VERSION"

if ! python3 -c "import venv" >/dev/null 2>&1; then
    err "módulo venv indisponível. Em distros baseadas em Debian/Ubuntu: sudo apt-get install -y python3-venv"
    exit 1
fi

# --- 2. Cria o ambiente virtual ---------------------------------------------
if [[ ! -d "venv" ]]; then
    log "Criando ambiente virtual em ./venv"
    python3 -m venv venv
else
    log "Ambiente virtual já existe em ./venv, reutilizando."
fi

log "Instalando dependências (requirements.txt)"
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# --- 3. Configura o .env -----------------------------------------------------
if [[ ! -f ".env" ]]; then
    log "Criando .env a partir de .env.example"
    cp .env.example .env
else
    log ".env já existe, mantendo o arquivo atual."
fi

# O .env guarda as senhas do FTP e do banco: só o usuário do serviço (e o root) pode ler
if [[ $EUID -eq 0 ]]; then
    chown "$SERVICE_USER:$SERVICE_GROUP" .env
fi
chmod 600 .env

# --- 4. Serviço systemd -------------------------------------------------------
if [[ "$INSTALL_SERVICE" == true ]]; then
    UNIT_FILE="/etc/systemd/system/watchdog-lpr.service"

    log "Gerando unidade systemd para o usuário '$SERVICE_USER' em $SCRIPT_DIR"
    sed \
        -e "s#WorkingDirectory=.*#WorkingDirectory=$SCRIPT_DIR#" \
        -e "s#EnvironmentFile=.*#EnvironmentFile=$SCRIPT_DIR/.env#" \
        -e "s#ExecStart=.*#ExecStart=$SCRIPT_DIR/venv/bin/python $SCRIPT_DIR/WatchDog.py#" \
        -e "s#^User=.*#User=$SERVICE_USER#" \
        -e "s#^Group=.*#Group=$SERVICE_GROUP#" \
        "watchdog-lpr.service" > "$UNIT_FILE"

    log "Recarregando systemd e habilitando o serviço"
    systemctl daemon-reload
    systemctl enable watchdog-lpr.service

    log "Serviço instalado. Edite o .env (dados do FTP e do banco) e inicie com:"
    log "  sudo systemctl start watchdog-lpr"
    log "  sudo systemctl status watchdog-lpr"
    log "  journalctl -u watchdog-lpr -f"
else
    log "Instalação do serviço systemd pulada (--no-service)."
fi

log "Instalação concluída."
