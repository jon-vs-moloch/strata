import React, { useState } from 'react';
import axios from 'axios';
import { motion, AnimatePresence } from 'framer-motion';
import { GitBranch, FlaskConical, ArchiveX, ChevronDown, ChevronRight, Activity, CheckCircle2, XCircle, Clock } from 'lucide-react';

const MotionDiv = motion.div;

const STATUS_MAP = {
  complete:            { bg: 'rgba(0,242,148,0.1)',   color: '#00f294', label: 'Completed',   progress: '100%' },
  working:             { bg: 'rgba(0,217,255,0.1)',   color: '#00d9ff', label: 'Working',     progress: '65%'  },
  blocked:             { bg: 'rgba(255,184,77,0.1)',  color: '#ffb84d', label: 'Blocked',     progress: '30%'  },
  abandoned:           { bg: 'rgba(255,153,0,0.1)',   color: '#ff9900', label: 'Abandoned',   progress: '100%' },
  cancelled:           { bg: 'rgba(148,153,173,0.1)', color: '#9499ad', label: 'Cancelled',   progress: '100%' },
  pushed:              { bg: 'rgba(130,87,229,0.1)',  color: '#8257e5', label: 'Pushed',      progress: '10%'  },
};
const DEFAULT_STATUS = { bg: 'rgba(148,153,173,0.1)', color: '#9499ad', label: 'Pending', progress: '0%' };

const OUTCOME_MAP = {
  succeeded: { color: '#00f294', Icon: CheckCircle2 },
  failed:    { color: '#ff4d4d', Icon: XCircle },
  cancelled: { color: '#9499ad', Icon: Clock },
};

const TYPE_MAP = {
  research: { color: '#00e5cc', label: 'RESEARCH', Icon: FlaskConical },
};

const TaskGroup = ({ title, tasks, defaultExpanded = false, onArchive }) => {
  const [expanded, setExpanded] = useState(defaultExpanded);
  if (tasks.length === 0) return null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '4px' }}>
      <button 
        onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }}
        style={{
          background: 'rgba(255,255,255,0.03)', border: 'none', display: 'flex', alignItems: 'center', gap: '8px',
          color: '#666', fontSize: '10px', fontWeight: 800, textTransform: 'uppercase',
          letterSpacing: '0.1em', cursor: 'pointer', padding: '6px 12px', borderRadius: '6px',
          width: 'fit-content', marginLeft: '12px'
        }}
      >
        {expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        {title} · {tasks.length}
      </button>
      <AnimatePresence>
        {expanded && (
          <MotionDiv
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            style={{ display: 'flex', flexDirection: 'column', gap: '8px', overflow: 'hidden' }}
          >
            {tasks.map(t => <TaskCard key={t.id} task={t} onArchive={onArchive} isNested={true} />)}
          </MotionDiv>
        )}
      </AnimatePresence>
    </div>
  );
};

const InterventionWidget = ({ taskId, onResolve }) => {
  const [input, setInput] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!input.trim()) return;
    setIsSubmitting(true);
    try {
      await axios.post(`http://localhost:8000/tasks/${taskId}/intervene`, {
        override: input
      });
      if (onResolve) onResolve();
      // Optionally reload the page or trigger a re-fetch
      window.location.reload(); 
    } catch (err) {
      console.error('Intervention failed:', err);
      alert('Failed to submit intervention. Check console.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="intervention-widget" style={{
      background: 'rgba(255,184,77,0.05)',
      border: '1px solid rgba(255,184,77,0.2)',
      borderRadius: '8px',
      padding: '12px',
      marginTop: '8px',
      display: 'flex',
      flexDirection: 'column',
      gap: '8px'
    }}>
      <div style={{ fontSize: '11px', color: '#ffb84d', fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        Human Intervention Required
      </div>
      <form onSubmit={handleSubmit} style={{ display: 'flex', gap: '8px' }}>
        <input 
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Provide missing context or instructions..."
          style={{
            flex: 1, background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,119,0,0.2)',
            borderRadius: '6px', padding: '6px 10px', color: '#fff', fontSize: '12px',
            outline: 'none'
          }}
        />
        <button 
          type="submit"
          disabled={isSubmitting}
          style={{
            background: '#ffb84d', color: '#000', border: 'none', borderRadius: '6px',
            padding: '4px 12px', fontSize: '11px', fontWeight: 800, cursor: 'pointer',
            opacity: isSubmitting ? 0.5 : 1
          }}
        >
          {isSubmitting ? '...' : 'SUBMIT'}
        </button>
      </form>
    </div>
  );
};

const TaskCard = ({ task, onArchive, isNested = false }) => {
  const [isExpanded, setIsExpanded] = useState(!isNested); // Main task expanded by default, nested collapsed
  const style = STATUS_MAP[task.status] ?? DEFAULT_STATUS;
  const typeInfo = task.type ? TYPE_MAP[task.type] : null;
  const accentColor = typeInfo ? typeInfo.color : style.color;

  const children = task.children || [];
  const pastStatuses = ['complete', 'abandoned', 'cancelled'];
  const futureStatuses = ['pending'];
  
  const pastTasks = children.filter(c => pastStatuses.includes(c.status));
  const futureTasks = children.filter(c => futureStatuses.includes(c.status));
  // Fallback: If it's not past or future, it belongs in present
  const presentTasks = children.filter(c => !pastStatuses.includes(c.status) && !futureStatuses.includes(c.status));
  
  const hasChildren = children.length > 0 || (task.attempts && task.attempts.length > 0);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <MotionDiv
        layout
        initial={{ opacity: 0, y: 10, scale: 0.97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        className="task-card"
        onClick={() => setIsExpanded(!isExpanded)}
        style={{
          background: isNested ? '#1a1a20' : '#141418',
          border: '1px solid rgba(255,255,255,0.07)',
          padding: '16px',
          borderRadius: '12px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px',
          cursor: 'pointer',
          position: 'relative',
          overflow: 'hidden',
          marginLeft: isNested ? '12px' : '0',
          borderLeft: isNested ? `2px solid ${accentColor}44` : '1px solid rgba(255,255,255,0.07)'
        }}
        whileHover={{ borderColor: accentColor, boxShadow: `0 0 0 1px ${accentColor}33` }}
      >
        {!isNested && <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: '1px', background: `linear-gradient(90deg, transparent, ${accentColor}44, transparent)` }} />}

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px' }}>
          <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
            {hasChildren && (
              isExpanded ? <ChevronDown size={14} color="#888" /> : <ChevronRight size={14} color="#888" />
            )}
            {typeInfo && (
              <span style={{
                padding: '2px 6px', borderRadius: '40px', fontSize: '8px',
                fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.08em',
                background: `${typeInfo.color}18`, color: typeInfo.color,
                display: 'flex', alignItems: 'center', gap: '3px'
              }}>
                <typeInfo.Icon size={8} /> {typeInfo.label}
              </span>
            )}
            <span style={{
              padding: '2px 8px', borderRadius: '40px', fontSize: '9px',
              fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.06em',
              background: style.bg, color: style.color
            }}>
              {style.label}
            </span>
          </div>
          
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
             {task.depth > 0 && (
                <div style={{ color: '#555', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '10px', fontFamily: "'JetBrains Mono', monospace" }}>
                  <GitBranch size={11} /> D{task.depth}
                </div>
             )}
             {!isNested && onArchive && (
                <button 
                  onClick={(e) => { e.stopPropagation(); onArchive(); }}
                  style={{ background: 'none', border: 'none', color: '#555', cursor: 'pointer', padding: '2px' }}
                >
                  <ArchiveX size={13} />
                </button>
             )}
          </div>
        </div>

        <div>
          <h3 style={{ fontWeight: 600, fontSize: '14px', color: '#edeeef', lineHeight: 1.3 }}>{task.title}</h3>
          {(isExpanded || !isNested) && task.description && (
            <p style={{ fontSize: '12px', color: '#6b6b7d', marginTop: '6px', lineHeight: '1.4' }}>{task.description}</p>
          )}
        </div>

        {task.status === 'blocked' && (
          <div onClick={(e) => e.stopPropagation()}>
            <InterventionWidget taskId={task.id} />
          </div>
        )}

        {!isExpanded && (
          <div style={{ height: '3px', background: 'rgba(255,255,255,0.04)', borderRadius: '10px', overflow: 'hidden' }}>
            <div style={{ height: '100%', width: style.progress, background: accentColor, borderRadius: '10px' }} />
          </div>
        )}
      </MotionDiv>

      <AnimatePresence>
        {isExpanded && (
          <MotionDiv
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            style={{ overflow: 'hidden', display: 'flex', flexDirection: 'column', gap: '8px', paddingLeft: '12px' }}
          >
            {/* Attempts */}
            {task.attempts && task.attempts.length > 0 && (
               <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                <div style={{ fontSize: '9px', fontWeight: 800, color: '#444', letterSpacing: '0.1em', marginLeft: '12px' }}>ATTEMPTS</div>
                {task.attempts.map((attempt, idx) => (
                  <AttemptRow key={attempt.id} attempt={attempt} index={idx + 1} />
                ))}
               </div>
            )}
            
            {/* Grouped Child Tasks */}
            <TaskGroup title="Present" tasks={presentTasks} defaultExpanded={true} onArchive={onArchive} />
            <TaskGroup title="Past" tasks={pastTasks} defaultExpanded={false} onArchive={onArchive} />
            <TaskGroup title="Future" tasks={futureTasks} defaultExpanded={false} onArchive={onArchive} />
          </MotionDiv>
        )}
      </AnimatePresence>
    </div>
  );
};

const AttemptRow = ({ attempt, index }) => {
  const outcome = OUTCOME_MAP[attempt.outcome] || { color: '#555', Icon: Activity };
  return (
    <div style={{ 
      background: 'rgba(255,255,255,0.02)', 
      border: '1px solid rgba(255,255,255,0.05)', 
      borderRadius: '8px', 
      padding: '10px 14px',
      display: 'flex',
      alignItems: 'center',
      gap: '10px',
      marginLeft: '12px',
      borderLeft: `2px solid ${outcome.color}66`
    }}>
      <div style={{ fontSize: '10px', fontWeight: 800, color: '#444', fontFamily: "'JetBrains Mono', monospace" }}>
        A{index}
      </div>
      <outcome.Icon size={12} color={outcome.color} />
      <div style={{ flex: 1 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ fontSize: '11px', color: '#888', fontWeight: 600, textTransform: 'uppercase' }}>
            Attempt {attempt.outcome || 'Pending'}
          </div>
          <div style={{ fontSize: '9px', color: '#444' }}>
            {new Date(attempt.started_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </div>
        </div>
        {attempt.reason && (
          <div style={{ fontSize: '11px', color: '#555', marginTop: '2px' }}>{attempt.reason}</div>
        )}
        {attempt.resolution && (
          <div style={{ fontSize: '9px', color: outcome.color, marginTop: '4px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Resolution: {attempt.resolution.replace(/_/g, ' ')}
          </div>
        )}
      </div>
    </div>
  );
};


export default TaskCard;
