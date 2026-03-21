import React, { useState, useEffect, useCallback, useRef } from 'react';
import Toolbar from './components/Toolbar.jsx';
import MapView from './components/MapView.jsx';
import Legend from './components/Legend.jsx';
import AnalysisPanel from './components/AnalysisPanel.jsx';

async function apiFetch(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json();
}

export default function App() {
  // ── Static data (loaded once) ─────────────────────────────────────────────
  const [geo, setGeo] = useState(null);
  const [months, setMonths] = useState([]);
  const [cbMonths, setCbMonths] = useState([]);

  // ── Dynamic data (reloaded on range change) ────────────────────────────────
  const [od, setOd] = useState(null);
  const [citibike, setCitibike] = useState(null);

  // ── UI state ───────────────────────────────────────────────────────────────
  const [selRange, setSelRange] = useState(null); // {start: {year,month}, end: {year,month}}
  const [hoveredInfo, setHoveredInfo] = useState(null);
  const [selectedZone, setSelectedZone] = useState(null); // {id, name}
  const [temporalData, setTemporalData] = useState(null);
  const [loading, setLoading] = useState(false);

  // Ref for map.invalidateSize callback
  const invalidateSizeRef = useRef(null);

  // ── Init: load static data ────────────────────────────────────────────────
  useEffect(() => {
    Promise.all([
      apiFetch('/api/zones'),
      apiFetch('/api/available_months'),
      apiFetch('/api/citibike_months').catch(() => ({ months: [] })),
    ]).then(([geoData, monthsData, cbData]) => {
      setGeo(geoData);
      const mList = monthsData.months ?? [];
      setMonths(mList);
      setCbMonths(cbData.months ?? []);

      // Auto-select last available month as default range
      if (mList.length > 0) {
        const last = mList[mList.length - 1];
        setSelRange({ start: last, end: last });
      }
    }).catch(err => {
      console.error('Failed to load initial data:', err);
    });
  }, []);

  // ── Load OD + citibike data when range changes ─────────────────────────────
  const loadRange = useCallback(async (range) => {
    if (!range?.start || !range?.end) return;
    const { start, end } = range;
    const params = new URLSearchParams({
      start_year:  start.year,
      start_month: start.month,
      end_year:    end.year,
      end_month:   end.month,
    });

    setLoading(true);
    try {
      const [odData, cbData] = await Promise.allSettled([
        apiFetch(`/api/od?${params}`),
        apiFetch(`/api/citibike?${params}`),
      ]);

      if (odData.status === 'fulfilled') {
        setOd(odData.value.od ?? null);
      } else {
        console.warn('OD data unavailable:', odData.reason);
        setOd(null);
      }

      if (cbData.status === 'fulfilled') {
        setCitibike(cbData.value);
      } else {
        setCitibike(null);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selRange) loadRange(selRange);
  }, [selRange, loadRange]);

  // ── Load temporal data on zone click ──────────────────────────────────────
  useEffect(() => {
    if (!selectedZone || !selRange) {
      setTemporalData(null);
      return;
    }

    const { start, end } = selRange;
    const params = new URLSearchParams({
      zone_id:     selectedZone.id,
      start_year:  start.year,
      start_month: start.month,
      end_year:    end.year,
      end_month:   end.month,
    });

    setTemporalData(null);
    apiFetch(`/api/temporal?${params}`)
      .then(data => setTemporalData(data))
      .catch(err => {
        console.error('Failed to load temporal data:', err);
        setTemporalData({ _error: true, message: err.message });
      });
  }, [selectedZone, selRange]);

  // ── Handlers ──────────────────────────────────────────────────────────────
  function handleRangeChange(start, end) {
    setSelRange({ start, end });
    setSelectedZone(null);
    setTemporalData(null);
  }

  function handleZoneClick(zone) {
    setSelectedZone(zone);
  }

  function handlePanelClose() {
    setSelectedZone(null);
    setTemporalData(null);
    // Invalidate map size after panel closes
    setTimeout(() => invalidateSizeRef.current?.(), 260);
  }

  return (
    <div className="app">
      <Toolbar
        selRange={selRange}
        months={months}
        cbMonths={cbMonths}
        loading={loading}
        onRangeChange={handleRangeChange}
      />

      <div className="main-area">
        <div className="map-wrapper">
          <MapView
            geo={geo}
            od={od}
            citibike={citibike}
            selectedZoneId={selectedZone?.id ?? null}
            onHoverChange={setHoveredInfo}
            onZoneClick={handleZoneClick}
            invalidateSizeRef={invalidateSizeRef}
          />

          <Legend
            od={od}
            citibike={citibike}
            hoveredInfo={hoveredInfo}
          />

          {!selRange && (
            <div className="info-overlay">
              <div className="info-overlay-content">
                <div className="info-overlay-title">NYC Transit Explorer</div>
                <div className="info-overlay-body">
                  Loading data…
                </div>
              </div>
            </div>
          )}

          {selRange && !od && !loading && (
            <div className="info-overlay info-overlay--hint">
              <div className="info-overlay-content">
                No taxi data available for this period.
              </div>
            </div>
          )}
        </div>

        <AnalysisPanel
          selectedZone={selectedZone}
          temporalData={temporalData}
          selRange={selRange}
          months={months}
          geo={geo}
          onClose={handlePanelClose}
          onInvalidateSize={() => invalidateSizeRef.current?.()}
        />
      </div>
    </div>
  );
}
