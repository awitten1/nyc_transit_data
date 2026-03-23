import React, { useState, useEffect, useCallback } from 'react';

function formatHour(h) {
  if (h === 0)  return '12 AM';
  if (h < 12)   return `${h} AM`;
  if (h === 12) return '12 PM';
  return `${h - 12} PM`;
}

export default function PulseSlider({ hour, onHourChange }) {
  const [playing, setPlaying] = useState(false);

  const tick = useCallback(() => {
    onHourChange(h => (h + 1) % 24);
  }, [onHourChange]);

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(tick, 700);
    return () => clearInterval(id);
  }, [playing, tick]);

  return (
    <div className="pulse-slider">
      <button
        className="pulse-play-btn"
        onClick={() => setPlaying(p => !p)}
        title={playing ? 'Pause' : 'Play'}
      >
        {playing ? '⏸' : '▶'}
      </button>
      <input
        type="range"
        min="0"
        max="23"
        value={hour}
        onChange={e => {
          setPlaying(false);
          onHourChange(Number(e.target.value));
        }}
        className="pulse-range"
      />
      <span className="pulse-label">{formatHour(hour)}</span>
    </div>
  );
}
