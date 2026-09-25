# Host Redimind behind Nginx on a private LAN

This example runs **Nginx on the same VM as Redimind** and uses the VM's LAN IP.
Replace `192.168.6.20` with the VM's actual IP everywhere below. Use either this
Nginx path or the Caddy example in [README.md](../../README.md), since both need
port 443. A public DNS name and internet-facing ports are not required.

The stock `/etc/nginx/` layout in Debian/Ubuntu is expected: `conf.d/` already
exists, while `tls/` is created in step 2. The two example config files are in
**the cloned Redimind repo** under `deploy/nginx/`, not under `/etc/nginx/` until
you copy them. Run the relative-path commands below from the repo root (check
`ls deploy/nginx`); use `conf.d/` for this example, not a second copy in
`sites-available/` or `sites-enabled/`.

## 1. Run Redis and Redimind on the VM

From a clone of this repo, start the Docker Redis instance and install the Python
package:

```sh
docker compose up -d --wait
uv sync
```

Set persistent, distinct agent and owner tokens in your VM's protected environment,
then start Redimind in token mode:

```sh
export REDIMIND_AUTH_MODE=tokens
export REDIMIND_REDIS_URL=redis://127.0.0.1:6379/0
export REDIMIND_AGENT_TOKEN='YOUR_PERSISTENT_AGENT_TOKEN'
export REDIMIND_OWNER_TOKEN='A_DIFFERENT_PERSISTENT_OWNER_TOKEN'
export REDIMIND_ALLOWED_HOSTS='192.168.6.20,192.168.6.20:*'
uv run redimind-server
```

Leave Redimind bound to its default `127.0.0.1:8000`. Run it under a process
manager to keep it available after logging out of the VM. Redis remains on the
VM's localhost-only Docker port; neither port 6379 nor 8000 needs LAN exposure.

## 2. Generate a LAN CA and VM certificate

On the VM, create a root CA and an IP-address certificate. Keep the CA **private
key** and server key on the VM; only the public CA certificate goes to clients.
The commands below assume OpenSSL 3 and root access to `/etc/nginx/tls/`:

```sh
sudo install -d -m 0755 /etc/nginx/tls
sudo cp deploy/nginx/openssl-server.cnf.example /etc/nginx/tls/openssl-server.cnf
sudoedit /etc/nginx/tls/openssl-server.cnf
```

Set both `CN` and `IP.1` in that copied file to the VM's actual IP, then run:

```sh
sudo openssl req -x509 -newkey rsa:4096 -nodes -sha256 -days 3650 \
  -subj '/CN=Redimind LAN CA' \
  -addext 'basicConstraints=critical,CA:TRUE' \
  -addext 'keyUsage=critical,keyCertSign,cRLSign' \
  -keyout /etc/nginx/tls/redimind-ca.key \
  -out /etc/nginx/tls/redimind-ca.crt
sudo openssl req -new -newkey rsa:3072 -nodes -sha256 \
  -config /etc/nginx/tls/openssl-server.cnf \
  -keyout /etc/nginx/tls/redimind.key \
  -out /etc/nginx/tls/redimind.csr
sudo openssl x509 -req -sha256 -days 365 \
  -in /etc/nginx/tls/redimind.csr \
  -CA /etc/nginx/tls/redimind-ca.crt \
  -CAkey /etc/nginx/tls/redimind-ca.key -CAcreateserial \
  -extfile /etc/nginx/tls/openssl-server.cnf -extensions server_ext \
  -out /etc/nginx/tls/redimind.crt
sudo openssl verify -CAfile /etc/nginx/tls/redimind-ca.crt \
  -verify_ip 192.168.6.20 /etc/nginx/tls/redimind.crt
sudo chmod 0600 /etc/nginx/tls/redimind-ca.key /etc/nginx/tls/redimind.key
sudo chmod 0644 /etc/nginx/tls/redimind-ca.crt /etc/nginx/tls/redimind.crt
```

The `openssl verify` command must report `OK`. Renew the server certificate before its
365-day validity ends using the same CA and current VM IP.

## 3. Install and start Nginx on the VM

Install Nginx using your VM's package manager (for example,
`sudo apt install nginx` on Debian/Ubuntu). Copy the example from this repo to
Nginx's configuration directory:

```sh
sudo cp deploy/nginx/redimind.conf.example /etc/nginx/conf.d/redimind.conf
sudoedit /etc/nginx/conf.d/redimind.conf
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

Replace `server_name` with the actual VM IP before `nginx -t`. The example keeps
the incoming `/mcp` path, original Host header, and streaming responses intact
for MCP elicitation. Allow your dev machine to reach **TCP 443** on the VM; the
Nginx listener is on the LAN, while Redimind and Redis stay on VM loopback.

## 4. Trust the CA and connect from your dev machine

Copy only the public CA certificate from the VM. The certificate is safe to
share with your own clients; keep `redimind-ca.key` private.

```sh
install -d -m 0700 "$HOME/.local/share/redimind"
scp vmuser@192.168.6.20:/etc/nginx/tls/redimind-ca.crt \
  "$HOME/.local/share/redimind/ca.crt"
curl --cacert "$HOME/.local/share/redimind/ca.crt" \
  https://192.168.6.20/health
export NODE_EXTRA_CA_CERTS="$HOME/.local/share/redimind/ca.crt"
```

Replace `vmuser` with your VM login. The health request should return
`{"status":"ok"}`. Keep `NODE_EXTRA_CA_CERTS` set in the shell that launches
Claude Code or OpenCode so they trust the private CA; no `-k` or disabled TLS
verification is needed. Configure the MCP client with
`https://192.168.6.20/mcp` and its **agent** bearer token, as shown in the
[README VM client examples](../../README.md#host-redimind-on-a-vm). Do not put the
owner token on the dev machine. `memory_propose` followed by `memory_review`
will show the Approve/Reject prompt in the connected interactive client.

Run one Redimind MCP worker for legacy-client elicitation; see
[operational notes](../operations.md#moving-the-service) before scaling out.
