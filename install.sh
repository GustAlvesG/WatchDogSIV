#!/usr/bin/env bash
#
# Instala o WatchDog LPR em um servidor Linux:
#   - instala e configura o servidor FTP (vsftpd) que recebe as imagens do equipamento de LPR
#   - cria o ambiente virtual Python e instala as dependências
#   - cria o arquivo .env a partir do .env.example (se ainda não existir)
#   - instala e habilita o serviço systemd
#
# Uso:
#   sudo ./install.sh                 # instala tudo: servidor FTP + venv/deps + serviço systemd
#   sudo ./install.sh --no-ftp        # pula o servidor FTP (já configurado por fora)
#   sudo ./install.sh --no-service    # pula a instalação do serviço systemd
#   ./install.sh --no-ftp --no-service   # só venv/deps/.env, sem privilégios de root
#
# Variáveis de ambiente opcionais para automatizar a configuração do FTP sem prompts interativos:
#   FTP_USER             usuário que o equipamento de LPR usa para enviar as imagens (ex: lpr)
#   FTP_PASSWORD
#   FTP_DIR              pasta onde as imagens são gravadas (ex: /srv/lpr)
#   FTP_PASV_MIN_PORT    (opcional, padrão 40000) início da faixa de portas do modo passivo
#   FTP_PASV_MAX_PORT    (opcional, padrão 40100) fim da faixa de portas do modo passivo
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

INSTALL_SERVICE=true
INSTALL_FTP=true
for arg in "$@"; do
    case "$arg" in
        --no-service) INSTALL_SERVICE=false ;;
        --no-ftp)     INSTALL_FTP=false ;;
        *) echo "Argumento desconhecido: $arg" >&2; exit 1 ;;
    esac
done

log() { echo "[install] $*"; }
err() { echo "[install] ERRO: $*" >&2; }

# --- 0. Precisa de root para configurar o FTP e/ou instalar o serviço -------
if { [[ "$INSTALL_FTP" == true ]] || [[ "$INSTALL_SERVICE" == true ]]; } && [[ $EUID -ne 0 ]]; then
    err "configurar o servidor FTP e/ou instalar o serviço systemd requer root."
    err "Rode novamente com: sudo ./install.sh"
    err "(ou use --no-ftp / --no-service para pular essas etapas sem precisar de root)"
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

# O .env guarda a senha do banco: só o usuário do serviço (e o root) pode ler
if [[ $EUID -eq 0 ]]; then
    chown "$SERVICE_USER:$SERVICE_GROUP" .env
fi
chmod 600 .env

# Atualiza uma variável no .env, criando a linha se ela não existir
set_env_var() {
    local key="$1" value="$2"
    if grep -q "^${key}=" .env; then
        sed -i "s#^${key}=.*#${key}=${value}#" .env
    else
        echo "${key}=${value}" >> .env
    fi
}

# Define uma opção no arquivo de configuração do vsftpd, substituindo o valor atual se houver
set_vsftpd_opt() {
    local conf="$1" key="$2" value="$3"
    sed -i "/^[[:space:]]*${key}=/d" "$conf"
    echo "${key}=${value}" >> "$conf"
}

# --- 4. Servidor FTP (vsftpd) que recebe as imagens do equipamento de LPR ---
setup_ftp() {
    # Coleta os parâmetros: usa variáveis de ambiente se fornecidas, senão pergunta (modo interativo)
    local ftp_user="${FTP_USER:-}"
    local ftp_pass="${FTP_PASSWORD:-}"
    local ftp_dir="${FTP_DIR:-}"
    local pasv_min="${FTP_PASV_MIN_PORT:-40000}"
    local pasv_max="${FTP_PASV_MAX_PORT:-40100}"

    if [[ -z "$ftp_user" || -z "$ftp_pass" || -z "$ftp_dir" ]]; then
        if [[ ! -t 0 ]]; then
            err "faltam FTP_USER / FTP_PASSWORD / FTP_DIR e o terminal não é interativo."
            err "Defina as variáveis de ambiente antes de rodar, ou use --no-ftp."
            exit 1
        fi
        log "Configuração do servidor FTP que vai receber as imagens do equipamento de LPR."
        if [[ -z "$ftp_user" ]]; then
            read -rp "Usuário FTP [lpr]: " ftp_user
            ftp_user="${ftp_user:-lpr}"
        fi
        if [[ -z "$ftp_dir" ]]; then
            read -rp "Pasta onde as imagens serão gravadas [/srv/lpr]: " ftp_dir
            ftp_dir="${ftp_dir:-/srv/lpr}"
        fi
        if [[ -z "$ftp_pass" ]]; then
            read -rsp "Senha FTP: " ftp_pass
            echo
        fi
    fi

    if [[ ! "$ftp_user" =~ ^[a-z_][a-z0-9_-]*$ ]] || [[ "$ftp_user" == "root" ]]; then
        err "usuário FTP inválido: '$ftp_user'."
        exit 1
    fi
    if [[ -z "$ftp_pass" ]]; then
        err "a senha FTP não pode ser vazia."
        exit 1
    fi
    if [[ "$ftp_dir" != /* || "$ftp_dir" == "/" ]]; then
        err "a pasta FTP deve ser um caminho absoluto (ex: /srv/lpr): '$ftp_dir'."
        exit 1
    fi
    ftp_dir="${ftp_dir%/}"

    if ! command -v vsftpd >/dev/null 2>&1; then
        log "Instalando vsftpd"
        if command -v apt-get >/dev/null 2>&1; then
            apt-get update -qq
            apt-get install -y vsftpd
        elif command -v dnf >/dev/null 2>&1; then
            dnf install -y vsftpd
        elif command -v yum >/dev/null 2>&1; then
            yum install -y vsftpd
        else
            err "gerenciador de pacotes não reconhecido. Instale 'vsftpd' manualmente e rode de novo."
            exit 1
        fi
    fi

    # O usuário FTP não tem acesso a shell. O PAM do vsftpd (pam_shells) só aceita usuários
    # cujo shell esteja listado em /etc/shells, por isso o nologin é adicionado lá.
    local nologin_shell
    nologin_shell="$(command -v nologin || echo /usr/sbin/nologin)"
    if ! grep -qxF "$nologin_shell" /etc/shells 2>/dev/null; then
        echo "$nologin_shell" >> /etc/shells
    fi

    if id "$ftp_user" >/dev/null 2>&1; then
        # Só reaproveita um usuário sem shell (criado por uma execução anterior deste script),
        # para nunca trocar a senha de uma conta real do servidor.
        local current_shell
        current_shell="$(getent passwd "$ftp_user" | cut -d: -f7)"
        if [[ "$current_shell" != *nologin && "$current_shell" != */false ]]; then
            err "o usuário '$ftp_user' já existe e tem acesso a shell ($current_shell)."
            err "Escolha outro nome para o usuário FTP."
            exit 1
        fi
        log "Usuário '$ftp_user' já existe, reutilizando (a senha será atualizada)."
    else
        log "Criando usuário '$ftp_user' (sem acesso a shell)"
        useradd --user-group --home-dir "$ftp_dir" --no-create-home --shell "$nologin_shell" "$ftp_user"
    fi
    local ftp_group
    ftp_group="$(id -gn "$ftp_user")"
    printf '%s:%s\n' "$ftp_user" "$ftp_pass" | chpasswd

    # A pasta pertence ao usuário FTP e é gravável pelo grupo. O usuário do serviço entra
    # nesse grupo para conseguir renomear e apagar os arquivos enviados pelo equipamento.
    mkdir -p "$ftp_dir"
    chown "$ftp_user:$ftp_group" "$ftp_dir"
    chmod 2775 "$ftp_dir"
    if [[ "$SERVICE_USER" != "root" && "$SERVICE_USER" != "$ftp_user" ]]; then
        log "Adicionando '$SERVICE_USER' ao grupo '$ftp_group'"
        usermod -aG "$ftp_group" "$SERVICE_USER"
    fi

    # Debian/Ubuntu usam /etc/vsftpd.conf; RHEL e derivados usam /etc/vsftpd/vsftpd.conf
    local conf="/etc/vsftpd.conf"
    if [[ -f /etc/vsftpd/vsftpd.conf ]]; then
        conf="/etc/vsftpd/vsftpd.conf"
    fi
    if [[ -f "$conf" ]]; then
        local backup
        backup="$conf.bak-watchdog-lpr-$(date +%Y%m%d%H%M%S)"
        log "Ajustando $conf (backup em $backup)"
        cp "$conf" "$backup"
        # Garante que o arquivo termina com quebra de linha antes de acrescentar opções
        if [[ -n "$(tail -c1 "$conf")" ]]; then
            echo >> "$conf"
        fi
    else
        log "Criando $conf"
        echo "listen=YES" > "$conf"
    fi

    # Somente os usuários deste arquivo podem entrar por FTP
    local userlist="/etc/vsftpd.lpr-userlist"
    touch "$userlist"
    if ! grep -qxF "$ftp_user" "$userlist"; then
        echo "$ftp_user" >> "$userlist"
    fi

    set_vsftpd_opt "$conf" anonymous_enable NO
    set_vsftpd_opt "$conf" local_enable YES
    set_vsftpd_opt "$conf" write_enable YES
    # umask 002: arquivos 664 e pastas 775, graváveis pelo grupo (usuário do serviço)
    set_vsftpd_opt "$conf" local_umask 002
    # O usuário fica preso à pasta das imagens, que passa a ser a raiz do FTP
    set_vsftpd_opt "$conf" local_root "$ftp_dir"
    set_vsftpd_opt "$conf" chroot_local_user YES
    set_vsftpd_opt "$conf" allow_writeable_chroot YES
    set_vsftpd_opt "$conf" userlist_enable YES
    set_vsftpd_opt "$conf" userlist_deny NO
    set_vsftpd_opt "$conf" userlist_file "$userlist"
    set_vsftpd_opt "$conf" pasv_enable YES
    set_vsftpd_opt "$conf" pasv_min_port "$pasv_min"
    set_vsftpd_opt "$conf" pasv_max_port "$pasv_max"

    # Com SELinux ativo (RHEL e derivados) o vsftpd precisa de permissão para gravar na pasta
    if command -v getenforce >/dev/null 2>&1 && [[ "$(getenforce)" != "Disabled" ]]; then
        log "SELinux ativo: liberando escrita do FTP (ftpd_full_access)"
        setsebool -P ftpd_full_access on || err "não foi possível ajustar o SELinux; libere a escrita do vsftpd em $ftp_dir manualmente."
    fi

    log "Habilitando e reiniciando o vsftpd"
    systemctl enable vsftpd
    systemctl restart vsftpd
    if ! systemctl is-active --quiet vsftpd; then
        err "o vsftpd não iniciou. Verifique: journalctl -u vsftpd"
        exit 1
    fi

    set_env_var "FOLDER_PATH" "$ftp_dir"
    log "FOLDER_PATH atualizado no .env para $ftp_dir"
    log "Servidor FTP pronto. Configure o equipamento de LPR com:"
    log "  porta 21, usuário '$ftp_user', pasta raiz '/' (corresponde a $ftp_dir)"
    log "Se houver firewall, libere as portas 21 e ${pasv_min}-${pasv_max} (modo passivo) para o equipamento."
}

if [[ "$INSTALL_FTP" == true ]]; then
    setup_ftp
else
    log "Configuração do servidor FTP pulada (--no-ftp). Garanta que FOLDER_PATH no .env aponte para a pasta onde as imagens são gravadas e que o usuário '$SERVICE_USER' possa renomear e apagar arquivos nela."
fi

# --- 5. Serviço systemd -------------------------------------------------------
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

    log "Serviço instalado. Após revisar o .env, inicie com:"
    log "  sudo systemctl start watchdog-lpr"
    log "  sudo systemctl status watchdog-lpr"
    log "  journalctl -u watchdog-lpr -f"
else
    log "Instalação do serviço systemd pulada (--no-service)."
fi

log "Instalação concluída."
