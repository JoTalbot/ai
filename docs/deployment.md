# Deployment

Aegis is a Python runtime and CLI, not a hosted multi-user API server. The image packages the CLI and runs it as an unprivileged user. It does not turn arbitrary Python handlers into a security sandbox.

## Build

Build the Python distributions first, then build the image:

```bash
python -m pip install --upgrade build
python -m build
docker build -t aegis-ai-runtime:0.4.0 .
```

The image uses Python 3.12, installs the wheel, runs as UID/GID 10001, and stores its audit database under `/var/lib/aegis`.

## Offline smoke test

```bash
docker run --rm --read-only --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --pids-limit=64 --memory=512m --cpus=1 \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  aegis-ai-runtime:0.4.0 demo
```

## Run with a model provider

Create a protected environment file outside the repository. Do not commit it:

```dotenv
AEGIS_BASE_URL=http://model-gateway.internal:8000/v1
AEGIS_MODEL=your-model
AEGIS_API_KEY=your-secret
```

Restrict its permissions (`chmod 600 aegis.env`) and run the container with a persistent audit volume:

```bash
docker volume create aegis-data
docker run --rm --read-only --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --pids-limit=64 --memory=512m --cpus=1 \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --mount source=aegis-data,target=/var/lib/aegis \
  --env-file ./aegis.env \
  aegis-ai-runtime:0.4.0 doctor
```

Replace `doctor` with `run "your prompt"` to invoke the configured provider. The container must be able to reach that provider. Configure Docker networking and host firewall rules to allow only the required egress; do not use host networking by default.

## Operational requirements

- Back up `/var/lib/aegis/audit.db` and retain signed audit checkpoints on a separate system.
- Supply `AEGIS_AUDIT_CHECKPOINT_KEY` through a secret manager or protected environment file. Keep it off the runtime host where possible.
- Monitor container exit status and provider failures. The CLI is a one-shot process, not a daemon.
- Give custom tool handlers only the data and permissions they require. A container reduces exposure when correctly configured, but it is not a substitute for a dedicated sandbox with restricted filesystem and network access.
- Do not mount the Docker socket, host root, SSH agent, or broad host directories into the container.
