#!/usr/bin/env bash
# ======================================================================= #
#  Tool Feeder Installer                                                  #
#  Combines Filament Feeder, Spoolman Lane Sync, and KlipperScreen        #
#  Filament Lanes into one installer with a component picker — and, for   #
#  Filament Feeder, generates feeder.cfg / per-tool config from your      #
#  answers instead of shipping static examples to hand-edit.              #
#                                                                         #
#  Usage:                                                                 #
#    bash <(curl -fsSL https://raw.githubusercontent.com/broncosis/Tool_feeder/main/install.sh)
#    ./install.sh [--dry-run] [--features feeder,lane_sync,klipperscreen] #
# ======================================================================= #

set -euo pipefail

REPO_URL="https://github.com/broncosis/Tool_feeder.git"
TARGET_DIR="${TOOL_FEEDER_DIR:-$HOME/Tool_feeder}"

# ---- Parse flags (also forwarded through self-clone re-exec) ----------------

DRY_RUN=0
FEATURES=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --features) FEATURES="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done
export DRY_RUN

# ---- Bootstrap: detect whether we're running from a real checkout -----------
# `bash <(curl ...)` only gives us this file's bytes, not the payload
# directories (src/printer, src/screen, src/sync) the installer copies from.

SCRIPT_SOURCE="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR=""
if [ -f "$SCRIPT_SOURCE" ]; then
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE")" && pwd)"
fi

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/src/printer/tool_feeder_macros.cfg" ]; then
    REPO_ROOT="$SCRIPT_DIR"
else
    echo "Cloning Tool Feeder to $TARGET_DIR ..."
    if [ -d "$TARGET_DIR/.git" ]; then
        git -C "$TARGET_DIR" pull --ff-only
    else
        git clone "$REPO_URL" "$TARGET_DIR"
    fi
    REPO_ROOT="$TARGET_DIR"
    ARGS=()
    [ "$DRY_RUN" = "1" ] && ARGS+=(--dry-run)
    [ -n "$FEATURES" ] && ARGS+=(--features "$FEATURES")
    exec bash "$REPO_ROOT/install.sh" "${ARGS[@]}"
fi

PRINTER_SRC="$REPO_ROOT/src/printer"
SCREEN_SRC="$REPO_ROOT/src/screen"
SYNC_SRC="$REPO_ROOT/src/sync"

# =======================================================================
#  Shared helpers
# =======================================================================

# ---- Colors --------------------------------------------------------------

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info()  { echo -e "  $*"; }
log_step()  { echo -e "${CYAN}${BOLD}$*${NC}"; }
log_ok()    { echo -e "  ${GREEN}$*${NC}"; }
log_warn()  { echo -e "  ${YELLOW}$*${NC}"; }
log_error() { echo -e "  ${RED}$*${NC}"; }

# ---- /dev/tty-safe prompts -------------------------------------------------
# Needed so prompts still work when this script is run via
# `bash <(curl -fsSL ...)`, where stdin is the piped script, not the terminal.
# `read -p` writes its prompt to stderr, so these are safe to call from
# inside a `$(...)` capture without the prompt text leaking into the result.

ask() {
    local prompt="$1" default="${2:-}" reply
    if [ -n "$default" ]; then
        read -r -p "$prompt [$default]: " reply </dev/tty
        echo "${reply:-$default}"
    else
        read -r -p "$prompt: " reply </dev/tty
        echo "$reply"
    fi
}

confirm() {
    local prompt="$1" default="${2:-n}" reply hint="y/N"
    [ "$default" = "y" ] && hint="Y/n"
    read -r -p "$prompt [$hint] " reply </dev/tty
    reply="${reply:-$default}"
    [[ "$reply" =~ ^[Yy] ]]
}

# ---- Detection --------------------------------------------------------------

detect_klipper_dir() {
    if [ -n "${KLIPPER_DIR:-}" ]; then
        echo "$KLIPPER_DIR"
        return
    fi
    local candidate
    for candidate in "$HOME/klipper" "/home/pi/klipper" "/opt/klipper"; do
        if [ -d "$candidate/klippy/extras" ]; then
            echo "$candidate"
            return
        fi
    done
}

detect_config_dir() {
    if [ -n "${KLIPPER_CONFIG_DIR:-}" ]; then
        echo "$KLIPPER_CONFIG_DIR"
        return
    fi
    local candidate
    for candidate in "$HOME/printer_data/config" "$HOME/klipper_config" \
                      "/home/pi/printer_data/config" "/home/pi/klipper_config"; do
        if [ -d "$candidate" ]; then
            echo "$candidate"
            return
        fi
    done
}

detect_klipperscreen_dir() {
    if [ -n "${KLIPPERSCREEN_DIR:-}" ]; then
        echo "$KLIPPERSCREEN_DIR"
        return
    fi
    local candidate
    for candidate in "$HOME/KlipperScreen" "/home/pi/KlipperScreen" "/opt/KlipperScreen"; do
        if [ -f "$candidate/panels/main_menu.py" ]; then
            echo "$candidate"
            return
        fi
    done
}

# Parses the `server` key out of the [spoolman] section of a moonraker.conf
# candidate. Prints nothing if not found.
parse_spoolman_url() {
    local config_dir="$1" candidate
    for candidate in "$config_dir/moonraker.conf" "$HOME/printer_data/config/moonraker.conf" \
                      "$HOME/klipper_config/moonraker.conf" "/etc/moonraker.conf"; do
        [ -f "$candidate" ] || continue
        awk '
            /^\[spoolman\]/ { insection=1; next }
            /^\[/ { insection=0 }
            insection && /^[[:space:]]*server[[:space:]]*[:=]/ {
                sub(/^[[:space:]]*server[[:space:]]*[:=][[:space:]]*/, "");
                print;
                exit
            }
        ' "$candidate" && return
    done
}

# Lists /dev/serial/by-id/* devices and lets the user pick one, or enter a
# path manually if none are found / none match. All display output goes to
# stderr so this is safe to call as `var="$(_pick_serial_device)"`.
_pick_serial_device() {
    local devices=()
    if [ -d /dev/serial/by-id ]; then
        while IFS= read -r -d '' dev; do
            devices+=("$dev")
        done < <(find /dev/serial/by-id -maxdepth 1 -type l -print0 2>/dev/null | sort -z)
    fi

    if [ "${#devices[@]}" -eq 0 ]; then
        log_warn "No /dev/serial/by-id devices found." >&2
        ask "Enter the feeder MCU serial path manually" "/dev/serial/by-id/usb-..."
        return
    fi

    {
        echo ""
        echo "Detected serial devices:"
        local i=1
        for dev in "${devices[@]}"; do
            echo "  $i) $dev"
            i=$((i + 1))
        done
        echo "  m) Enter manually"
    } >&2

    local choice
    choice="$(ask "Select the feeder MCU" "1")"
    if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#devices[@]}" ]; then
        echo "${devices[$((choice - 1))]}"
    else
        ask "Enter the feeder MCU serial path manually"
    fi
}

# Counts extruder* objects via Moonraker's REST API (extruder, extruder1, …),
# same detection Klipper's own toolchanger config already implies — mirrors
# the identical regex-based approach already used in spoolman_lane_sync.py's
# _get_num_tools() and the KlipperScreen panel's _detect_tool_count(), so all
# three components agree on what "number of tools" means. Prints nothing if
# Moonraker isn't reachable or no tools are found yet (e.g. Klipper hasn't
# been told about the toolchanger's tools yet on a brand new setup).
_detect_tool_count() {
    local response count
    response="$(curl -s --max-time 3 "http://localhost:7125/printer/objects/list" 2>/dev/null)" || true
    [ -z "$response" ] && return
    count="$(echo "$response" | grep -oE '"extruder[0-9]*"' | sort -u | wc -l)"
    [ "${count:-0}" -gt 0 ] 2>/dev/null && echo "$count"
}

# ---- Mutating helpers (DRY_RUN-aware) ---------------------------------------

# _backup_existing <dest>
# Renames an existing file out of the way (timestamped, never clobbers a
# prior backup) instead of letting it get silently overwritten. Prints the
# backup path, or nothing if there was no existing file.
_backup_existing() {
    local dest="$1" backup
    [ -f "$dest" ] || return 0
    backup="${dest}.bak.$(date +%Y%m%d-%H%M%S)"
    cp "$dest" "$backup"
    echo "$backup"
}

# _merge_old_values <old_file> <new_file>
# Carries real (non-CHANGE_ME_) values from old_file into new_file wherever
# the same [section] + key exists in both — so re-running the installer
# doesn't wipe out wiring you already filled in. Matches by section+key, not
# by placeholder text, since a filled-in value no longer contains
# "CHANGE_ME_" for this script to spot. New file's inline comment (if any)
# is kept; only the value itself is swapped. No-ops quietly if python3 is
# unavailable — the freshly generated file is used as-is in that case.
_merge_old_values() {
    local old_file="$1" new_file="$2"
    command -v python3 >/dev/null 2>&1 || return 0
    python3 - "$old_file" "$new_file" <<'PYEOF'
import re
import sys

old_path, new_path = sys.argv[1], sys.argv[2]
kv_re = re.compile(r'^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:\s*)(.*)$')


def parse_values(path):
    values = {}
    section = None
    with open(path) as f:
        for line in f:
            stripped = line.rstrip("\n")
            if stripped.startswith("["):
                section = stripped.strip()
                continue
            m = kv_re.match(stripped)
            if not m:
                continue
            key = m.group(2)
            value = m.group(4).split("#", 1)[0].strip()
            if value and not value.startswith("CHANGE_ME_"):
                values[(section, key)] = value
    return values


old_values = parse_values(old_path)

section = None
out_lines = []
with open(new_path) as f:
    for line in f:
        stripped = line.rstrip("\n")
        if stripped.startswith("["):
            section = stripped.strip()
            out_lines.append(line)
            continue
        m = kv_re.match(stripped)
        if m:
            indent, key, sep, rest = m.groups()
            old_val = old_values.get((section, key))
            if old_val is not None:
                if "#" in rest:
                    _, comment = rest.split("#", 1)
                    new_rest = f"{old_val}  #{comment}"
                else:
                    new_rest = old_val
                out_lines.append(f"{indent}{key}{sep}{new_rest}\n")
                continue
        out_lines.append(line)

with open(new_path, "w") as f:
    f.writelines(out_lines)
PYEOF
}

# copy_with_prompt <src> <dest_dir>
copy_with_prompt() {
    local src="$1" dest_dir="$2" name dest backup
    [ -f "$src" ] || { log_warn "Not found, skipping: $(basename "$src")"; return; }
    name="$(basename "$src")"
    dest="$dest_dir/$name"

    if [ -f "$dest" ]; then
        if ! confirm "  '$name' already exists — replace it?" "n"; then
            log_info "Skipped: $name"
            return
        fi
    fi

    if [ "${DRY_RUN:-0}" = "1" ]; then
        log_info "[dry-run] would back up existing $dest (if any) and copy $src -> $dest"
        return
    fi
    backup="$(_backup_existing "$dest")"
    [ -n "$backup" ] && log_info "Backed up existing '$name' to $(basename "$backup")"
    mkdir -p "$dest_dir"
    cp "$src" "$dest"
    log_ok "Installed: $dest"
}

# write_generated_with_prompt <tmp_file> <dest_path>
# Like copy_with_prompt, but for a file generated from a template rather
# than copied verbatim — dest is a full path, not a directory, since
# generated filenames (T0.cfg, T1.cfg, ...) don't match the template's name.
# Never silently overwrites: an existing file is backed up (renamed, never
# clobbered), and if you want, real values already filled in there (pins,
# canbus_uuid, dock position, etc. — anything that's no longer a CHANGE_ME_
# placeholder) get carried over into the newly generated file.
write_generated_with_prompt() {
    local tmp_file="$1" dest="$2" name backup
    name="$(basename "$dest")"

    if [ -f "$dest" ]; then
        if ! confirm "  '$name' already exists — replace it?" "n"; then
            log_info "Skipped: $name"
            return
        fi

        if [ "${DRY_RUN:-0}" = "1" ]; then
            log_info "[dry-run] would back up existing $dest and write a new one"
        else
            backup="$(_backup_existing "$dest")"
            log_info "Backed up existing '$name' to $(basename "$backup")"
            if confirm "  Carry over values already filled in at '$name' into the new file?" "y"; then
                _merge_old_values "$backup" "$tmp_file"
                log_ok "Carried over existing values where the new file still had a placeholder."
            fi
        fi
    fi

    if [ "${DRY_RUN:-0}" = "1" ]; then
        log_info "[dry-run] would write $dest"
        return
    fi
    mkdir -p "$(dirname "$dest")"
    cp "$tmp_file" "$dest"
    log_ok "Installed: $dest"
}

# symlink_with_warning <src> <dest>
symlink_with_warning() {
    local src="$1" dest="$2"
    if [ "${DRY_RUN:-0}" = "1" ]; then
        log_info "[dry-run] would symlink $dest -> $src"
        return
    fi
    if [ -L "$dest" ]; then
        ln -sf "$src" "$dest"
        log_ok "Updated symlink: $dest"
    elif [ -e "$dest" ]; then
        log_warn "'$dest' already exists and is not a symlink — remove it manually and re-run."
    else
        mkdir -p "$(dirname "$dest")"
        ln -s "$src" "$dest"
        log_ok "Linked: $dest -> $src"
    fi
}

# restart_service <name>
restart_service() {
    local name="$1"
    if [ "${DRY_RUN:-0}" = "1" ]; then
        log_info "[dry-run] would restart service: $name"
        return
    fi
    if systemctl is-active --quiet "$name" 2>/dev/null; then
        if confirm "Restart $name now?" "n"; then
            sudo systemctl restart "$name"
            log_ok "$name restarted."
        else
            log_info "Skipped — remember to restart $name before changes take effect."
        fi
    else
        log_info "$name service not detected — restart it manually when ready."
    fi
}

# =======================================================================
#  Filament Feeder
# =======================================================================

install_feeder() {
    log_step "── Filament Feeder ──"

    if [ -z "$KLIPPER_DIR" ]; then
        KLIPPER_DIR="$(ask "Enter your Klipper directory path")"
        KLIPPER_DIR="${KLIPPER_DIR%/}"
        if [ ! -d "$KLIPPER_DIR/klippy/extras" ]; then
            log_error "klippy/extras not found under $KLIPPER_DIR"
            return 1
        fi
    fi
    local extras_dir="$KLIPPER_DIR/klippy/extras"

    if [ -z "$CONFIG_DIR" ]; then
        CONFIG_DIR="$(ask "Enter your Klipper config directory path")"
        CONFIG_DIR="${CONFIG_DIR%/}"
        if [ ! -d "$CONFIG_DIR" ]; then
            log_error "Directory not found: $CONFIG_DIR"
            return 1
        fi
    fi

    echo ""
    echo -e "${BOLD}Tell us about your setup:${NC}"
    local spoolman_enabled num_tools detected_tools load_temp has_buttons feeder_serial

    if confirm "Do you run Moonraker + Spoolman?" "y"; then
        spoolman_enabled="True"
    else
        spoolman_enabled="False"
    fi

    detected_tools="$(_detect_tool_count)"
    if [ -n "$detected_tools" ]; then
        log_info "Detected $detected_tools tool(s) from Klipper."
        num_tools="$(ask "Number of tools/lanes" "$detected_tools")"
    else
        num_tools="$(ask "Number of tools/lanes (couldn't auto-detect — Klipper not reachable, or tools not configured yet)" "5")"
    fi

    load_temp="$(ask "Load/unload/purge temperature" "220")"

    if confirm "Do you have physical unload buttons at the feeder?" "y"; then
        has_buttons=1
    else
        has_buttons=0
        log_info "Skipping unload button config — trigger UNLOAD_ANY_TOOL from the"
        log_info "KlipperScreen panel (if installed) or your own macro/console instead."
    fi

    feeder_serial="$(_pick_serial_device)"

    # ---- Generate feeder.cfg: prelude + N tool blocks (stepper + buffer [+ button]) + vars ----
    local feeder_tmp
    feeder_tmp="$(mktemp /tmp/tool_feeder_feeder_cfg_XXXXXX)"

    sed "s|@@NUM_TOOLS@@|$num_tools|g; s|@@FEEDER_SERIAL@@|$feeder_serial|g" \
        "$PRINTER_SRC/feeder_prelude.cfg.template" > "$feeder_tmp"

    local n extruder_ref
    for (( n = 0; n < num_tools; n++ )); do
        extruder_ref="extruder"
        [ "$n" -gt 0 ] && extruder_ref="extruder$n"
        {
            echo ""
            sed "s|@@N@@|$n|g; s|@@EXTRUDER@@|$extruder_ref|g" \
                "$PRINTER_SRC/tool_stepper.cfg.template"
            sed "s|@@N@@|$n|g" "$PRINTER_SRC/tool_buffer.cfg.template"
            if [ "$has_buttons" = "1" ]; then
                sed "s|@@N@@|$n|g" "$PRINTER_SRC/tool_button.cfg.template"
            fi
        } >> "$feeder_tmp"
    done

    sed "s|@@LOAD_TEMP@@|$load_temp|g; s|@@SPOOLMAN_ENABLED@@|$spoolman_enabled|g" \
        "$PRINTER_SRC/feeder_vars.cfg.template" >> "$feeder_tmp"

    log_info "Generating config for $num_tools tool(s) in $CONFIG_DIR:"
    write_generated_with_prompt "$feeder_tmp" "$CONFIG_DIR/feeder.cfg"
    rm -f "$feeder_tmp"

    copy_with_prompt "$PRINTER_SRC/tool_feeder_macros.cfg" "$CONFIG_DIR"

    # ---- Generate T0.cfg .. T{n-1}.cfg from tool.cfg.template ----
    local n_suffix tool_tmp
    for (( n = 0; n < num_tools; n++ )); do
        n_suffix=""
        [ "$n" -gt 0 ] && n_suffix="$n"
        tool_tmp="$(mktemp /tmp/tool_feeder_toolcfg_XXXXXX)"
        sed "s|@@N@@|$n|g; s|@@N_SUFFIX@@|$n_suffix|g" \
            "$PRINTER_SRC/tool.cfg.template" > "$tool_tmp"
        write_generated_with_prompt "$tool_tmp" "$CONFIG_DIR/T$n.cfg"
        rm -f "$tool_tmp"
    done

    _install_buffer_extra "$extras_dir"

    restart_service klipper

    echo ""
    log_ok "Filament Feeder installed."
    echo ""
    echo "  Next steps:"
    echo "    - Find every CHANGE_ME_* placeholder and fill it in to match your"
    echo "      wiring: grep -rn CHANGE_ME_ $CONFIG_DIR/feeder.cfg $CONFIG_DIR/T*.cfg"
    echo "      (per-tool pins, TMC UART pins, canbus_uuid, dock park position,"
    echo "      input shaper tuning — all unique per build, can't be auto-filled)"
    echo "    - Purge bucket position defaulted to bucket_x=25/bucket_y=-4, and"
    echo "      Bowden length to 1400mm — both in feeder.cfg's _FEEDER_VARS /"
    echo "      T{n}.cfg — adjust to match your machine"
    echo "    - Buffer defaults to two-sensor mode. For single-sensor"
    echo "      (Belay-compatible) hardware, comment/uncomment the alternate"
    echo "      [feeder_buffer] block in feeder.cfg for that tool — both are"
    echo "      generated, only one should ever be active at a time"
    echo "    - [include feeder.cfg] in printer.cfg (it pulls in tool_feeder_macros.cfg itself)"
    echo "    - [include T0.cfg] etc. for each tool"
}

_install_buffer_extra() {
    local extras_dir="$1"
    log_step "Installing feeder_buffer.py into Klipper extras:"
    if [ "${DRY_RUN:-0}" = "1" ]; then
        log_info "[dry-run] would install feeder_buffer.py into $extras_dir"
        return
    fi
    local dest="$extras_dir/feeder_buffer.py"
    if [ -f "$dest" ]; then
        if confirm "  feeder_buffer.py already exists — overwrite?" "n"; then
            cp "$PRINTER_SRC/feeder_buffer.py" "$dest"
            log_ok "Installed: $dest"
        else
            log_info "Skipped Klipper extra."
        fi
    else
        cp "$PRINTER_SRC/feeder_buffer.py" "$dest"
        log_ok "Installed: $dest"
    fi
}

# =======================================================================
#  Spoolman Lane Sync
# =======================================================================
# Runs directly from this checkout (src/sync/) rather than a separate
# clone, so updating Tool Feeder updates this service too.

install_lane_sync() {
    log_step "── Spoolman Lane Sync ──"
    local venv_dir env_file script service_file
    venv_dir="$SYNC_SRC/venv"
    env_file="$SYNC_SRC/.env"
    script="$SYNC_SRC/spoolman_lane_sync.py"
    service_file="/etc/systemd/system/spoolman-lane-sync.service"

    _lane_sync_check_deps || return 1

    log_step "Creating virtual environment:"
    if [ "${DRY_RUN:-0}" = "1" ]; then
        log_info "[dry-run] would create venv at $venv_dir and pip install requirements"
    else
        if [ -f "$venv_dir/bin/python" ]; then
            log_info "venv already exists — skipping"
        else
            python3 -m venv "$venv_dir"
        fi
        log_info "Installing requirements..."
        "$venv_dir/bin/pip" install --quiet --upgrade -r "$SYNC_SRC/requirements.txt"
        log_ok "Dependencies installed."
    fi

    _lane_sync_write_env "$env_file"
    _lane_sync_write_service "$service_file" "$venv_dir" "$script" "$SYNC_SRC" "$env_file"

    if [ "${DRY_RUN:-0}" = "1" ]; then
        log_info "[dry-run] would enable and start spoolman-lane-sync"
    else
        sudo systemctl daemon-reload
        sudo systemctl enable --now spoolman-lane-sync
        log_ok "Enabled and started: spoolman-lane-sync"
    fi

    INSTALLED_LANE_SYNC=1

    echo ""
    log_ok "Spoolman Lane Sync installed."
    echo "  Watch logs:   journalctl -u spoolman-lane-sync -f"
    echo "  Config file:  $env_file"
}

_lane_sync_check_deps() {
    local missing=()
    command -v git >/dev/null 2>&1 || missing+=(git)
    command -v python3 >/dev/null 2>&1 || missing+=(python3)
    python3 -c "import venv" 2>/dev/null || missing+=(python3-venv)

    if [ "${#missing[@]}" -gt 0 ]; then
        log_step "Installing missing packages: ${missing[*]}"
        if [ "${DRY_RUN:-0}" = "1" ]; then
            log_info "[dry-run] would apt-get install ${missing[*]}"
        else
            sudo apt-get update -qq
            sudo apt-get install -y "${missing[@]}"
        fi
    fi

    if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" 2>/dev/null; then
        log_error "Python 3.9+ is required. Try: sudo apt-get install -y python3.11"
        return 1
    fi
}

_lane_sync_write_env() {
    local env_file="$1"
    if [ -f "$env_file" ]; then
        log_info "$(basename "$env_file") already exists — skipping (delete it to reconfigure)"
        return
    fi

    local detected moonraker_url spoolman_url api_key
    detected="$(parse_spoolman_url "$CONFIG_DIR")"

    moonraker_url="$(ask "Moonraker URL" "http://localhost:7125")"
    spoolman_url="$(ask "Spoolman URL" "${detected:-http://localhost:7912}")"
    api_key="$(ask "Moonraker API key (blank if auth is off)" "")"

    if [ "${DRY_RUN:-0}" = "1" ]; then
        log_info "[dry-run] would write $env_file"
        return
    fi

    cat > "$env_file" <<ENVEOF
# Spoolman Lane Sync — configuration
# Edit these values to match your setup, then: sudo systemctl restart spoolman-lane-sync

MOONRAKER_URL=${moonraker_url}
SPOOLMAN_URL=${spoolman_url}
MOONRAKER_API_KEY=${api_key}
LOG_LEVEL=INFO
ENVEOF
    log_ok "Created: $env_file"
}

_lane_sync_write_service() {
    local service_file="$1" venv_dir="$2" script="$3" work_dir="$4" env_file="$5"
    local current_user
    current_user="$(whoami)"

    log_step "Writing $service_file (sudo required):"
    if [ "${DRY_RUN:-0}" = "1" ]; then
        log_info "[dry-run] would write systemd unit for spoolman-lane-sync"
        return
    fi

    sudo tee "$service_file" > /dev/null <<EOF
[Unit]
Description=Spoolman to Moonraker lane_data sync
Documentation=https://github.com/broncosis/Tool_feeder
After=network-online.target moonraker.service
Wants=network-online.target

[Service]
Type=simple
User=${current_user}
WorkingDirectory=${work_dir}
ExecStart=${venv_dir}/bin/python ${script}
Restart=always
RestartSec=10
EnvironmentFile=-${env_file}

[Install]
WantedBy=multi-user.target
EOF
}

# =======================================================================
#  KlipperScreen Filament Lanes
# =======================================================================

install_klipperscreen_panel() {
    log_step "── KlipperScreen Filament Lanes ──"

    local ks_dir ks_panels
    ks_dir="$(detect_klipperscreen_dir)"
    if [ -z "$ks_dir" ]; then
        log_error "Could not find a KlipperScreen installation."
        log_error "Looked for ~/KlipperScreen, /home/pi/KlipperScreen, /opt/KlipperScreen"
        log_error "Make sure KlipperScreen is installed before installing this component."
        return 1
    fi
    ks_panels="$ks_dir/panels"
    log_info "Found KlipperScreen panels at: $ks_panels"

    # spoolman_common.py, tool_routing.py, and touch_picker.py are shared
    # modules imported as siblings by the actual Panel files — they need to
    # be symlinked alongside them too, not just the Panel files themselves.
    for f in filament_lanes.py filament_lanes_spoolman.py filament_lanes_manual.py \
             filament_lanes_routing.py filament_lanes_toolmap.py \
             spoolman_common.py tool_routing.py touch_picker.py; do
        symlink_with_warning "$SCREEN_SRC/$f" "$ks_panels/$f"
    done

    if [ -z "$CONFIG_DIR" ]; then
        CONFIG_DIR="$(ask "Enter your Klipper config directory path")"
        CONFIG_DIR="${CONFIG_DIR%/}"
    fi
    if [ -d "$CONFIG_DIR" ]; then
        log_info "Installing materials list (used by manual assignment) to $CONFIG_DIR:"
        copy_with_prompt "$PRINTER_SRC/materials.cfg" "$CONFIG_DIR"
    else
        log_warn "Config dir not found — skipped materials.cfg (manual assignment will fall back to a built-in default list)."
    fi

    restart_service KlipperScreen

    INSTALLED_KLIPPERSCREEN=1

    echo ""
    log_ok "KlipperScreen Filament Lanes installed."
    echo ""
    echo "  Add this to ~/printer_data/config/KlipperScreen.conf:"
    echo ""
    sed 's/^/    /' "$SCREEN_SRC/filament_lanes.conf"
    echo ""
}

# =======================================================================
#  Main
# =======================================================================

INSTALLED_LANE_SYNC=0
INSTALLED_KLIPPERSCREEN=0

echo ""
echo -e "${CYAN}${BOLD}╔═══════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║              Tool Feeder               ║${NC}"
echo -e "${CYAN}${BOLD}╚═══════════════════════════════════════╝${NC}"
echo ""

KLIPPER_DIR="$(detect_klipper_dir)"
CONFIG_DIR="$(detect_config_dir)"

[ -n "$KLIPPER_DIR" ] && log_info "Klipper:     ${GREEN}$KLIPPER_DIR${NC}" || log_info "Klipper:     ${YELLOW}not found (will prompt if needed)${NC}"
[ -n "$CONFIG_DIR" ] && log_info "Config dir:  ${GREEN}$CONFIG_DIR${NC}" || log_info "Config dir:  ${YELLOW}not found (will prompt if needed)${NC}"
echo ""

# ---- Component selection -----------------------------------------------------

SELECTED_FEEDER=0
SELECTED_LANE_SYNC=0
SELECTED_KLIPPERSCREEN=0

if [ -n "$FEATURES" ]; then
    IFS=',' read -ra parts <<< "$FEATURES"
    for p in "${parts[@]}"; do
        case "$p" in
            feeder) SELECTED_FEEDER=1 ;;
            lane_sync) SELECTED_LANE_SYNC=1 ;;
            klipperscreen) SELECTED_KLIPPERSCREEN=1 ;;
        esac
    done
else
    echo "Select components to install:"
    echo "  1) Filament Feeder          (Klipper macros + dual/single-sensor buffer sync)"
    echo "  2) Spoolman Lane Sync       (background service)"
    echo "  3) KlipperScreen Filament Lanes panel"
    echo "  a) All of the above"
    echo ""
    sel="$(ask "Enter choices (e.g. '1 3', or 'a')" "a")"
    if [[ "$sel" == *a* ]]; then
        SELECTED_FEEDER=1; SELECTED_LANE_SYNC=1; SELECTED_KLIPPERSCREEN=1
    else
        [[ "$sel" == *1* ]] && SELECTED_FEEDER=1
        [[ "$sel" == *2* ]] && SELECTED_LANE_SYNC=1
        [[ "$sel" == *3* ]] && SELECTED_KLIPPERSCREEN=1
    fi
fi

if [ "$SELECTED_FEEDER" = 0 ] && [ "$SELECTED_LANE_SYNC" = 0 ] && [ "$SELECTED_KLIPPERSCREEN" = 0 ]; then
    log_warn "Nothing selected. Exiting."
    exit 0
fi

# ---- Dispatch -----------------------------------------------------------------

[ "$SELECTED_FEEDER" = 1 ] && install_feeder
[ "$SELECTED_LANE_SYNC" = 1 ] && install_lane_sync
[ "$SELECTED_KLIPPERSCREEN" = 1 ] && install_klipperscreen_panel

# ---- Consolidated Moonraker update_manager entry -------------------------------
# One entry for the whole Tool Feeder checkout — updating it (git pull) updates
# every installed component together, since they're all vendored in this repo.

if [ -n "$CONFIG_DIR" ] && [ -f "$CONFIG_DIR/moonraker.conf" ]; then
    if ! grep -q '^\[update_manager tool_feeder\]' "$CONFIG_DIR/moonraker.conf" 2>/dev/null; then
        if confirm "Add Tool Feeder to Moonraker's update manager?" "y"; then
            services="klipper"
            [ "$INSTALLED_LANE_SYNC" = 1 ] && services="$services spoolman-lane-sync"
            [ "$INSTALLED_KLIPPERSCREEN" = 1 ] && services="$services KlipperScreen"

            if [ "${DRY_RUN:-0}" = "1" ]; then
                log_info "[dry-run] would append [update_manager tool_feeder] to moonraker.conf"
            else
                {
                    echo ""
                    echo "[update_manager tool_feeder]"
                    echo "type: git_repo"
                    echo "path: $REPO_ROOT"
                    echo "origin: $REPO_URL"
                    echo "primary_branch: main"
                    echo "install_script: install.sh"
                    echo "managed_services: $services"
                } >> "$CONFIG_DIR/moonraker.conf"
                log_ok "Added [update_manager tool_feeder] to moonraker.conf"
            fi
        fi
    fi

    if [ "$INSTALLED_LANE_SYNC" = 1 ] && [ -f "$HOME/printer_data/moonraker.asvc" ]; then
        if ! grep -qx "spoolman-lane-sync" "$HOME/printer_data/moonraker.asvc"; then
            if [ "${DRY_RUN:-0}" = "1" ]; then
                log_info "[dry-run] would register spoolman-lane-sync in moonraker.asvc"
            else
                echo "spoolman-lane-sync" >> "$HOME/printer_data/moonraker.asvc"
                log_ok "Registered spoolman-lane-sync in moonraker.asvc"
            fi
        fi
    fi

    restart_service moonraker
fi

echo ""
log_ok "${BOLD}Done!${NC}"
