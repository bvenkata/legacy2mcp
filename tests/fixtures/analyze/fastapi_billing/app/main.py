from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI

from app.jobs import send_overdue_reminders
from app.routers import invoices

app = FastAPI()
app.include_router(invoices.router)

scheduler = BackgroundScheduler()
scheduler.add_job(send_overdue_reminders, "cron", hour=9)


@app.get("/health")
async def health():
    return {"ok": True}
