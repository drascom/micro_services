# Microservices

## Install

1. Create the environment file:
   ```bash
   cp scan/.env.example scan/.env
   ```
2. Edit `scan/.env` and set the required secrets.
3. Install dependencies and set up the system service:
   ```bash
   cd "scan"
   ./install.sh
   ```

## Use

Start the service (systemd):
```bash
systemctl start scan-emails
```

Check status:
```bash
systemctl status scan-emails
```

Manual run (stops the service first and does not restart it):
```bash
cd "scan"
./run.sh
```

The API starts on `http://0.0.0.0:1000`.
