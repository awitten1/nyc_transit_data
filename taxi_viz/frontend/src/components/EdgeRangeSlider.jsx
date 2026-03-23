import { useEffect, useRef, useCallback } from 'react';

const STEP     = 5;
const TRACK_H  = 130;
const THUMB_R  = 7; // px radius

export default function EdgeRangeSlider({ value, onChange }) {
  const [low, high] = value;
  const trackRef  = useRef(null);
  const dragging  = useRef(null); // 'low' | 'high'
  const valueRef  = useRef(value);
  useEffect(() => { valueRef.current = value; }, [value]);

  const pctFromEvent = useCallback(clientY => {
    const rect = trackRef.current.getBoundingClientRect();
    const raw  = 1 - (clientY - rect.top) / rect.height; // 0=bottom 1=top
    return Math.round(Math.max(0, Math.min(100, raw * 100)) / STEP) * STEP;
  }, []);

  const startDrag = (handle, e) => {
    e.preventDefault();
    dragging.current = handle;
  };

  useEffect(() => {
    const onMove = e => {
      if (!dragging.current || !trackRef.current) return;
      const pct = pctFromEvent(e.clientY);
      const [lo, hi] = valueRef.current;
      if (dragging.current === 'high') {
        onChange([lo, Math.max(pct, lo + STEP)]);
      } else {
        onChange([Math.min(pct, hi - STEP), hi]);
      }
    };
    const onUp = () => { dragging.current = null; };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [onChange, pctFromEvent]);

  return (
    <div className="edge-range-slider">
      <span className="edge-range-label">Edges</span>
      <div ref={trackRef} className="edge-range-track" style={{ height: TRACK_H }}>
        <div className="edge-range-bg" />
        <div className="edge-range-fill" style={{ bottom: `${low}%`, top: `${100 - high}%` }} />

        {/* High (top) handle */}
        <div
          className="edge-range-thumb"
          style={{ bottom: `${high}%` }}
          onMouseDown={e => startDrag('high', e)}
        >
          <span className="edge-range-tip edge-range-tip--left">{high}%</span>
        </div>

        {/* Low (bottom) handle */}
        <div
          className="edge-range-thumb"
          style={{ bottom: `${low}%` }}
          onMouseDown={e => startDrag('low', e)}
        >
          <span className="edge-range-tip edge-range-tip--left">{low}%</span>
        </div>
      </div>
    </div>
  );
}
