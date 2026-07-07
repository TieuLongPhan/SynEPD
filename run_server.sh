#!/usr/bin/env bash
# SynEPD setup/build/server helper.

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$ROOT_DIR"

ENV_NAME="${SYNEPD_CONDA_ENV:-synepd}"
HOST="${SYNEPD_HOST:-127.0.0.1}"
PORT="${SYNEPD_PORT:-8000}"
DB_PATH="${SYNEPD_DATABASE_URL:-data/epdb.sqlite}"
RELOAD=1
FORCE_ENV=0
FORCE_DEPS=0
MODE="full"

usage() {
    cat <<EOF
Usage: ./run_server.sh [full|setup|build|run] [options]

Modes:
  full   Create/update env if needed, check deps, build DB, run server. Default.
  setup  Create/update env if needed and check deps only.
  build  Create/update env if needed, check deps, build DB only.
  run    Run server only; skip env setup, dependency install, and DB build.

Options:
  --env-name NAME   Conda environment name. Default: ${ENV_NAME}
  --host HOST       Server host. Default: ${HOST}
  --port PORT       Server port. Default: ${PORT}
  --db PATH         Database path or URL. Default: ${DB_PATH}
  --no-reload       Disable uvicorn reload.
  --reload          Enable uvicorn reload. Default.
  --force-env       Run conda env update even if the env exists.
  --force-deps      Reinstall pip dependencies even if imports are available.
  --skip-build      Skip database build, useful with full mode.
  --build-db        Build database, useful with run mode.
  -h, --help        Show this help message.

Examples:
  ./run_server.sh
  ./run_server.sh run
  ./run_server.sh full --skip-build
  ./run_server.sh build --env-name synepd
EOF
}

if [[ $# -gt 0 ]]; then
    case "$1" in
        full|setup|build|run|run-server|server)
            MODE="$1"
            shift
            ;;
    esac
fi

case "$MODE" in
    full)
        SETUP_ENV=1
        CHECK_DEPS=1
        BUILD_DB=1
        START_SERVER=1
        ;;
    setup)
        SETUP_ENV=1
        CHECK_DEPS=1
        BUILD_DB=0
        START_SERVER=0
        ;;
    build)
        SETUP_ENV=1
        CHECK_DEPS=1
        BUILD_DB=1
        START_SERVER=0
        ;;
    run|run-server|server)
        SETUP_ENV=0
        CHECK_DEPS=0
        BUILD_DB=0
        START_SERVER=1
        ;;
    *)
        echo "Unknown mode: ${MODE}" >&2
        usage
        exit 2
        ;;
esac

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-name)
            ENV_NAME="$2"
            shift 2
            ;;
        --host)
            HOST="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --db)
            DB_PATH="$2"
            shift 2
            ;;
        --no-reload)
            RELOAD=0
            shift
            ;;
        --reload)
            RELOAD=1
            shift
            ;;
        --force-env)
            FORCE_ENV=1
            SETUP_ENV=1
            CHECK_DEPS=1
            shift
            ;;
        --force-deps)
            FORCE_DEPS=1
            SETUP_ENV=1
            CHECK_DEPS=1
            shift
            ;;
        --skip-build)
            BUILD_DB=0
            shift
            ;;
        --build-db)
            BUILD_DB=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage
            exit 2
            ;;
    esac
done

log() {
    printf '\n==> %s\n' "$1"
}

find_conda() {
    if command -v conda >/dev/null 2>&1; then
        command -v conda
        return 0
    fi

    for candidate in "$HOME/miniconda3/bin/conda" "$HOME/anaconda3/bin/conda"; do
        if [[ -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    return 1
}

CONDA_BIN=""
if CONDA_BIN="$(find_conda)"; then
    :
else
    CONDA_BIN=""
fi

conda_env_exists() {
    [[ -n "$CONDA_BIN" ]] || return 1
    "$CONDA_BIN" env list | awk 'NF && $1 !~ /^#/ {print $1}' | grep -Fxq "$ENV_NAME"
}

run_in_env() {
    if conda_env_exists; then
        "$CONDA_BIN" run --no-capture-output -n "$ENV_NAME" "$@"
    else
        "$@"
    fi
}

ensure_conda_available() {
    if [[ -z "$CONDA_BIN" ]]; then
        echo "Conda was not found in PATH, $HOME/miniconda3, or $HOME/anaconda3." >&2
        echo "Install Conda first, or use './run_server.sh run' with an already prepared Python environment." >&2
        exit 1
    fi
}

ensure_env() {
    ensure_conda_available

    if conda_env_exists; then
        log "Conda environment '${ENV_NAME}' already exists"
        if [[ "$FORCE_ENV" -eq 1 ]]; then
            log "Updating conda environment from env.yaml"
            "$CONDA_BIN" env update -n "$ENV_NAME" -f env.yaml --prune
        fi
    else
        log "Creating conda environment '${ENV_NAME}' from env.yaml"
        "$CONDA_BIN" env create -n "$ENV_NAME" -f env.yaml
    fi
}

deps_available() {
    conda_env_exists || return 1
    "$CONDA_BIN" run -n "$ENV_NAME" python -c '
import importlib

checks = [
    "fastapi",
    "networkx",
    "numpy",
    "rdkit",
    "synkit",
    "synrfp",
    "synepd",
    "uvicorn",
]

for package in checks:
    importlib.import_module(package)
'
}

ensure_deps() {
    if [[ "$FORCE_DEPS" -eq 0 ]] && deps_available >/dev/null 2>&1; then
        log "Python dependencies are already available in '${ENV_NAME}'"
        return
    fi

    log "Installing Python dependencies in '${ENV_NAME}'"
    ensure_conda_available
    "$CONDA_BIN" run --no-capture-output -n "$ENV_NAME" python -m pip install -r requirements.txt
}

build_database() {
    log "Building data/epdb.sqlite"
    run_in_env python synepd/construct/build_release_db.py
}

start_server() {
    export SYNEPD_DATABASE_URL="$DB_PATH"

    log "Starting SynEPD Mechanistic Web Service"
    echo "Mode: ${MODE}"
    echo "Conda environment: ${ENV_NAME}"
    echo "Database location: ${SYNEPD_DATABASE_URL}"
    echo "URL: http://${HOST}:${PORT}"

    uvicorn_args=(python -m uvicorn synepd.web.server:app --host "$HOST" --port "$PORT")
    if [[ "$RELOAD" -eq 1 ]]; then
        uvicorn_args+=(--reload)
    fi

    if conda_env_exists; then
        exec "$CONDA_BIN" run --no-capture-output -n "$ENV_NAME" "${uvicorn_args[@]}"
    fi

    exec "${uvicorn_args[@]}"
}

log "SynEPD pipeline"
echo "Project: ${ROOT_DIR}"
echo "Mode: ${MODE}"

if [[ "$SETUP_ENV" -eq 1 ]]; then
    ensure_env
fi

if [[ "$CHECK_DEPS" -eq 1 ]]; then
    ensure_deps
fi

if [[ "$BUILD_DB" -eq 1 ]]; then
    build_database
elif [[ "$MODE" == "run" || "$MODE" == "run-server" || "$MODE" == "server" ]]; then
    log "Skipping database build for run-only mode"
fi

if [[ "$START_SERVER" -eq 1 ]]; then
    start_server
fi

log "Done"
