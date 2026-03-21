import React from 'react';
import { TAXI_RAMP, BIKE_RAMP } from '../utils/colors.js';

function GradientBar({ ramp, label, minLabel, maxLabel }) {
  const gradient = `linear-gradient(to right, ${ramp.join(', ')})`;
  return (
    <div className="legend-gradient-block">
      <div className="legend-gradient-label">{label}</div>
      <div className="legend-gradient-bar" style={{ background: gradient }} />
      <div className="legend-gradient-ticks">
        <span>{minLabel}</span>
        <span>{maxLabel}</span>
      </div>
    </div>
  );
}

export default function Legend({ od, citibike, hoveredInfo }) {
  const hasTaxi = od && Object.keys(od).length > 0;
  const hasBike = citibike?.zone_flows && Object.keys(citibike.zone_flows).length > 0;

  if (!hasTaxi && !hasBike) return null;

  // Compute global trip-count range for taxi legend labels
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

  return (
    <div className="legend-panel">
      {hasTaxi && (
        <GradientBar
          ramp={TAXI_RAMP}
          label="Taxi trips to destination"
          minLabel={formatN(taxiMin)}
          maxLabel={formatN(taxiMax)}
        />
      )}
      {hasBike && (
        <GradientBar
          ramp={BIKE_RAMP}
          label="Citibike trips"
          minLabel={formatN(bikeMin)}
          maxLabel={formatN(bikeMax)}
        />
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
        </div>
      )}
    </div>
  );
}
