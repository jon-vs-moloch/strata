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
  laneQueuedTasks,
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
  laneDetails,
  laneCurrentTaskTitles,
  providerTelemetry,
  onOpenProcedure,
  onOpenTask,
  onOpenWorkbench,
  showStartupActions = false,
  hasPersistedSessions = false,
  onLoadPreviousSession,
  onStartNewSession,
}) {
  const scopeLaneDetail = currentScope === 'home' ? null : (laneDetails?.[currentScope] || null);
  const scopeModeLabel = currentScope === 'home'
    ? `trainer ${String(laneDetails?.trainer?.activity_label || 'Idle').toLowerCase()} · agent ${String(laneDetails?.agent?.activity_label || 'Idle').toLowerCase()}`
    : (scopeLaneDetail?.activity_label || laneStatuses?.[currentScope] || 'IDLE');
  const scopeHeartbeatLabel = currentScope === 'home'
    ? 'shared runtime'
    : scopeLaneDetail?.heartbeat_age_s == null
    ? (scopeLaneDetail?.activity_mode === 'GENERATING' ? 'starting' : 'no heartbeat')
    : `${scopeLaneDetail?.heartbeat_state || 'unknown'} · ${Math.round(Number(scopeLaneDetail?.heartbeat_age_s || 0))}s ago`;
  const scopeCurrentTaskLabel = currentScope === 'home'
    ? `${laneCurrentTaskTitles?.agent || laneCurrentTaskTitles?.trainer || 'no active task'}`
    : (laneCurrentTaskTitles?.[currentScope] || 'no active task');
  const scopeStepLabel = currentScope === 'home'
    ? [
        laneDetails?.trainer?.step_label ? `trainer ${String(laneDetails.trainer.step_label).toLowerCase()}` : '',
        laneDetails?.agent?.step_label ? `agent ${String(laneDetails.agent.step_label).toLowerCase()}` : '',
      ].filter(Boolean).join(' · ') || 'no active step'
    : (scopeLaneDetail?.step_label || 'no active step');
  const scopeStepDetail = currentScope === 'home'
    ? ''
    : [scopeLaneDetail?.step_detail, scopeLaneDetail?.progress_label].filter(Boolean).join(' · ');
  const presentTaskRoots = Array.isArray(focusedTaskPaneTree)
    ? focusedTaskPaneTree.filter((task) => task.status !== 'pending')
    : [];
  const queuedTaskRoots = Array.isArray(laneQueuedTasks) ? laneQueuedTasks : [];
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
            <span>Recently Completed · {laneFinishedTasks.length}</span>
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
                  <TaskCard
                    key={task.id}
                    task={task}
                    onArchive={() => handleArchiveTask(task.id)}
                    nowMs={activityNowMs}
                    laneDetail={laneDetails?.[task.lane] || null}
                    detailLevel="compact"
                    onOpenProcedure={onOpenProcedure}
                    onOpenTask={onOpenTask}
                    onOpenWorkbench={onOpenWorkbench}
                  />
                ))}
              </MotionDiv>
            )}
          </AnimatePresence>
        </div>
      )}
      {presentTaskRoots.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <div style={{ fontSize: '10px', color: '#555', fontWeight: 800, letterSpacing: '0.12em' }}>
            PRESENT TASKS
          </div>
          <AnimatePresence>
            {presentTaskRoots.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                onArchive={() => handleArchiveTask(task.id)}
                nowMs={activityNowMs}
                laneDetail={laneDetails?.[task.lane] || null}
                detailLevel="compact"
                onOpenProcedure={onOpenProcedure}
                onOpenTask={onOpenTask}
                onOpenWorkbench={onOpenWorkbench}
              />
            ))}
          </AnimatePresence>
        </div>
      )}

      {queuedTaskRoots.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <div style={{ fontSize: '10px', color: '#555', fontWeight: 800, letterSpacing: '0.12em' }}>
            QUEUED / PENDING
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {queuedTaskRoots.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                onArchive={() => handleArchiveTask(task.id)}
                nowMs={activityNowMs}
                laneDetail={laneDetails?.[task.lane] || null}
                detailLevel="compact"
                onOpenProcedure={onOpenProcedure}
                onOpenTask={onOpenTask}
                onOpenWorkbench={onOpenWorkbench}
              />
            ))}
          </div>
        </div>
      )}

      {presentTaskRoots.length === 0 && queuedTaskRoots.length === 0 && (
        <div style={{ textAlign: 'center', color: '#2d2d38', padding: '48px 24px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '12px' }}>
          <Terminal size={28} color="#222228" />
          <div style={{ fontSize: '13px', color: '#333' }}>
            {laneFinishedTasks.length > 0 || scopeHasAnyTasks ? 'No current tasks' : 'No tasks yet'}
          </div>
          {showStartupActions && (
            <div style={{ display: 'flex', gap: '10px', marginTop: '8px', flexWrap: 'wrap', justifyContent: 'center' }}>
              {hasPersistedSessions ? (
                <button
                  type="button"
                  onClick={onLoadPreviousSession}
                  style={{
                    borderRadius: '999px',
                    border: '1px solid rgba(255,255,255,0.08)',
                    background: 'rgba(255,255,255,0.04)',
                    color: '#d7d9e6',
                    padding: '10px 16px',
                    fontSize: '12px',
                    fontWeight: 700,
                    cursor: 'pointer',
                  }}
                >
                  Load Previous Session
                </button>
              ) : null}
              <button
                type="button"
                onClick={onStartNewSession}
                style={{
                  borderRadius: '999px',
                  border: '1px solid rgba(130,87,229,0.28)',
                  background: 'rgba(130,87,229,0.14)',
                  color: '#dccfff',
                  padding: '10px 16px',
                  fontSize: '12px',
                  fontWeight: 700,
                  cursor: 'pointer',
                }}
              >
                Start New
              </button>
            </div>
          )}
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
            <span style={{ color: '#7f8091' }}>lane mode</span>
            <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>
              {scopeModeLabel}
            </span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
            <span style={{ color: '#7f8091' }}>heartbeat</span>
            <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>
              {scopeHeartbeatLabel}
            </span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
            <span style={{ color: '#7f8091' }}>current task</span>
            <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace", textAlign: 'right' }}>
              {scopeCurrentTaskLabel}
            </span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
            <span style={{ color: '#7f8091' }}>live step</span>
            <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace", textAlign: 'right' }}>
              {scopeStepLabel}
            </span>
          </div>
          {scopeStepDetail && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
              <span style={{ color: '#7f8091' }}>step detail</span>
              <span style={{ color: '#8ddfff', fontFamily: "'JetBrains Mono', monospace", textAlign: 'right' }}>
                {scopeStepDetail}
              </span>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
