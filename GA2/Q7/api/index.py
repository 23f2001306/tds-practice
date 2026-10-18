from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


API_KEY = "ak_3jyg1ni7egodyxmiaetvjzhv"
EMAIL = "23f2001306@ds.study.iitm.ac.in"

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Event(BaseModel):
    user: str
    amount: float
    ts: int


class AnalyticsRequest(BaseModel):
    events: list[Event]


@app.post("/analytics")
def analytics(request: AnalyticsRequest, x_api_key: str | None = Header(default=None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    totals: dict[str, float] = {}
    revenue = 0.0
    for event in request.events:
        if event.amount > 0:
            revenue += event.amount
            totals[event.user] = totals.get(event.user, 0.0) + event.amount

    top_user = max(totals, key=totals.get) if totals else None
    return {
        "email": EMAIL,
        "total_events": len(request.events),
        "unique_users": len({event.user for event in request.events}),
        "revenue": revenue,
        "top_user": top_user,
    }
