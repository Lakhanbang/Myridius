import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { UploadCloud, FileText, Trash2, CheckCircle2, AlertCircle, Loader2, DollarSign, Database, Activity } from 'lucide-react';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api';

export default function KnowledgebaseTab({ showToast }) {
  const [documents, setDocuments] = useState([]);
  const [telemetry, setTelemetry] = useState(null);
  const [isDragging, setIsDragging] = useState(false);
  const [activeError, setActiveError] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [docRes, telRes] = await Promise.all([
        axios.get(`${API_BASE}/documents`),
        axios.get(`${API_BASE}/telemetry/summary`)
      ]);
      setDocuments(docRes.data);
      setTelemetry(telRes.data);
    } catch (err) {
      console.error("Failed to fetch data", err);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 3000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const handleDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setIsDragging(true);
    } else if (e.type === 'dragleave') {
      setIsDragging(false);
    }
  };

  const handleDrop = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileUpload(e.dataTransfer.files[0]);
    }
  };

  const handleFileInput = (e) => {
    if (e.target.files && e.target.files[0]) {
      handleFileUpload(e.target.files[0]);
    }
  };

  const handleFileUpload = async (file) => {
    if (file.type !== 'application/pdf' && file.type !== 'application/octet-stream' && !file.name.toLowerCase().endsWith('.pdf')) {
      showToast('Please upload a PDF file.', 'error');
      return;
    }

    const formData = new FormData();
    formData.append('file', file);

    try {
      showToast('Uploading document...', 'success');
      await axios.post(`${API_BASE}/upload`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      showToast('Upload successful! Processing...', 'success');
      fetchData();
    } catch (err) {
      if (err.response && err.response.status === 409) {
        alert('FILE ALREADY EXISTS: This exact document is already uploaded and present in the database!');
        showToast('File already exists in the database.', 'warning');
      } else if (err.response?.data?.detail) {
        showToast(err.response.data.detail, 'error');
      } else {
        showToast('Upload failed.', 'error');
      }
    }
  };

  const handleDelete = async (filename) => {
    try {
      await axios.delete(`${API_BASE}/documents/${filename}`);
      showToast('Document deleted.', 'success');
      if (activeError === filename) setActiveError(null);
      fetchData();
    } catch (err) {
      showToast('Delete failed.', 'error');
    }
  };

  const getStatusBadge = (doc) => {
    if (doc.status === 'processing') {
      return (
        <span className="status-badge status-processing">
          <Loader2 className="spinner" size={12} style={{ marginRight: 4 }} /> Processing
        </span>
      );
    }
    if (doc.status === 'ingested') {
      return (
        <span className="status-badge status-ingested">
          <CheckCircle2 size={12} style={{ marginRight: 4 }} /> Ingested
        </span>
      );
    }
    if (doc.status === 'error') {
      return (
        <span
          className="status-badge status-error"
          title="Click to toggle error details"
          style={{ cursor: 'pointer' }}
          onClick={() => setActiveError(activeError === doc.filename ? null : doc.filename)}
        >
          <AlertCircle size={12} style={{ marginRight: 4 }} /> Error (Click for details)
        </span>
      );
    }
    return null;
  };

  return (
    <div className="content-container">
      <div
        className={`upload-zone glass-panel ${isDragging ? 'drag-active' : ''}`}
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
      >
        <UploadCloud className="upload-icon" />
        <div className="upload-text">Upload previous data or historical data</div>
        <div className="upload-subtext">Click to upload</div>
        <input
          type="file"
          accept="application/pdf"
          className="upload-input"
          onChange={handleFileInput}
        />
      </div>

      <div className="glass-panel" style={{ padding: '2rem' }}>
        <h2 className="doc-section-title">Ingested Documents</h2>
        {documents.length === 0 ? (
          <p style={{ color: 'var(--text-muted)' }}>No documents uploaded yet.</p>
        ) : (
          <div className="premium-table-container">
            <table className="premium-table">
              <thead>
                <tr>
                  <th>File Name</th>
                  <th>Uploaded On</th>
                  <th>Status</th>
                  <th>Cost</th>
                  <th>Tokens</th>
                  <th style={{ textAlign: 'right' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {documents.map((doc) => (
                  <React.Fragment key={doc.filename}>
                    <tr>
                      <td style={{ fontWeight: 500, color: 'var(--primary)' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                          <FileText size={16} />
                          {doc.filename}
                        </div>
                      </td>
                      <td>{new Date(doc.uploaded_at).toLocaleString()}</td>
                      <td>
                        {getStatusBadge(doc)}
                      </td>
                      <td style={{ color: '#059669', fontWeight: 600 }}>
                        ${(doc.cost || 0).toFixed(6)}
                      </td>
                      <td style={{ color: '#9333ea', fontWeight: 600 }}>
                        {(doc.tokens || 0).toLocaleString()}
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        <button
                          onClick={() => handleDelete(doc.filename)}
                          className="delete-btn"
                          title="Delete Document"
                          style={{ marginLeft: 'auto' }}
                        >
                          <Trash2 size={18} />
                        </button>
                      </td>
                    </tr>
                    {activeError === doc.filename && doc.status === 'error' && (
                      <tr>
                        <td colSpan="6" style={{ padding: '0' }}>
                          <div style={{ background: 'rgba(239, 68, 68, 0.1)', borderLeft: '3px solid #ef4444', padding: '1rem', margin: '0.5rem', borderRadius: '4px', color: '#ef4444', fontSize: '0.9rem' }}>
                            <strong>Error Details:</strong> {doc.error_message || 'This error occurred before the system was upgraded to track error reasons, so the exact log is lost. Please delete and try uploading again.'}
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
