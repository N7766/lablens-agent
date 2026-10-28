"""HTTP boundary for document RAG; synchronous routes run in FastAPI's worker pool."""
from fastapi import APIRouter, HTTPException, Header
from pydantic import Field
from typing import Annotated
from uuid import UUID
from .cancellation import registry, ExecutionCancelled, ExecutionConflict, RegistryFull
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
           x_lablens_budget_seconds: Annotated[float | None, Header(gt=0, le=180)] = None,
           x_lablens_task_id: Annotated[UUID | None, Header()] = None,
           x_lablens_parent_task_id: Annotated[UUID | None, Header()] = None):
    if x_lablens_task_id and x_lablens_parent_task_id:
        raise HTTPException(422, detail='Root and parent execution headers are mutually exclusive.')
    if x_lablens_parent_task_id and isinstance(request, WorkspaceAgentRequest):
        raise HTTPException(422, detail='Only retrieval may run as a child execution.')
    try:
        scope = (registry.child_execution(str(x_lablens_parent_task_id)) if x_lablens_parent_task_id else
                 registry.execution(str(x_lablens_task_id) if x_lablens_task_id else None))
        with scope as execution:
            result = _answer(request, x_lablens_budget_seconds)
            if not x_lablens_parent_task_id:
                execution.result = {'usage': result.get('usage')}
    except ExecutionCancelled:
        # A pre-dispatch tombstone has no provider work in this invocation. Usage
        # from a previous process is deliberately not inferred from this response.
        raise HTTPException(409, detail={'status': 'agent_cancelled', 'error': 'Task cancellation was observed.'}) from None
    except ExecutionConflict as exc:
        raise HTTPException(409, detail={'status': 'execution_conflict', 'error': str(exc)}) from None
    except RegistryFull as exc:
        raise HTTPException(503, detail={'status': 'worker_registry_full', 'error': str(exc)}) from None
    code = {'unavailable': 503, 'provider_error': 502, 'invalid_citations': 502,
            'agent_timeout': 504, 'agent_budget_exceeded': 422, 'context_budget_exceeded': 422,
            'agent_cancelled': 409, 'agent_protocol_error': 502, 'agent_tool_error': 502}.get(result['status'])
    if code:
        raise HTTPException(code, detail=result)
    return result


@router.post('/executions/{task_id}/cancel')
def cancel_execution(task_id: UUID):
    try:
        return registry.cancel(str(task_id))
    except RegistryFull as exc:
        raise HTTPException(503, detail={'status': 'worker_registry_full', 'error': str(exc)}) from None


def _answer(request, budget_seconds):
    if isinstance(request, WorkspaceAgentRequest):
        try:
            result = Agent(HTTPModel()).run(request.question, workspace_context=request.model_dump(exclude={'question','mode'}))
        except ModelError:
            result = unavailable_agent_result(request)
    else:
        result = answer_rag(request, budget_seconds=budget_seconds)
    return result
