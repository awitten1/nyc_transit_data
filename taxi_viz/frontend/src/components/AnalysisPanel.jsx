import React, { useEffect, useRef } from 'react';
import { OD_PALETTE } from '../utils/colors.js';
import UsageChart from './charts/UsageChart.jsx';
import FareChart from './charts/FareChart.jsx';

// Numeric keys match the `x` field returned by /api/temporal
const HOUR_KEYS  = Array.from({ length: 24 }, (_, i) => i);       // 0–23
const DOW_KEYS   = [0, 1, 2, 3, 4, 5, 6];                         // Sun–Sat
const MONTH_KEYS = Array.from({ length: 12 }, (_, i) => i + 1);   // 1–12

const HOUR_LABELS = Array.from({ length: 24 }, (_, i) => {
  const h = i % 12 || 12;
  return `${h}${i < 12 ? 'a' : 'p'}`;
});
const DOW_LABELS   = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function formatPeriod(selRange, months) {
  if (!selRange?.start || !selRange?.end || !months?.length) return '';
  const find = ({ year, month }) => months.find(m => m.year === year && m.month === month);
  const s = find(selRange.start);
  const e = find(selRange.end);
  if (!s || !e) return '';
  if (s.year === s.year && s.month === e.month) return s.label;
  return `${s.label} – ${e.label}`;
}

function resolveZoneName(id, geo) {
  if (!geo?.features) return String(id);
  const f = geo.features.find(f => f.properties.LocationID === id);
  return f?.properties?.zone ?? String(id);
}

export default function AnalysisPanel({
  selectedZone,
  temporalData,
  selRange,
  months,
  geo,
  onClose,
  onInvalidateSize,
}) {
  const open = !!selectedZone;
  const prevOpen = useRef(false);

  // Call map.invalidateSize after panel animation completes
  useEffect(() => {
    if (open && !prevOpen.current) {
      const timer = setTimeout(() => onInvalidateSize?.(), 260);
      prevOpen.current = true;
      return () => clearTimeout(timer);
    }
    if (!open) {
      prevOpen.current = false;
    }
  }, [open, onInvalidateSize]);

  const periodLabel = formatPeriod(selRange, months);

  const hasBike = temporalData?.bike_by_hour?.length > 0;
  const odTop = temporalData?.od_top ?? [];


  // Resolve destination names
  const destNames = odTop.map(id => resolveZoneName(id, geo));

  return (
    <div className={`analysis-panel ${open ? 'analysis-panel--open' : ''}`}>
      {open && (
        <>
          <div className="analysis-panel-header">
            <div className="analysis-panel-title-block">
              <div className="analysis-panel-zone">{selectedZone?.name}</div>
              {periodLabel && (
                <div className="analysis-panel-period">{periodLabel}</div>
              )}
            </div>
            <button
              className="analysis-panel-close"
              onClick={onClose}
              aria-label="Close analysis panel"
            >
              ✕
            </button>
          </div>

          {!temporalData ? (
            <div className="analysis-panel-loading">
              <span className="spinner spinner--dark" />
              <span>Loading data…</span>
            </div>
          ) : temporalData._error ? (
            <div className="analysis-panel-empty">
              <span>Failed to load data.</span>
            </div>
          ) : (
            <div className="analysis-panel-body">
              {/* Usage Section */}
              <div className="analysis-section">
                <div className="analysis-section-label">Usage</div>
                {hasBike && (
                  <div className="analysis-legend">
                    <span className="analysis-legend-item">
                      <span className="analysis-legend-dot" style={{ background: 'rgba(91,106,240,0.82)' }} />
                      Taxi
                    </span>
                    <span className="analysis-legend-item">
                      <span className="analysis-legend-dot" style={{ background: 'rgba(33,113,181,0.82)' }} />
                      Citibike
                    </span>
                  </div>
                )}

                {!temporalData.taxi_by_hour?.length && !temporalData.bike_by_hour?.length ? (
                  <div className="analysis-panel-empty">No trip data for this zone in the selected period.</div>
                ) : (
                  <>
                    <UsageChart
                      title="By Hour of Day"
                      taxiData={temporalData.taxi_by_hour}
                      bikeData={hasBike ? temporalData.bike_by_hour : null}
                      keys={HOUR_KEYS} labels={HOUR_LABELS}
                    />
                    <UsageChart
                      title="By Day of Week"
                      taxiData={temporalData.taxi_by_dow}
                      bikeData={hasBike ? temporalData.bike_by_dow : null}
                      keys={DOW_KEYS} labels={DOW_LABELS}
                    />
                    <UsageChart
                      title="By Month"
                      taxiData={temporalData.taxi_by_month}
                      bikeData={hasBike ? temporalData.bike_by_month : null}
                      keys={MONTH_KEYS} labels={MONTH_LABELS}
                    />
                  </>
                )}
              </div>

              {/* Fare Section */}
              {odTop.length > 0 && (
                <div className="analysis-section">
                  <div className="analysis-section-label">Taxi Fare — Top 5 Destinations</div>

                  {/* Destination legend */}
                  <div className="analysis-dest-legend">
                    {odTop.map((id, i) => (
                      <div key={id} className="analysis-dest-item">
                        <span
                          className="analysis-dest-dot"
                          style={{ background: OD_PALETTE[i % OD_PALETTE.length] }}
                        />
                        <span className="analysis-dest-name">{destNames[i]}</span>
                      </div>
                    ))}
                  </div>

                  <FareChart
                    title="By Hour of Day"
                    fareByKey={temporalData.od_fare_by_hour}
                    keys={HOUR_KEYS} labels={HOUR_LABELS}
                    destIds={odTop}
                  />
                  <FareChart
                    title="By Day of Week"
                    fareByKey={temporalData.od_fare_by_dow}
                    keys={DOW_KEYS} labels={DOW_LABELS}
                    destIds={odTop}
                  />
                  <FareChart
                    title="By Month"
                    fareByKey={temporalData.od_fare_by_month}
                    keys={MONTH_KEYS} labels={MONTH_LABELS}
                    destIds={odTop}
                  />
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
