import { useEffect, useMemo, useRef, useState } from "react";
import type { CandleType, IndicatorPick, TechCatalog, TechTimeframe } from "../api";
import { IndicatorTip } from "./IndicatorTip";
import { byType, MINUTE_FRAMES, normalPick, pickKey, pickStandardText, pickText, PRICE_NAMES } from "./TechRules";

type Indicator = TechCatalog["indicators"][number];

/** A popup form to add an indicator to a strategy, or change one's settings. */
export function IndicatorForm({
  catalog,
  picks,
  editing,
  intraday,
  defaultFrame,
  onSave,
  onClose,
}: {
  catalog: TechCatalog;
  /** The strategy's indicators now, to refuse a second copy with the same settings. */
  picks: IndicatorPick[];
  /** The indicator being changed, or null to add one. */
  editing: IndicatorPick | null;
  /** Whether the strategy decides through the day: only then can indicators read 5-minute
   * to hourly candles. */
  intraday: boolean;
  /** The interval a new indicator starts on: the one the strategy decides on. */
  defaultFrame: TechTimeframe;
  onSave: (pick: IndicatorPick) => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [name, setName] = useState(editing?.name ?? "");
  const [values, setValues] = useState<Record<string, string>>(() => texts(catalog, editing));
  const [tried, setTried] = useState(false);
  const [label, setLabel] = useState(editing?.label ?? "");
  const [timeframe, setTimeframe] = useState<TechTimeframe>(editing?.timeframe ?? defaultFrame);
  const [candles, setCandles] = useState<CandleType>(editing?.candles ?? "normal");
  const frames = catalog.timeframes.filter(
    (frame) => intraday || !MINUTE_FRAMES.includes(frame.value) || frame.value === timeframe,
  );

  // Opens once, with the search ready when adding.
  useEffect(() => {
    const box = dialog.current;
    if (box && !box.open) box.showModal();
    if (!editing) search.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Esc closes the dialog natively; the builder must hear of it to drop the form.
  useEffect(() => {
    const box = dialog.current;
    box?.addEventListener("close", onClose);
    return () => box?.removeEventListener("close", onClose);
  }, [onClose]);

  const offered = useMemo(
    () => catalog.indicators.filter((item) => !PRICE_NAMES.includes(item.name)),
    [catalog],
  );
  const found = offered.filter((item) =>
    `${item.label} ${item.name} ${item.description}`.toLowerCase().includes(query.trim().toLowerCase()),
  );
  const groups = byType(catalog, found, (item: Indicator) => item.name);
  const chosen = offered.find((item) => item.name === name);

  const choose = (item: Indicator) => {
    setName(item.name);
    setValues(texts(catalog, { name: item.name, params: {} }));
    // Intraday-only indicators, such as the opening range, start on minute candles.
    if (item.intraday_only && !MINUTE_FRAMES.includes(timeframe) && intraday) {
      setTimeframe(MINUTE_FRAMES.includes(defaultFrame) ? defaultFrame : "15m");
    }
    setTried(false);
  };

  // Each setting's problem, if any, and the pick the form describes.
  const problems: Record<string, string> = {};
  const params: Record<string, number> = {};
  for (const param of chosen?.params ?? []) {
    const raw = values[param.name] ?? "";
    const value = Number(raw);
    if (raw.trim() === "" || !Number.isFinite(value)) problems[param.name] = "Enter a number.";
    else if (value < param.min || value > param.max) problems[param.name] = `Use ${param.min} to ${param.max}.`;
    else if (param.whole && !Number.isInteger(value)) problems[param.name] = "Use a whole number.";
    else params[param.name] = value;
  }
  const pick = chosen
    ? normalPick(catalog, chosen.name, params, { label: label.trim() || null, timeframe, candles })
    : null;
  const ownKey = editing ? pickKey(catalog, editing) : null;
  const others = picks.filter((other) => pickKey(catalog, other) !== ownKey);
  const duplicate =
    pick !== null &&
    !Object.keys(problems).length &&
    others.some((other) => pickKey(catalog, other) === pickKey(catalog, pick));
  const nameTaken =
    label.trim() !== "" &&
    others.some((other) => pickText(catalog, other).toLowerCase() === label.trim().toLowerCase());
  const unavailable = chosen?.intraday_only && !intraday;
  const needsMinutes = chosen?.intraday_only && intraday && !MINUTE_FRAMES.includes(timeframe);

  const save = () => {
    setTried(true);
    if (!pick || Object.keys(problems).length || duplicate || nameTaken || unavailable || needsMinutes) return;
    onSave(pick);
  };

  return (
    <dialog
      ref={dialog}
      className="map-preview indicator-form"
      aria-labelledby="indicator-form-title"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <form
        className="map-preview-box"
        onSubmit={(event) => {
          event.preventDefault();
          save();
        }}
      >
        <div className="map-preview-head">
          <div>
            <h2 id="indicator-form-title" className="h5 mb-0">
              {editing ? `Change ${chosen?.label ?? "indicator"}` : "Add an indicator"}
            </h2>
            <small className="text-body-secondary">
              {editing
                ? "New settings apply to every rule that uses it."
                : "Every case's entry and exit rules can then use it."}
            </small>
          </div>
          <button type="button" className="btn btn-outline-secondary btn-sm" onClick={onClose}>
            Cancel
          </button>
        </div>

        <div className={`indicator-form-body${editing ? " is-editing" : ""}`}>
          {!editing && (
            <div className="indicator-form-list">
              <label className="visually-hidden" htmlFor="indicator-search">
                Find an indicator
              </label>
              <input
                id="indicator-search"
                ref={search}
                type="search"
                className="form-control form-control-sm"
                placeholder="Find an indicator, for example RSI"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  // Enter picks the first match rather than submitting an empty form.
                  const first = groups[0]?.items[0];
                  if (event.key === "Enter" && !chosen && first) {
                    event.preventDefault();
                    choose(first);
                  }
                }}
              />
              <div className="indicator-form-options" role="listbox" aria-label="Indicators">
                {groups.map((group) => (
                  <div key={group.title}>
                    <div className="indicator-form-group">{group.title}</div>
                    {group.items.map((item) => (
                      <IndicatorTip key={item.name} indicator={item} className="indicator-tip-row">
                        <button
                          type="button"
                          role="option"
                          aria-selected={item.name === name}
                          className={`indicator-form-option${item.name === name ? " is-chosen" : ""}`}
                          onClick={() => choose(item)}
                        >
                          <span>{item.label}</span>
                          {item.intraday_only && <small>intraday</small>}
                        </button>
                      </IndicatorTip>
                    ))}
                  </div>
                ))}
                {!groups.length && <p className="form-text px-2">No indicator matches "{query}".</p>}
              </div>
            </div>
          )}

          <div className="indicator-form-settings">
            {!chosen ? (
              <p className="form-text mt-0">Choose an indicator from the list to set it up.</p>
            ) : (
              <>
                <h3 className="h6 mb-1">{chosen.label}</h3>
                {chosen.description && <p className="form-text mt-0 mb-1">{chosen.description}</p>}
                {chosen.formula && <code className="indicator-tip-formula mb-3">{chosen.formula}</code>}
                <div className="row g-2 mb-2">
                  <div className="col-12 col-md-6">
                    <label className="form-label small mb-1" htmlFor="indicator-label">
                      Name
                    </label>
                    <input
                      id="indicator-label"
                      className={`form-control form-control-sm${nameTaken ? " is-invalid" : ""}`}
                      maxLength={40}
                      placeholder={pick ? pickStandardText(catalog, { ...pick, label: null }) : undefined}
                      value={label}
                      onChange={(event) => setLabel(event.target.value)}
                    />
                    {nameTaken ? (
                      <div className="invalid-feedback d-block">Another indicator already has this name.</div>
                    ) : (
                      <div className="form-text">Optional. Rules list it by this name, such as "fast EMA".</div>
                    )}
                  </div>
                  <div className="col-6 col-md-3">
                    <label className="form-label small mb-1" htmlFor="indicator-interval">
                      Candle interval
                    </label>
                    <select
                      id="indicator-interval"
                      className={`form-select form-select-sm${needsMinutes ? " is-invalid" : ""}`}
                      value={timeframe}
                      onChange={(event) => setTimeframe(event.target.value as TechTimeframe)}
                    >
                      {frames.map((frame) => (
                        <option key={frame.value} value={frame.value}>
                          {frame.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="col-6 col-md-3">
                    <span className="form-label small mb-1 d-block" id="indicator-chart-label">
                      Chart type
                    </span>
                    <div className="btn-group btn-group-sm w-100" role="group" aria-labelledby="indicator-chart-label">
                      {(catalog.candle_types ?? [{ value: "normal", label: "Normal" }]).map((kind) => (
                        <button
                          key={kind.value}
                          type="button"
                          className={`btn ${candles === kind.value ? "btn-primary" : "btn-outline-secondary"}`}
                          aria-pressed={candles === kind.value}
                          onClick={() => setCandles(kind.value as CandleType)}
                        >
                          {kind.value === "normal" ? "Candles" : kind.label}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
                {!intraday && (
                  <p className="form-text mt-0">
                    5-minute to hourly intervals need a decision every 5, 15 or 60 minutes, set under Basics.
                  </p>
                )}
                {chosen.params.length ? (
                  <div className="row g-2">
                    {chosen.params.map((param) => {
                      const id = `indicator-param-${param.name}`;
                      const problem = tried || values[param.name] !== undefined ? problems[param.name] : undefined;
                      return (
                        <div className="col-6 col-md-4" key={param.name}>
                          <label className="form-label small mb-1" htmlFor={id}>
                            {param.label}
                          </label>
                          <input
                            id={id}
                            type="number"
                            className={`form-control form-control-sm${problem ? " is-invalid" : ""}`}
                            step={param.whole ? 1 : "any"}
                            min={param.min}
                            max={param.max}
                            value={values[param.name] ?? ""}
                            onChange={(event) => setValues({ ...values, [param.name]: event.target.value })}
                          />
                          {problem ? (
                            <div className="invalid-feedback d-block">{problem}</div>
                          ) : (
                            <div className="form-text">
                              {param.min} to {param.max}, default {param.default}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <p className="form-text">No settings.</p>
                )}
                {chosen.fields.length > 0 && (
                  <p className="small mt-3 mb-0">
                    <span className="text-body-secondary">Lines rules can read: </span>
                    {chosen.fields.map((field) => field.label).join(", ")}
                  </p>
                )}
                {pick && !Object.keys(problems).length && (
                  <p className="small mt-2 mb-0">
                    <span className="text-body-secondary">Listed as </span>
                    <strong>{pickText(catalog, pick)}</strong>
                  </p>
                )}
                {duplicate && (
                  <div className="alert alert-warning py-2 small mt-3 mb-0">
                    The strategy already has {pick ? pickText(catalog, pick) : "this"}. Change a setting to add a second
                    copy.
                  </div>
                )}
                {needsMinutes && (
                  <div className="alert alert-warning py-2 small mt-3 mb-0">
                    {chosen.label} works on 5-minute, 15-minute or hourly candles. Choose one of those intervals.
                  </div>
                )}
                {unavailable && (
                  <div className="alert alert-warning py-2 small mt-3 mb-0">
                    {chosen.label} needs 5-minute to hourly candles. Under Basics, decide every 5, 15 or 60 minutes
                    first.
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        <div className="indicator-form-foot">
          <button type="button" className="btn btn-outline-secondary btn-sm" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary btn-sm" disabled={!chosen}>
            {editing ? "Save changes" : "Add indicator"}
          </button>
        </div>
      </form>
    </dialog>
  );
}

/** The settings as editable text, filled with the pick's values or the defaults. */
function texts(catalog: TechCatalog, pick: IndicatorPick | null): Record<string, string> {
  if (!pick) return {};
  const full = normalPick(catalog, pick.name, pick.params);
  return Object.fromEntries(Object.entries(full.params).map(([key, value]) => [key, String(value)]));
}
