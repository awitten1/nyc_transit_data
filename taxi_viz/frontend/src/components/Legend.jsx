import React from 'react';
import { TAXI_RAMP, BIKE_RAMP, PRESSURE_RAMP } from '../utils/colors.js';

function GradientBar({ ramp, label, minLabel, maxLabel, midLabel }) {
  const gradient = `linear-gradient(to right, ${ramp.join(', ')})`;
  return (
    <div className="legend-gradient-block">
      <div className="legend-gradient-label">{label}</div>
      <div className="legend-gradient-bar" style={{ background: gradient }} />
      <div className="legend-gradient-ticks">
        <span>{minLabel}</span>
        {midLabel && <span>{midLabel}</span>}
        <span>{maxLabel}</span>
      </div>
    </div>
  );
}

export default function Legend({ od, citibike, citibikeHourly, hourlyPressure, hoveredInfo, taxiViz, showPressure, showTaxi, showBike, pressure }) {
  const hasTaxi    = od && Object.keys(od).length > 0;
  const hasBike    = citibike?.zone_flows && Object.keys(citibike.zone_flows).length > 0;
  const isPulse    = taxiViz === 'edge';
  const isPressure = showPressure;

  if (!isPulse && !isPressure && !hasTaxi && !hasBike) return null;

  // Global trip-count range for flow mode labels
  let taxiMin = Infinity, taxiMax = -Infinity;
  if (hasTaxi) {
    for (const dests of Object.values(od)) {
      for (const { n } of Object.values(dests)) {
        if (n < taxiMin) taxiMin = n;
        if (n > taxiMax) taxiMax = n;
      }
    }
  }
  if (taxiMin === Infinity) { taxiMin = 0; taxiMax = 100; }

  let bikeMin = Infinity, bikeMax = -Infinity;
  if (hasBike) {
    for (const arrows of Object.values(citibike.zone_flows)) {
      for (const { n } of arrows) {
        if (n < bikeMin) bikeMin = n;
        if (n > bikeMax) bikeMax = n;
      }
    }
  }
  if (bikeMin === Infinity) { bikeMin = 0; bikeMax = 1; }

  function formatN(n) {
    if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
    return String(Math.round(n));
  }

  function formatNet(n) {
    const v = Math.abs(Math.round(n));
    return (v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v));
  }

  return (
    <div className="legend-panel">
      {((isPulse && showTaxi) || showBike) && (
        <div className="legend-note" style={{ marginBottom: 4 }}>
          Click a zone to reveal connections<br />
          Line color = trip volume
        </div>
      )}
      {isPulse && showTaxi && (
        <GradientBar
          ramp={TAXI_RAMP}
          label="Taxi flow (selected hour)"
          minLabel="fewer trips"
          maxLabel="more trips"
        />
      )}
      {showBike && citibikeHourly && (
        <GradientBar
          ramp={BIKE_RAMP}
          label="Citi Bike flow (selected hour)"
          minLabel="fewer trips"
          maxLabel="more trips"
        />
      )}
      {isPressure && (showTaxi || showBike) && (
        <>
          <GradientBar
            ramp={PRESSURE_RAMP}
            label="Net flow (color) · Total traffic (size)"
            minLabel="net inflow"
            midLabel="balanced"
            maxLabel="net outflow"
          />
          <div className="legend-note" style={{ marginTop: 4 }}>
            {showTaxi && '◆ taxi zone'}{showTaxi && showBike && '\u00a0\u00a0'}{showBike && '● bike station'}
          </div>
        </>
      )}

      {hoveredInfo && (
        <div className="legend-hover-info">
          <div className="legend-hover-name">{hoveredInfo.name}</div>
          {hoveredInfo.taxiCount > 0 && (
            <div className="legend-hover-stat">
              <span className="legend-hover-icon taxi-icon">🚕</span>
              {formatN(hoveredInfo.taxiCount)} trips
              {hoveredInfo.minFare != null && (
                <span className="legend-hover-fare">
                  {' '}· ${hoveredInfo.minFare.toFixed(0)}–${hoveredInfo.maxFare.toFixed(0)} avg
                </span>
              )}
            </div>
          )}
          {hoveredInfo.bikeFlows > 0 && (
            <div className="legend-hover-stat">
              <span className="legend-hover-icon bike-icon">🚲</span>
              {formatN(hoveredInfo.bikeFlows)} bike trips
            </div>
          )}
          {isPressure && hoveredInfo.netFlow != null && (
            <div className="legend-hover-stat">
              <span className="legend-hover-icon">⚡</span>
              Net outflow: {hoveredInfo.netFlow > 0 ? '+' : ''}{formatN(hoveredInfo.netFlow)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
