from fastapi import APIRouter, Depends, BackgroundTasks
from autohub.database import model
from autohub.api.login import get_current_user
from autohub.automation.pipeline import run_full_pipeline

router = APIRouter()


@router.post("/pipeline/run", tags=["Pipeline"])
def trigger_pipeline(
    background_tasks: BackgroundTasks,
    _: model.User = Depends(get_current_user),
):
    background_tasks.add_task(run_full_pipeline)
    return {"message": "Full pipeline triggered in background"}