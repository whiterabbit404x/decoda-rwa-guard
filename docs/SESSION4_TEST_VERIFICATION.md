# Session 4 Test Verification

This guide verifies Session 4 detection → alert → incident → action workflows and related web paths.

## Backend (Linux/macOS)

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r services/api/requirements.txt
python -m pip install -r services/api/requirements-dev.txt
python -c "import psycopg; print(psycopg.__version__)"
python -m pytest services/api/tests/test_detection_alert_incident_action_chain.py services/api/tests/test_monitoring_investigation_timeline.py -q
python -m pytest services/api/tests -q
```

## Backend (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r services/api/requirements.txt
python -m pip install -r services/api/requirements-dev.txt
python -c "import psycopg; print(psycopg.__version__)"
python -m pytest services/api/tests/test_detection_alert_incident_action_chain.py services/api/tests/test_monitoring_investigation_timeline.py -q
python -m pytest services/api/tests -q
```

## Backend (Windows CMD)

```cmd
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r services/api/requirements.txt
python -m pip install -r services/api/requirements-dev.txt
python -c "import psycopg; print(psycopg.__version__)"
python -m pytest services/api/tests/test_detection_alert_incident_action_chain.py services/api/tests/test_monitoring_investigation_timeline.py -q
python -m pytest services/api/tests -q
```

## Frontend

```bash
cd apps/web
npm ci
# Only required when Playwright browser binaries are not yet installed:
npx playwright install
npm run test -- --grep "chain|incident|alert|proof"
npm run build
```
