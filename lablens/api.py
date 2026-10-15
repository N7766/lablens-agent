from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import Field
from .agent import Agent, DEMO_QUESTION
from .model import HTTPModel, ReplayModel, ModelError
from .schemas import StrictModel

app = FastAPI(title="LabLens", version="0.4.0", description="Local experiment analysis workbench")


class RunRequest(StrictModel):
    question: str = Field(min_length=1, max_length=2000)


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/demo")
def demo():
    return Agent(ReplayModel()).run(DEMO_QUESTION)


@app.post("/api/run")
def run(request: RunRequest):
    if not request.question.strip():
        raise HTTPException(422, "Question cannot be blank")
    try:
        result = Agent(HTTPModel()).run(request.question)
    except ModelError as exc:
        raise HTTPException(503, str(exc)) from None
    if result["status"] == "model_error":
        return result
    return result


# Uploaded data stays within this request; these routes never call a model.
from .datasets import DatasetBundle
from .workbench import QueryRequest, inspect, preview, execute, verify


@app.get('/api/dataset')
def default_dataset():
    return inspect()


@app.post('/api/dataset/inspect')
def inspect_dataset(dataset: DatasetBundle):
    try:
        return inspect(dataset)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.post('/api/query/preview')
def preview_query(request: QueryRequest):
    try:
        return preview(request)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.post('/api/query/execute')
def execute_query(request: QueryRequest):
    try:
        return execute(request)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.post('/api/receipt/verify')
def verify_receipt(receipt: dict):
    try:
        return verify(receipt)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from None


from fastapi.staticfiles import StaticFiles
app.mount('/static', StaticFiles(directory=Path(__file__).parent / 'static'), name='static')
