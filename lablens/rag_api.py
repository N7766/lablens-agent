"""HTTP boundary for document RAG; synchronous routes run in FastAPI's worker pool."""
from fastapi import APIRouter, HTTPException, Header
from pydantic import Field
from typing import Annotated
from .agent import Agent
from .model import HTTPModel, ModelError
from .workspace_agent import WorkspaceAgentRequest, unavailable_agent_result

from .rag import RAGRequest, answer_rag, rag_status


router = APIRouter(prefix='/api/rag', tags=['document-rag'])


@router.get('/status')
def status():
    return rag_status()


@router.post('/answer')
def answer(request: Annotated[RAGRequest | WorkspaceAgentRequest, Field(discriminator='mode')],
           x_lablens_budget_seconds: Annotated[float | None, Header(gt=0, le=180)] = None):
    if isinstance(request, WorkspaceAgentRequest):
        try:
            result = Agent(HTTPModel()).run(request.question, workspace_context=request.model_dump(exclude={'question','mode'}))
        except ModelError:
            result = unavailable_agent_result(request)
    else:
        result = answer_rag(request, budget_seconds=x_lablens_budget_seconds)
    code = {'unavailable': 503, 'provider_error': 502, 'invalid_citations': 502,
            'agent_timeout': 504, 'agent_budget_exceeded': 422, 'agent_protocol_error': 502, 'agent_tool_error': 502}.get(result['status'])
    if code:
        raise HTTPException(code, detail=result)
    return result
