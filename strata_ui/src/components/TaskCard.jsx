import React, { useMemo, useState } from 'react';
import axios from 'axios';
import { motion, AnimatePresence } from 'framer-motion';
import { GitBranch, FlaskConical, ArchiveX, ChevronDown, ChevronRight, Activity, CheckCircle2, XCircle, Clock, Signal } from 'lucide-react';

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

const TERMINAL_STATUSES = new Set(['complete', 'abandoned', 'cancelled']);

function formatAbsolute(dateString) {
  if (!dateString) return '—';
  const date = new Date(dateString);
  return date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function formatRelative(dateString) {
  if (!dateString) return 'unknown';
  const deltaMs = Date.now() - new Date(dateString).getTime();
  const minutes = Math.max(0, Math.floor(deltaMs / 60000));
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function formatElapsed(startedAt, endedAt = null) {
  if (!startedAt) return 'unknown';
  const start = new Date(startedAt).getTime();
  const end = endedAt ? new Date(endedAt).getTime() : Date.now();
  const totalSeconds = Math.max(0, Math.floor((end - start) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 1) return `${seconds}s`;
  if (minutes < 60) return `${minutes}m ${seconds}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

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

const InterventionWidget = ({ taskId, question, onResolve }) => {
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
      <div style={{ fontSize: '12px', color: '#ffe2b8', lineHeight: 1.45 }}>
        {question || 'Provide the missing context or decision this blocked task needs.'}
      </div>
      <form onSubmit={handleSubmit} style={{ display: 'flex', gap: '8px' }}>
        <input 
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Answer this question as specifically as you can..."
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
  const defaultExpanded = useMemo(() => !TERMINAL_STATUSES.has(task.status), [task.status]);
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);
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
          <div style={{ display: 'flex', gap: '12px', marginTop: '8px', flexWrap: 'wrap', fontSize: '10px', color: '#626275' }}>
            <span title={formatAbsolute(task.created_at)}>Created {formatRelative(task.created_at)}</span>
            <span title={formatAbsolute(task.updated_at)}>Updated {formatRelative(task.updated_at)}</span>
          </div>
        </div>

        {task.status === 'blocked' && (
          <div onClick={(e) => e.stopPropagation()}>
            <InterventionWidget taskId={task.id} question={task.pending_question} />
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
                  <AttemptRow key={attempt.id} attempt={attempt} index={idx + 1} taskUpdatedAt={task.updated_at} />
                ))}
               </div>
            )}
            
            {/* Grouped Child Tasks */}
            <TaskGroup title="Present" tasks={presentTasks} defaultExpanded={true} onArchive={onArchive} />
            <TaskGroup title="Past" tasks={pastTasks} defaultExpanded={false} onArchive={onArchive} />
            <TaskGroup title="Future" tasks={futureTasks} defaultExpanded={true} onArchive={onArchive} />
          </MotionDiv>
        )}
      </AnimatePresence>
    </div>
  );
};

const AttemptRow = ({ attempt, index, taskUpdatedAt }) => {
  const [expanded, setExpanded] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());
  React.useEffect(() => {
    if (attempt.ended_at || attempt.outcome) return undefined;
    const interval = window.setInterval(() => setNowMs(Date.now()), 15000);
    return () => window.clearInterval(interval);
  }, [attempt.ended_at, attempt.outcome]);
  const outcome = OUTCOME_MAP[attempt.outcome] || { color: '#555', Icon: Activity };
  const isActive = !attempt.ended_at && !attempt.outcome;
  const lastActivityAt = taskUpdatedAt || attempt.started_at;
  const recentlyActive = lastActivityAt ? (nowMs - new Date(lastActivityAt).getTime()) < 90000 : false;
  const artifacts = attempt.artifacts && typeof attempt.artifacts === 'object' ? attempt.artifacts : null;
  const summaryBits = [];
  if (artifacts?.job_kind) summaryBits.push(`job ${artifacts.job_kind}`);
  if (artifacts?.duration_s) summaryBits.push(`${Number(artifacts.duration_s).toFixed(1)}s`);
  if (attempt.resolution) summaryBits.push(`resolution ${attempt.resolution.replace(/_/g, ' ')}`);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginLeft: '12px' }}>
      <button
        onClick={() => setExpanded(!expanded)}
        style={{ 
          background: isActive ? 'rgba(0,217,255,0.08)' : 'rgba(255,255,255,0.02)', 
          border: isActive ? '1px solid rgba(0,217,255,0.2)' : '1px solid rgba(255,255,255,0.05)', 
          borderRadius: '10px', 
          padding: '12px 14px',
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          borderLeft: `3px solid ${isActive ? '#00d9ff' : `${outcome.color}66`}`,
          textAlign: 'left',
          width: '100%',
          cursor: 'pointer'
        }}
      >
        <div style={{ fontSize: '10px', fontWeight: 800, color: '#666', fontFamily: "'JetBrains Mono', monospace", minWidth: '24px' }}>
          A{index}
        </div>
        {isActive ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', minWidth: '28px' }}>
            <MotionDiv
              animate={{ opacity: [0.4, 1, 0.4], scale: [0.96, 1.08, 0.96] }}
              transition={{ duration: 1.4, repeat: Infinity }}
              style={{ width: '8px', height: '8px', borderRadius: '999px', background: recentlyActive ? '#00f294' : '#ffb84d', boxShadow: `0 0 10px ${recentlyActive ? '#00f29455' : '#ffb84d55'}` }}
            />
            <Signal size={12} color={recentlyActive ? '#00f294' : '#ffb84d'} />
          </div>
        ) : (
          <outcome.Icon size={12} color={outcome.color} />
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }}>
            <div style={{ fontSize: '11px', color: isActive ? '#d8f8ff' : '#888', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              {isActive ? 'Attempt Active' : `Attempt ${attempt.outcome || 'Pending'}`}
            </div>
            <div style={{ fontSize: '9px', color: '#555' }}>
              {formatAbsolute(attempt.started_at)}
            </div>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', marginTop: '3px', flexWrap: 'wrap' }}>
            <div style={{ fontSize: '10px', color: isActive ? '#86dfff' : '#666' }}>
              {isActive ? `Running for ${formatElapsed(attempt.started_at)}` : `Ran for ${formatElapsed(attempt.started_at, attempt.ended_at)}`}
            </div>
            <div style={{ fontSize: '10px', color: recentlyActive ? '#00f294' : '#777' }}>
              {isActive ? (recentlyActive ? 'actively updating' : 'no recent heartbeat') : `ended ${formatRelative(attempt.ended_at)}`}
            </div>
          </div>
          {summaryBits.length > 0 && (
            <div style={{ fontSize: '10px', color: '#666', marginTop: '4px', fontFamily: "'JetBrains Mono', monospace", overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {summaryBits.join(' · ')}
            </div>
          )}
        </div>
        {expanded ? <ChevronDown size={13} color="#777" /> : <ChevronRight size={13} color="#777" />}
      </button>
      <AnimatePresence>
        {expanded && (
          <MotionDiv
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            style={{ overflow: 'hidden' }}
          >
            <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.05)', borderRadius: '8px', padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
              <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap', fontSize: '10px', color: '#777' }}>
                <span title={formatAbsolute(attempt.started_at)}>Started {formatRelative(attempt.started_at)}</span>
                <span title={formatAbsolute(lastActivityAt)}>Last task update {formatRelative(lastActivityAt)}</span>
                {attempt.ended_at && <span title={formatAbsolute(attempt.ended_at)}>Ended {formatRelative(attempt.ended_at)}</span>}
              </div>
              {attempt.reason && (
                <div style={{ fontSize: '11px', color: '#a8a8b5', lineHeight: 1.45 }}>
                  {attempt.reason}
                </div>
              )}
              {artifacts && (
                <div style={{ fontSize: '10px', color: '#8b8d9e', fontFamily: "'JetBrains Mono', monospace", whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {JSON.stringify(artifacts, null, 2)}
                </div>
              )}
              {!attempt.reason && !artifacts && (
                <div style={{ fontSize: '10px', color: '#666' }}>
                  No detailed trace captured yet.
                </div>
              )}
            </div>
          </MotionDiv>
        )}
      </AnimatePresence>
    </div>
  );
};


export default TaskCard;
