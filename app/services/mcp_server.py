import os
import uuid
import math
from datetime import datetime
from typing import Optional, List
from mcp.server.fastmcp import FastMCP, Context
from pydantic import BaseModel, Field
from google import genai
from beanie.operators import In
from app.core.config import settings
from app.models.document import DocumentItem
from app.models.chunk import ChunkItem
from app.services.ingestion import start_document_ingestion

# Initialize the FastMCP server in stateless mode for production scalability
mcp = FastMCP("flint_mcp", stateless_http=True)

# Helper function to compute cosine similarity for vector search
def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    dot_product = sum(x * y for x, y in zip(v1, v2))
    magnitude1 = math.sqrt(sum(x * x for x in v1))
    magnitude2 = math.sqrt(sum(x * x for x in v2))
    if magnitude1 == 0 or magnitude2 == 0:
        return 0.0
    return dot_product / (magnitude1 * magnitude2)

# Schema definitions for MCP inputs
class CompressInput(BaseModel):
    text: str = Field(..., description="Raw thought/notes to compress", min_length=1)
    title: Optional[str] = Field(None, description="Optional custom title for this thought")

class GetEntryInput(BaseModel):
    entry_id: str = Field(..., description="The unique ID of the thought entry/document")

class SearchInput(BaseModel):
    query: str = Field(..., description="Semantic search query across your thoughts and documents", min_length=1)

# Helper to verify authenticated user inside MCP tools
def get_authenticated_user(ctx: Context):
    request = getattr(ctx.request_context, "request", None)
    user = getattr(request.state, "user", None) if request else None
    if not user:
        raise ValueError("Unauthorized access. Make sure your personal access token is configured correctly.")
    return user

@mcp.tool(
    name="flint_compress_thought",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
)
async def compress_thought(params: CompressInput, ctx: Context) -> str:
    """Compress a raw thought/note into Flint's structured format and save it in your workspace."""
    user = get_authenticated_user(ctx)
    
    # Generate unique filename for storage
    unique_filename = f"{uuid.uuid4()}.txt"
    upload_dir = "uploads"
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, unique_filename)
    
    # Save the thought raw text content
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(params.text)
        
    title = params.title or f"Thought - {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"
    
    # Create Document record
    doc = DocumentItem(
        user_id=user.id,
        title=title,
        filename=unique_filename,
        file_path=file_path,
        file_size=len(params.text),
        status="pending"
    )
    await doc.insert()
    
    # Execute the Gemini ingestion pipeline (chunking, embedding, concept extraction, 5-level summaries)
    await start_document_ingestion(str(doc.id))
    
    # Reload document to fetch summaries
    doc = await DocumentItem.get(doc.id)
    if not doc or doc.status == "failed":
        return "Failed to ingest and compress the thought. Please try again."
        
    # Format a beautiful response
    response_md = f"""# {doc.title}
Successfully saved and compressed in your Flint workspace.

## Core Insight (Level 1)
{doc.summary_level_1}

## Executive Summary (Level 3)
{doc.overall_summary}

## Key Takeaways
{chr(10).join(f'- {t}' for t in doc.key_takeaways)}

## Action Items
{chr(10).join(f'- {a}' for a in doc.action_items)}

## Conceptual Pillars (Level 2)
"""
    for idx, pillar in enumerate(doc.summary_level_2):
        response_md += f"\n### {idx + 1}. {pillar.get('name', 'Pillar')}\n{pillar.get('explanation', '')}\n"
        
    return response_md

@mcp.tool(
    name="flint_list_entries",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
async def list_entries(ctx: Context) -> str:
    """Retrieve list of all saved thoughts, documents, and notes in your Flint workspace."""
    user = get_authenticated_user(ctx)
    
    docs = await DocumentItem.find(DocumentItem.user_id == user.id).sort(-DocumentItem.created_at).to_list()
    if not docs:
        return "You don't have any notes or documents in your Flint workspace yet. Create one using flint_compress_thought!"
        
    result_md = "# Flint Workspace Entries\n\n| ID | Title | Status | Size | Date |\n|---|---|---|---|---|\n"
    for doc in docs:
        size_kb = f"{doc.file_size / 1024:.1f} KB" if doc.file_size else "0 KB"
        date_str = doc.created_at.strftime('%Y-%m-%d %H:%M')
        result_md += f"| `{doc.id}` | {doc.title} | {doc.status} | {size_kb} | {date_str} |\n"
        
    return result_md

@mcp.tool(
    name="flint_get_entry",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
async def get_entry(params: GetEntryInput, ctx: Context) -> str:
    """Retrieve detailed summaries, conceptual pillars, and RAG analysis for a specific workspace entry ID."""
    user = get_authenticated_user(ctx)
    
    try:
        from bson import ObjectId
        doc_id = ObjectId(params.entry_id)
    except Exception:
        return "Error: Invalid entry ID format."
        
    doc = await DocumentItem.get(doc_id)
    if not doc or doc.user_id != user.id:
        return f"Error: Document with ID '{params.entry_id}' not found in your workspace."
        
    # Format and present all 5 compression layers
    response_md = f"""# {doc.title} (Status: {doc.status})

## Layer 1: Core Insight (1-2 sentences)
{doc.summary_level_1 or 'Not generated.'}

## Layer 2: Conceptual Pillars
"""
    if doc.summary_level_2:
        for idx, pillar in enumerate(doc.summary_level_2):
            response_md += f"\n### Pillar [{pillar.get('name', 'Pillar')}]:\n{pillar.get('explanation', '')}\n"
    else:
        response_md += "None extracted.\n"
        
    response_md += f"""
## Layer 3: Executive Summary & Action Steps
{doc.overall_summary or 'Not generated.'}

### Key Takeaways:
{chr(10).join(f'- {t}' for t in doc.key_takeaways) if doc.key_takeaways else 'None.'}

### Action Items:
{chr(10).join(f'- {a}' for a in doc.action_items) if doc.action_items else 'None.'}

## Layer 4: Detailed Analysis (~600-800 words)
{doc.summary_level_4 or 'Not generated.'}
"""
    return response_md

@mcp.tool(
    name="flint_search_notes",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
async def search_notes(params: SearchInput, ctx: Context) -> str:
    """Perform a semantic vector search across your notes and documents using Gemini embeddings to find relevant contexts."""
    user = get_authenticated_user(ctx)
    
    # 1. Fetch user's documents
    user_docs = await DocumentItem.find(DocumentItem.user_id == user.id).to_list()
    if not user_docs:
        return "You have no documents to search. Create one using flint_compress_thought!"
        
    doc_ids = [d.id for d in user_docs]
    doc_title_map = {d.id: d.title for d in user_docs}
    
    # 2. Fetch chunks
    chunks = await ChunkItem.find(In(ChunkItem.document_id, doc_ids)).to_list()
    if not chunks:
        return "No text contents have been indexed yet."
        
    api_key = settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return "Error: Gemini API Key is not configured on the Flint server."
        
    try:
        # 3. Generate query embedding
        client = genai.Client(api_key=api_key)
        response = client.models.embed_content(
            model='gemini-embedding-001',
            contents=params.query
        )
        query_vector = response.embeddings[0].values
    except Exception as e:
        return f"Error generating query embedding: {str(e)}"
        
    # 4. Compute cosine similarity & rank
    scored_results = []
    for chunk in chunks:
        score = cosine_similarity(query_vector, chunk.embedding)
        scored_results.append((score, chunk))
        
    # Sort descending by score and pick top 5
    scored_results.sort(key=lambda x: x[0], reverse=True)
    top_matches = scored_results[:5]
    
    result_md = f"# Semantic Search Results for: \"{params.query}\"\n\n"
    for idx, (score, chunk) in enumerate(top_matches):
        if score < 0.3:  # Low relevance filter
            continue
        title = doc_title_map.get(chunk.document_id, "Unknown Note")
        result_md += f"### Match #{idx + 1}: {title} (Relevance: {score * 100:.1f}%)\n"
        result_md += f"> {chunk.text}\n\n"
        
    if len(result_md.strip()) <= len(f"# Semantic Search Results for: \"{params.query}\"\n\n"):
        return "No highly relevant matches were found for your query."
        
    return result_md
