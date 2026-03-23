import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  Plus, RefreshCw, Layers, TrendingUp, Zap, 
  MessageSquare, Send, History, Cpu, 
  Terminal, AlertCircle, CheckCircle2 
} from 'lucide-react';
import Sidebar from './components/Sidebar';
import TaskCard from './components/TaskCard';

const MOCK_TASKS = [
  { id: 1, title: 'Build a robust string reverser', description: 'Implement Unicode support.', status: 'completed', depth: 0 },
  { id: 2, title: 'Refactor Auth Middleware', description: 'Optimize token validation.', status: 'running', depth: 1 },
  { id: 3, title: 'Setup Redis Connection', description: 'Configure host and port.', status: 'waiting_dependencies', depth: 2 },
  { id: 4, title: 'Write unit tests for reverser', description: 'Cover all edge cases.', status: 'completed', depth: 1 },
];

const MOCK_MESSAGES = [
  { id: 1, role: 'assistant', content: 'Hello! I am ready to coordinate your local worker swarm. What should we build today?' },
  { id: 2, role: 'user', content: 'Let’s start by refactoring the authentication middleware to use Redis for session caching.' },
  { id: 3, role: 'assistant', content: 'Understood. I have initiated a root task and decomposed it into 3 subtasks. You can monitor the progress in the task pane.', is_task_creation: true },
  { id: 4, role: 'system', content: 'WORKER BLOCKED: Redis worker requires host configuration.', is_intervention: true }
];

function App() {
  const [activeTab, setActiveTab] = useState('tasks');
  const [messages] = useState(MOCK_MESSAGES);
  const [tasks] = useState(MOCK_TASKS);

  return (
    <div className="app-container" style={{ display: 'flex', height: '100vh', width: '100vw', background: '#0a0a0c' }}>
      
      {/* COLUMN 1: NAVIGATION & PERSISTENCE (LEFT) */}
      <aside style={{ width: '80px', background: '#141418', borderRight: '1px solid rgba(255,255,255,0.05)', display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '24px 0', gap: '24px' }}>
        <div style={{ width: '40px', height: '40px', background: 'linear-gradient(135deg, #8257e5, #00d9ff)', borderRadius: '10px' }} />
        <nav style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
           <div style={{ color: '#8257e5', padding: '12px', background: 'rgba(130, 87, 229, 0.1)', borderRadius: '12px' }}><MessageSquare size={24} /></div>
           <div style={{ color: '#9499ad', padding: '12px' }}><History size={24} /></div>
           <div style={{ color: '#9499ad', padding: '12px' }}><Cpu size={24} /></div>
        </nav>
      </aside>

      {/* COLUMN 2: CHAT INTERFACE (MIDDLE - PRIMARY) */}
      <section style={{ flex: 1, display: 'flex', flexDirection: 'column', background: '#0a0a0c', borderRight: '1px solid rgba(255,255,255,0.05)' }}>
        <header style={{ padding: '24px 32px', borderBottom: '1px solid rgba(255,255,255,0.05)', display: 'flex', justifyContent: 'space-between' }}>
          <div>
            <h2 style={{ fontSize: '18px', fontWeight: 700 }}>Orchestrator Chat</h2>
            <p style={{ fontSize: '12px', color: '#9499ad' }}>Session: Session-Alpha-42</p>
          </div>
          <Zap size={20} style={{ color: '#00f294' }} />
        </header>

        <div style={{ flex: 1, overflowY: 'auto', padding: '32px', display: 'flex', flexDirection: 'column', gap: '24px' }}>
          {messages.map((msg) => (
             <motion.div 
              key={msg.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              style={{
                alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
                maxWidth: '80%',
                background: msg.is_intervention ? 'rgba(255, 77, 77, 0.05)' : (msg.role === 'user' ? '#8257e5' : '#141418'),
                padding: '16px 20px',
                borderRadius: '16px',
                border: msg.is_intervention ? '1px solid #ff4d4d' : '1px solid rgba(255,255,255,0.05)',
                color: msg.role === 'user' ? 'white' : '#edeeef'
              }}
             >
               {msg.is_intervention && <div style={{ color: '#ff4d4d', fontSize: '11px', fontWeight: 800, marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '4px' }}><AlertCircle size={14} /> ACTION REQUIRED</div>}
               <p style={{ fontSize: '15px', lineHeight: '1.5' }}>{msg.content}</p>
             </motion.div>
          ))}
        </div>

        <div style={{ padding: '24px 32px', borderTop: '1px solid rgba(255,255,255,0.05)' }}>
          <div style={{ background: '#141418', borderRadius: '12px', padding: '12px 20px', display: 'flex', alignItems: 'center', gap: '16px', border: '1px solid rgba(255,255,255,0.1)' }}>
            <input 
              placeholder="Give an instruction to the swarm..." 
              style={{ flex: 1, background: 'transparent', border: 'none', color: 'white', outline: 'none', fontSize: '15px' }} 
            />
            <button style={{ background: '#8257e5', border: 'none', color: 'white', padding: '8px', borderRadius: '8px', cursor: 'pointer' }}>
              <Send size={18} />
            </button>
          </div>
        </div>
      </section>

      {/* COLUMN 3: TASK SWARM (RIGHT) */}
      <section style={{ width: '450px', display: 'flex', flexDirection: 'column', background: '#0a0a0c' }}>
        <header style={{ padding: '24px 32px', borderBottom: '1px solid rgba(255,255,255,0.05)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ fontSize: '18px', fontWeight: 700 }}>Active Swarm</h2>
          <div style={{ display: 'flex', gap: '8px' }}>
            <span style={{ fontSize: '12px', color: '#00f294', fontWeight: 600 }}>{tasks.filter(t => t.status === 'completed').length} DONE</span>
          </div>
        </header>

        <div style={{ flex: 1, overflowY: 'auto', padding: '24px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {tasks.map((task) => (
             <TaskCard key={task.id} task={task} />
          ))}

          {/* SYSTEM TELEMETRY (FIXED BOTTOM) */}
          <div style={{ marginTop: 'auto', background: '#141418', borderRadius: '16px', padding: '20px', border: '1px solid rgba(255,255,255,0.05)', display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: '12px', color: '#9499ad', fontWeight: 800 }}>SWARM TELEMETRY</span>
              <Terminal size={14} style={{ color: '#9499ad' }} />
            </div>
            <div style={{ display: 'flex', gap: '24px' }}>
              <div><div style={{ fontSize: '18px', fontWeight: 800 }}>92%</div><div style={{ fontSize: '10px', color: '#9499ad' }}>PASS RATE</div></div>
              <div><div style={{ fontSize: '18px', fontWeight: 800 }}>+12</div><div style={{ fontSize: '10px', color: '#9499ad' }}>META-EVAL</div></div>
              <div><div style={{ fontSize: '18px', fontWeight: 800 }}>8.4k</div><div style={{ fontSize: '10px', color: '#9499ad' }}>TOKEN/M</div></div>
            </div>
          </div>
        </div>
      </section>

    </div>
  );
}

export default App;
