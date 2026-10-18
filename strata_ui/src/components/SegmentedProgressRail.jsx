import React from 'react';

const clampPercent = (value) => Math.max(0, Math.min(100, Number(value || 0)));

const segmentTone = (status, accentColor) => {
  const normalized = String(status || '').trim().toLowerCase();
  if (normalized === 'complete' || normalized === 'succeeded') {
    return {
      fill: 'linear-gradient(90deg, var(--ok), var(--ok))',
      track: 'color-mix(in oklab, var(--ok) 14%, transparent)',
      dot: 'var(--ok)',
    };
  }
  if (normalized === 'blocked' || normalized === 'failed') {
    return {
      fill: 'linear-gradient(90deg, var(--warn), var(--danger))',
      track: 'color-mix(in oklab, var(--danger) 14%, transparent)',
      dot: 'var(--danger)',
    };
  }
  if (normalized === 'pending') {
    return {
      fill: 'linear-gradient(90deg, var(--info-soft), var(--info))',
      track: 'color-mix(in oklab, var(--info) 14%, transparent)',
      dot: 'var(--info-soft)',
    };
  }
  if (normalized === 'pushed' || normalized === 'decomposed') {
    return {
      fill: 'linear-gradient(90deg, var(--warn), var(--warn))',
      track: 'color-mix(in oklab, var(--warn) 14%, transparent)',
      dot: 'var(--warn)',
    };
  }
  return {
    fill: `linear-gradient(90deg, ${accentColor}, color-mix(in oklab, ${accentColor} 80%, transparent))`,
    track: 'color-mix(in oklab, var(--ink) 8%, transparent)',
    dot: accentColor,
  };
};

export default function SegmentedProgressRail({
  percent = 0,
  accentColor = 'var(--text-3)',
  segments = [],
  compact = false,
}) {
  const normalizedSegments = Array.isArray(segments)
    ? segments.map((segment) => ({
        percent: clampPercent(segment?.percent),
        status: String(segment?.status || '').trim().toLowerCase(),
      }))
    : [];
  const railHeight = compact ? 4 : 5;
  const dotSize = compact ? 5 : 6;
  const normalizedPercent = clampPercent(percent);

  return (
    <div style={{ display: 'flex', alignItems: 'center', minWidth: 0, flex: 1, width: '100%' }}>
      <div style={{ flex: 1, height: `${railHeight}px`, borderRadius: 'var(--r-pill)', background: 'color-mix(in oklab, var(--ink) 8%, transparent)', overflow: 'hidden', position: 'relative' }}>
        <div
          style={{
            width: `${normalizedPercent}%`,
            height: '100%',
            borderRadius: 'var(--r-pill)',
            background: `linear-gradient(90deg, ${accentColor}, color-mix(in oklab, ${accentColor} 80%, transparent))`,
            transition: 'width 0.22s ease',
          }}
        />
        {normalizedSegments.length > 0 && (
          <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
            {normalizedSegments.map((segment, index) => {
              const tone = segmentTone(segment.status, accentColor);
              const left = normalizedSegments.length === 1
                ? 100
                : ((index + 1) / normalizedSegments.length) * 100;
              return (
                <div
                  key={`dot-${index}`}
                  style={{
                    position: 'absolute',
                    left: `${left}%`,
                    top: '50%',
                    width: `${dotSize}px`,
                    height: `${dotSize}px`,
                    borderRadius: 'var(--r-pill)',
                    background: tone.dot,
                    transform: 'translate(-50%, -50%)',
                    boxShadow: `0 0 0 1px color-mix(in oklab, var(--bg) 90%, transparent), 0 0 8px ${tone.dot}`,
                  }}
                />
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
