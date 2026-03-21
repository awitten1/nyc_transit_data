import React, { useEffect, useRef, memo } from 'react';
import L from 'leaflet';
import { scaleColor, TAXI_RAMP, BIKE_RAMP } from '../utils/colors.js';

const DEFAULT_STYLE = {
  color: '#555',
  weight: 0.8,
  fillColor: '#334155',
  fillOpacity: 0.55,
};

const HOVER_STYLE = {
  color: '#fff',
  weight: 2,
  fillOpacity: 0.85,
};

const CLICK_STYLE = {
  color: '#facc15',
  weight: 2.5,
  fillOpacity: 0.9,
};



const MapView = memo(function MapView({
  geo,
  od,
  citibike,
  selectedZoneId,
  onHoverChange,
  onZoneClick,
  invalidateSizeRef,
}) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const geoLayerRef = useRef(null);
  const layersMapRef = useRef({}); // zoneId -> L.Layer

  // Mutable refs — avoid stale closures in Leaflet event handlers
  const odRef = useRef(od);
  const bikeRef = useRef(citibike);
  const selectedZoneIdRef = useRef(selectedZoneId);
  const hoveredRef = useRef(null); // currently hovered zone id

  // Arrow / animation refs
  const arrowLayerRef = useRef(null);
  const stationLayerRef = useRef(null);
  const animFrameRef = useRef(null);
  const animMarkersRef = useRef([]); // {marker, path: [{lat,lng}], t, speed}

  // Keep refs in sync
  useEffect(() => { odRef.current = od; }, [od]);
  useEffect(() => { bikeRef.current = citibike; }, [citibike]);
  useEffect(() => { selectedZoneIdRef.current = selectedZoneId; }, [selectedZoneId]);

  // Expose invalidateSize for AnalysisPanel
  useEffect(() => {
    if (invalidateSizeRef) {
      invalidateSizeRef.current = () => mapRef.current?.invalidateSize();
    }
  }, [invalidateSizeRef]);

  // ── Initialize map once ───────────────────────────────────────────────────
  useEffect(() => {
    if (mapRef.current) return;

    const map = L.map(containerRef.current, {
      center: [40.73, -73.97],
      zoom: 11,
      zoomControl: true,
    });

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; <a href="https://carto.com/">CARTO</a>',
      subdomains: 'abcd',
      maxZoom: 19,
    }).addTo(map);

    // Custom pane with pointer-events:none so bike overlays never intercept clicks
    map.createPane('bikePane');
    map.getPane('bikePane').style.pointerEvents = 'none';
    map.getPane('bikePane').style.zIndex = 450;

    arrowLayerRef.current = L.layerGroup().addTo(map);
    stationLayerRef.current = L.layerGroup().addTo(map);

    mapRef.current = map;

    return () => {
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // ── Add GeoJSON once when geo arrives ────────────────────────────────────
  useEffect(() => {
    if (!geo || !mapRef.current) return;
    if (geoLayerRef.current) return; // already added

    const layersMap = layersMapRef.current;

    const geoLayer = L.geoJSON(geo, {
      style: () => ({ ...DEFAULT_STYLE }),
      onEachFeature(feature, layer) {
        const zoneId = feature.properties.LocationID;
        layersMap[zoneId] = layer;

        layer.on('mouseover', function (e) {
          handleHover(zoneId, layer, e);
        });
        layer.on('mouseout', function () {
          handleOut(zoneId, layer);
        });
        layer.on('click', function () {
          handleClick(zoneId, feature.properties);
        });
      },
    }).addTo(mapRef.current);

    geoLayerRef.current = geoLayer;
  }, [geo]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Highlight selected zone ──────────────────────────────────────────────
  useEffect(() => {
    const layers = layersMapRef.current;
    // Reset all to default
    for (const [id, layer] of Object.entries(layers)) {
      const zid = parseInt(id, 10);
      if (zid !== selectedZoneId && zid !== hoveredRef.current) {
        layer.setStyle({ ...DEFAULT_STYLE });
      }
    }
    // Apply selected style
    if (selectedZoneId && layers[selectedZoneId]) {
      layers[selectedZoneId].setStyle({ ...DEFAULT_STYLE, ...CLICK_STYLE });
    }
  }, [selectedZoneId]);

  // ── Leaflet event handlers (read from refs, not closures) ────────────────

  function handleHover(zoneId, layer, e) {
    hoveredRef.current = zoneId;
    const od = odRef.current;
    const bike = bikeRef.current;

    // Color destination zones by trip count FROM this zone
    const dests = od?.[zoneId] ?? {};
    const counts = Object.entries(dests).map(([, { n }]) => n);
    const countMin = counts.length ? Math.min(...counts) : 0;
    const countMax = counts.length ? Math.max(...counts) : 1;

    for (const [id, l] of Object.entries(layersMapRef.current)) {
      const zid = parseInt(id, 10);
      if (zid === selectedZoneIdRef.current || zid === zoneId) continue;
      const dest = dests[zid];
      if (dest) {
        const color = scaleColor(dest.n, countMin, countMax, TAXI_RAMP);
        l.setStyle({ ...DEFAULT_STYLE, fillColor: color, fillOpacity: 0.8 });
      } else {
        l.setStyle({ ...DEFAULT_STYLE, fillOpacity: 0.25 });
      }
    }

    layer.setStyle({ ...DEFAULT_STYLE, ...HOVER_STYLE });
    layer.bringToFront();

    // Bike arrows for this zone
    drawBikeArrows(zoneId, bike);

    // Build hover info
    let taxiCount = 0;
    let minFare = Infinity, maxFare = -Infinity;
    for (const { f, n } of Object.values(dests)) {
      taxiCount += n;
      if (f < minFare) minFare = f;
      if (f > maxFare) maxFare = f;
    }
    if (minFare === Infinity) minFare = null;
    if (maxFare === -Infinity) maxFare = null;

    const zoneFlows = bike?.zone_flows?.[String(zoneId)] ?? [];
    let bikeMinN = Infinity, bikeMaxN = -Infinity;
    let bikeFlows = 0;
    for (const a of zoneFlows) {
      bikeFlows += a.n;
      if (a.n < bikeMinN) bikeMinN = a.n;
      if (a.n > bikeMaxN) bikeMaxN = a.n;
    }
    if (bikeMinN === Infinity) bikeMinN = null;
    if (bikeMaxN === -Infinity) bikeMaxN = null;

    onHoverChange({
      id: zoneId,
      name: e.target.feature?.properties?.zone ?? String(zoneId),
      taxiCount,
      bikeFlows,
      minFare,
      maxFare,
      bikeMinN,
      bikeMaxN,
    });
  }

  function handleOut(zoneId, layer) {
    if (hoveredRef.current !== zoneId) return;
    hoveredRef.current = null;

    // Reset all zone colors
    for (const [id, l] of Object.entries(layersMapRef.current)) {
      const zid = parseInt(id, 10);
      if (zid === selectedZoneIdRef.current) {
        l.setStyle({ ...DEFAULT_STYLE, ...CLICK_STYLE });
      } else {
        l.setStyle({ ...DEFAULT_STYLE });
      }
    }

    clearBikeArrows();
    onHoverChange(null);
  }

  function handleClick(zoneId, properties) {
    onZoneClick({ id: zoneId, name: properties.zone ?? String(zoneId) });
  }

  // ── Bike arrow drawing ────────────────────────────────────────────────────

  function clearBikeArrows() {
    if (animFrameRef.current) {
      cancelAnimationFrame(animFrameRef.current);
      animFrameRef.current = null;
    }
    animMarkersRef.current = [];
    arrowLayerRef.current?.clearLayers();
    stationLayerRef.current?.clearLayers();
  }

  function drawBikeArrows(zoneId, bike) {
    clearBikeArrows();
    if (!bike?.zone_flows) return;

    const arrows = bike.zone_flows[String(zoneId)];
    if (!arrows || arrows.length === 0) return;

    // Use per-zone min/max so color variation is always visible
    const ns = arrows.map(a => a.n);
    const localMin = Math.min(...ns);
    const localMax = Math.max(...ns);
    const animMarkers = [];

    for (const arrow of arrows) {
      const { slat, slng, elat, elng, n } = arrow;
      if (slat == null || elat == null) continue;

      const color = scaleColor(n, localMin, localMax, BIKE_RAMP);
      const weight = 1 + Math.min(4, ((n - localMin) / Math.max(1, localMax - localMin)) * 4);

      // Draw polyline
      const line = L.polyline([[slat, slng], [elat, elng]], {
        color,
        weight,
        opacity: 0.7,
        interactive: false,
        pane: 'bikePane',
      });
      arrowLayerRef.current.addLayer(line);

      // Animated circle marker along the path
      const marker = L.circleMarker([slat, slng], {
        radius: 4,
        color,
        fillColor: color,
        fillOpacity: 0.9,
        weight: 1,
        interactive: false,
        pane: 'bikePane',
      });
      arrowLayerRef.current.addLayer(marker);

      // Build path points for animation
      const path = interpolatePath([slat, slng], [elat, elng], 20);
      animMarkers.push({ marker, path, t: Math.random(), speed: 0.003 + Math.random() * 0.003 });
    }

    animMarkersRef.current = animMarkers;

    // Station dots at start (origin) and end (destination) of arrows
    const seenDots = new Set();
    const addDot = (lat, lng, isOrigin) => {
      const key = `${lat},${lng}`;
      if (seenDots.has(key)) return;
      seenDots.add(key);
      stationLayerRef.current.addLayer(L.circleMarker([lat, lng], {
        radius: isOrigin ? 5 : 4,
        color: '#fff',
        fillColor: isOrigin ? '#43a2ca' : '#7bccc4',
        fillOpacity: 1,
        weight: 1.5,
        interactive: false,
        pane: 'bikePane',
      }));
    };
    for (const arrow of arrows) {
      addDot(arrow.slat, arrow.slng, true);
      addDot(arrow.elat, arrow.elng, false);
    }

    // Start animation loop
    function animate() {
      for (const item of animMarkersRef.current) {
        item.t = (item.t + item.speed) % 1;
        const idx = Math.floor(item.t * (item.path.length - 1));
        const pt = item.path[idx];
        if (pt) item.marker.setLatLng(pt);
      }
      animFrameRef.current = requestAnimationFrame(animate);
    }
    animFrameRef.current = requestAnimationFrame(animate);
  }

  return (
    <div
      ref={containerRef}
      className="map-container"
      style={{ width: '100%', height: '100%' }}
    />
  );
});

export default MapView;

// Interpolate N points between two lat/lng positions
function interpolatePath([lat1, lng1], [lat2, lng2], n) {
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    pts.push([lat1 + (lat2 - lat1) * t, lng1 + (lng2 - lng1) * t]);
  }
  return pts;
}
