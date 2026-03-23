import React from 'react';
import { motion } from 'framer-motion';
import { GitBranch, Clock, CheckCircle2, PlayCircle } from 'lucide-react';

const TaskCard = ({ task }) => {
  const getStatusStyle = (status) => {
    switch(status) {
      case 'completed': return { bg: 'rgba(0, 242, 148, 0.1)', color: '#00f294', label: 'Completed' };
      case 'running': return { bg: 'rgba(0, 217, 255, 0.1)', color: '#00d9ff', label: 'Running' };
      case 'waiting_dependencies': return { bg: 'rgba(255, 184, 77, 0.1)', color: '#ffb84d', label: 'Blocked' };
      default: return { bg: 'rgba(148, 153, 173, 0.1)', color: '#9499ad', label: 'Queued' };
    }
  };

  const style = getStatusStyle(task.status);

  return (
    <motion.div 
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      className="task-card"
      style={{
        background: '#141418',
        border: '1px solid rgba(255, 255, 255, 0.1)',
        padding: '24px',
        borderRadius: '16px',
        display: 'flex',
        flexDirection: 'column',
        gap: '16px',
        cursor: 'pointer'
      }}
      whileHover={{ scale: 1.01, borderColor: '#8257e5' }}
    >
      <div className="task-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span 
          style={{
            padding: '4px 10px',
            borderRadius: '40px',
            fontSize: '11px',
            fontWeight: 800,
            textTransform: 'uppercase',
            letterSpacing: '0.5px',
            background: style.bg,
            color: style.color
          }}
        >
          {style.label}
        </span>
        <div style={{ color: '#9499ad', display: 'flex', gap: '8px' }}>
          <GitBranch size={16} />
          {task.depth && <span style={{ fontSize: '12px' }}>D{task.depth}</span>}
        </div>
      </div>

      <div>
        <h3 style={{ fontWeight: 600, fontSize: '18px', marginBottom: '4px' }}>{task.title}</h3>
        <p style={{ fontSize: '14px', color: '#9499ad', lineHeight: '1.4' }}>{task.description}</p>
      </div>

      <div className="progress-container" style={{ height: '6px', background: '#0a0a0c', borderRadius: '10px', overflow: 'hidden' }}>
        <motion.div 
          initial={{ width: 0 }}
          animate={{ width: task.status === 'completed' ? '100%' : '65%' }}
          className="progress-bar"
          style={{ height: '100%', background: 'linear-gradient(90deg, #8257e5, #00d9ff)', borderRadius: '10px' }}
        />
      </div>

      <div style={{ display: 'flex', gap: '16px', color: '#9499ad', fontSize: '12px', fontFamily: "'JetBrains Mono', monospace" }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Clock size={14} /> 2m ago
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <PlayCircle size={14} /> 4 candidates
        </div>
      </div>
    </motion.div>
  );
};

export default TaskCard;
