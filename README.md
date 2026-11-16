# RFP Intelligence

> An enterprise-grade AI system that automates Request for Proposal (RFP) processing. It reads complex RFP documents, extracts questions, searches a company knowledgebase, and drafts professional, accurate responses while strictly controlling API budgets and multi-LLM routing.

---

## Project Overview

**RFP Intelligence** is built around a code-orchestrated multi-agent pipeline using **CrewAI**. It ensures zero-hallucination responses by employing a strict Retrieval-Augmented Generation (RAG) architecture powered by **Qdrant** (Hybrid Vector Search) and **MCP** (Model Context Protocol).

Every single LLM call is routed through our custom **LiteLLM Control Gateway**, which provides intelligent model selection, rate limiting, and Redis-backed budget tracking.

---

## High-Level Architecture & Folder Structure

```
r&d/
├── api.py                      # FastAPI Backend — routes, SSE streaming, file handling
├── ingest_knowledge.py         # Knowledgebase Vector DB Ingestion Pipeline
├── run.py                      # Interactive CLI entry point
├── pyproject.toml              # Dependencies and project metadata
├── .env                        # Environment variables and API keys
├── frontend/                   # React/Vite web interface
├── rfp/                        # Core RFP processing pipeline and orchestrator
├── agents/                     # Shared agent framework and MCP tool wrappers
├── litellm_router/             # Multi-LLM gateway, budget tracking, and routing
├── mcp_server/                 # MCP client and prompt management
└── Knowledgebase/              # Local storage for uploaded company documents
```

---

## Core Components Explained

### 1. `api.py` — The Backend API
A **FastAPI** server that acts as the bridge between the React frontend and the backend AI pipelines.
- **Endpoints**: `/api/upload` (for knowledgebase PDFs), `/api/generate_rfp` (kicks off the agent pipeline), `/api/documents` (list/delete DB entries).
- **SSE Streaming**: Uses Server-Sent Events (`/api/logs/{job_id}`) to stream real-time agent thoughts, tool outputs, and telemetry directly to the frontend chat UI.
- **Background Tasks**: Long-running RFP generation runs in the background to prevent HTTP timeouts.

### 2. `ingest_knowledge.py` — Vector DB Ingestion
The ingestion engine for company knowledge.
- **Document Parsing**: Uses `pymupdf4llm` to convert PDFs to markdown.
- **Smart Chunking**: Splits text semantically by markdown headers (`#`, `##`), falling back to character limits for oversized sections.
- **Hybrid Embeddings**: Generates both dense vectors (OpenAI `text-embedding-3-small` via LiteLLM) and sparse vectors (local `FastEmbed BM25`) for highly accurate keyword + semantic retrieval.
- **Cost Tracking**: Hooks into the `BudgetTracker` to log embedding costs to Redis.
- **Deduplication**: Hashes files (`md5`) to skip re-uploading unmodified documents.

### 3. `rfp/` — The RFP Processing Pipeline
The domain-specific workflow orchestrator (`rfp_orchestrator.py`).
- **Sequential Pipeline**: A hardcoded flow: Extract questions → Batch Search (3 at a time) → Draft Answers → Compile PDF.
- **Agents**: Defines the `Organizer` (extracts questions via MCP) and the `Drafter` (writes answers based strictly on retrieved context).
- **Tools**: Implements the hybrid Qdrant search tool using RRF (Reciprocal Rank Fusion) and the FPDF2 PDF writer.

### 4. `litellm_router/` — LLM Control Gateway
The control plane for all LLM interactions in the system.
- **Enterprise Router**: Classifies tasks and routes them to the best model (OpenAI vs Gemini vs Groq) based on a formula weighing Quality (30%), Latency (20%), Cost (40%), and Success (10%).
- **Budget Tracking**: Redis-backed spend limits block requests if the monthly budget is exceeded.
- **Rate Limiting**: Prevents API throttling (HTTP 429) via token-per-minute tracking.

### 5. `agents/` — Core Agent Framework
The foundation for all CrewAI agents.
- **GatewayAdapter**: Bridges CrewAI to the `litellm_router`, ensuring agents use the gateway for internal reasoning instead of calling APIs directly.
- **MCP Meta-Tools**: Universal tool wrappers that allow any agent to dynamically discover and execute tools exposed by connected MCP servers.
- **Anti-Hallucination Guardrails**: Intercepts LLM outputs to prevent fabricated tool executions or invented final answers.

### 6. `mcp_server/` — Model Context Protocol
The integration layer for external tools.
- **MultiMCPClient**: Connects to multiple MCP servers simultaneously via stdio (subprocesses) or HTTP.
- **Servers Used**: Connects to `mcp-document-reader` (uvx subprocess for reading PDFs) and `@codemcp/prompts` (npx subprocess for serving markdown prompts).

### 7. `frontend/` — Web Interface
A modern **React + Vite** single-page application.
- **Knowledgebase Tab**: Upload, list, and delete company documents.
- **RFP Generator Tab**: Upload an RFP, watch agents work in real-time via SSE streams (with expandable thinking blocks), and download the final PDF.
- **Design System**: Glassmorphism UI with a cohesive dark mode palette built in vanilla CSS.

---

## Technology Stack & Rationale

| Technology | Purpose | Why We Chose It |
|------------|---------|-----------------|
| **CrewAI** | Agent Framework | Perfect for role-based, sequential multi-agent workflows. |
| **LiteLLM** | API Abstraction | One unified API call for OpenAI, Gemini, and Groq + exact cost calculation. |
| **Qdrant** | Vector Database | Native support for Dense + Sparse (Hybrid) search via Reciprocal Rank Fusion. |
| **MCP** | Tool Protocol | Standardizes tool calling; isolates tools into separate, crash-proof subprocesses. |
| **FastAPI** | Backend Web Framework | Asynchronous by default; trivial to implement Server-Sent Events (SSE). |
| **React + Vite** | Frontend Framework | Blazing fast HMR; robust component ecosystem for complex streaming UIs. |
| **Redis** | State Persistence | Safely tracks budget and rate limits across multiple concurrent processes. |

---

## Setup & Execution

### Prerequisites
- Python 3.10+
- Node.js (for the frontend and `npx` MCP server)
- Redis server running locally or accessible remotely
- API Keys for OpenAI, Gemini, Groq, and Qdrant

### 1. Backend Setup

```bash
# Create and activate virtual environment
python -m venv venv
.\venv\Scripts\activate   # On Windows

# Install dependencies
pip install -e ".[dev]"

# Set up environment variables
cp .env
# Edit .env and add your API keys + Redis configuration
```

### 2. Frontend Setup

```bash
cd frontend
npm install
```

### 3. Running the Application

You need two terminal windows:

**Terminal 1: FastAPI Backend**
```bash
# Run the API server (Do NOT use --reload to avoid restarting during file saves)
.\venv\Scripts\python.exe -m uvicorn api:app --port 8000
```

**Terminal 2: React Frontend**
```bash
cd frontend
npm run dev
```

The web interface will be available at `http://localhost:5173`.