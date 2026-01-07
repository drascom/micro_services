# Microservices

## Install

1. Create the environment file:
   ```bash
   cp scan/.env.example scan/.env
   ```
2. Edit `scan/.env` and set the required secrets.
3. Create and install dependencies:
   ```bash
   cd "scan"
   python3 -m venv venv
   source venv/bin/activate
   python -m pip install -r requirements.txt
   ```

## Use

Start the service:
```bash
cd "scan"
./run.sh
```

The API starts on `http://0.0.0.0:1000`.
