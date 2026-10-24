"use client";

import { type FormEvent, useEffect, useState } from "react";
import { ApiError, GeocodeSchema, type Place, api } from "@/lib/api";

type Props = {
  label: string;
  marker: "A" | "B";
  value: Place | null;
  onChange: (place: Place) => void;
};

/** A place box: Enter looks the text up in the local gazetteer; several matches become a pick list. */
export default function PlaceInput({ label, marker, value, onChange }: Props) {
  const [text, setText] = useState(value?.name ?? "");
  const [choices, setChoices] = useState<Place[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const id = `place-${marker}`;

  useEffect(() => setText(value?.name ?? ""), [value]);

  async function lookup(event: FormEvent) {
    event.preventDefault();
    const query = text.trim();
    if (!query || query === value?.name) return;
    setMessage(null);
    setChoices([]);
    try {
      const result = await api(`/api/geocode?q=${encodeURIComponent(query)}`, GeocodeSchema);
      if (result.status === "ok") onChange(result.candidates[0]);
      else if (result.status === "ambiguous") setChoices(result.candidates);
      else setMessage("No match. Try an MBTA station, a Bluebikes dock, or lat,lon.");
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : "Lookup failed.");
    }
  }

  function pick(place: Place) {
    setChoices([]);
    onChange(place);
  }

  return (
    <form className="place" onSubmit={lookup}>
      <span className={`dot dot-${marker}`} aria-hidden>
        {marker}
      </span>
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <input
        id={id}
        value={text}
        placeholder={label}
        onChange={(e) => setText(e.target.value)}
        onBlur={(e) => {
          if (text.trim() && text.trim() !== value?.name) void lookup(e);
        }}
        autoComplete="off"
      />
      {message && <p className="place-msg">{message}</p>}
      {choices.length > 0 && (
        <ul className="choices" aria-label={`Matches for ${label}`}>
          {choices.map((c) => (
            <li key={`${c.name}-${c.point.lat}`}>
              <button type="button" onClick={() => pick(c)}>
                {c.name}
                <span>{c.source.replace("_", " ")}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </form>
  );
}
