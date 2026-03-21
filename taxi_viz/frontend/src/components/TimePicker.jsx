import React from 'react';

function cmpMonth(a, b) {
  if (!a || !b) return 0;
  if (a.year !== b.year) return a.year - b.year;
  return a.month - b.month;
}

function inRange(m, start, end) {
  if (!start || !end) return false;
  const lo = cmpMonth(start, end) <= 0 ? start : end;
  const hi = cmpMonth(start, end) <= 0 ? end : start;
  return cmpMonth(m, lo) >= 0 && cmpMonth(m, hi) <= 0;
}

function isSame(a, b) {
  return a && b && a.year === b.year && a.month === b.month;
}

export default function TimePicker({ months, cbMonths, selRange, onRangeChange }) {
  const [pendingStart, setPendingStart] = React.useState(null);
  const [hoveredMonth, setHoveredMonth] = React.useState(null);

  const cbSet = React.useMemo(() => {
    const s = new Set();
    (cbMonths || []).forEach(m => s.add(`${m.year}-${m.month}`));
    return s;
  }, [cbMonths]);

  const hasBike = m => cbSet.has(`${m.year}-${m.month}`);

  // Group months by year
  const byYear = React.useMemo(() => {
    const map = new Map();
    (months || []).forEach(m => {
      if (!map.has(m.year)) map.set(m.year, []);
      map.get(m.year).push(m);
    });
    return Array.from(map.entries()).sort((a, b) => a[0] - b[0]);
  }, [months]);

  function handleClick(m) {
    if (!pendingStart) {
      // First click: set pending start
      setPendingStart(m);
    } else {
      // Second click: confirm range
      let start = pendingStart;
      let end = m;
      if (cmpMonth(start, end) > 0) {
        [start, end] = [end, start];
      }
      setPendingStart(null);
      setHoveredMonth(null);
      onRangeChange(start, end);
    }
  }

  function getPillClass(m) {
    const classes = ['month-pill'];

    if (pendingStart) {
      // In selection mode: show preview with hover
      if (isSame(m, pendingStart)) {
        classes.push('month-pill--pending');
      } else if (hoveredMonth) {
        const previewStart = cmpMonth(pendingStart, hoveredMonth) <= 0 ? pendingStart : hoveredMonth;
        const previewEnd = cmpMonth(pendingStart, hoveredMonth) <= 0 ? hoveredMonth : pendingStart;
        if (isSame(m, previewStart)) classes.push('month-pill--range-start');
        else if (isSame(m, previewEnd)) classes.push('month-pill--range-end');
        else if (inRange(m, previewStart, previewEnd)) classes.push('month-pill--in-range');
      }
    } else if (selRange?.start && selRange?.end) {
      // Show confirmed range
      if (isSame(m, selRange.start)) classes.push('month-pill--range-start');
      else if (isSame(m, selRange.end)) classes.push('month-pill--range-end');
      else if (inRange(m, selRange.start, selRange.end)) classes.push('month-pill--in-range');
    }

    if (hasBike(m)) classes.push('month-pill--has-bike');

    return classes.join(' ');
  }

  const instructionText = pendingStart
    ? 'Click a second month to set the end of range'
    : 'Click a month to start selecting a range';

  return (
    <div className="time-picker">
      <div className="time-picker-instruction">{instructionText}</div>

      {pendingStart && (
        <button
          className="time-picker-cancel"
          onClick={() => { setPendingStart(null); setHoveredMonth(null); }}
        >
          Cancel
        </button>
      )}

      <div className="time-picker-grid">
        {byYear.map(([year, yearMonths]) => (
          <div key={year} className="time-picker-year-row">
            <span className="time-picker-year-label">{year}</span>
            <div className="time-picker-months">
              {yearMonths.map(m => (
                <button
                  key={`${m.year}-${m.month}`}
                  className={getPillClass(m)}
                  onClick={() => handleClick(m)}
                  onMouseEnter={() => setHoveredMonth(m)}
                  onMouseLeave={() => setHoveredMonth(null)}
                  title={hasBike(m) ? `${m.label} (Citibike data available)` : m.label}
                >
                  {m.label.split(' ')[0]}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="time-picker-legend">
        <span className="time-picker-legend-item">
          <span className="legend-dot legend-dot--bike" /> Citibike data
        </span>
      </div>
    </div>
  );
}
