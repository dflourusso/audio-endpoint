from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.audio import OutputNotSupported, list_outputs, select_output
from app.system_info import read_text

router = APIRouter(prefix="/api/audio")


class OutputBody(BaseModel):
    id: str


@router.get("/outputs")
def outputs(request: Request):
    cards = read_text(request.app.state.settings.host_proc / "asound" / "cards")
    return {"outputs": list_outputs(cards), "current": "bluetooth"}


@router.post("/output")
def choose_output(body: OutputBody):
    try:
        return select_output(body.id.strip())
    except OutputNotSupported as exc:
        return JSONResponse(status_code=409, content={"error": exc.code, "message": exc.message})
