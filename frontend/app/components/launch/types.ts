/**
 * Shared TypeScript shapes for family-specific launch subforms (Phase D).
 *
 * Mirrors the Pydantic override models in
 * `liveoption_web/runs/families.py` field-by-field. Each subform owns
 * its slice of override state and serialises directly into the
 * `overrides` payload of `POST /v1/runs`.
 *
 * Empty / undefined fields are omitted by the parent `LaunchForm` so the
 * scenario's base YAML applies — match the null-tolerant backend models.
 */

export interface HoldingSpecValue {
  symbol: string;
  shares: number | "";
  cost_basis: number | "";
}

export interface OverlayValues {
  underlying_holdings?: HoldingSpecValue[];
  overlay_dte_range?: [number, number];
}

export interface EarningsValues {
  tickers?: string[];
  event_window?: [number, number];
  quarter?: "2025Q1" | "2025Q2" | "2025Q3" | "2025Q4";
}

export interface IntradayValues {
  decision_interval_minutes?: 1 | 5 | 15 | 30;
  max_concurrent_positions?: number;
  dte_range?: [number, number];
  llm_model?: string;
  prompt_override?: string;
}

export type FamilyValues = OverlayValues | EarningsValues | IntradayValues;

export type Family = "overlay" | "earnings" | "intraday";

/** Shared error map keyed by field name. */
export type FieldErrors = Record<string, string | undefined>;

export interface FamilyFormProps<T> {
  value: T;
  onChange: (next: T) => void;
  errors?: FieldErrors;
}
