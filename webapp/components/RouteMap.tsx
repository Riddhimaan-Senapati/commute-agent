"use client";

import "leaflet/dist/leaflet.css";
import L from "leaflet";
import { useEffect, useRef } from "react";
import { z } from "zod";
import type { Point, Route } from "@/lib/api";
import { legColor, legDash } from "@/lib/format";

const ATTRIBUTION = "© OpenStreetMap contributors";
const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const BOSTON: L.LatLngTuple = [42.33, -71.07];
const FeatureCollection = z.object({
  type: z.literal("FeatureCollection"),
  features: z.array(z.unknown()),
});

type Props = {
  routes: Route[];
  selectedId: string | null;
  origin: Point | null;
  destination: Point | null;
  onSelect: (id: string) => void;
  /** Extra left padding so the route is not hidden under the floating panel. */
  panelWidth: number;
};

function pin(letter: string, kind: "start" | "end"): L.DivIcon {
  return L.divIcon({
    className: "",
    html: `<span class="pin pin-${kind}">${letter}</span>`,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  });
}

function latlngs(coords: [number, number][]): L.LatLng[] {
  return coords.map(([lon, lat]) => L.latLng(lat, lon));
}

/** Street map with OSM tiles; falls back to the local basemap when tiles cannot load. */
export default function RouteMap({ routes, selectedId, origin, destination, onSelect, panelWidth }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!container.current) return;
    const m = L.map(container.current, { zoomControl: false, attributionControl: true });
    m.setView(BOSTON, 12);
    L.control.zoom({ position: "bottomright" }).addTo(m);
    m.attributionControl.setPrefix(false);

    let basemapShown = false;
    const showBasemap = () => {
      if (basemapShown) return;
      basemapShown = true;
      fetch("/basemap.geojson")
        .then((r) => (r.ok ? r.json() : null))
        .then((data: unknown) => {
          if (!FeatureCollection.safeParse(data).success) return;
          L.geoJSON(data as GeoJSON.FeatureCollection, {
            style: { color: "#c3c8d0", weight: 1.2 },
            interactive: false,
          }).addTo(m);
          m.attributionControl.addAttribution("offline basemap");
        })
        .catch(() => undefined);
    };
    const tiles = L.tileLayer(TILES, { maxZoom: 19, attribution: ATTRIBUTION });
    tiles.on("tileerror", showBasemap);
    tiles.addTo(m);
    if (!navigator.onLine) showBasemap();

    layer.current = L.layerGroup().addTo(m);
    map.current = m;
    return () => {
      m.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    const group = layer.current;
    if (!m || !group) return;
    group.clearLayers();
    const bounds = L.latLngBounds([]);

    // Unselected routes first, so the selected one draws on top.
    const ordered = [...routes].sort((a, b) => Number(a.id === selectedId) - Number(b.id === selectedId));
    for (const route of ordered) {
      const selected = route.id === selectedId;
      for (const leg of route.legs) {
        if (leg.coords.length < 2) continue;
        const path = latlngs(leg.coords);
        path.forEach((p) => bounds.extend(p));
        if (!selected) {
          L.polyline(path, { color: "#8f99a8", weight: 5, opacity: 0.55, dashArray: legDash(leg) })
            .on("click", () => onSelect(route.id))
            .bindTooltip(route.summary, { sticky: true })
            .addTo(group);
          continue;
        }
        L.polyline(path, { color: "#ffffff", weight: 10, opacity: 0.95, interactive: false }).addTo(group);
        L.polyline(path, {
          color: legColor(leg),
          weight: 6,
          opacity: 1,
          dashArray: legDash(leg),
          className: "route-selected",
        }).addTo(group);
      }
    }
    if (origin) L.marker([origin.lat, origin.lon], { icon: pin("A", "start") }).addTo(group);
    if (destination) L.marker([destination.lat, destination.lon], { icon: pin("B", "end") }).addTo(group);
    if (origin) bounds.extend([origin.lat, origin.lon]);
    if (destination) bounds.extend([destination.lat, destination.lon]);
    if (bounds.isValid()) {
      m.fitBounds(bounds, { paddingTopLeft: [panelWidth + 32, 32], paddingBottomRight: [32, 32] });
    }
  }, [routes, selectedId, origin, destination, onSelect, panelWidth]);

  return <div ref={container} className="map" role="region" aria-label="Route map" />;
}
