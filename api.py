import os
import re
import sys
import io
import time
import json
import uuid
import asyncio
import sqlite3
import shutil
import contextlib
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Form, Response, Request
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Import our refactored ingestion logic
from ingest_knowledge import (
    setup_qdrant, 
    delete_document, 
    ingest_document, 
    get_file_hash
)
from fastembed import SparseTextEmbedding
from litellm_router.protections.budget_tracking import BudgetTracker
from litellm_router.config import AppConfig

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, sparse_model, tracker
    AppConfig.initialize_environment()
    tracker = BudgetTracker()
    print("Loading BM25 Model and connecting to Qdrant...")
    
    # 1. Warm up the singletons used by the Database Search Tool so it doesn't happen on the first batch
    from rfp.tools import get_sparse_model, get_qdrant_client
    sparse_model = get_sparse_model()
    _ = get_qdrant_client()
    
    # 2. Keep the existing ingestion connection logic untouched
    client = setup_qdrant()
    
    init_db()
    if not os.path.exists(KNOWLEDGEBASE_DIR):
        os.makedirs(KNOWLEDGEBASE_DIR, exist_ok=True)
    yield

app = FastAPI(title="Knowledgebase API", lifespan=lifespan)

# Allow CORS for the React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/public", StaticFiles(directory="frontend/public"), name="public")

DB_PATH = "documents.db"
KNOWLEDGEBASE_DIR = os.path.join(os.path.dirname(__file__), "Knowledgebase")

# Global dependencies (initialized on startup)
client = None
sparse_model = None
tracker = None

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS documents 
                 (filename TEXT PRIMARY KEY, hash TEXT, uploaded_at TEXT, status TEXT, cost REAL DEFAULT 0.0, tokens INTEGER DEFAULT 0)''')
    try:
        c.execute('ALTER TABLE documents ADD COLUMN cost REAL DEFAULT 0.0')
        c.execute('ALTER TABLE documents ADD COLUMN tokens INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        pass
    try:
        c.execute('ALTER TABLE documents ADD COLUMN error_message TEXT')
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()



class DocumentResponse(BaseModel):
    filename: str
    uploaded_at: str
    status: str
    cost: float = 0.0
    tokens: int = 0
    error_message: str | None = None

@app.get("/api/documents", response_model=list[DocumentResponse])
def get_documents():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT filename, uploaded_at, status, cost, tokens, error_message FROM documents ORDER BY uploaded_at DESC")
    rows = c.fetchall()
    conn.close()
    return [{"filename": r[0], "uploaded_at": r[1], "status": r[2], "cost": r[3] or 0.0, "tokens": r[4] or 0, "error_message": r[5]} for r in rows]

@app.get("/api/telemetry/summary")
def get_telemetry_summary():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT SUM(tokens) FROM documents")
    row = c.fetchone()
    conn.close()
    
    total_tokens = row[0] or 0
    total_cost = tracker.get_current_spend() if tracker else 0.0
    budget_limit = tracker.max_monthly_budget_usd if tracker else 50.0
    
    return {
        "total_cost": total_cost,
        "total_tokens": total_tokens,
        "budget_limit": budget_limit,
        "budget_remaining": max(0.0, budget_limit - total_cost)
    }

def background_ingest(filepath: str, filename: str, file_hash: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        result = ingest_document(client, sparse_model, tracker, filepath, filename)
        status = "ingested" if result["status"] == "success" else result["status"]
        error_message = result.get("error", None)
        cost = result.get("cost", 0.0)
        tokens = result.get("tokens", 0)
        c.execute("UPDATE documents SET status = ?, cost = ?, tokens = ?, error_message = ? WHERE filename = ?", (status, cost, tokens, error_message, filename))
    except Exception as e:
        print(f"Ingestion error: {e}")
        c.execute("UPDATE documents SET status = 'error', error_message = ? WHERE filename = ?", (str(e), filename))
    conn.commit()
    conn.close()

@app.post("/api/upload")
async def upload_document(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    safe_filename = os.path.basename(file.filename)
    filepath = os.path.join(KNOWLEDGEBASE_DIR, safe_filename)
    
    # Save the file temporarily to compute hash
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    file_hash = get_file_hash(filepath)
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT hash FROM documents WHERE filename = ?", (safe_filename,))
    row = c.fetchone()
    
    if row and row[0] == file_hash:
        # File is exactly the same as what's in DB
        conn.close()
        os.remove(filepath)
        raise HTTPException(status_code=409, detail="Data already present. File is identical to the one in database.")
    
    # New or modified file -> Add to DB as processing
    c.execute("INSERT OR REPLACE INTO documents (filename, hash, uploaded_at, status, cost, tokens) VALUES (?, ?, ?, ?, ?, ?)",
              (safe_filename, file_hash, datetime.now().isoformat(), "processing", 0.0, 0))
    conn.commit()
    conn.close()
    
    # Process the Heavy OpenAI stuff in the background so UI doesn't freeze
    background_tasks.add_task(background_ingest, filepath, file.filename, file_hash)
    
    return {"message": "Upload successful, processing in background.", "filename": file.filename}

@app.delete("/api/documents/{filename}")
def delete_doc(filename: str):
    # 1. Delete vectors from Qdrant
    try:
        delete_document(client, filename)
    except Exception as e:
        print(f"Error deleting from Qdrant: {e}")
        
    # 2. Delete actual PDF file
    filepath = os.path.join(KNOWLEDGEBASE_DIR, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        
    # 3. Remove from SQLite tracking DB
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM documents WHERE filename = ?", (filename,))
    conn.commit()
    conn.close()
    
    return {"message": f"Successfully deleted {filename}"}


job_logs = {}
CANCELLED_JOBS = set()

class TaskCancelledError(Exception):
    pass


from rfp.rfp_orchestrator import RFPOrchestrator

class QueueStream(io.StringIO):
    def __init__(self, queue, job_id, start_time, loop):
        super().__init__()
        self.queue = queue
        self.job_id = job_id
        self.start_time = start_time
        self.loop = loop
        self._buffer = ""

    def write(self, text):
        if self.job_id in CANCELLED_JOBS:
            raise TaskCancelledError("Job was cancelled by user.")
        
        # Strip ANSI codes for clean UI
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        text = ansi_escape.sub('', text)
        
        # Strip ASCII box characters generated by rich/CrewAI
        text = re.sub(r'[╭─╮│╰╯├┤┬┴┼]', '', text)

        # Skip CrewAI summary tree spam at the end
        if "Crew: crew" in text or ("Task:" in text and "-" in text and len(text) > 40 and "Completed" not in text):
            return
        if "Assigned to:" in text or "Status: Completed" in text:
            return

        self._buffer += text
        # If we have a newline, push the complete lines
        if '\n' in self._buffer:
            lines = self._buffer.split('\n')
            self._buffer = lines.pop() # Keep the last incomplete line
            for line in lines:
                if line.strip():
                    current_time = time.time() - self.start_time
                    budget_left = tracker.max_monthly_budget_usd - tracker.get_current_spend() if tracker else 50.0
                    asyncio.run_coroutine_threadsafe(
                        self.queue.put(json.dumps({
                            'type': 'log', 
                            'content': line.strip(), 
                            'cost': 0, # Frontend will extract exact cost via Regex, but we send global budget
                            'tokens': 0,
                            'budget_left': budget_left,
                            'time': current_time
                        })),
                        self.loop
                    )

async def rfp_agent_orchestrator(job_id: str, prompt: str, filename: str):
    logs = job_logs[job_id]
    start_time = time.time()
    loop = asyncio.get_running_loop()
    
    def run_orchestrator():
        # Redirect stdout to our custom QueueStream
        qs = QueueStream(logs, job_id, start_time, loop)
        with contextlib.redirect_stdout(qs), contextlib.redirect_stderr(qs):
            try:
                orchestrator = RFPOrchestrator()
                # Pass a unique output filename so it doesn't overwrite
                output_pdf = f"RFP_{job_id}.pdf"
                orchestrator.run(query=prompt, filename=filename, output_filename=output_pdf)
            except TaskCancelledError as e:
                err_msg = f"[CANCELLED] Job was stopped by user."
                print(err_msg)
                asyncio.run_coroutine_threadsafe(
                    logs.put(json.dumps({'error': err_msg})), loop
                )
            except Exception as e:
                err_msg = f"[ERROR] Fatal exception in Orchestrator: {e}"
                print(err_msg)
                asyncio.run_coroutine_threadsafe(
                    logs.put(json.dumps({'error': err_msg})), loop
                )
                
        # Push any remaining buffer
        if qs._buffer.strip():
            budget_left = tracker.max_monthly_budget_usd - tracker.get_current_spend() if tracker else 50.0
            asyncio.run_coroutine_threadsafe(
                logs.put(json.dumps({
                    'type': 'log', 'content': qs._buffer.strip(), 
                    'cost': 0, 'tokens': 0, 'budget_left': budget_left, 'time': time.time() - start_time
                })),
                loop
            )
            
    # Run synchronously in a thread
    await asyncio.to_thread(run_orchestrator)
    
    
    # Check if the PDF actually got generated before reporting success
    rd_dir = os.path.dirname(os.path.abspath(__file__))
    expected_pdf = os.path.join(rd_dir, f"RFP_{job_id}.pdf")
    if os.path.exists(expected_pdf):
        await logs.put(json.dumps({'type': 'done', 'content': 'PDF Generation Complete!', 'filename': f"RFP_{job_id}.pdf"}))
    else:
        await logs.put(json.dumps({'error': 'PDF generation failed internally. Please check the logs.'}))
        
    global is_generating
    is_generating = False

is_generating = False

@app.post('/api/generate_rfp')
async def generate_rfp(background_tasks: BackgroundTasks, file: UploadFile = File(...), prompt: str = Form(...)):
    global is_generating
    if is_generating:
        raise HTTPException(status_code=429, detail="A PDF generation job is already running. Please wait for it to finish.")
    
    is_generating = True
    job_id = str(uuid.uuid4())
    job_logs[job_id] = asyncio.Queue()
    if job_id in CANCELLED_JOBS:
        CANCELLED_JOBS.remove(job_id)
    
    # Save the uploaded file to the Documents folder
    docs_dir = os.path.join(os.path.dirname(__file__), 'Documents')
    os.makedirs(docs_dir, exist_ok=True)
    safe_filename = os.path.basename(file.filename)
    file_path = os.path.join(docs_dir, safe_filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    # Start orchestrator in background using the saved file path
    background_tasks.add_task(rfp_agent_orchestrator, job_id, prompt, file_path)
    return {'job_id': job_id}

@app.post('/api/cancel/{job_id}')
async def cancel_rfp(job_id: str):
    CANCELLED_JOBS.add(job_id)
    return {"status": "cancelled"}

@app.get('/api/logs/{job_id}')
async def stream_logs(job_id: str, request: Request):
    async def event_generator():
        try:
            if job_id not in job_logs:
                yield f'data: {json.dumps({"error": "Job not found"})}\n\n'
                return
                
            queue = job_logs[job_id]
            while True:
                if await request.is_disconnected():
                    break
                    
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                    
                yield f'data: {msg}\n\n'
                data = json.loads(msg)
                if data.get('type') == 'done':
                    break
        finally:
            if job_id in job_logs:
                del job_logs[job_id]
                
    return StreamingResponse(event_generator(), media_type='text/event-stream')


@app.get('/api/download/{job_id}')
async def download_rfp(job_id: str):
    rd_dir = os.path.dirname(os.path.abspath(__file__))
    filename = f"RFP_{job_id}.pdf"
    filepath = os.path.join(rd_dir, filename)
    
    if os.path.exists(filepath):
        with open(filepath, "rb") as f:
            pdf_bytes = f.read()
        headers = {
            'Content-Disposition': f'attachment; filename="Generated_RFP_Response.pdf"',
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0'
        }
        return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)
    else:
        # Fallback dummy if not found for some reason
        dummy_pdf_content = b"%PDF-1.4\n1 0 obj\n<< /Title (RFP Not Found) >>\nendobj\n"
        headers = {
            'Content-Disposition': 'attachment; filename="Not_Found.pdf"',
            'Cache-Control': 'no-cache, no-store, must-revalidate'
        }
        return Response(content=dummy_pdf_content, media_type="application/pdf", headers=headers)


