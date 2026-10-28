"""HTTP boundary for document RAG; synchronous routes run in FastAPI's worker pool."""
from fastapi import APIRouter, HTTPException

from .rag import RAGRequest, answer_rag, rag_status


router = APIRouter(prefix='/api/rag', tags=['document-rag'])


@router.get('/status')
def status():
    return rag_status()


@router.post('/answer')
def answer(request: RAGRequest):
    result = answer_rag(request)
    code = {'unavailable': 503, 'provider_error': 502, 'invalid_citations': 502}.get(result['status'])
    if code:
        raise HTTPException(code, detail=result)
    return result
