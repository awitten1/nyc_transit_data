import { useEffect, useRef, memo } from 'react';
import L from 'leaflet';
import { scaleColor, PRESSURE_RAMP, TAXI_RAMP, BIKE_RAMP } from '../utils/colors.js';

const DEFAULT_STYLE = {
  color: '#555',
  weight: 0.8,
  fillColor: '#334155',
  fillOpacity: 0.55,
};

const PRESSURE_ZONE_STYLE = {
  fillOpacity: 0.25,
  color: '#6b7280',
  weight: 0.6,
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
  hourlyOd,
  citibikeHourly,
  pressure,
  hourlyPressure,
  taxiViz,          // 'edge' | 'zone'
  showPressure,
  showTaxi,
  showBike,
  pulseHour,
  edgeRange,
  selectedZoneId,
  onHoverChange,
  onZoneClick,
  invalidateSizeRef,
}) {
  const containerRef = useRef(null);
  const mapRef       = useRef(null);
  const geoLayerRef  = useRef(null);
  const layersMapRef = useRef({});
  const centroidsRef = useRef({});

  // Mutable refs for Leaflet event handlers
  const odRef             = useRef(od);
  const bikeRef           = useRef(citibike);
  const selectedZoneIdRef = useRef(selectedZoneId);
  const hoveredRef        = useRef(null);
  const taxiVizRef        = useRef(taxiViz);
  const showPressureRef   = useRef(showPressure);
  const showTaxiRef       = useRef(showTaxi);
  const showBikeRef       = useRef(showBike);
  const pulseHourRef      = useRef(pulseHour);
  const pressureRef       = useRef(pressure);
  const hourlyOdRef       = useRef(hourlyOd);
  const citibikeHourlyRef = useRef(citibikeHourly);
  const hourlyPressureRef = useRef(hourlyPressure);
  const edgeRangeRef      = useRef(edgeRange);

  // Overlay layer refs
  const pulseArrowLayerRef = useRef(null);
  const feederLayerRef     = useRef(null);

  const animFrameRef    = useRef(null);
  const arrowLayerRef   = useRef(null);
  const stationLayerRef = useRef(null);

  // Keep refs in sync
  useEffect(() => { odRef.current = od; }, [od]);
  useEffect(() => { bikeRef.current = citibike; }, [citibike]);
  useEffect(() => { selectedZoneIdRef.current = selectedZoneId; }, [selectedZoneId]);
  useEffect(() => { taxiVizRef.current = taxiViz; }, [taxiViz]);
  useEffect(() => { showPressureRef.current = showPressure; }, [showPressure]);
  useEffect(() => { showTaxiRef.current = showTaxi; }, [showTaxi]);
  useEffect(() => { showBikeRef.current = showBike; }, [showBike]);
  useEffect(() => { pulseHourRef.current = pulseHour; }, [pulseHour]);
  useEffect(() => { pressureRef.current = pressure; }, [pressure]);
  useEffect(() => { hourlyOdRef.current = hourlyOd; }, [hourlyOd]);
  useEffect(() => { citibikeHourlyRef.current = citibikeHourly; }, [citibikeHourly]);
  useEffect(() => { hourlyPressureRef.current = hourlyPressure; }, [hourlyPressure]);
  useEffect(() => { edgeRangeRef.current = edgeRange; }, [edgeRange]);

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

    map.createPane('bikePane');
    map.getPane('bikePane').style.pointerEvents = 'none';
    map.getPane('bikePane').style.zIndex = 455;

    map.createPane('pulsePane');
    map.getPane('pulsePane').style.pointerEvents = 'none';
    map.getPane('pulsePane').style.zIndex = 450;

    arrowLayerRef.current      = L.layerGroup().addTo(map);
    stationLayerRef.current    = L.layerGroup().addTo(map);
    pulseArrowLayerRef.current = L.layerGroup().addTo(map);
    feederLayerRef.current     = L.layerGroup().addTo(map);

    mapRef.current = map;

    return () => {
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // ── Add GeoJSON once ─────────────────────────────────────────────────────
  useEffect(() => {
    if (!geo || !mapRef.current || geoLayerRef.current) return;

    const layersMap = layersMapRef.current;
    const centroids = centroidsRef.current;

    const geoLayer = L.geoJSON(geo, {
      style: () => ({ ...DEFAULT_STYLE }),
      onEachFeature(feature, layer) {
        const zoneId = feature.properties.LocationID;
        layersMap[zoneId] = layer;
        const center = layer.getBounds().getCenter();
        centroids[String(zoneId)] = [center.lat, center.lng];
        layer.on('mouseover', function (e) { handleHover(zoneId, layer, e); });
        layer.on('mouseout',  function ()  { handleOut(zoneId); });
        layer.on('click',     function ()  { handleClick(zoneId, feature.properties); });
      },
    }).addTo(mapRef.current);

    geoLayerRef.current = geoLayer;
  }, [geo]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Zone polygon style ────────────────────────────────────────────────────
  useEffect(() => {
    const layers = layersMapRef.current;
    if (!Object.keys(layers).length) return;

    // Zone Color mode: color zones by OD volume
    let zoneColors = null;
    if (taxiViz === 'zone' && showTaxi && hourlyOd) {
      const flows = hourlyOd.by_hour?.[String(pulseHour)] ?? [];
      const vol = {};

      if (selectedZoneId != null) {
        // Color by trip volume to/from selected zone
        for (const { pu, do: doZ, n } of flows) {
          if (pu === selectedZoneId && doZ !== selectedZoneId) {
            vol[String(doZ)] = (vol[String(doZ)] ?? 0) + n;
          } else if (doZ === selectedZoneId && pu !== selectedZoneId) {
            vol[String(pu)] = (vol[String(pu)] ?? 0) + n;
          }
        }
      } else {
        // No selection: global departure count
        for (const { pu, n } of flows) vol[String(pu)] = (vol[String(pu)] ?? 0) + n;
      }

      const vals = Object.values(vol);
      zoneColors = { vol, min: Math.min(...vals, 0), max: Math.max(...vals, 1) };
    }

    const baseStyle = showPressure
      ? { ...DEFAULT_STYLE, ...PRESSURE_ZONE_STYLE }
      : { ...DEFAULT_STYLE };

    for (const [id, layer] of Object.entries(layers)) {
      const zid = parseInt(id, 10);
      if (zid === selectedZoneId) {
        layer.setStyle({ ...DEFAULT_STYLE, ...CLICK_STYLE });
      } else if (zoneColors) {
        const n = zoneColors.vol[String(zid)] ?? 0;
        layer.setStyle({
          ...baseStyle,
          fillColor: n > 0 ? scaleColor(n, zoneColors.min, zoneColors.max, TAXI_RAMP) : '#1e293b',
          fillOpacity: n > 0 ? 0.75 : 0.15,
        });
      } else {
        layer.setStyle(baseStyle);
      }
    }
  }, [showPressure, taxiViz, showTaxi, pulseHour, hourlyOd, selectedZoneId]);

  // ── Edge layer: taxi edges + bike edges for selected zone ─────────────────
  useEffect(() => {
    pulseArrowLayerRef.current?.clearLayers();
    if (!selectedZoneId) return;

    const hour      = pulseHour;
    const centroids = centroidsRef.current;
    const [lo, hi] = edgeRange;

    function filterRange(arr) {
      if (lo === 0 && hi === 100) return arr;
      const sorted = [...arr].sort((a, b) => b.n - a.n);
      const N      = sorted.length;
      const start  = Math.floor(N * (1 - hi / 100));
      const end    = Math.ceil(N * (1 - lo / 100));
      return sorted.slice(start, end);
    }

    // Taxi OD edges (only in 'edge' mode)
    if (taxiViz === 'edge' && showTaxi) {
      const flows = hourlyOd?.by_hour?.[String(hour)] ?? [];

      // All flows touching this zone, tagged by type
      const allZone = [
        ...flows.filter(f => f.pu === selectedZoneId && f.do !== selectedZoneId).map(f => ({ ...f, _t: 'out' })),
        ...flows.filter(f => f.do === selectedZoneId && f.pu !== selectedZoneId).map(f => ({ ...f, _t: 'in' })),
        ...flows.filter(f => f.pu === selectedZoneId && f.do === selectedZoneId).map(f => ({ ...f, _t: 'self' })),
      ];

      // Color scale from the full unfiltered pool so colors don't shift when sliding
      const allN = allZone.map(f => f.n);
      const minN = allN.length ? Math.min(...allN) : 0;
      const maxN = allN.length ? Math.max(...allN) : 1;

      // Filter the combined pool so every edge type competes on the same percentile scale
      const filtered        = filterRange(allZone);
      const outgoing        = filtered.filter(f => f._t === 'out');
      const incoming        = filtered.filter(f => f._t === 'in');
      const filteredSelfLoop = filtered.filter(f => f._t === 'self');

      const src = centroids[String(selectedZoneId)];

      const drawLine = (p1, p2, n) => {
        const color  = scaleColor(n, minN, maxN, TAXI_RAMP);
        const weight = 2;
        const dur    = 1.0;
        const line   = L.polyline([p1, p2], { color, weight, opacity: 0.85, interactive: false, pane: 'pulsePane' });
        pulseArrowLayerRef.current.addLayer(line);
        const el = line.getElement();
        if (el) { el.style.strokeDasharray = '12 16'; el.style.animation = `taxiFlow ${dur}s linear infinite`; }
      };

      for (const { do: doZone, n } of outgoing) {
        const dst = centroids[String(doZone)];
        if (src && dst) drawLine(src, dst, n);
      }
      for (const { pu, n } of incoming) {
        const puSrc = centroids[String(pu)];
        if (puSrc && src) drawLine(puSrc, src, n);
      }
      for (const { n } of filteredSelfLoop) {
        if (!src) continue;
        const color  = scaleColor(n, minN, maxN, TAXI_RAMP);
        const weight = 2;
        const dur    = 1.0;
        const arc    = selfLoopArc(src, 150, 48);
        const line   = L.polyline(arc, { color, weight, opacity: 0.85, interactive: false, pane: 'pulsePane' });
        pulseArrowLayerRef.current.addLayer(line);
        const el = line.getElement();
        if (el) { el.style.strokeDasharray = '12 16'; el.style.animation = `taxiFlow ${dur}s linear infinite`; }
      }
    }

    // Bike OD edges — independent of taxiViz mode
    if (showBike) {
      const flows = citibikeHourly?.by_hour?.[String(hour)] ?? [];
      const bike  = bikeRef.current;

      const stnZone = {};
      if (bike?.stations) {
        for (const stn of bike.stations) {
          if (stn.zone > 0) stnZone[stn.id] = stn.zone;
        }
      }

      const allMatched = flows.filter(f => {
        const depZ = stnZone[f.ssid];
        const arrZ = stnZone[f.esid];
        return depZ === selectedZoneId || arrZ === selectedZoneId;
      });

      // Color scale from full unfiltered set
      const bikeNs  = allMatched.map(f => f.n);
      const bikeMin = bikeNs.length ? Math.min(...bikeNs) : 0;
      const bikeMax = bikeNs.length ? Math.max(...bikeNs) : 1;

      const zoneFlows = filterRange(allMatched);

      if (zoneFlows.length > 0) {
        for (const { slat, slng, elat, elng, n } of zoneFlows) {
          const color  = scaleColor(n, bikeMin, bikeMax, BIKE_RAMP);
          const weight = 2;
          const dur    = 1.4;
          const line   = L.polyline([[slat, slng], [elat, elng]], { color, weight, opacity: 0.75, interactive: false, pane: 'bikePane' });
          pulseArrowLayerRef.current.addLayer(line);
          const el = line.getElement();
          if (el) { el.style.strokeDasharray = '6 10'; el.style.animation = `bikeFlow ${dur}s linear infinite`; }
        }
      }
    }
  }, [selectedZoneId, taxiViz, showTaxi, showBike, pulseHour, hourlyOd, citibikeHourly, edgeRange]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Pressure layer: taxi diamonds + bike circles ──────────────────────────
  useEffect(() => {
    feederLayerRef.current?.clearLayers();
    if (!showPressure) return;

    const hp = hourlyPressureRef.current;
    if (!hp) return;
    const zoneData = hp.by_hour?.[String(pulseHour)] ?? {};

    const totalVals    = Object.values(zoneData).map(z => z.total ?? 0);
    const netVals      = Object.values(zoneData).map(z => z.net   ?? 0);
    const taxiTotalMax = Math.max(...totalVals, 1);
    const taxiAbsMax   = Math.max(...netVals.map(Math.abs), 1);
    const centroids    = centroidsRef.current;

    if (showTaxi) {
      for (const [zoneStr, zd] of Object.entries(zoneData)) {
        const latLng = centroids[zoneStr];
        if (!latLng || (zd.total ?? 0) === 0) continue;
        const diameter = Math.round(8 + Math.sqrt((zd.total ?? 0) / taxiTotalMax) * 20);
        const side  = Math.round(diameter / Math.SQRT2);
        const color = scaleColor(zd.net ?? 0, -taxiAbsMax, taxiAbsMax, PRESSURE_RAMP);
        feederLayerRef.current.addLayer(L.marker(latLng, {
          icon: L.divIcon({
            className: '',
            html: `<div style="width:${side}px;height:${side}px;background:${color};border:1px solid #0f172a;opacity:0.88;transform:rotate(45deg);pointer-events:none;box-sizing:border-box;"></div>`,
            iconSize:   [diameter, diameter],
            iconAnchor: [diameter / 2, diameter / 2],
          }),
          interactive: false,
          pane: 'pulsePane',
        }));
      }
    }

    if (showBike) {
      const cbh = citibikeHourlyRef.current;
      const bikeFlows = cbh?.by_hour?.[String(pulseHour)] ?? [];
      const stationMap = {};
      for (const { slat, slng, elat, elng, n } of bikeFlows) {
        const dk = `${slat},${slng}`, ak = `${elat},${elng}`;
        if (!stationMap[dk]) stationMap[dk] = { lat: slat, lng: slng, dep: 0, arr: 0 };
        if (!stationMap[ak]) stationMap[ak] = { lat: elat, lng: elng, dep: 0, arr: 0 };
        stationMap[dk].dep += n;
        stationMap[ak].arr += n;
      }
      const bikeStations = Object.values(stationMap);
      const bikeTotalMax = Math.max(...bikeStations.map(s => s.dep + s.arr), 1);
      const bikeAbsMax   = Math.max(...bikeStations.map(s => Math.abs(s.dep - s.arr)), 1);
      for (const { lat, lng, dep, arr } of bikeStations) {
        const total = dep + arr;
        if (!total) continue;
        const radius = 2 + Math.sqrt(total / bikeTotalMax) * 7;
        const color  = scaleColor(dep - arr, -bikeAbsMax, bikeAbsMax, PRESSURE_RAMP);
        feederLayerRef.current.addLayer(L.circleMarker([lat, lng], {
          radius, color: '#0f172a', weight: 0.5,
          fillColor: color, fillOpacity: 0.8,
          interactive: false, pane: 'bikePane',
        }));
      }
    }
  }, [showPressure, showTaxi, showBike, pulseHour, hourlyPressure, citibikeHourly]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Leaflet event handlers ────────────────────────────────────────────────

  function handleHover(zoneId, _layer, e) {
    hoveredRef.current = zoneId;
    const od   = odRef.current;
    const bike = bikeRef.current;
    const dests = od?.[zoneId] ?? {};
    let taxiCount = 0, minFare = Infinity, maxFare = -Infinity;
    for (const { f, n } of Object.values(dests)) {
      taxiCount += n;
      if (f < minFare) minFare = f;
      if (f > maxFare) maxFare = f;
    }
    if (minFare === Infinity)  minFare = null;
    if (maxFare === -Infinity) maxFare = null;
    const bikeTotal = (bike?.zone_flows?.[String(zoneId)] ?? []).reduce((s, a) => s + a.n, 0);
    onHoverChange({
      id:        zoneId,
      name:      e.target.feature?.properties?.zone ?? String(zoneId),
      taxiCount,
      bikeFlows: bikeTotal,
      minFare,
      maxFare,
      netFlow: hourlyPressureRef.current?.by_hour?.[String(pulseHourRef.current)]?.[String(zoneId)]?.net ?? null,
    });
  }

  function handleOut(zoneId) {
    if (hoveredRef.current !== zoneId) return;
    hoveredRef.current = null;
    onHoverChange(null);
  }

  function handleClick(zoneId, properties) {
    onZoneClick({ id: zoneId, name: properties.zone ?? String(zoneId) });
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

function selfLoopArc([lat, lng], radiusM, numPts = 48) {
  const dLat = radiusM / 111320;
  const dLng = radiusM / (111320 * Math.cos(lat * Math.PI / 180));
  const pts = [];
  for (let i = 0; i <= numPts; i++) {
    const a = -Math.PI / 2 + (i / numPts) * 2 * Math.PI;
    pts.push([lat + dLat + dLat * Math.sin(a), lng + dLng * Math.cos(a)]);
  }
  return pts;
}
