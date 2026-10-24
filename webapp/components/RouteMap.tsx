"use client";

import "leaflet/dist/leaflet.css";
import L from "leaflet";
import { useEffect, useRef } from "react";
import { z } from "zod";
import type { Plan } from "@/lib/api";
import { LEG_STYLE, routeColor } from "@/lib/format";

const FeatureCollection = z.object({
  type: z.literal("FeatureCollection"),
  features: z.array(z.unknown()),
});

const ATTRIBUTION = "© OpenStreetMap contributors";
const BOSTON: L.LatLngTuple = [42.33, -71.07];

type Props = {
  plan: Plan | null;
  live: boolean;
  highlightedId: string | null;
  onHighlight: (id: string | null) => void;
};

export default function RouteMap({ plan, live, highlightedId, onHighlight }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const routes = useRef<L.LayerGroup | null>(null);
  const lines = useRef<Map<string, L.Polyline[]>>(new Map());

  useEffect(() => {
    if (!container.current) return;
    const m = L.map(container.current, { zoomControl: true, attributionControl: true });
    m.setView(BOSTON, 12);
    m.attributionControl.setPrefix(false).addAttribution(ATTRIBUTION);
    if (live) {
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: ATTRIBUTION,
      }).addTo(m);
    }
    // Offline: a local street basemap, and no tile requests.
    fetch("/basemap.geojson")
      .then((r) => (r.ok ? r.json() : null))
      .then((data: unknown) => {
        // Cast only after checking it is a FeatureCollection from our own server.
        if (FeatureCollection.safeParse(data).success) {
          L.geoJSON(data as GeoJSON.FeatureCollection, {
            style: { color: "#b9bec7", weight: 1, opacity: 0.8 },
            interactive: false,
          }).addTo(m);
        }
      })
      .catch(() => undefined);
    routes.current = L.layerGroup().addTo(m);
    map.current = m;
    return () => {
      m.remove();
      map.current = null;
    };
  }, [live]);

  useEffect(() => {
    const m = map.current;
    const group = routes.current;
    if (!m || !group) return;
    group.clearLayers();
    lines.current = new Map();
    if (!plan) return;
    const bounds = L.latLngBounds([]);
    plan.routes.forEach((route, index) => {
      const drawn: L.Polyline[] = [];
      for (const leg of route.legs) {
        if (leg.coords.length < 2) continue;
        const latlngs = leg.coords.map(([lon, lat]) => L.latLng(lat, lon));
        const line = L.polyline(latlngs, {
          color: routeColor(index),
          opacity: 0.85,
          ...LEG_STYLE[leg.mode],
        });
        line.on("mouseover", () => onHighlight(route.id));
        line.on("mouseout", () => onHighlight(null));
        line.bindTooltip(route.summary, { sticky: true });
        line.addTo(group);
        drawn.push(line);
        latlngs.forEach((p) => bounds.extend(p));
      }
      lines.current.set(route.id, drawn);
    });
    if (bounds.isValid()) m.fitBounds(bounds, { padding: [24, 24] });
  }, [plan, onHighlight]);

  useEffect(() => {
    for (const [id, drawn] of lines.current) {
      const dim = highlightedId !== null && id !== highlightedId;
      for (const line of drawn) {
        line.setStyle({ opacity: dim ? 0.15 : 0.9 });
        if (id === highlightedId) line.bringToFront();
      }
    }
  }, [highlightedId, plan]);

  return <div ref={container} className="map" role="region" aria-label="Route map" />;
}
