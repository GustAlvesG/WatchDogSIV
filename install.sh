#!/usr/bin/env bash
#
# Instala o WatchDog LPR em um servidor Linux:
#   - monta o compartilhamento de rede Windows (SMB/CIFS) de origem das imagens
#   - cria o ambiente virtual Python e instala as dependências
#   - cria o arquivo .env a partir do .env.example (se ainda não existir)
#   - instala e habilita o serviço systemd
#
# Uso:
#   sudo ./install.sh                 # instala tudo: mount SMB + venv/deps + serviço systemd
#   sudo ./install.sh --no-mount      # pula a montagem do compartilhamento (já montado manualmente)
#   sudo ./install.sh --no-service    # pula a instalação do serviço systemd
#   ./install.sh --no-mount --no-service   # só venv/deps/.env, sem privilégios de root
#
# Variáveis de ambiente opcionais para automatizar a montagem sem prompts interativos:
#   SMB_SHARE          ex: //192.168.10.3/lpr
#   SMB_MOUNT_POINT    ex: /mnt/lpr
#   SMB_USERNAME
#   SMB_PASSWORD
#   SMB_DOMAIN         (opcional, ex: WORKGROUP)
#   SMB_VERS           (opcional, ex: 3.0 — força a versão do protocolo SMB)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

INSTALL_SERVICE=true
INSTALL_MOUNT=true
for arg in "$@"; do
    case "$arg" in
        --no-service) INSTALL_SERVICE=false ;;
        --no-mount)   INSTALL_MOUNT=false ;;
        *) echo "Argumento desconhecido: $arg" >&2; exit 1 ;;
    esac
done

log() { echo "[install] $*"; }
err() { echo "[install] ERRO: $*" >&2; }

# --- 0. Precisa de root para montar o share e/ou instalar o serviço ---------
if { [[ "$INSTALL_MOUNT" == true ]] || [[ "$INSTALL_SERVICE" == true ]]; } && [[ $EUID -ne 0 ]]; then
    err "montar o compartilhamento SMB e/ou instalar o serviço systemd requer root."
    err "Rode novamente com: sudo ./install.sh"
    err "(ou use --no-mount / --no-service para pular essas etapas sem precisar de root)"
    exit 1
fi

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

# Atualiza FOLDER_PATH no .env, criando a linha se ela não existir
set_env_var() {
    local key="$1" value="$2"
    if grep -q "^${key}=" .env; then
        sed -i "s#^${key}=.*#${key}=${value}#" .env
    else
        echo "${key}=${value}" >> .env
    fi
}

# --- 4. Monta o compartilhamento de rede Windows (SMB/CIFS) de origem ------
mount_share() {
    if ! command -v mount.cifs >/dev/null 2>&1; then
        log "Instalando cifs-utils (necessário para montar compartilhamentos SMB/Windows)"
        if command -v apt-get >/dev/null 2>&1; then
            apt-get update -qq
            apt-get install -y cifs-utils
        elif command -v dnf >/dev/null 2>&1; then
            dnf install -y cifs-utils
        elif command -v yum >/dev/null 2>&1; then
            yum install -y cifs-utils
        else
            err "gerenciador de pacotes não reconhecido. Instale 'cifs-utils' manualmente e rode de novo."
            exit 1
        fi
    fi

    # Coleta os parâmetros: usa variáveis de ambiente se fornecidas, senão pergunta (modo interativo)
    local share="${SMB_SHARE:-}"
    local mount_point="${SMB_MOUNT_POINT:-}"
    local smb_user="${SMB_USERNAME:-}"
    local smb_pass="${SMB_PASSWORD:-}"
    local smb_domain="${SMB_DOMAIN:-}"

    if [[ -z "$share" || -z "$mount_point" || -z "$smb_user" ]]; then
        if [[ ! -t 0 ]]; then
            err "faltam SMB_SHARE / SMB_MOUNT_POINT / SMB_USERNAME e o terminal não é interativo."
            err "Defina as variáveis de ambiente antes de rodar, ou use --no-mount."
            exit 1
        fi
        log "Configuração do compartilhamento de origem (pasta Windows compartilhada via SMB/CIFS)."
        [[ -z "$share" ]]       && read -rp "Caminho do compartilhamento (ex: //192.168.10.3/lpr): " share
        [[ -z "$mount_point" ]] && read -rp "Ponto de montagem local (ex: /mnt/lpr): " mount_point
        [[ -z "$smb_user" ]]    && read -rp "Usuário SMB: " smb_user
        if [[ -z "$smb_pass" ]]; then
            read -rsp "Senha SMB: " smb_pass
            echo
        fi
    fi

    mkdir -p "$mount_point"

    # Usuário/grupo que vai rodar o serviço, para que o watchdog consiga ler/renomear/apagar os arquivos
    local service_user="${SUDO_USER:-$(whoami)}"
    local uid gid
    uid="$(id -u "$service_user")"
    gid="$(id -g "$service_user")"

    local creds_file="/etc/samba/lpr-credentials"
    log "Gravando credenciais SMB em $creds_file (permissão 600)"
    mkdir -p "$(dirname "$creds_file")"
    {
        echo "username=$smb_user"
        echo "password=$smb_pass"
        [[ -n "$smb_domain" ]] && echo "domain=$smb_domain"
    } > "$creds_file"
    chmod 600 "$creds_file"
    chown root:root "$creds_file"

    local vers_opt=""
    [[ -n "$smb_vers" ]] && vers_opt=",vers=$smb_vers"
    local mount_opts="credentials=${creds_file},uid=${uid},gid=${gid},iocharset=utf8,file_mode=0664,dir_mode=0775,_netdev${vers_opt}"

    if grep -qE "^\S+\s+${mount_point}\s+cifs" /etc/fstab 2>/dev/null; then
        log "Já existe uma entrada para '$mount_point' em /etc/fstab, não será duplicada."
    else
        log "Adicionando entrada em /etc/fstab (backup em /etc/fstab.bak-watchdog-lpr)"
        cp /etc/fstab "/etc/fstab.bak-watchdog-lpr-$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
        echo "$share $mount_point cifs $mount_opts 0 0" >> /etc/fstab
    fi

    log "Montando $share em $mount_point"
    if mountpoint -q "$mount_point"; then
        log "Já está montado."
    else
        mount "$mount_point"
    fi

    if mountpoint -q "$mount_point"; then
        log "Montagem confirmada em $mount_point."
    else
        err "não foi possível confirmar a montagem em $mount_point. Verifique 'dmesg' e as credenciais."
        exit 1
    fi

    set_env_var "FOLDER_PATH" "$mount_point"
    log "FOLDER_PATH atualizado no .env para $mount_point"
}

if [[ "$INSTALL_MOUNT" == true ]]; then
    smb_vers="${SMB_VERS:-}"
    mount_share
else
    log "Montagem do compartilhamento SMB pulada (--no-mount). Garanta que FOLDER_PATH no .env aponte para uma pasta já montada."
fi

# --- 5. Serviço systemd -------------------------------------------------------
if [[ "$INSTALL_SERVICE" == true ]]; then
    SERVICE_USER="${SUDO_USER:-$(whoami)}"
    UNIT_FILE="/etc/systemd/system/watchdog-lpr.service"

    log "Gerando unidade systemd para o usuário '$SERVICE_USER' em $SCRIPT_DIR"
    sed \
        -e "s#WorkingDirectory=.*#WorkingDirectory=$SCRIPT_DIR#" \
        -e "s#EnvironmentFile=.*#EnvironmentFile=$SCRIPT_DIR/.env#" \
        -e "s#ExecStart=.*#ExecStart=$SCRIPT_DIR/venv/bin/python $SCRIPT_DIR/WatchDog.py#" \
        -e "s#^User=.*#User=$SERVICE_USER#" \
        -e "s#^Group=.*#Group=$SERVICE_USER#" \
        "watchdog-lpr.service" > "$UNIT_FILE"

    log "Recarregando systemd e habilitando o serviço"
    systemctl daemon-reload
    systemctl enable watchdog-lpr.service

    log "Serviço instalado. Após revisar o .env, inicie com:"
    log "  sudo systemctl start watchdog-lpr"
    log "  sudo systemctl status watchdog-lpr"
    log "  journalctl -u watchdog-lpr -f"
else
    log "Instalação do serviço systemd pulada (--no-service)."
fi

log "Instalação concluída."
