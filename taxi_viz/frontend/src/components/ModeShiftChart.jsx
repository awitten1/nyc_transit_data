import { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import * as d3 from 'd3';

const TAXI_COLOR = '#f97316';
const BIKE_COLOR = '#06b6d4';
const H = 90;
const MARGIN = { top: 14, right: 16, bottom: 24, left: 36 };

function hourLabel(h) {
  if (h === 0)  return '12a';
  if (h === 12) return '12p';
  return h < 12 ? `${h}a` : `${h - 12}p`;
}

export default function ModeShiftChart({ hourlyOd, citibikeHourly, pulseHour, showTaxi, showBike, loading }) {
  const svgRef  = useRef(null);
  const [width, setWidth] = useState(0);

  // Callback ref: fires whenever the wrap div mounts/unmounts.
  // Avoids the bug where useLayoutEffect([]) ran while component returned null
  // (wrapRef never attached), so ResizeObserver was never set up.
  const roRef = useRef(null);
  const wrapRef = useCallback(node => {
    if (roRef.current) { roRef.current.disconnect(); roRef.current = null; }
    if (!node) { setWidth(0); return; }
    console.log('[ModeShiftChart] wrap mounted, clientWidth=', node.getBoundingClientRect().width);
    const ro = new ResizeObserver(entries => {
      const w = entries[0].contentRect.width;
      console.log('[ModeShiftChart] ResizeObserver width=', w);
      setWidth(w);
    });
    ro.observe(node);
    roRef.current = ro;
    setWidth(node.getBoundingClientRect().width);
  }, []);

  const data = useMemo(() => {
    return Array.from({ length: 24 }, (_, h) => {
      const hStr = String(h);
      const taxi = showTaxi && hourlyOd
        ? (hourlyOd.by_hour?.[hStr] ?? []).reduce((s, f) => s + f.n, 0) : 0;
      const bike = showBike && citibikeHourly
        ? (citibikeHourly.by_hour?.[hStr] ?? []).reduce((s, f) => s + f.n, 0) : 0;
      const total = taxi + bike;
      return { h, taxiPct: total > 0 ? taxi / total : 0 };
    });
  }, [hourlyOd, citibikeHourly, showTaxi, showBike]);

  useEffect(() => {
    console.log('[ModeShiftChart] D3 effect — svgRef=', !!svgRef.current, 'width=', width, 'hourlyOd=', !!hourlyOd, 'citibikeHourly=', !!citibikeHourly);
    if (!svgRef.current || width === 0) return;

    const iW = width  - MARGIN.left - MARGIN.right;
    const iH = H - MARGIN.top - MARGIN.bottom;

    const svg = d3.select(svgRef.current)
      .attr('width', width)
      .attr('height', H);

    svg.selectAll('*').remove();

    const g = svg.append('g').attr('transform', `translate(${MARGIN.left},${MARGIN.top})`);

    const x = d3.scaleLinear().domain([0, 23]).range([0, iW]);
    const y = d3.scaleLinear().domain([0, 1]).range([iH, 0]);

    // Background
    g.append('rect').attr('width', iW).attr('height', iH).attr('fill', '#0f172a').attr('rx', 2);

    // Horizontal grid lines
    [0, 0.5, 1].forEach(t => {
      g.append('line')
        .attr('x1', 0).attr('x2', iW)
        .attr('y1', y(t)).attr('y2', y(t))
        .attr('stroke', '#1e293b').attr('stroke-width', 1);
      g.append('text')
        .attr('x', -4).attr('y', y(t) + 4)
        .attr('text-anchor', 'end')
        .attr('fill', '#64748b')
        .style('font-size', '10px')
        .text(`${Math.round(t * 100)}%`);
    });

    // Taxi area (bottom stack)
    if (showTaxi && hourlyOd) {
      g.append('path')
        .datum(data)
        .attr('fill', TAXI_COLOR)
        .attr('opacity', 0.8)
        .attr('d', d3.area()
          .x(d => x(d.h))
          .y0(iH)
          .y1(d => y(d.taxiPct))
          .curve(d3.curveCatmullRom));
    }

    // Bike area (top stack, from taxiPct to 100%)
    if (showBike && citibikeHourly) {
      g.append('path')
        .datum(data)
        .attr('fill', BIKE_COLOR)
        .attr('opacity', 0.8)
        .attr('d', d3.area()
          .x(d => x(d.h))
          .y0(d => y(d.taxiPct))
          .y1(0)
          .curve(d3.curveCatmullRom));
    }

    // X-axis tick marks + labels
    [0, 6, 12, 18, 23].forEach(h => {
      const cx = x(h);
      g.append('line')
        .attr('x1', cx).attr('x2', cx)
        .attr('y1', iH).attr('y2', iH + 4)
        .attr('stroke', '#475569').attr('stroke-width', 1);
      g.append('text')
        .attr('x', cx).attr('y', iH + 15)
        .attr('text-anchor', 'middle')
        .attr('fill', '#94a3b8')
        .style('font-size', '10px')
        .text(hourLabel(h));
    });

    // Border
    g.append('rect')
      .attr('width', iW).attr('height', iH)
      .attr('fill', 'none').attr('stroke', '#334155')
      .attr('stroke-width', 1).attr('rx', 2);

    // Current hour marker (on top)
    const mx = x(pulseHour);
    g.append('line')
      .attr('x1', mx).attr('x2', mx)
      .attr('y1', 0).attr('y2', iH)
      .attr('stroke', '#facc15').attr('stroke-width', 1.5)
      .attr('stroke-dasharray', '3 3').attr('opacity', 0.9);
    g.append('text')
      .attr('x', mx).attr('y', -3)
      .attr('text-anchor', 'middle')
      .attr('fill', '#facc15')
      .style('font-size', '10px')
      .text(hourLabel(pulseHour));

  }, [data, pulseHour, showTaxi, showBike, hourlyOd, citibikeHourly, width]);

  if (!hourlyOd && !citibikeHourly) {
    if (!loading) return null;
    return (
      <div className="mode-shift-panel mode-shift-loading">
        <div className="mode-shift-spinner" />
        <span className="mode-shift-loading-text">Loading chart…</span>
      </div>
    );
  }

  return (
    <div className="mode-shift-panel">
      <div className="mode-shift-title">Mode Shift by Hour</div>
      <div ref={wrapRef} className="mode-shift-wrap">
        <svg ref={svgRef} />
      </div>
      <div className="mode-shift-legend">
        {showTaxi && hourlyOd && (
          <span className="mode-shift-key">
            <span style={{ background: TAXI_COLOR }} className="mode-shift-swatch" /> Taxi
          </span>
        )}
        {showBike && citibikeHourly && (
          <span className="mode-shift-key">
            <span style={{ background: BIKE_COLOR }} className="mode-shift-swatch" /> Citi Bike
          </span>
        )}
      </div>
    </div>
  );
}
