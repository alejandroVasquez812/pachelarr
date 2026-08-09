// Client-side validation mirroring pachelarr/settings.py validate_value.
// Operates on the raw string from an input field and returns a typed value
// for the PUT body via coerceValue.

import type { SettingEntry } from "./types";

// Returns an error message string, or null if the raw string is valid for
// the setting's type. Mirrors the backend's per-type coercion rules.
export function validateField(
  key: string,
  value: string,
  entry: SettingEntry,
): string | null {
  switch (entry.type) {
    case "str":
      // str always accepts any string, including empty.
      return null;
    case "int": {
      if (value.trim() === "") return null;
      const n = Number(value);
      if (!Number.isInteger(n)) return "must be an integer";
      return null;
    }
    case "float": {
      if (value.trim() === "") return null;
      const n = parseFloat(value);
      if (Number.isNaN(n)) return "must be a number";
      return null;
    }
    case "bool": {
      const s = value.trim().toLowerCase();
      if (["1", "true", "yes", "0", "false", "no", ""].includes(s)) return null;
      return "must be true or false";
    }
    default:
      return null;
  }
}

// Converts a raw string to the typed value expected by the PUT body.
// str stays a string; int/float parse; bool maps "1"/"true"/"yes" -> true.
export function coerceValue(
  value: string,
  entry: SettingEntry,
): string | number | boolean {
  switch (entry.type) {
    case "int": {
      const n = parseInt(value, 10);
      return Number.isNaN(n) ? 0 : n;
    }
    case "float": {
      const n = parseFloat(value);
      return Number.isNaN(n) ? 0 : n;
    }
    case "bool": {
      const s = value.trim().toLowerCase();
      return ["1", "true", "yes"].includes(s);
    }
    case "str":
    default:
      return value;
  }
}
