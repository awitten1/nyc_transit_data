export default function MapModeBar({
  showPressure, onTogglePressure,
  showTaxi, onToggleTaxi, taxiViz, onSetTaxiViz,
  showBike, onToggleBike,
}) {
  return (
    <div className="map-mode-bar">
      <div className="map-mode-row">
        <button className={`map-mode-pill${showPressure ? ' active' : ''}`} onClick={onTogglePressure}>Pressure</button>
        <div className="map-mode-divider" />
        <button className={`map-mode-pill${showTaxi ? ' active' : ''}`} onClick={onToggleTaxi}>Taxi</button>
        <button className={`map-mode-pill${showBike  ? ' active' : ''}`} onClick={onToggleBike}>Bike</button>
      </div>
      {showTaxi && (
        <div className="map-mode-sub-row">
          <button
            className={`map-mode-sub-pill${taxiViz === 'zone' ? ' active' : ''}`}
            onClick={() => onSetTaxiViz('zone')}
          >Zone Color</button>
          <button
            className={`map-mode-sub-pill${taxiViz === 'edge' ? ' active' : ''}`}
            onClick={() => onSetTaxiViz('edge')}
          >Edge</button>
        </div>
      )}
    </div>
  );
}
