import React, { useRef, useEffect } from 'react';
import * as d3 from 'd3';
import { OD_PALETTE } from '../../utils/colors.js';

const MARGIN = { top: 12, right: 16, bottom: 32, left: 48 };

export default function FareChart({ fareByKey, keys, labels, destIds, title }) {
  // fareByKey: { "132": [{x, avg_fare}, ...], ... }
  // keys:    numeric array matching server `x` field (e.g. [0..23] for hours)
  // labels:  display strings, same length as keys
  // destIds: [132, 161, ...] — ordered list of dest IDs

  const svgRef = useRef(null);
  const containerRef = useRef(null);

  useEffect(() => {
    if (!fareByKey || !destIds?.length || !labels?.length) return;

    const container = containerRef.current;
    const totalWidth = container.clientWidth || 300;
    const totalHeight = 170;
    const width = totalWidth - MARGIN.left - MARGIN.right;
    const height = totalHeight - MARGIN.top - MARGIN.bottom;

    // Build one series per dest
    const series = destIds.slice(0, 5).map((id, i) => ({
      id: String(id),
      color: OD_PALETTE[i % OD_PALETTE.length],
      points: fareByKey[String(id)] ?? [],
    })).filter(s => s.points.length > 0);

    if (!series.length) return;

    // Map numeric key → display label for x positioning
    const keyToLabel = Object.fromEntries((keys || []).map((k, i) => [k, labels[i]]));

    // Scales — domain uses display labels
    const x = d3.scalePoint().domain(labels).range([0, width]).padding(0.3);

    const allFares = series.flatMap(s => s.points.map(p => p.avg_fare).filter(v => v != null));
    const yMin = d3.min(allFares) ?? 0;
    const yMax = d3.max(allFares) ?? 1;
    const yPad = (yMax - yMin) * 0.1 || 1;
    const y = d3.scaleLinear().domain([Math.max(0, yMin - yPad), yMax + yPad]).nice().range([height, 0]);

    // Line generator — use keyToLabel to map d.x (numeric) → label (string)
    const line = d3.line()
      .x(d => x(keyToLabel[d.x]) ?? 0)
      .y(d => y(d.avg_fare))
      .defined(d => d.avg_fare != null)
      .curve(d3.curveCatmullRom.alpha(0.5));

    // Clear and setup SVG
    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();
    svg.attr('width', totalWidth).attr('height', totalHeight);

    const g = svg.append('g').attr('transform', `translate(${MARGIN.left},${MARGIN.top})`);

    // Grid lines
    g.append('g')
      .call(
        d3.axisLeft(y)
          .tickSize(-width)
          .tickFormat('')
          .ticks(4)
      )
      .call(gSel => gSel.select('.domain').remove())
      .call(gSel => gSel.selectAll('.tick line')
        .attr('stroke', 'rgba(200,200,200,0.18)')
        .attr('stroke-dasharray', '3,3')
      );

    // Lines
    for (const s of series) {
      g.append('path')
        .datum(s.points)
        .attr('fill', 'none')
        .attr('stroke', s.color)
        .attr('stroke-width', 2)
        .attr('opacity', 0.85)
        .attr('d', line);

      // Dots
      g.selectAll(`.dot-${s.id}`)
        .data(s.points.filter(d => d.avg_fare != null))
        .join('circle')
        .attr('r', 3)
        .attr('cx', d => x(keyToLabel[d.x]) ?? 0)
        .attr('cy', d => y(d.avg_fare))
        .attr('fill', s.color)
        .attr('opacity', 0.9);
    }

    // X axis
    g.append('g')
      .attr('transform', `translate(0,${height})`)
      .call(d3.axisBottom(x).tickSize(0))
      .call(gSel => gSel.select('.domain').attr('stroke', 'rgba(200,200,200,0.25)'))
      .call(gSel => gSel.selectAll('.tick text')
        .attr('fill', '#aaa')
        .attr('font-size', '9px')
        .attr('dy', '1.2em')
      );

    // Y axis
    g.append('g')
      .call(
        d3.axisLeft(y)
          .ticks(4)
          .tickFormat(d => `$${d}`)
      )
      .call(gSel => gSel.select('.domain').remove())
      .call(gSel => gSel.selectAll('.tick text').attr('fill', '#aaa').attr('font-size', '9px'))
      .call(gSel => gSel.selectAll('.tick line').remove());

  }, [fareByKey, keys, labels, destIds]);

  return (
    <div className="chart-block" ref={containerRef}>
      <div className="chart-header">
        <span className="chart-title">{title}</span>
      </div>
      <svg ref={svgRef} style={{ display: 'block', width: '100%' }} />
    </div>
  );
}
