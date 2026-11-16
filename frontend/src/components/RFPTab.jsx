import { useState, useRef, useEffect, useMemo } from 'react';
import axios from 'axios';
import { Send, Paperclip, Terminal, Activity, FileText, Download, X, PanelLeftClose, PanelLeftOpen, ChevronUp, ChevronDown, ChevronRight, Square, UploadCloud } from 'lucide-react';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api';

export default function RFPTab({ showToast }) {
  const [messages, setMessages] = useState([]);
  const [selectedFile, setSelectedFile] = useState(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [logs, setLogs] = useState([]);
  const [telemetry, setTelemetry] = useState({ cost: 0.0, tokens: 0, budget_left: 50.0, time: 0, time_saved: 0.0 });
  const [showModal, setShowModal] = useState(false);
  const [jobId, setJobId] = useState(null);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(400);
  const [showTelemetryDetails, setShowTelemetryDetails] = useState(false);
  const eventSourceRef = useRef(null);
  const [expandedSteps, setExpandedSteps] = useState({});
  const timeoutRef = useRef(null);

  const logsEndRef = useRef(null);
  const messagesEndRef = useRef(null);

  useEffect(() => {
    const fetchInitialTelemetry = async () => {
      try {
        const res = await axios.get(`${API_BASE}/telemetry/summary`);
        setTelemetry(prev => ({
          ...prev,
          budget_left: res.data.budget_remaining || 50.0
        }));
      } catch (err) {
        console.error("Failed to fetch initial telemetry", err);
      }
    };
    fetchInitialTelemetry();
  }, []);

  const { terminalBlocks } = useMemo(() => {
    const termBlocks = [];

    let currentTerm = null;
    let tempActionInput = [];
    let tempAction = null;
    let inActionInput = false;

    logs.forEach(rawLine => {
      // Strip out rich ASCII box characters (just in case any slip through or for historical logs)
      let line = rawLine.replace(/[╭─╮│╰╯├┤┬┴┼]/g, '').trim();
      const trimmed = line;
      if (!trimmed) return;

      if (trimmed.includes('[START]')) {
        const blockId = `term-${termBlocks.length}-${trimmed.replace(/[^a-zA-Z0-9]/g, '').substring(0, 15)}`;
        currentTerm = { id: blockId, agent: trimmed.replace(/\[.*?\]\s*/, ''), task: '', toolInputs: [], toolOutputs: [], finalAnswer: '', llmReceipt: { model: '', cost: 0, tokens: 0, time: 0 } };
        termBlocks.push(currentTerm);
        return;
      }

      if (trimmed.startsWith('[SESSION SUMMARY]')) {
        return;
      }

      if (!currentTerm) {
        currentTerm = { id: `term-${termBlocks.length}-sysboot`, agent: 'System Boot', task: '', toolInputs: [], toolOutputs: [], finalAnswer: '', llmReceipt: { model: '', cost: 0, tokens: 0, time: 0 } };
        termBlocks.push(currentTerm);
      }

      // Ignore redundant "Agent: RFP Organizer" prints inside the execution
      if (trimmed.startsWith('Agent:') && !trimmed.includes('Execution')) {
        return;
      }

      if (trimmed.startsWith('Task:')) {
        inActionInput = false;
        currentTerm.task = trimmed.replace('Task:', '').trim();
      } else if (trimmed === 'Agent Tool Execution' || trimmed.startsWith('Action:')) {
        inActionInput = false;
        tempAction = trimmed.startsWith('Action:') ? trimmed.replace('Action:', '').trim() : 'Executing Tool...';
      } else if (trimmed.startsWith('Using Tool:')) {
        tempAction = trimmed.replace('Using Tool:', '').trim();
      } else if (trimmed === 'Tool Input' || trimmed.startsWith('Action Input:')) {
        inActionInput = true;
        tempActionInput = []; // Start fresh, don't push the header itself
      } else if (trimmed === 'Tool Output' || trimmed.startsWith('Tool Output:') || trimmed.startsWith('Observation:')) {
        inActionInput = false;
        if (tempAction) {
          const inputStr = tempActionInput.join('\n');
          currentTerm.toolInputs.push({ action: tempAction, input: inputStr });
          tempAction = null;
          tempActionInput = [];
        }

        let initialContent = trimmed.replace(/^(Tool Output:|Observation:)/, '').trim();
        if (initialContent === 'Tool Output') initialContent = ''; // Clear if it was exactly the header
        currentTerm.toolOutputs.push(initialContent);
      } else if (trimmed.startsWith('Final Answer:')) {
        inActionInput = false;
        const ans = trimmed.replace('Final Answer:', '').trim();
        if (currentTerm && currentTerm.isFinalAnswerBlock) {
          currentTerm.finalAnswer += '\n' + ans;
        } else {
          const parentAgent = currentTerm ? currentTerm.agent.replace('Agent Started: ', '') : 'Unknown Agent';
          const parentBlockIndex = termBlocks.length - 1;
          currentTerm = {
            id: `term-${termBlocks.length}-final`,
            agent: `Agent Final Answer: ${parentAgent}`,
            task: '',
            toolInputs: [],
            toolOutputs: [],
            finalAnswer: ans,
            llmReceipt: { model: '', cost: 0, tokens: 0, time: 0 },
            isFinalAnswerBlock: true,
            parentBlockIndex: parentBlockIndex
          };
          termBlocks.push(currentTerm);
        }
      } else if (trimmed.startsWith('Model Selected:')) {
        const targetTerm = currentTerm.isFinalAnswerBlock && currentTerm.parentBlockIndex >= 0 ? termBlocks[currentTerm.parentBlockIndex] : currentTerm;
        targetTerm.llmReceipt.model = trimmed.replace('Model Selected:', '').trim();
      } else if (trimmed.startsWith('Cost:')) {
        const targetTerm = currentTerm.isFinalAnswerBlock && currentTerm.parentBlockIndex >= 0 ? termBlocks[currentTerm.parentBlockIndex] : currentTerm;
        const match = trimmed.match(/Cost:\s*\$?([\d.]+)/);
        if (match) targetTerm.llmReceipt.cost = parseFloat(match[1]);
      } else if (trimmed.startsWith('Tokens Needed:')) {
        const targetTerm = currentTerm.isFinalAnswerBlock && currentTerm.parentBlockIndex >= 0 ? termBlocks[currentTerm.parentBlockIndex] : currentTerm;
        const match = trimmed.match(/Tokens Needed:\s*([\d,]+)/);
        if (match) targetTerm.llmReceipt.tokens = parseInt(match[1].replace(/,/g, ''));
      } else if (trimmed.startsWith('Time:')) {
        const targetTerm = currentTerm.isFinalAnswerBlock && currentTerm.parentBlockIndex >= 0 ? termBlocks[currentTerm.parentBlockIndex] : currentTerm;
        const match = trimmed.match(/Time:\s*([\d.]+)s?/);
        if (match) targetTerm.llmReceipt.time = parseFloat(match[1]);
      } else {
        if (trimmed.startsWith('Fallback Selected:') ||
          trimmed.startsWith('Budget Left:') ||
          trimmed.startsWith('(Skipped models during fallback:)') ||
          trimmed.match(/^- (Groq|Gemini|OpenAI|Anthropic|Azure):/i)) {
          return;
        }

        if (inActionInput) {
          tempActionInput.push(trimmed);
        } else if (currentTerm.isFinalAnswerBlock) {
          currentTerm.finalAnswer += (currentTerm.finalAnswer ? '\n' : '') + trimmed;
        } else if (currentTerm.toolOutputs.length > 0) {
          currentTerm.toolOutputs[currentTerm.toolOutputs.length - 1] += (currentTerm.toolOutputs[currentTerm.toolOutputs.length - 1] ? '\n' : '') + trimmed;
        } else {
          // Ignore general chat spam
        }
      }
    });

    return { terminalBlocks: termBlocks };
  }, [logs]);

  const scrollToBottomLogs = () => {
    if (logsEndRef.current) {
      const container = logsEndRef.current.parentElement;
      const isScrolledUp = container.scrollHeight - container.scrollTop > container.clientHeight + 100;
      if (!isScrolledUp) {
        logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
      }
    }
  };
  const scrollToBottomChat = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => scrollToBottomLogs(), [logs]);
  useEffect(() => scrollToBottomChat(), [messages]);

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      if (file.type !== 'application/pdf' && file.type !== 'application/octet-stream' && !file.name.toLowerCase().endsWith('.pdf')) {
        showToast('Only PDF files are supported.', 'error');
        return;
      }
      setSelectedFile(e.target.files[0]);
    }
  };

  const handleDownloadPdf = (id) => {
    window.location.href = `${API_BASE}/download/${id}`;
  };

  const handleMouseDown = (e) => {
    e.preventDefault();
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
  };

  const handleMouseMove = (e) => {
    const newWidth = Math.max(200, Math.min(e.clientX, 800));
    setSidebarWidth(newWidth);
  };

  const handleMouseUp = () => {
    document.removeEventListener('mousemove', handleMouseMove);
    document.removeEventListener('mouseup', handleMouseUp);
  };

  const handleStop = async () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    if (jobId) {
      try {
        await axios.post(`${API_BASE}/cancel/${jobId}`);
        showToast('Cancellation signal sent. Stopping...', 'info');
        if (eventSourceRef.current) {
          eventSourceRef.current.close();
          eventSourceRef.current = null;
        }
        setIsProcessing(false);
      } catch (err) {
        console.error("Failed to cancel", err);
      }
    }
  };

  const handleSend = async () => {
    if (!selectedFile) return;

    const userMessage = {
      role: 'user',
      text: `Uploaded document: ${selectedFile.name}`,
      file: selectedFile.name
    };

    setMessages(prev => [...prev, userMessage]);
    const currentFile = selectedFile;

    setIsProcessing(true);
    setLogs([]);
    setExpandedSteps({});
    setTelemetry(prev => ({ ...prev, cost: 0.0, tokens: 0, time: 0, time_saved: 0.0 }));

    try {
      const formData = new FormData();
      formData.append('file', currentFile);
      formData.append('prompt', 'Generate RFP based on this document.');

      const res = await axios.post(`${API_BASE}/generate_rfp`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      const currentJobId = res.data.job_id;
      setJobId(currentJobId);

      const es = new EventSource(`${API_BASE}/logs/${currentJobId}`);
      eventSourceRef.current = es;

      const resetTimeout = () => {
        if (timeoutRef.current) clearTimeout(timeoutRef.current);
        timeoutRef.current = setTimeout(() => {
          if (eventSourceRef.current) {
            eventSourceRef.current.close();
            eventSourceRef.current = null;
            setIsProcessing(false);
            showToast('Processing timed out. The backend took too long to respond.', 'error');
          }
        }, 5 * 60 * 1000);
      };
      resetTimeout();

      es.onmessage = (event) => {
        resetTimeout();
        const data = JSON.parse(event.data);
        if (data.error) {
          showToast(data.error, 'error');
          setMessages(prev => [...prev, { role: 'error', text: `System Error: ${data.error}` }]);
          if (timeoutRef.current) clearTimeout(timeoutRef.current);
          es.close();
          eventSourceRef.current = null;
          setIsProcessing(false);
          return;
        }

        if (data.type === 'log') {
          setLogs(prev => [...prev, data.content]);
          let exactCost = 0;
          let exactTokens = 0;
          let exactBudget = undefined;
          let exactTime = undefined;

          if (data.content.startsWith('[SESSION SUMMARY]')) {
            const summaryCost = data.content.match(/Cost:\s*\$?([\d.]+)/);
            const summaryTokens = data.content.match(/Tokens Needed:\s*([\d,]+)/);
            const summaryBudget = data.content.match(/Budget Left:\s*\$?([\d.]+)/);
            const summaryTime = data.content.match(/Time:\s*([\d.]+)s?/);
            const summaryTimeSaved = data.content.match(/Time Saved:\s*([\d.]+)/);
            setTelemetry(prev => ({
              ...prev,
              cost: summaryCost ? parseFloat(summaryCost[1]) : prev.cost,
              tokens: summaryTokens ? parseInt(summaryTokens[1].replace(/,/g, '')) : prev.tokens,
              budget_left: summaryBudget !== null && summaryBudget ? parseFloat(summaryBudget[1]) : prev.budget_left,
              time: summaryTime ? parseFloat(summaryTime[1]) : prev.time,
              time_saved: summaryTimeSaved ? parseFloat(summaryTimeSaved[1]) : prev.time_saved
            }));
            return;
          }

          const costMatch = data.content.match(/Cost:\s*\$?([\d.]+)/);
          if (costMatch) exactCost = parseFloat(costMatch[1]);
          const tokensMatch = data.content.match(/Tokens Needed:\s*([\d,]+)/);
          if (tokensMatch) exactTokens = parseInt(tokensMatch[1].replace(/,/g, ''));
          const budgetMatch = data.content.match(/Budget Left:\s*\$?([\d.]+)/);
          if (budgetMatch) exactBudget = parseFloat(budgetMatch[1]);
          const timeMatch = data.content.match(/Time:\s*([\d.]+)s?/);
          if (timeMatch) exactTime = parseFloat(timeMatch[1]);

          if (exactCost > 0 || exactTokens > 0 || exactBudget !== undefined || exactTime !== undefined || data.budget_left !== undefined || data.time !== undefined) {
            setTelemetry(prev => ({
              ...prev,
              cost: exactCost > 0 ? prev.cost + exactCost : prev.cost,
              tokens: exactTokens > 0 ? prev.tokens + exactTokens : prev.tokens,
              budget_left: exactBudget !== undefined ? exactBudget : (data.budget_left !== undefined ? data.budget_left : prev.budget_left),
              time: exactTime !== undefined ? exactTime : (data.time !== undefined ? data.time : prev.time)
            }));
          }
        } else if (data.type === 'done') {
          if (timeoutRef.current) clearTimeout(timeoutRef.current);
          setMessages(prev => [...prev, {
            role: 'agent',
            text: `The PDF is generated with its name: Generated_RFP_Response.pdf`,
            isComplete: true,
            jobId: currentJobId
          }]);
          setIsProcessing(false);
          es.close();
          eventSourceRef.current = null;
        }
      };

      es.onerror = (err) => {
        if (timeoutRef.current) clearTimeout(timeoutRef.current);
        setMessages(prev => [...prev, { role: 'error', text: 'Lost connection to backend. Processing stopped.' }]);
        es.close();
        eventSourceRef.current = null;
        setIsProcessing(false);
      };

    } catch (err) {
      console.error(err);
      showToast('Failed to start RFP generation.', 'error');
      setIsProcessing(false);
    }
  };

  return (
    <div className="chat-layout">
      <div
        className={`sidebar ${isSidebarOpen ? '' : 'collapsed'}`}
        style={isSidebarOpen ? { width: `${sidebarWidth}px`, flexShrink: 0, position: 'relative' } : {}}
      >
        <div className="sidebar-header"><span>Agent Workspace</span></div>
        <div className="terminal-log" style={{ padding: '1rem', overflowY: 'auto' }}>
          <div style={{ color: '#fff', marginBottom: '1rem', fontWeight: 'bold' }}>
            Terminal Output
          </div>
          {logs.length === 0 ? (
            <div style={{ color: '#94a3b8' }}>Awaiting agent initialization...</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginBottom: '1rem' }}>


              {(() => {
                const mcpBlock = terminalBlocks.find(b => b.agent === 'MCP Initialization');
                const mcpExp = expandedSteps['sidebar_mcp'] || false;

                return (
                  <>
                    {mcpBlock && (
                      <div>
                        <div
                          style={{ color: mcpBlock.finalAnswer ? (mcpBlock.finalAnswer.includes('Failed') ? '#ef4444' : '#10b981') : '#f59e0b', display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}
                          onClick={() => setExpandedSteps(prev => ({ ...prev, 'sidebar_mcp': !mcpExp }))}
                        >
                          {mcpExp ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                          {mcpBlock.finalAnswer ? (mcpBlock.finalAnswer.includes('Failed') ? '✗ MCP servers failed to connect' : '✓ MCP servers connected') : '↻ Connecting to MCP servers...'}
                        </div>
                        {mcpExp && (
                          <div style={{ color: '#94a3b8', paddingLeft: '1.75rem', marginTop: '0.25rem', fontSize: '0.85rem' }}>
                            {mcpBlock.finalAnswer ? (
                              mcpBlock.finalAnswer.replace('Successfully connected to:', 'Connected servers:').trim().split(',').filter(Boolean).map((s, i) => (
                                <div key={i}>• {s.trim()}</div>
                              ))
                            ) : (
                              <div>{mcpBlock.task}</div>
                            )}
                          </div>
                        )}
                      </div>
                    )}

                    {terminalBlocks.filter(b => b.agent !== 'MCP Initialization').map((block, idx, arr) => {
                      const isLast = idx === arr.length - 1;
                      const isExpanded = expandedSteps[`sidebar_${block.id}_expanded`] !== undefined
                        ? expandedSteps[`sidebar_${block.id}_expanded`]
                        : (block.isFinalAnswerBlock ? false : isLast);

                      return (
                        <div key={idx} style={{ marginTop: '1rem' }}>
                          <div
                            style={{ color: '#10b981', fontWeight: 'bold', display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer', width: '100%' }}
                            onClick={() => setExpandedSteps(prev => ({ ...prev, [`sidebar_${block.id}_expanded`]: !isExpanded }))}
                          >
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                              {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />} {block.isFinalAnswerBlock ? block.agent : `Agent Started: ${block.agent}`}
                            </div>
                            {(block.llmReceipt.cost > 0 || block.llmReceipt.tokens > 0) && (
                              <div style={{ color: '#a855f7', fontSize: '0.75rem', fontWeight: 600, display: 'flex', gap: '0.75rem', paddingRight: '0.5rem' }}>
                                <span>Cost: ${block.llmReceipt.cost.toFixed(4)}</span>
                                <span>Time: {block.llmReceipt.time.toFixed(1)}s</span>
                                <span>Tokens: {block.llmReceipt.tokens.toLocaleString()}</span>
                              </div>
                            )}
                          </div>

                          {isExpanded && (
                            <div style={{ paddingLeft: '1.25rem', marginTop: '0.5rem', display: 'flex', flexDirection: 'column', gap: '0.5rem', fontSize: '0.85rem' }}>
                              {block.task && (
                                <div style={{ color: '#94a3b8' }}>
                                  <span style={{ fontWeight: 'bold', color: '#fff' }}>Task:</span> {block.task}
                                </div>
                              )}

                              {block.toolInputs.map((ti, tiIdx) => {
                                const execExp = expandedSteps[`sidebar_${block.id}_exec_${tiIdx}`] || false;
                                const inputExp = expandedSteps[`sidebar_${block.id}_input_${tiIdx}`] || false;
                                const outputExp = expandedSteps[`sidebar_${block.id}_output_${tiIdx}`] || false;

                                return (
                                  <div key={tiIdx} style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginTop: '0.5rem' }}>
                                    <div>
                                      <div
                                        style={{ color: '#60a5fa', display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', fontWeight: 600 }}
                                        onClick={() => setExpandedSteps(prev => ({ ...prev, [`sidebar_${block.id}_exec_${tiIdx}`]: !execExp }))}
                                      >
                                        {execExp ? <ChevronDown size={14} /> : <ChevronRight size={14} />} Agent Tool Execution
                                      </div>
                                      {execExp && <div style={{ color: '#e2e8f0', paddingLeft: '1.25rem', marginTop: '0.25rem' }}>{ti.action}</div>}
                                    </div>

                                    <div>
                                      <div
                                        style={{ color: '#a78bfa', display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', fontWeight: 600 }}
                                        onClick={() => setExpandedSteps(prev => ({ ...prev, [`sidebar_${block.id}_input_${tiIdx}`]: !inputExp }))}
                                      >
                                        {inputExp ? <ChevronDown size={14} /> : <ChevronRight size={14} />} Tool Input
                                      </div>
                                      {inputExp && <div style={{ color: '#e2e8f0', paddingLeft: '1.25rem', marginTop: '0.25rem', whiteSpace: 'pre-wrap', fontFamily: 'monospace' }}>{ti.input}</div>}
                                    </div>

                                    {block.toolOutputs[tiIdx] && (
                                      <div>
                                        <div
                                          style={{ color: '#10b981', display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', fontWeight: 600 }}
                                          onClick={() => setExpandedSteps(prev => ({ ...prev, [`sidebar_${block.id}_output_${tiIdx}`]: !outputExp }))}
                                        >
                                          {outputExp ? <ChevronDown size={14} /> : <ChevronRight size={14} />} Tool Output
                                        </div>
                                        {outputExp && <div style={{ color: '#e2e8f0', paddingLeft: '1.25rem', marginTop: '0.25rem', whiteSpace: 'pre-wrap', fontFamily: 'monospace' }}>{block.toolOutputs[tiIdx]}</div>}
                                      </div>
                                    )}
                                  </div>
                                );
                              })}

                              {block.finalAnswer && (
                                <div style={{ marginTop: '0.5rem' }}>
                                  <div style={{ color: '#f59e0b', fontWeight: 600 }}>Agent Final Answer:</div>
                                  <div style={{ color: '#e2e8f0', paddingLeft: '1.25rem', marginTop: '0.25rem', whiteSpace: 'pre-wrap', fontStyle: 'italic' }}>{block.finalAnswer}</div>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </>
                );
              })()}

              {messages.some(m => m.isComplete) && (
                <div style={{ color: '#10b981', marginTop: '1rem' }}>✓ PDF is generated</div>
              )}
            </div>
          )}
          <div ref={logsEndRef} />
        </div>
        <div className="telemetry-panel">
          <div className="glass-panel" style={{ padding: '1rem', display: 'flex', flexDirection: 'column', gap: '1rem' }} onClick={() => setShowTelemetryDetails(!showTelemetryDetails)}>
            <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', justifyContent: 'space-between', color: '#fff' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}><Activity size={18} color="#a855f7" /> Session Telemetry</span>
              {showTelemetryDetails ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
            </h3>
            {showTelemetryDetails && (
              <>
                <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '0.5rem', marginTop: '0.5rem' }}>
                  <span style={{ color: '#94a3b8' }}>Session Cost</span>
                  <span style={{ color: '#34d399', fontWeight: 'bold' }}>${telemetry.cost.toFixed(6)}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '0.5rem' }}>
                  <span style={{ color: '#94a3b8' }}>Tokens Used</span>
                  <span style={{ color: '#a855f7', fontWeight: 'bold' }}>{telemetry.tokens.toLocaleString()}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '0.5rem' }}>
                  <span style={{ color: '#94a3b8' }}>Budget Left</span>
                  <span style={{ color: '#3b82f6', fontWeight: 'bold' }}>${telemetry.budget_left.toFixed(6)}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#94a3b8' }}>Time Needed</span>
                  <span style={{ color: '#f59e0b', fontWeight: 'bold' }}>{telemetry.time.toFixed(1)}s</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '0.5rem', paddingTop: '0.5rem', borderTop: '1px dashed rgba(255,255,255,0.2)' }}>
                  <span style={{ color: '#94a3b8' }}>Est. Time Saved</span>
                  <span style={{ color: '#10b981', fontWeight: 'bold' }}>{telemetry.time_saved.toFixed(1)} hours</span>
                </div>
              </>
            )}
          </div>
        </div>
        <div style={{ position: 'absolute', right: 0, top: 0, bottom: 0, width: '4px', cursor: 'col-resize', backgroundColor: 'transparent' }} onMouseDown={handleMouseDown} />
      </div>

      <div className="chat-main">
        <button className="sidebar-toggle-btn" onClick={() => setIsSidebarOpen(!isSidebarOpen)} title="Toggle Sidebar">
          {isSidebarOpen ? <PanelLeftClose size={20} /> : <PanelLeftOpen size={20} />}
        </button>

        <div className="chat-history">
          {messages.length === 0 && !isProcessing ? (
            <div style={{ textAlign: 'center', color: 'var(--text-muted)', marginTop: '20vh' }}>
              <FileText size={48} style={{ margin: '0 auto 1rem', opacity: 0.5 }} />
              <h2 style={{ color: 'var(--text-main)', marginBottom: '0.5rem' }}>Where should we begin?</h2>
              <p>Attach an RFP document to start generating.</p>
            </div>
          ) : (
            <>
              {messages.map((msg, idx) => {
                if (msg.role === 'error') {
                  return (
                    <div key={`msg_${idx}`} className="chat-bubble chat-agent" style={{ background: 'rgba(239, 68, 68, 0.1)', border: '1px solid rgba(239, 68, 68, 0.3)' }}>
                      <div style={{ color: '#ef4444', fontWeight: 'bold', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <X size={16} /> Error
                      </div>
                      <div style={{ color: '#f87171', marginTop: '0.25rem' }}>{msg.text}</div>
                    </div>
                  );
                }
                return (
                  <div key={`msg_${idx}`} className={`chat-bubble ${msg.role === 'user' ? 'chat-user' : 'chat-agent'}`}>
                    {msg.role === 'user' && msg.file && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem', fontSize: '0.9rem', opacity: 0.8 }}>
                        <Paperclip size={14} /> {msg.file}
                      </div>
                    )}
                    <div>{msg.text}</div>
                    {msg.isComplete && msg.jobId && (
                      <div style={{ marginTop: '1rem' }}>
                        <div
                          onClick={() => handleDownloadPdf(msg.jobId)}
                          style={{
                            display: 'inline-flex', alignItems: 'center', gap: '0.75rem', padding: '0.75rem 1rem',
                            background: 'rgba(255,255,255,0.05)', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.1)',
                            cursor: 'pointer', transition: 'background 0.2s'
                          }}
                        >
                          <div style={{ background: '#ef4444', color: '#fff', padding: '0.5rem', borderRadius: '8px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                            <FileText size={20} />
                          </div>
                          <div style={{ display: 'flex', flexDirection: 'column' }}>
                            <span style={{ color: '#fff', fontSize: '0.9rem', fontWeight: 500 }}>Generated_RFP_Response.pdf</span>
                            <span style={{ color: '#94a3b8', fontSize: '0.75rem', fontWeight: 600 }}>PDF Document • Click to Download</span>
                          </div>
                          <div style={{ marginLeft: '0.5rem', color: '#94a3b8' }}>
                            <Download size={16} />
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
              {isProcessing && terminalBlocks.filter(b => b.agent !== 'MCP Initialization').map((block, idx, arr) => {
                const isLast = idx === arr.length - 1;
                const isThinkingExpanded = expandedSteps[`${block.id}_thinking`] !== undefined ? expandedSteps[`${block.id}_thinking`] : isLast;

                return (
                  <div key={idx} className="chat-bubble chat-agent">
                    <div style={{ color: '#10b981', fontWeight: 'bold', marginBottom: '0.5rem' }}>
                      Agent {block.agent} has started its work
                    </div>

                    <div
                      style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '0.5rem', color: '#475569' }}
                      onClick={() => setExpandedSteps(prev => ({ ...prev, [`${block.id}_thinking`]: !isThinkingExpanded }))}
                    >
                      {isThinkingExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      <span style={{ fontWeight: 600 }}>Agent is thinking...</span>
                    </div>

                    {isThinkingExpanded && (
                      <div style={{ marginTop: '0.5rem', color: '#1e293b', paddingLeft: '1.25rem', fontSize: '0.9rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                        {block.task && (
                          <div style={{ color: '#334155' }}>
                            <span style={{ fontWeight: 600 }}>Task:</span> {block.task}
                          </div>
                        )}

                        {block.toolInputs.map((ti, tiIdx) => {
                          const execExpanded = expandedSteps[`${block.id}_exec_${tiIdx}`] || false;
                          const inputExpanded = expandedSteps[`${block.id}_input_${tiIdx}`] || false;
                          const outputExpanded = expandedSteps[`${block.id}_output_${tiIdx}`] || false;

                          return (
                            <div key={tiIdx} style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginTop: '0.5rem' }}>
                              <div>
                                <div
                                  style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '0.5rem', color: '#2563eb' }}
                                  onClick={() => setExpandedSteps(prev => ({ ...prev, [`${block.id}_exec_${tiIdx}`]: !execExpanded }))}
                                >
                                  {execExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                                  <span style={{ fontWeight: 600 }}>Agent Tool Execution</span>
                                </div>
                                {execExpanded && <div style={{ color: '#1e293b', paddingLeft: '1.25rem', marginTop: '0.25rem' }}>{ti.action}</div>}
                              </div>

                              <div>
                                <div
                                  style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '0.5rem', color: '#7c3aed' }}
                                  onClick={() => setExpandedSteps(prev => ({ ...prev, [`${block.id}_input_${tiIdx}`]: !inputExpanded }))}
                                >
                                  {inputExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                                  <span style={{ fontWeight: 600 }}>Tool Input</span>
                                </div>
                                {inputExpanded && <div style={{ color: '#1e293b', paddingLeft: '1.25rem', marginTop: '0.25rem', whiteSpace: 'pre-wrap', fontFamily: 'monospace' }}>{ti.input}</div>}
                              </div>

                              {block.toolOutputs[tiIdx] && (
                                <div>
                                  <div
                                    style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '0.5rem', color: '#059669' }}
                                    onClick={() => setExpandedSteps(prev => ({ ...prev, [`${block.id}_output_${tiIdx}`]: !outputExpanded }))}
                                  >
                                    {outputExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                                    <span style={{ fontWeight: 600 }}>Tool Output</span>
                                  </div>
                                  {outputExpanded && <div style={{ color: '#1e293b', paddingLeft: '1.25rem', marginTop: '0.25rem', whiteSpace: 'pre-wrap', fontFamily: 'monospace' }}>{block.toolOutputs[tiIdx]}</div>}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}

                    {block.finalAnswer && (
                      <div style={{ color: '#10b981', fontWeight: 'bold', marginTop: '0.75rem' }}>
                        Agent {block.agent} has completed its work
                      </div>
                    )}
                  </div>
                );
              })}

              {isProcessing && (
                <div className="chat-bubble chat-agent" style={{ opacity: 0.7 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <div style={{ width: '16px', height: '16px', border: '2px solid rgba(255,255,255,0.1)', borderTop: '2px solid var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite' }} />
                    <span>Working...</span>
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </>
          )}
        </div>

        {/* Upload Zone & Generate Button */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1rem', marginTop: 'auto', paddingTop: '1rem', width: '100%', padding: '0 2rem 2rem 2rem' }}>
          {!isProcessing && !selectedFile && (
            <div
              className="upload-zone glass-panel"
              style={{
                width: '100%', maxWidth: '600px', margin: '0 auto',
                cursor: 'pointer'
              }}
              onClick={() => document.getElementById('rfp-file').click()}
            >
              <UploadCloud className="upload-icon" />
              <div className="upload-text">Upload RFP PDF</div>
              <div className="upload-subtext">Click to select a document</div>
              <input type="file" id="rfp-file" style={{ display: 'none' }} accept="application/pdf" onChange={handleFileChange} />
            </div>
          )}

          {selectedFile && !isProcessing && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1rem', width: '100%' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', padding: '1.25rem', background: '#f8fafc', borderRadius: '12px', width: '100%', maxWidth: '600px' }}>
                <div style={{ background: '#ef4444', color: '#fff', padding: '0.75rem', borderRadius: '8px' }}>
                  <FileText size={24} />
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', flex: 1 }}>
                  <span style={{ color: '#1e293b', fontWeight: 600, fontSize: '1.1rem' }}>{selectedFile.name}</span>
                  <span style={{ color: '#64748b', fontSize: '0.85rem' }}>Ready to generate</span>
                </div>
                <button onClick={() => setSelectedFile(null)} style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: '#64748b', padding: '0.5rem' }}>
                  <X size={24} />
                </button>
              </div>
              <button
                className="chat-send-btn"
                style={{ width: '100%', maxWidth: '600px', padding: '1rem', fontSize: '1.1rem', borderRadius: '12px', display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '0.5rem', background: '#10b981', color: '#fff' }}
                onClick={handleSend}
              >
                <Terminal size={20} /> Generate RFP
              </button>
            </div>
          )}

          {isProcessing && (
            <button
              className="chat-send-btn"
              style={{ width: '100%', maxWidth: '600px', padding: '1rem', fontSize: '1.1rem', borderRadius: '12px', background: '#ef4444', color: '#fff', display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '0.5rem', border: 'none', cursor: 'pointer' }}
              onClick={handleStop}
            >
              <Square size={16} fill="currentColor" /> Stop Generation
            </button>
          )}
        </div>
      </div>


    </div>
  );
}
