from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth import require_admin_token, verify_origin


TRACKING_DB_PATH: Path | None = None

app = FastAPI(title="Algo Experiment Control API", version="2.0")


class TransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    target_state: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    expected_version: int | None = Field(default=None, ge=0)
    approval_scope: str = ""
    idempotency_key: str | None = None


def _route_db_path() -> Path | None:
    return Path(TRACKING_DB_PATH) if TRACKING_DB_PATH is not None else None


@app.get("/api/experiments")
def get_experiments() -> dict:
    from src.analytics import experiments

    if _route_db_path() is not None:
        experiments.TRACKING_DB_PATH = _route_db_path()
    items = experiments.list_experiments()
    return {"total": len(items), "items": items}


@app.post("/api/experiments/{experiment_id}/transitions")
def post_transition(
    experiment_id: str,
    request: TransitionRequest,
    actor_type: str = Depends(require_admin_token),
    _origin: None = Depends(verify_origin),
) -> dict:
    from src.analytics import experiments
    from src.analytics.experiments import ExperimentTransitionError

    if _route_db_path() is not None:
        experiments.TRACKING_DB_PATH = _route_db_path()
    try:
        return experiments.transition_experiment(
            experiment_id,
            request.target_state,
            actor_type=actor_type,
            actor_id="admin",
            reason=request.reason,
            expected_version=request.expected_version,
            approval_scope=request.approval_scope,
            idempotency_key=request.idempotency_key,
        )
    except ExperimentTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/experiments/{experiment_id}/metrics")
def get_metrics(experiment_id: str) -> dict:
    from src.analytics import metrics

    if _route_db_path() is not None:
        metrics.TRACKING_DB_PATH = _route_db_path()
    return metrics.get_experiment_metrics(experiment_id)
