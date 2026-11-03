#!/usr/bin/env bash

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "$SCRIPT_DIR" || exit

python - <<'PY'
import os
import shutil
from pathlib import Path

try:
    from dotenv import find_dotenv, load_dotenv

    base_dir = Path.cwd().parent
    load_dotenv(find_dotenv(str(base_dir / ".env")))
except ImportError:
    pass

data_dir = Path(os.environ.get("DATA_DIR", Path.cwd() / "data"))
cache_dir = data_dir / "cache"


def migrate_cache(source, destination):
    if not source.is_dir() or source.resolve() == destination.resolve():
        return

    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir() and target.is_dir():
            migrate_cache(item, target)
            try:
                item.rmdir()
            except OSError:
                pass
        elif not target.exists():
            shutil.move(str(item), str(target))

    try:
        source.rmdir()
    except OSError:
        pass


for legacy_path, destination in (
    (cache_dir / "txtai", Path(os.environ.get("HF_HOME", cache_dir / "huggingface"))),
    (cache_dir / "transformers", Path(os.environ.get("HF_HOME", cache_dir / "huggingface"))),
    (
        cache_dir / "embedding" / "models",
        Path(os.environ.get("SENTENCE_TRANSFORMERS_HOME", cache_dir / "embedding")),
    ),
    (
        cache_dir / "whisper" / "models",
        Path(os.environ.get("WHISPER_MODEL_DIR", cache_dir / "whisper")),
    ),
):
    migrate_cache(legacy_path, destination)
PY

KEY_FILE=.webui_secret_key

PORT="${PORT:-8080}"
HOST="${HOST:-0.0.0.0}"
UVICORN_WORKERS="${UVICORN_WORKERS:-1}"

if ! [[ "$UVICORN_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "UVICORN_WORKERS must be a positive integer, got '$UVICORN_WORKERS'" >&2
  exit 1
fi

if (( UVICORN_WORKERS > 1 )); then
  if ! python - <<'PY'
import os
import sys
from pathlib import Path

try:
    from dotenv import find_dotenv, load_dotenv

    base_dir = Path.cwd().parent
    load_dotenv(find_dotenv(str(base_dir / ".env")))
except ImportError:
    pass

websocket_manager = os.environ.get("WEBSOCKET_MANAGER", "")
websocket_redis_url = os.environ.get(
    "WEBSOCKET_REDIS_URL",
    os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
)
task_coordination_backend = os.environ.get("TASK_COORDINATION_BACKEND", "local").lower()
task_coordination_url = os.environ.get("TASK_COORDINATION_URL", "")

if (
    websocket_manager != "redis"
    or not websocket_redis_url
    or task_coordination_backend != "redis"
    or not task_coordination_url
):
    print(
        "UVICORN_WORKERS > 1 requires Redis-backed WebSocket and task coordination "
        "with their URLs configured.",
        file=sys.stderr,
    )
    sys.exit(1)
PY
  then
    exit 1
  fi
fi

if test "$WEBUI_SECRET_KEY $WEBUI_JWT_SECRET_KEY" = " "; then
  echo "Loading WEBUI_SECRET_KEY from file, not provided as an environment variable."

  if ! [ -e "$KEY_FILE" ]; then
    echo "Generating WEBUI_SECRET_KEY"
    # Generate a random value to use as a WEBUI_SECRET_KEY in case the user didn't provide one.
    echo $(head -c 12 /dev/random | base64) > "$KEY_FILE"
  fi

  echo "Loading WEBUI_SECRET_KEY from $KEY_FILE"
  WEBUI_SECRET_KEY=$(cat "$KEY_FILE")
fi

if [[ "${USE_OLLAMA_DOCKER,,}" == "true" ]]; then
    echo "USE_OLLAMA is set to true, starting ollama serve."
    ollama serve &
fi

if [[ "${USE_CUDA_DOCKER,,}" == "true" ]]; then
  echo "CUDA is enabled, appending LD_LIBRARY_PATH to include torch/cudnn & cublas libraries."
  export LD_LIBRARY_PATH="$LD_LIBRARY_PATH:/usr/local/lib/python3.11/site-packages/torch/lib:/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib"
fi

# Check if SPACE_ID is set, if so, configure for space
if [ -n "$SPACE_ID" ]; then
  echo "Configuring for HuggingFace Space deployment"
  if [ -n "$ADMIN_USER_EMAIL" ] && [ -n "$ADMIN_USER_PASSWORD" ]; then
    echo "Admin user configured, creating"
    WEBUI_SECRET_KEY="$WEBUI_SECRET_KEY" uvicorn open_webui.main:app --host "$HOST" --port "$PORT" --forwarded-allow-ips '*' &
    webui_pid=$!
    echo "Waiting for webui to start..."
    while ! curl -s http://localhost:8080/health > /dev/null; do
      sleep 1
    done
    echo "Creating admin user..."
    curl \
      -X POST "http://localhost:8080/api/v1/auths/signup" \
      -H "accept: application/json" \
      -H "Content-Type: application/json" \
      -d "{ \"email\": \"${ADMIN_USER_EMAIL}\", \"password\": \"${ADMIN_USER_PASSWORD}\", \"name\": \"Admin\" }"
    echo "Shutting down webui..."
    kill $webui_pid
  fi

  export WEBUI_URL=${SPACE_HOST}
fi

WEBUI_SECRET_KEY="$WEBUI_SECRET_KEY" exec uvicorn open_webui.main:app --host "$HOST" --port "$PORT" --forwarded-allow-ips '*' --workers "$UVICORN_WORKERS"
