import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronDown, ChevronRight, Terminal } from 'lucide-react';
import TaskCard from '../components/TaskCard';

const MotionDiv = motion.div;

const TelemetryCell = ({ value, label }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
    <div style={{ fontSize: '16px', fontWeight: 800, color: '#edeeef', fontFamily: "'JetBrains Mono', monospace" }}>{value}</div>
    <div style={{ fontSize: '9px', color: '#444', fontWeight: 700, letterSpacing: '0.1em' }}>{label}</div>
  </div>
);

export default function TaskPaneContent({
  activeNav,
  laneFinishedTasks,
  showFinishedTasks,
  setShowFinishedTasks,
  focusedTaskPaneTree,
  handleArchiveTask,
  activityNowMs,
  scopeHasAnyTasks,
  scopeOperationalMetrics,
  scopeAttemptMetrics,
  currentScope,
  workerStatus,
  laneStatuses,
  providerTelemetry,
}) {
  return (
    <>
      {activeNav !== 'dashboard' && laneFinishedTasks.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <button
            onClick={() => setShowFinishedTasks(!showFinishedTasks)}
            style={{
              background: 'rgba(255,255,255,0.03)',
              border: '1px solid rgba(255,255,255,0.05)',
              color: '#8f90a3',
              borderRadius: '10px',
              padding: '10px 12px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              cursor: 'pointer',
              fontSize: '11px',
              fontWeight: 800,
              letterSpacing: '0.08em',
              textTransform: 'uppercase'
            }}
          >
            <span>Recent Finished · {laneFinishedTasks.length}</span>
            {showFinishedTasks ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
          <AnimatePresence>
            {showFinishedTasks && (
              <MotionDiv
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                style={{ overflow: 'hidden', display: 'flex', flexDirection: 'column', gap: '10px' }}
              >
                {laneFinishedTasks.map((task) => (
                  <TaskCard key={task.id} task={task} onArchive={() => handleArchiveTask(task.id)} nowMs={activityNowMs} />
                ))}
              </MotionDiv>
            )}
          </AnimatePresence>
        </div>
      )}
      <AnimatePresence>
        {focusedTaskPaneTree.map((task) => (
          <TaskCard key={task.id} task={task} onArchive={() => handleArchiveTask(task.id)} nowMs={activityNowMs} />
        ))}
      </AnimatePresence>

      {focusedTaskPaneTree.length === 0 && (
        <div style={{ textAlign: 'center', color: '#2d2d38', padding: '48px 24px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '12px' }}>
          <Terminal size={28} color="#222228" />
          <div style={{ fontSize: '13px', color: '#333' }}>
            {laneFinishedTasks.length > 0 || scopeHasAnyTasks ? 'No current tasks' : 'No tasks yet'}
          </div>
        </div>
      )}

      <div style={{ padding: '16px 20px', borderTop: '1px solid rgba(255,255,255,0.05)', background: '#0c0c0e', flexShrink: 0 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
          <span style={{ fontSize: '10px', color: '#555', fontWeight: 800, letterSpacing: '0.12em' }}>OPERATIONAL TELEMETRY</span>
          <Terminal size={12} style={{ color: '#333' }} />
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px', marginBottom: '12px' }}>
          <TelemetryCell value={scopeOperationalMetrics.working || '—'} label="TASKS RUNNING NOW" />
          <TelemetryCell value={scopeOperationalMetrics.queued || '—'} label="TASKS QUEUED NEXT" />
          <TelemetryCell value={scopeOperationalMetrics.blocked || '—'} label="TASKS BLOCKED" />
          <TelemetryCell value={scopeOperationalMetrics.needsYou || '—'} label="TASKS WAITING ON YOU" />
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px', marginBottom: '12px' }}>
          <TelemetryCell value={scopeAttemptMetrics.success10 || '—'} label="SUCCEEDED OF LAST 10 ATTEMPTS" />
          <TelemetryCell value={scopeAttemptMetrics.success50 || '—'} label="SUCCEEDED OF LAST 50 ATTEMPTS" />
          <TelemetryCell value={scopeAttemptMetrics.averageDurationLabel || '—'} label="AVERAGE TIME PER ATTEMPT" />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', fontSize: '11px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
            <span style={{ color: '#7f8091' }}>context</span>
            <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>{scopeOperationalMetrics.loadedContextCount} files · {scopeOperationalMetrics.loadedContextBudget} tok</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
            <span style={{ color: '#7f8091' }}>scope health</span>
            <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>
              {currentScope === 'home' ? workerStatus : (laneStatuses?.[currentScope] || 'IDLE')}
            </span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
            <span style={{ color: '#7f8091' }}>transport wait</span>
            <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>
              {Object.values(providerTelemetry || {})[0]?.avg_wait_ms ?? '—'}ms
            </span>
          </div>
        </div>
      </div>
    </>
  );
}
