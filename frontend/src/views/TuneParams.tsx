import type { TuneParam } from "../api";
import { Button } from "../components/ui";

const GROUP_ORDER = [
  "Exits",
  "Legs",
  "Entry",
  "Entry rules",
  "Exit rules",
  "Market conditions",
  "Timing",
];

function grouped(params: TuneParam[]): [string, TuneParam[]][] {
  const groups = new Map<string, TuneParam[]>();
  for (const param of params) {
    groups.set(param.group, [...(groups.get(param.group) ?? []), param]);
  }
  const rank = (name: string) => {
    const at = GROUP_ORDER.indexOf(name);
    return at < 0 ? GROUP_ORDER.length : at;
  };
  return [...groups.entries()].sort(([a], [b]) => rank(a) - rank(b));
}

const VALUE_NOTE: Partial<Record<TuneParam["kind"], string>> = {
  rule: "any part",
  add: "AI only",
};

function Row({
  param,
  chosen,
  onChange,
}: {
  param: TuneParam;
  chosen: boolean;
  onChange: (chosen: boolean) => void;
}) {
  return (
    <label className={`tune-param-row${chosen ? " chosen" : ""}`}>
      <input
        type="checkbox"
        checked={chosen}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="tune-param-name">{param.label}</span>
      <span className="tune-param-now">
        {VALUE_NOTE[param.kind] ?? param.value}
      </span>
    </label>
  );
}

/** The strategy's settings as checkboxes, by group: only the ticked ones are tuned. */
export function TuneParams({
  params,
  chosen,
  aiOn,
  onChange,
}: {
  params: TuneParam[];
  chosen: Set<string>;
  aiOn: boolean;
  onChange: (chosen: Set<string>) => void;
}) {
  const set = (paths: string[], on: boolean) => {
    const next = new Set(chosen);
    for (const path of paths) {
      if (on) next.add(path);
      else next.delete(path);
    }
    onChange(next);
  };
  const all = params.map((p) => p.path);
  return (
    <fieldset className="tune-params">
      <legend>
        Settings to tune{" "}
        <span className="muted">
          · {chosen.size} of {params.length} chosen
        </span>
      </legend>
      <div className="tune-params-bar">
        <Button variant="link" onClick={() => set(all, true)}>
          Choose all
        </Button>
        <Button variant="link" onClick={() => set(all, false)}>
          Clear
        </Button>
        <span className="secondary">
          Only ticked settings are changed. A rule allows any change inside it.
          {!aiOn && " Adding new rules needs the AI model."}
        </span>
      </div>
      {grouped(params).map(([group, items]) => {
        const paths = items.map((p) => p.path);
        const on = paths.filter((path) => chosen.has(path)).length;
        return (
          <div className="tune-params-group" key={group}>
            <div className="tune-params-head">
              <strong>{group}</strong>
              <span className="muted">
                {on} of {items.length}
              </span>
              <Button
                variant="link"
                onClick={() => set(paths, on < items.length)}
              >
                {on < items.length ? "Choose all" : "Clear"}
              </Button>
            </div>
            <ul className="tune-param-rows">
              {items
                .filter((p) => !p.parent)
                .map((param) => {
                  const inside = items.filter((p) => p.parent === param.path);
                  return (
                    <li key={param.path}>
                      <Row
                        param={param}
                        chosen={chosen.has(param.path)}
                        onChange={(next) => set([param.path], next)}
                      />
                      {inside.length > 0 && (
                        <ul className="tune-param-rows inside">
                          {inside.map((child) => (
                            <li key={child.path}>
                              <Row
                                param={child}
                                chosen={chosen.has(child.path)}
                                onChange={(next) => set([child.path], next)}
                              />
                            </li>
                          ))}
                        </ul>
                      )}
                    </li>
                  );
                })}
            </ul>
          </div>
        );
      })}
    </fieldset>
  );
}
