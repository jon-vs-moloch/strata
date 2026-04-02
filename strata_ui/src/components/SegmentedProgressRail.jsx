import React from 'react';

const clampPercent = (value) => Math.max(0, Math.min(100, Number(value || 0)));

const segmentTone = (status, accentColor) => {
  const normalized = String(status || '').trim().toLowerCase();
  if (normalized === 'complete' || normalized === 'succeeded') {
    return {
      fill: 'linear-gradient(90deg, rgba(0,242,148,0.96), rgba(104,255,198,0.84))',
      track: 'rgba(0,242,148,0.14)',
      dot: 'rgba(0,242,148,0.92)',
    };
  }
  if (normalized === 'blocked' || normalized === 'failed') {
    return {
      fill: 'linear-gradient(90deg, rgba(255,184,77,0.96), rgba(255,92,92,0.84))',
      track: 'rgba(255,92,92,0.14)',
      dot: 'rgba(255,92,92,0.92)',
    };
  }
  if (normalized === 'pending') {
    return {
      fill: 'linear-gradient(90deg, rgba(143,214,255,0.96), rgba(92,156,255,0.84))',
      track: 'rgba(143,214,255,0.14)',
      dot: 'rgba(143,214,255,0.92)',
    };
  }
  if (normalized === 'pushed' || normalized === 'decomposed') {
    return {
      fill: 'linear-gradient(90deg, rgba(255,215,120,0.96), rgba(255,168,76,0.84))',
      track: 'rgba(255,184,77,0.14)',
      dot: 'rgba(255,184,77,0.92)',
    };
  }
  return {
    fill: `linear-gradient(90deg, ${accentColor}, ${accentColor}cc)`,
    track: 'rgba(255,255,255,0.08)',
    dot: accentColor,
  };
};

export default function SegmentedProgressRail({
  percent = 0,
  accentColor = '#9ca1b4',
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
      <div style={{ flex: 1, height: `${railHeight}px`, borderRadius: '999px', background: 'rgba(255,255,255,0.08)', overflow: 'hidden', position: 'relative' }}>
        <div
          style={{
            width: `${normalizedPercent}%`,
            height: '100%',
            borderRadius: '999px',
            background: `linear-gradient(90deg, ${accentColor}, ${accentColor}cc)`,
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
                    borderRadius: '999px',
                    background: tone.dot,
                    transform: 'translate(-50%, -50%)',
                    boxShadow: `0 0 0 1px rgba(10,10,12,0.9), 0 0 8px ${tone.dot}`,
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
