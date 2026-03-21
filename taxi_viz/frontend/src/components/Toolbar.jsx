import React from 'react';
import TimePicker from './TimePicker.jsx';

function formatPeriod(start, end, months) {
  if (!start || !end || !months || months.length === 0) return 'Select period';
  const find = ({ year, month }) =>
    months.find(m => m.year === year && m.month === month);
  const s = find(start);
  const e = find(end);
  if (!s || !e) return 'Select period';
  if (s.year === e.year && s.month === e.month) return s.label;
  return `${s.label} – ${e.label}`;
}

export default function Toolbar({
  selRange,
  months,
  cbMonths,
  loading,
  onRangeChange,
}) {
  const [open, setOpen] = React.useState(false);
  const dropdownRef = React.useRef(null);

  // Close on outside click
  React.useEffect(() => {
    if (!open) return;
    function handle(e) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [open]);

  const periodLabel = formatPeriod(selRange?.start, selRange?.end, months);

  function handleRangeChange(start, end) {
    onRangeChange(start, end);
    setOpen(false);
  }

  return (
    <header className="toolbar">
      <div className="toolbar-left">
        <span className="toolbar-title">NYC Transit Explorer</span>
      </div>

      <div className="toolbar-center" ref={dropdownRef}>
        <button
          className={`period-badge ${open ? 'period-badge--open' : ''}`}
          onClick={() => setOpen(v => !v)}
          aria-expanded={open}
          aria-label="Select time period"
        >
          <span className="period-badge-icon">📅</span>
          <span className="period-badge-label">{periodLabel}</span>
          <span className="period-badge-caret">{open ? '▲' : '▼'}</span>
        </button>

        {open && (
          <div className="time-picker-dropdown">
            <TimePicker
              months={months}
              cbMonths={cbMonths}
              selRange={selRange}
              onRangeChange={handleRangeChange}
            />
          </div>
        )}
      </div>

      <div className="toolbar-right">
        {loading && (
          <div className="loading-indicator" aria-label="Loading">
            <span className="spinner" />
            <span className="loading-text">Loading…</span>
          </div>
        )}
      </div>
    </header>
  );
}
