import { useState } from 'react';
import { CheckCircle2, AlertCircle } from 'lucide-react';
import KnowledgebaseTab from './components/KnowledgebaseTab';
import RFPTab from './components/RFPTab';

function App() {
  const [activeTab, setActiveTab] = useState('knowledgebase');
  const [toast, setToast] = useState(null);

  const showToast = (message, type = 'success') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 4000);
  };

  return (
    <div className="app-wrapper">
      <header className="app-header glass-panel">
        <div className="brand">
          <h1>RFP Intelligence</h1>
        </div>
        <div className="nav-tabs">
          <button
            className={`tab-btn ${activeTab === 'rfp' ? 'active' : ''}`}
            onClick={() => setActiveTab('rfp')}
          >
            RFP Generator
          </button>
          <button
            className={`tab-btn ${activeTab === 'knowledgebase' ? 'active' : ''}`}
            onClick={() => setActiveTab('knowledgebase')}
          >
            Knowledgebase Ingestion
          </button>
        </div>
      </header>

      <main className="app-content">
        {activeTab === 'knowledgebase' && <KnowledgebaseTab showToast={showToast} />}
        {activeTab === 'rfp' && <RFPTab showToast={showToast} />}
      </main>

      {toast && (
        <div className="toast-container">
          <div className="toast glass-panel" style={{
            background: toast.type === 'error' ? 'rgba(239, 68, 68, 0.2)' :
              toast.type === 'warning' ? 'rgba(245, 158, 11, 0.2)' :
                'rgba(16, 185, 129, 0.2)'
          }}>
            {toast.type === 'error' ? <AlertCircle size={20} className="error" /> :
              toast.type === 'warning' ? <AlertCircle size={20} className="warning" /> :
                <CheckCircle2 size={20} className="success" />}
            <span>{toast.message}</span>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
