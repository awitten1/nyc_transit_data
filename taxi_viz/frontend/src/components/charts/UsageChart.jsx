import React, { useRef, useEffect } from 'react';
import * as d3 from 'd3';

const TAXI_COLOR = 'rgba(91,106,240,0.82)';
const BIKE_COLOR = 'rgba(33,113,181,0.82)';

const MARGIN = { top: 12, right: 12, bottom: 32, left: 44 };

// keys: numeric array matching server `x` field (e.g. [0..23] for hours)
// labels: display strings, same length as keys
export default function UsageChart({ taxiData, bikeData, keys, labels, title }) {
  const svgRef = useRef(null);
  const containerRef = useRef(null);

  useEffect(() => {
    if (!taxiData?.length && !bikeData?.length) return;
    if (!keys?.length || !labels?.length) return;

    const container = containerRef.current;
    const totalWidth = container.clientWidth || 300;
    const totalHeight = 160;
    const width = totalWidth - MARGIN.left - MARGIN.right;
    const height = totalHeight - MARGIN.top - MARGIN.bottom;

    const hasBike = bikeData && bikeData.length > 0;
    const groups = hasBike ? ['taxi', 'bike'] : ['taxi'];

    // Build numeric-key → count lookup
    const taxiMap = Object.fromEntries((taxiData || []).map(d => [d.x, d.n]));
    const bikeMap = Object.fromEntries((bikeData || []).map(d => [d.x, d.n]));

    // Merged dataset: one entry per label, keyed by numeric key
    const data = keys.map((k, i) => ({
      label: labels[i],
      taxiN: taxiMap[k] ?? 0,
      bikeN: bikeMap[k] ?? 0,
    }));

    const maxVal = d3.max(data, d => Math.max(d.taxiN, hasBike ? d.bikeN : 0)) || 1;

    // Scales — domain uses display labels
    const x0 = d3.scaleBand().domain(labels).range([0, width]).paddingInner(0.2).paddingOuter(0.1);
    const x1 = d3.scaleBand().domain(groups).range([0, x0.bandwidth()]).padding(0.08);
    const y  = d3.scaleLinear().domain([0, maxVal * 1.1]).nice().range([height, 0]);

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();
    svg.attr('width', totalWidth).attr('height', totalHeight);

    const g = svg.append('g').attr('transform', `translate(${MARGIN.left},${MARGIN.top})`);

    // Grid lines
    g.append('g')
      .call(d3.axisLeft(y).tickSize(-width).tickFormat('').ticks(4))
      .call(s => s.select('.domain').remove())
      .call(s => s.selectAll('.tick line')
        .attr('stroke', 'rgba(200,200,200,0.18)').attr('stroke-dasharray', '3,3'));

    // Bars
    const groupSel = g.selectAll('.bar-group')
      .data(data)
      .join('g')
      .attr('class', 'bar-group')
      .attr('transform', d => `translate(${x0(d.label)},0)`);

    groupSel.selectAll('rect')
      .data(d => groups.map(grp => ({ grp, value: grp === 'taxi' ? d.taxiN : d.bikeN })))
      .join('rect')
      .attr('x', d => x1(d.grp))
      .attr('y', d => y(d.value))
      .attr('width', x1.bandwidth())
      .attr('height', d => Math.max(0, height - y(d.value)))
      .attr('fill', d => d.grp === 'taxi' ? TAXI_COLOR : BIKE_COLOR)
      .attr('rx', 2);

    // X axis
    g.append('g')
      .attr('transform', `translate(0,${height})`)
      .call(d3.axisBottom(x0).tickSize(0))
      .call(s => s.select('.domain').attr('stroke', 'rgba(200,200,200,0.25)'))
      .call(s => s.selectAll('.tick text').attr('fill', '#aaa').attr('font-size', '9px').attr('dy', '1.2em'));

    // Y axis
    g.append('g')
      .call(d3.axisLeft(y).ticks(4).tickFormat(d3.format('~s')))
      .call(s => s.select('.domain').remove())
      .call(s => s.selectAll('.tick text').attr('fill', '#aaa').attr('font-size', '9px'))
      .call(s => s.selectAll('.tick line').remove());

  }, [taxiData, bikeData, keys, labels]);

  const hasBike = bikeData && bikeData.length > 0;

  return (
    <div className="chart-block" ref={containerRef}>
      <div className="chart-header">
        <span className="chart-title">{title}</span>
        {hasBike && (
          <span className="chart-legend-inline">
            <span className="chart-legend-dot" style={{ background: TAXI_COLOR }} />
            Taxi
            <span className="chart-legend-dot" style={{ background: BIKE_COLOR, marginLeft: 8 }} />
            Citibike
          </span>
        )}
      </div>
      <svg ref={svgRef} style={{ display: 'block', width: '100%' }} />
    </div>
  );
}
