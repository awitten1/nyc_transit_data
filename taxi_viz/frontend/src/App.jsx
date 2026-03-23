import { useState, useEffect, useCallback, useRef } from 'react';
import Toolbar from './components/Toolbar.jsx';
import MapView from './components/MapView.jsx';
import Legend from './components/Legend.jsx';
import MapModeBar from './components/MapModeBar.jsx';
import PulseSlider from './components/PulseSlider.jsx';
import EdgeRangeSlider from './components/EdgeRangeSlider.jsx';
import ModeShiftChart from './components/ModeShiftChart.jsx';

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
  const [hourlyOd, setHourlyOd] = useState(null);
  const [citibikeHourly, setCitibikeHourly] = useState(null);
  const [pressure, setPressure] = useState(null);
  const [hourlyPressure, setHourlyPressure] = useState(null);

  // ── UI state ───────────────────────────────────────────────────────────────
  const [selRange, setSelRange] = useState(null);
  const [hoveredInfo, setHoveredInfo] = useState(null);
  const [selectedZone, setSelectedZone] = useState(null);
  const [loading, setLoading] = useState(false);
  const [taxiViz, setTaxiViz] = useState('edge'); // 'edge' | 'zone'
  const [edgeRange, setEdgeRange] = useState([0, 100]);
  const [showPressure, setShowPressure] = useState(true);
  const [showTaxi, setShowTaxi] = useState(true);
  const [showBike, setShowBike] = useState(true);
  const [pulseHour, setPulseHour] = useState(12);

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
      if (mList.length > 0) {
        const last = mList[mList.length - 1];
        setSelRange({ start: last, end: last });
      }
    }).catch(err => console.error('Failed to load initial data:', err));
  }, []);

  // ── Load data when range changes ───────────────────────────────────────────
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
      const [odData, cbData, hourlyOdData, pressureData, cbHourlyData, hourlyPressureData] = await Promise.allSettled([
        apiFetch(`/api/od?${params}`),
        apiFetch(`/api/citibike?${params}`),
        apiFetch(`/api/hourly_od?${params}`),
        apiFetch(`/api/pressure?${params}`),
        apiFetch(`/api/citibike_hourly?${params}`),
        apiFetch(`/api/hourly_pressure?${params}`),
      ]);

      setOd(odData.status === 'fulfilled' ? (odData.value.od ?? null) : null);
      setCitibike(cbData.status === 'fulfilled' ? cbData.value : null);

      if (hourlyOdData.status === 'fulfilled') {
        const hod = hourlyOdData.value;
        setHourlyOd(Object.keys(hod.by_hour ?? {}).length > 0 ? hod : null);
      } else {
        setHourlyOd(null);
      }

      setPressure(pressureData.status === 'fulfilled' ? pressureData.value : null);

      if (cbHourlyData.status === 'fulfilled') {
        const cbh = cbHourlyData.value;
        setCitibikeHourly(Object.keys(cbh.by_hour ?? {}).length > 0 ? cbh : null);
      } else {
        setCitibikeHourly(null);
      }

      if (hourlyPressureData.status === 'fulfilled') {
        const hp = hourlyPressureData.value;
        setHourlyPressure(Object.keys(hp.by_hour ?? {}).length > 0 ? hp : null);
      } else {
        setHourlyPressure(null);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selRange) loadRange(selRange);
  }, [selRange, loadRange]);

  // ── Handlers ──────────────────────────────────────────────────────────────
  function handleRangeChange(start, end) {
    setSelRange({ start, end });
    setSelectedZone(null);
  }

  function handleZoneClick(zone) {
    setSelectedZone(prev => prev?.id === zone.id ? null : zone);
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
        <div className="map-col">
          <div className="map-wrapper">
            <MapModeBar
              showPressure={showPressure}
              onTogglePressure={() => setShowPressure(p => !p)}
              showTaxi={showTaxi}
              onToggleTaxi={() => setShowTaxi(p => !p)}
              taxiViz={taxiViz}
              onSetTaxiViz={setTaxiViz}
              showBike={showBike}
              onToggleBike={() => setShowBike(p => !p)}
            />
            <PulseSlider hour={pulseHour} onHourChange={setPulseHour} />

            {(showTaxi || showBike) && (
              <EdgeRangeSlider value={edgeRange} onChange={setEdgeRange} />
            )}

            <MapView
              geo={geo}
              od={od}
              citibike={citibike}
              hourlyOd={hourlyOd}
              citibikeHourly={citibikeHourly}
              pressure={pressure}
              hourlyPressure={hourlyPressure}
              taxiViz={taxiViz}
              showPressure={showPressure}
              showTaxi={showTaxi}
              showBike={showBike}
              pulseHour={pulseHour}
              edgeRange={edgeRange}
              selectedZoneId={selectedZone?.id ?? null}
              onHoverChange={setHoveredInfo}
              onZoneClick={handleZoneClick}
              invalidateSizeRef={invalidateSizeRef}
            />

            <Legend
              od={od}
              citibike={citibike}
              hoveredInfo={hoveredInfo}
              taxiViz={taxiViz}
              showPressure={showPressure}
              showTaxi={showTaxi}
              showBike={showBike}
              pressure={pressure}
              hourlyPressure={hourlyPressure}
              citibikeHourly={citibikeHourly}
            />

            {!selRange && (
              <div className="info-overlay">
                <div className="info-overlay-content">
                  <div className="info-overlay-title">NYC Transit Explorer</div>
                  <div className="info-overlay-body">Loading data…</div>
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

          <ModeShiftChart
            hourlyOd={hourlyOd}
            citibikeHourly={citibikeHourly}
            pulseHour={pulseHour}
            showTaxi={showTaxi}
            showBike={showBike}
            loading={loading}
          />
        </div>
      </div>
    </div>
  );
}
