# Statistics API

## Configure

Replace `EMAIL` in `app/main.py` with the logged-in email address before deploying.

## Run locally

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

The endpoint is available at `http://localhost:8000/stats?values=1,2,3`.

## Deploy on Vercel

The `api/index.py` entrypoint and `vercel.json` route all requests to the
FastAPI application. From this directory, run:

```powershell
npx vercel
```

For a production deployment:

```powershell
npx vercel --prod
```

The included `Dockerfile` remains available for non-Vercel deployments.
