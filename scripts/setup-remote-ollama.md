# Remote Ollama Inference Server Setup (Windows GPU)

Set up a remote Ollama inference server for the Mnemos second brain project. This Windows machine has 128GB RAM and an 8GB GPU. The server will be accessed over Tailscale by the main secondbrain deployment.

## Steps

### 1. Install Ollama (if not already installed)

Download and install Ollama for Windows from https://ollama.com/download/windows. After install, verify it's running:
```
ollama --version
```

### 2. Clone the repo (for reference/config only)

```
git clone git@github.com:snedea/secondbrain.git
cd secondbrain
```

### 3. Pull the required models

The secondbrain uses two models — an LLM for generation and an embedding model:

```
ollama pull llama3.2
ollama pull nomic-embed-text
```

`llama3.2` is a 3B parameter model that fits comfortably in 8GB VRAM. `nomic-embed-text` is tiny. Both should fully GPU-accelerate on this machine.

### 4. Configure Ollama to listen on all interfaces

By default Ollama only binds to `127.0.0.1`. We need it to listen on `0.0.0.0` so Tailscale peers can reach it.

On Windows, set a system environment variable:
```
setx OLLAMA_HOST "0.0.0.0:11434"
```

Then restart the Ollama service (close the Ollama tray icon and reopen it, or restart from Task Manager > Services > `Ollama`).

Verify it's listening on all interfaces:
```
netstat -an | findstr 11434
```

You should see `0.0.0.0:11434` in the output.

### 5. Verify Tailscale connectivity

Make sure Tailscale is running on this machine. Find this machine's Tailscale IP:
```
tailscale ip -4
```

Note that IP (e.g. `100.x.y.z`). Test Ollama is reachable locally:
```
curl http://localhost:11434/api/tags
```

### 6. Test from the secondbrain host

From the Mac running secondbrain (Tailscale IP `100.70.254.21`), it should be able to reach:
```
http://<this-machine-tailscale-ip>:11434
```

### 7. Windows Firewall

If Tailscale peers can't connect, allow Ollama through Windows Firewall:
```powershell
New-NetFirewallRule -DisplayName "Ollama Tailscale" -Direction Inbound -LocalPort 11434 -Protocol TCP -Action Allow -RemoteAddress 100.64.0.0/10
```

The `100.64.0.0/10` range covers all Tailscale IPs, so only Tailscale peers can reach it — not the public internet.

## Back on the Mac: Point secondbrain at the remote Ollama

Change `OLLAMA_URL` in `.env` from the Docker-internal address to the Windows machine's Tailscale IP:

```
OLLAMA_URL=http://<windows-tailscale-ip>:11434
```

Then in `docker-compose.yml`, you can remove (or comment out) the `ollama` service entirely and remove it from the backend's `depends_on` — inference is now remote. Restart the backend:

```
docker compose up -d --build backend
```

All inference (chat, embeddings, connections, tag suggestions) will transparently route to the GPU machine. No code changes needed — the backend already talks to Ollama via the configurable `OLLAMA_URL`.
