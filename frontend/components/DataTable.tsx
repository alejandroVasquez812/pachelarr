"use client";

import React, { useMemo, useState, useCallback } from "react";
import {
  RiArrowUpLine,
  RiArrowDownLine,
  RiSearchLine,
  RiFilter3Line,
} from "@remixicon/react";

import { cx, focusInput } from "@/lib/utils";
import {
  Table,
  TableHead,
  TableHeaderCell,
  TableBody,
  TableRow,
  TableCell,
} from "@/components/Table";
import { Input } from "@/components/Input";

export type SortDirection = "asc" | "desc";

export interface DataTableColumn<T> {
  /** Unique key used to look up the row value. */
  key: string;
  /** Header label. */
  header: React.ReactNode;
  /** Render function for a cell. Defaults to the raw value. */
  cell?: (row: T) => React.ReactNode;
  /** How to read the comparable value for sorting. Defaults to row[key]. */
  accessor?: (row: T) => string | number | boolean | null | undefined;
  /** Whether this column is sortable. Default `false`. */
  sortable?: boolean;
  /** Whether this column participates in the global search. Default `true`. */
  searchable?: boolean;
  /** Optional per-column filter menu (see `filter` prop on DataTable). */
  filterable?: boolean;
  /** Optional custom className for header cells. */
  headerClassName?: string;
  /** Optional custom className for body cells. */
  cellClassName?: string;
}

export interface DataTableFilterOption {
  /** Stable value used in the filter state. */
  value: string;
  /** Label rendered in the dropdown. */
  label: React.ReactNode;
}

export interface DataTableProps<T> {
  /** Rows to render. */
  rows: T[];
  /** Column definitions. */
  columns: DataTableColumn<T>[];
  /** Stable row key for React. Falls back to row index. */
  getRowId?: (row: T, index: number) => React.Key;
  /** Placeholder for the global search input. */
  searchPlaceholder?: string;
  /** Hide the global search bar. Default `false` (shown). */
  hideSearch?: boolean;
  /** Initial sort configuration. */
  initialSort?: { columnKey: string; direction: SortDirection } | null;
  /** Controlled sort (optional). */
  sort?: { columnKey: string; direction: SortDirection } | null;
  onSortChange?: (sort: { columnKey: string; direction: SortDirection } | null) => void;
  /** Per-column filter options keyed by column key. */
  filters?: Record<string, DataTableFilterOption[]>;
  /** Initial selected filter values keyed by column key. */
  initialFilters?: Record<string, string[]>;
  /** Controlled filter state (optional). */
  filterState?: Record<string, string[]>;
  onFilterChange?: (filters: Record<string, string[]>) => void;
  /** Optional caption shown when there are no rows after filtering. */
  emptyMessage?: React.ReactNode;
  /** Optional toolbar rendered on the right of the search input. */
  toolbar?: React.ReactNode;
  className?: string;
  /** Screen-reader label for the table. */
  ariaLabel?: string;
}

const DEFAULT_ACCESSOR = <T,>(row: T, key: string) => {
  const value = (row as Record<string, unknown>)?.[key];
  return value as string | number | boolean | null | undefined;
};

function normalizeSearchValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function compareValues(
  a: string | number | boolean | null | undefined,
  b: string | number | boolean | null | undefined,
): number {
  if (a == null && b == null) return 0;
  if (a == null) return 1; // nulls last
  if (b == null) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  if (typeof a === "boolean" && typeof b === "boolean") return a === b ? 0 : a ? 1 : -1;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

/**
 * Generic data table with global search, per-column filter dropdowns, and
 * client-side sorting. Drop-in replacement for hand-rolled Table usage.
 */
export function DataTable<T>({
  rows,
  columns,
  getRowId,
  searchPlaceholder = "Search…",
  hideSearch = false,
  initialSort = null,
  sort: controlledSort,
  onSortChange,
  filters,
  initialFilters,
  filterState: controlledFilters,
  onFilterChange,
  emptyMessage = "No matching rows",
  toolbar,
  className,
  ariaLabel,
}: DataTableProps<T>) {
  const [internalSort, setInternalSort] = useState<{
    columnKey: string;
    direction: SortDirection;
  } | null>(initialSort);
  const sort = controlledSort ?? internalSort;

  const [internalFilters, setInternalFilters] = useState<Record<string, string[]>>(
    initialFilters ?? {},
  );
  const filterState = controlledFilters ?? internalFilters;

  const [query, setQuery] = useState("");
  const [openFilterKey, setOpenFilterKey] = useState<string | null>(null);

  const setSort = useCallback(
    (next: { columnKey: string; direction: SortDirection } | null) => {
      if (controlledSort === undefined) setInternalSort(next);
      onSortChange?.(next);
    },
    [controlledSort, onSortChange],
  );

  const handleHeaderClick = useCallback(
    (column: DataTableColumn<T>) => {
      if (!column.sortable) return;
      if (sort?.columnKey !== column.key) {
        setSort({ columnKey: column.key, direction: "asc" });
        return;
      }
      // Toggle: asc -> desc -> none
      if (sort.direction === "asc") {
        setSort({ columnKey: column.key, direction: "desc" });
      } else {
        setSort(null);
      }
    },
    [sort, setSort],
  );

  const setFilterValues = useCallback(
    (key: string, values: string[]) => {
      const next = { ...filterState, [key]: values.length ? values : undefined };
      delete next[key]; // remove if empty
      if (controlledFilters === undefined) {
        setInternalFilters(next);
      }
      onFilterChange?.(next);
    },
    [filterState, controlledFilters, onFilterChange],
  );

  const toggleFilterValue = useCallback(
    (key: string, value: string) => {
      const current = filterState[key] ?? [];
      const next = current.includes(value)
        ? current.filter((v) => v !== value)
        : [...current, value];
      setFilterValues(key, next);
    },
    [filterState, setFilterValues],
  );

  const processed = useMemo(() => {
    const searchableColumns = columns.filter((c) => c.searchable !== false);
    const q = query.trim().toLowerCase();

    let out = rows;

    // Global search
    if (q) {
      out = out.filter((row) =>
        searchableColumns.some((c) => {
          const v = c.accessor ? c.accessor(row) : DEFAULT_ACCESSOR(row, c.key);
          return normalizeSearchValue(v).toLowerCase().includes(q);
        }),
      );
    }

    // Per-column filters
    for (const [key, values] of Object.entries(filterState)) {
      if (!values?.length) continue;
      const col = columns.find((c) => c.key === key);
      if (!col) continue;
      out = out.filter((row) => {
        const v = col.accessor ? col.accessor(row) : DEFAULT_ACCESSOR(row, col.key);
        return values.includes(normalizeSearchValue(v));
      });
    }

    // Sort
    if (sort) {
      const col = columns.find((c) => c.key === sort.columnKey);
      if (col) {
        const dir = sort.direction === "asc" ? 1 : -1;
        const accessor = col.accessor ?? ((row: T) => DEFAULT_ACCESSOR(row, col.key));
        out = [...out].sort((a, b) => dir * compareValues(accessor(a), accessor(b)));
      }
    }

    return out;
  }, [rows, columns, query, filterState, sort]);

  const activeFilterCount = useMemo(
    () => Object.values(filterState).reduce((n, vs) => n + (vs?.length ?? 0), 0),
    [filterState],
  );

  const hasActiveFilters = query.trim().length > 0 || activeFilterCount > 0;

  const clearAll = useCallback(() => {
    setQuery("");
    if (controlledFilters === undefined) setInternalFilters({});
    onFilterChange?.({});
    setSort(null);
  }, [controlledFilters, onFilterChange, setSort]);

  return (
    <div className={cx("flex flex-col gap-3", className)}>
      {/* Toolbar */}
      {!hideSearch || toolbar || hasActiveFilters ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          {!hideSearch ? (
            <div className="relative w-full max-w-xs">
              <Input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={searchPlaceholder}
                aria-label="Search table"
                className="pl-8"
              />
              <RiSearchLine className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-[var(--muted)]" />
            </div>
          ) : (
            <div />
          )}
          <div className="flex flex-wrap items-center gap-2">
            {toolbar}
            {hasActiveFilters ? (
              <button
                type="button"
                onClick={clearAll}
                className={cx(
                  "rounded-md border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5",
                  "text-xs text-[var(--muted)] hover:bg-[var(--bg)]",
                  focusInput,
                )}
              >
                Clear filters
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

      {/* Table */}
      <Table aria-label={ariaLabel}>
        <TableHead>
          <TableRow>
            {columns.map((column) => {
              const isSorted = sort?.columnKey === column.key;
              const filterOptions = column.filterable ? filters?.[column.key] : undefined;
              const selected = filterState[column.key] ?? [];
              const isOpen = openFilterKey === column.key;
              return (
                <TableHeaderCell
                  key={column.key}
                  className={cx(
                    column.sortable && "cursor-pointer select-none",
                    column.headerClassName,
                  )}
                >
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      tabIndex={column.sortable ? 0 : -1}
                      onClick={() => handleHeaderClick(column)}
                      className={cx(
                        "flex items-center gap-1",
                        column.sortable
                          ? "rounded outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40"
                          : "cursor-default",
                      )}
                      aria-sort={
                        isSorted
                          ? sort?.direction === "asc"
                            ? "ascending"
                            : "descending"
                          : column.sortable
                            ? "none"
                            : undefined
                      }
                    >
                      {column.header}
                      {column.sortable ? (
                        isSorted ? (
                          sort?.direction === "asc" ? (
                            <RiArrowUpLine className="size-3.5 text-[var(--accent)]" />
                          ) : (
                            <RiArrowDownLine className="size-3.5 text-[var(--accent)]" />
                          )
                        ) : (
                          <span className="text-[var(--muted)] opacity-0 group-hover:opacity-100">
                            <RiArrowUpLine className="size-3.5" />
                          </span>
                        )
                      ) : null}
                    </button>

                    {filterOptions ? (
                      <div className="relative">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setOpenFilterKey(isOpen ? null : column.key);
                          }}
                          className={cx(
                            "rounded px-1 py-0.5 text-[var(--muted)] hover:bg-[var(--bg)]",
                            "outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40",
                            selected.length > 0 &&
                              "bg-[var(--accent)]/10 text-[var(--accent)]",
                          )}
                          aria-label={`Filter ${column.key}`}
                          aria-haspopup="menu"
                          aria-expanded={isOpen}
                        >
                          <RiFilter3Line className="size-3.5" />
                        </button>
                        {isOpen ? (
                          <>
                            <div
                              className="fixed inset-0 z-10"
                              onClick={() => setOpenFilterKey(null)}
                              aria-hidden
                            />
                            <div
                              role="menu"
                              className={cx(
                                "absolute right-0 z-20 mt-1 min-w-40 max-h-60 overflow-auto",
                                "rounded-md border border-[var(--border)] bg-[var(--surface)] p-1",
                                "shadow-[var(--shadow-card)]",
                              )}
                            >
                              {filterOptions.map((opt) => {
                                const checked = selected.includes(opt.value);
                                return (
                                  <label
                                    key={opt.value}
                                    className={cx(
                                      "flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm",
                                      "text-[var(--text)] hover:bg-[var(--bg)]",
                                    )}
                                  >
                                    <input
                                      type="checkbox"
                                      checked={checked}
                                      onChange={() => toggleFilterValue(column.key, opt.value)}
                                      className="accent-[var(--accent)]"
                                    />
                                    <span>{opt.label}</span>
                                  </label>
                                );
                              })}
                              {selected.length > 0 ? (
                                <button
                                  type="button"
                                  onClick={() => setFilterValues(column.key, [])}
                                  className={cx(
                                    "mt-1 w-full rounded px-2 py-1 text-left text-xs",
                                    "text-[var(--muted)] hover:bg-[var(--bg)]",
                                  )}
                                >
                                  Clear
                                </button>
                              ) : null}
                            </div>
                          </>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                </TableHeaderCell>
              );
            })}
          </TableRow>
        </TableHead>
        <TableBody>
          {processed.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={columns.length}
                className="text-center text-[var(--muted)]"
              >
                {emptyMessage}
              </TableCell>
            </TableRow>
          ) : (
            processed.map((row, index) => (
              <TableRow key={getRowId ? getRowId(row, index) : index}>
                {columns.map((column) => {
                  const content = column.cell
                    ? column.cell(row)
                    : (DEFAULT_ACCESSOR(row, column.key) ?? "—");
                  return (
                    <TableCell key={column.key} className={column.cellClassName}>
                      {content}
                    </TableCell>
                  );
                })}
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>

      {/* Footer summary */}
      <div className="flex items-center justify-between text-xs text-[var(--muted)]">
        <span>
          {processed.length === rows.length
            ? `${rows.length} row${rows.length === 1 ? "" : "s"}`
            : `${processed.length} of ${rows.length} rows`}
        </span>
        {hasActiveFilters ? (
          <span>
            {query.trim() && `search: “${query.trim()}”`}
            {query.trim() && activeFilterCount > 0 ? " · " : ""}
            {activeFilterCount > 0 && `${activeFilterCount} filter${activeFilterCount === 1 ? "" : "s"}`}
          </span>
        ) : null}
      </div>
    </div>
  );
}

export default DataTable;