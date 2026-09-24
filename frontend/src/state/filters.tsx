// The sidebar's global filters. One selection, shared by every page; each
// page declares which filters it honours (usePageFilters), and the sidebar
// greys out the rest with a note, so nobody wonders why a page ignores them.

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Params } from "../api/client";
import { useApi } from "../api/hooks";
import type { FilterOptions } from "../api/types";
import { GROUP_LABEL, monthLabel } from "../lib/format";

export type FilterKey = "month" | "support_group" | "region" | "area_code" | "urban_rural";
export type FilterValues = Record<FilterKey, string>;

export const EMPTY: FilterValues = { month: "", support_group: "", region: "", area_code: "", urban_rural: "" };
// v2: the default became "everything" — older saved selections are left behind.
const KEY = "hf.filters.v2";

interface FiltersState {
  values: FilterValues;
  set: (key: FilterKey, value: string) => void;
  reset: () => void;
  options: FilterOptions | undefined;
  active: FilterKey[];
  setActive: (keys: FilterKey[]) => void;
}

const FiltersContext = createContext<FiltersState | null>(null);

function load(): FilterValues | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? { ...EMPTY, ...JSON.parse(raw) } : null;
  } catch {
    return null;
  }
}

export function FiltersProvider({ children }: { children: ReactNode }) {
  const options = useApi<FilterOptions>("/dashboard/filters").data;
  const [values, setValues] = useState<FilterValues>(() => load() ?? EMPTY);
  const [active, setActive] = useState<FilterKey[]>([]);

  // Everything is shown until someone narrows it; the choice is remembered.
  useEffect(() => {
    try { localStorage.setItem(KEY, JSON.stringify(values)); } catch { /* storage blocked */ }
  }, [values]);

  const value = useMemo<FiltersState>(() => ({
    values,
    options,
    active,
    setActive,
    reset: () => setValues(EMPTY),
    set: (key, v) => setValues((prev) => {
      const next = { ...prev, [key]: v };
      // A district belongs to one region; changing region clears a district outside it.
      if (key === "region" && prev.area_code && options) {
        const d = options.districts.find((x) => x.area_code === prev.area_code);
        if (d && v && d.region !== v) next.area_code = "";
      }
      return next;
    }),
  }), [values, options, active]);

  return <FiltersContext.Provider value={value}>{children}</FiltersContext.Provider>;
}

export function useFilters(): FiltersState {
  const ctx = useContext(FiltersContext);
  if (!ctx) throw new Error("useFilters outside FiltersProvider");
  return ctx;
}

/** Declare the filters this page uses; returns them as API query params. */
export function usePageFilters(keys: FilterKey[]): Params {
  const { values, setActive } = useFilters();
  const sig = keys.join(",");
  useEffect(() => {
    setActive(keys);
    return () => setActive([]);
  }, [sig]); // eslint-disable-line react-hooks/exhaustive-deps
  return useMemo(() => {
    const p: Params = {};
    for (const k of keys) if (values[k]) p[k] = values[k];
    return p;
  }, [values, sig]); // eslint-disable-line react-hooks/exhaustive-deps
}

/** Human-readable chips for the filters a page uses. */
export function useFilterChips(keys: FilterKey[]): string[] {
  const { values, options } = useFilters();
  const chips: string[] = [];
  if (keys.includes("month")) chips.push(monthLabel(values.month || null));
  if (keys.includes("support_group")) chips.push(values.support_group ? GROUP_LABEL[values.support_group] : "All support types");
  if (keys.includes("region")) chips.push(values.region || "All regions");
  if (keys.includes("area_code") && values.area_code) {
    chips.push(options?.districts.find((d) => d.area_code === values.area_code)?.area_name ?? values.area_code);
  }
  if (keys.includes("urban_rural")) chips.push(values.urban_rural ? `${values.urban_rural[0].toUpperCase()}${values.urban_rural.slice(1)} only` : "Urban and rural");
  return chips;
}
