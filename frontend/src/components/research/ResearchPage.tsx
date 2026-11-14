import { useCallback, useEffect, useState } from "react";
import {
  api,
  type JobInfo,
  type MlStudy,
  type PairStudy,
  type ResearchPayload,
} from "../../api";
import { Legend, LineChart, ValueColumns, timeTicks } from "../../charts";
import { num, shortDate } from "../../format";
import {
  Alert,
  Button,
  cx,
  Empty,
  JobStatus,
  Panel,
  Segmented,
  Status,
  Table,
} from "../ui";

const POLL_MS = 2000;
const day = (iso: string) => new Date(`${iso}T00:00:00Z`).getTime();
// Long histories: the axis shows month and year, the tooltip the full date.
const fromMs = (x: number) =>
  new Date(x).toLocaleDateString("en-IN", {
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
const fullDate = (x: number) =>
  new Date(x).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
const pct = (value: number | null | undefined, digits = 1) =>
  value === null || value === undefined ? "–" : `${num(value, digits)}%`;
const share = (value: number | null | undefined) =>
  value === null || value === undefined ? "–" : `${Math.round(value * 100)}%`;
const ratio = (value: number | null | undefined) =>
  value === null || value === undefined ? "–" : num(value, 2);
const signed = (value: number, digits = 1) =>
  `${value > 0 ? "+" : value < 0 ? "−" : ""}${num(Math.abs(value), digits)}`;

function Covers({
  sessions,
  start,
  end,
}: {
  sessions: number;
  start?: string | null;
  end?: string | null;
}) {
  return (
    <span className="muted">
      {num(sessions)} sessions
      {start && end ? `, ${fullDate(day(start))} to ${fullDate(day(end))}` : ""}
    </span>
  );
}

function TimeOfDay({ study }: { study: ResearchPayload["time_of_day"] }) {
  if (!study.rows.length) return <Empty title="No one-minute candles yet" />;
  return (
    <>
      <ValueColumns
        data={study.rows.map((row) => ({ key: row.slot, value: row.mean_pct }))}
        height={200}
        label="Average move in each 15-minute slot, percent"
        formatX={(key) => key}
        formatY={(value) => `${num(value, 3)}%`}
        formatTip={(value) => `${signed(value, 3)}% on average`}
      />
      <Table>
        <thead>
          <tr>
            <th>Slot</th>
            <th className="right">Average move</th>
            <th className="right">Up</th>
            <th className="right">Typical size</th>
          </tr>
        </thead>
        <tbody>
          {study.rows.map((row) => (
            <tr key={row.slot}>
              <td>{row.slot}</td>
              <td className="right">{signed(row.mean_pct, 3)}%</td>
              <td className="right">{share(row.up_share)}</td>
              <td className="right">{num(row.abs_pct, 3)}%</td>
            </tr>
          ))}
        </tbody>
      </Table>
      <p className="panel-note">
        Close against open inside each slot, so the overnight gap is left out.
        Typical size is the average move ignoring direction.
      </p>
    </>
  );
}

function Volatility({
  study,
  name,
}: {
  study: NonNullable<ResearchPayload["vix_vs_rv"]>;
  name: string;
}) {
  const series = [
    {
      name,
      color: "var(--series-3)",
      points: study.points.map((p) => ({
        x: day(`${p.month}-01`),
        y: p.implied,
      })),
    },
    {
      name: `Realised over the next ${study.window} sessions`,
      color: "var(--series-4)",
      points: study.points.map((p) => ({
        x: day(`${p.month}-01`),
        y: p.following,
      })),
    },
  ];
  return (
    <>
      <p className="m-0 px-3 pt-3 text-[13px] text-ink">
        {name} was above the volatility that followed on{" "}
        <b>{share(study.premium_positive_share)}</b> of sessions, by{" "}
        <b>{num(study.mean_premium, 1)} points</b> on average.
        {study.latest &&
          ` Now ${num(study.latest.implied, 1)} against ${num(study.latest.trailing, 1)} realised over the last ${study.window} sessions.`}
      </p>
      <Legend items={series} />
      <LineChart
        series={series}
        height={220}
        tickX={timeTicks}
        label={`${name} against the realised volatility that followed, monthly averages`}
        formatX={fromMs}
        formatY={(y) => num(y, 0)}
        formatTipX={fullDate}
        formatTipY={(y) => num(y, 1)}
      />
      <p className="panel-note">
        Realised volatility over the following sessions is known only in
        hindsight. A steady positive gap is the premium option sellers are paid
        for the risk of a shock.
      </p>
    </>
  );
}

function tint(value: number): string {
  const colour = value < 0 ? "var(--pnl-down)" : "var(--series-3)";
  return `color-mix(in srgb, ${colour} ${Math.round(Math.abs(value) * 55)}%, transparent)`;
}

function Correlations({ data }: { data: ResearchPayload }) {
  const { correlations, rolling_correlation: rolling } = data;
  if (!correlations.names.length)
    return <Empty title="Not enough shared history" />;
  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse whitespace-nowrap font-mono text-[12px] tabular-nums [&_td]:px-2 [&_th]:px-2">
          <caption className="sr-only">
            Correlation of daily returns between each pair of indices.
          </caption>
          <thead>
            <tr className="border-b border-line">
              <th scope="col" className="py-2" />
              {correlations.names.map((name) => (
                <th
                  key={name}
                  scope="col"
                  className="py-2 text-right font-sans text-[11.5px] font-medium text-ink-3"
                >
                  {name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {correlations.matrix.map((row, i) => (
              <tr key={correlations.names[i]} className="border-b border-grid">
                <th
                  scope="row"
                  className="py-1.5 text-left font-sans text-[11.5px] font-medium text-ink-2"
                >
                  {correlations.names[i]}
                </th>
                {row.map((value, j) => (
                  <td
                    key={j}
                    className={cx("py-1.5 text-right", i === j && "text-ink-3")}
                    style={i === j ? undefined : { background: tint(value) }}
                  >
                    {num(value, 2)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rolling && rolling.points.length > 0 && (
        <>
          <p className="m-0 px-3 pt-3 text-[13px] text-ink-2">
            {data.symbol} against {rolling.other}, correlation over the last 60
            sessions
          </p>
          <LineChart
            series={[
              {
                name: "Correlation",
                color: "var(--series-3)",
                points: rolling.points.map((p) => ({
                  x: day(p.week),
                  y: p.corr,
                })),
              },
            ]}
            height={180}
            tickX={timeTicks}
            label={`Rolling correlation of ${data.symbol} and ${rolling.other}`}
            formatX={fromMs}
            formatY={(y) => num(y, 1)}
            formatTipX={fullDate}
            formatTipY={(y) => num(y, 2)}
          />
        </>
      )}
      <p className="panel-note">
        Daily returns over the last {correlations.sessions} sessions every index
        shares. Sector indices start in 2025, so they set how far back this
        goes.
      </p>
    </>
  );
}

function Gaps({ study }: { study: ResearchPayload["gaps"] }) {
  return (
    <>
      <Table>
        <thead>
          <tr>
            <th>Opening gap</th>
            <th className="right">Sessions</th>
            <th className="right">Filled that day</th>
            <th className="right">Closed on the gap side</th>
            <th className="right">Typical time to fill</th>
          </tr>
        </thead>
        <tbody>
          {study.rows.map((row) => (
            <tr key={row.label}>
              <td>{row.label}</td>
              <td className="right">{num(row.sessions)}</td>
              <td className="right">{share(row.filled_share)}</td>
              <td className="right">{share(row.held_share)}</td>
              <td className="right">
                {row.median_fill_minutes === null ||
                row.median_fill_minutes === undefined
                  ? "–"
                  : `${num(row.median_fill_minutes)} min`}
              </td>
            </tr>
          ))}
        </tbody>
      </Table>
      <p className="panel-note">
        A gap fills when the price returns to the previous close during the
        session. Time to fill uses one-minute candles, which start in 2022.
      </p>
    </>
  );
}

function Pair({ pair }: { pair: PairStudy }) {
  if (pair.error)
    return (
      <p className="m-0 p-3 text-[13px] text-ink-2">
        {pair.names.join(" and ")}: {pair.error}
      </p>
    );
  const rows: [string, PairStudy["train"]][] = [
    ["Earlier sessions", pair.train],
    ["Last 30% of sessions", pair.test],
  ];
  return (
    <div className="flex flex-col gap-2 border-t border-line pt-3 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2 px-3 text-[13px]">
        <b className="text-ink">{pair.names.join(" against ")}</b>
        <Status tone={pair.cointegrated_at ? "good" : "neutral"}>
          {pair.cointegrated_at
            ? `Cointegrated at ${pair.cointegrated_at}`
            : "Not cointegrated"}
        </Status>
        <span className="text-ink-3">
          ADF {ratio(pair.adf_t)}, half-life{" "}
          {pair.half_life_sessions === null ||
          pair.half_life_sessions === undefined
            ? "–"
            : `${num(pair.half_life_sessions, 0)} sessions`}
          , z-score now {ratio(pair.latest_z)}
        </span>
      </div>
      <Table>
        <thead>
          <tr>
            <th>Z-score rule</th>
            <th className="right">Sessions</th>
            <th className="right">Trades</th>
            <th className="right">Return</th>
            <th className="right">Sharpe</th>
            <th className="right">Max drawdown</th>
            <th className="right">Winning trades</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, window]) => (
            <tr key={label}>
              <td>{label}</td>
              <td className="right">{num(window?.sessions)}</td>
              <td className="right">{num(window?.trades)}</td>
              <td className="right">{pct(window?.total_return_pct)}</td>
              <td className="right">{ratio(window?.sharpe)}</td>
              <td className="right">{pct(window?.max_drawdown_pct)}</td>
              <td className="right">{share(window?.win_share)}</td>
            </tr>
          ))}
        </tbody>
      </Table>
      {pair.equity && pair.equity.length > 1 && (
        <LineChart
          series={[
            {
              name: "Cumulative return",
              color: "var(--series-3)",
              points: pair.equity.map((p) => ({ x: day(p.day), y: p.value })),
            },
          ]}
          height={160}
          polarity
          includeZero
          tickX={timeTicks}
          markers={
            pair.test_start
              ? [{ x: day(pair.test_start), label: "Unseen" }]
              : []
          }
          label={`Cumulative return of the z-score rule on ${pair.names.join(" and ")}`}
          formatX={fromMs}
          formatY={(y) => `${num(y, 0)}%`}
          formatTipX={fullDate}
          formatTipY={(y) => `${num(y, 1)}%`}
        />
      )}
    </div>
  );
}

function MlResult({ study }: { study: MlStudy }) {
  const series = [
    {
      name: "ML signal",
      color: "var(--series-3)",
      points: study.equity.map((p) => ({ x: day(p.day), y: p.ml })),
    },
    {
      name: study.best_rule,
      color: "var(--series-4)",
      points: study.equity.map((p) => ({ x: day(p.day), y: p.best_rule })),
    },
  ];
  const rows = [{ name: "ML signal", ...study.ml }, ...study.rules];
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 px-3 text-[13px]">
        <Status tone={study.adopted ? "good" : "warning"}>
          {study.adopted ? "Adopted" : "Not adopted"}
        </Status>
        <span className="text-ink-2">
          {study.adopted
            ? `Beat ${study.best_rule.toLowerCase()} on unseen sessions, with ${share(study.prob_better_than_best)} confidence.`
            : `Did not beat ${study.best_rule.toLowerCase()} on unseen sessions by more than luck explains (${share(study.prob_better_than_best)} chance it is better).`}
        </span>
      </div>
      <p className="m-0 px-3 text-[12.5px] text-ink-3">
        {num(study.sessions_tested)} unseen sessions,{" "}
        {fullDate(day(study.start))} to {fullDate(day(study.end))}, in{" "}
        {study.folds.length} blocks. Accuracy {share(study.ml.accuracy)} against{" "}
        {share(study.ml.base_rate)} up sessions; AUC {ratio(study.ml.auc)}.{" "}
        {study.rules_note}
      </p>
      <Table>
        <thead>
          <tr>
            <th>On unseen sessions</th>
            <th className="right">Return</th>
            <th className="right">Sharpe</th>
            <th className="right">Max drawdown</th>
            <th className="right">Hit rate</th>
            <th className="right">In the market</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.name}>
              <td>{row.name}</td>
              <td className="right">{pct(row.total_return_pct, 0)}</td>
              <td className="right">{ratio(row.sharpe)}</td>
              <td className="right">{pct(row.max_drawdown_pct, 0)}</td>
              <td className="right">{share(row.hit_rate)}</td>
              <td className="right">{share(row.time_in_market)}</td>
            </tr>
          ))}
        </tbody>
      </Table>
      <Legend items={series} />
      <LineChart
        series={series}
        height={200}
        includeZero
        tickX={timeTicks}
        label="Cumulative return on unseen sessions, ML signal against the best simple rule"
        formatX={fromMs}
        formatY={(y) => `${num(y, 0)}%`}
        formatTipX={fullDate}
        formatTipY={(y) => `${num(y, 1)}%`}
      />
      {study.importance.length > 0 && (
        <div className="px-3">
          <p className="m-0 mb-1.5 text-[12.5px] text-ink-2">
            What the latest model leans on, by how much AUC drops when the
            feature is shuffled
          </p>
          <ol className="m-0 grid list-none gap-1 pl-0 sm:grid-cols-2">
            {study.importance.slice(0, 8).map((row) => (
              <li
                key={row.feature}
                className="flex justify-between gap-3 text-[12.5px]"
              >
                <span className="text-ink">{row.feature}</span>
                <span className="font-mono text-ink-3 tabular-nums">
                  {num(row.importance, 3)}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
      <p className="panel-note">
        Latest reading for {shortDate(study.latest.day)}:{" "}
        {share(study.latest.prob_up)} chance of an up close next. Lab strategies
        can use it as the condition &ldquo;ML chance the index closes up
        next&rdquo;; every value they read came from a model that had not seen
        that session.
      </p>
    </div>
  );
}

function MlPanel({
  symbol,
  study,
  onDone,
}: {
  symbol: string;
  study: MlStudy | null;
  onDone: () => void;
}) {
  const [job, setJob] = useState<JobInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const running = job?.state === "running";
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      api
        .researchJob()
        .then(({ job: latest }) => {
          setJob(latest);
          if (latest && latest.state !== "running") onDone();
        })
        .catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, onDone]);
  const start = () => {
    setError(null);
    api
      .researchMl(symbol, 1)
      .then(setJob)
      .catch((err: Error) => setError(err.message));
  };
  return (
    <div className="flex flex-col gap-3 py-3">
      <div className="flex flex-wrap items-center gap-3 px-3">
        <Button variant="primary" disabled={running} onClick={start}>
          {study ? "Run the study again" : "Run the study"}
        </Button>
        <span className="text-[12.5px] text-ink-3">
          Gradient-boosted trees on {study?.features.length ?? 18} indicators
          and India VIX, trained walk-forward. Takes under a minute.
        </span>
      </div>
      {error && (
        <div className="px-3">
          <Alert tone="critical">{error}</Alert>
        </div>
      )}
      {job && (
        <div className="px-3">
          <JobStatus job={job} />
        </div>
      )}
      {study ? (
        <MlResult study={study} />
      ) : (
        <p className="m-0 px-3 text-[13px] text-ink-2">
          Not run yet for {symbol}.
        </p>
      )}
    </div>
  );
}

/** Market studies from stored candles, and the ML signal study. */
export function ResearchPage() {
  const [symbol, setSymbol] = useState("NIFTY");
  const [data, setData] = useState<ResearchPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setError(null);
    api
      .research(symbol)
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }, [symbol]);
  useEffect(load, [load]);

  if (error) return <Alert tone="critical">{error}</Alert>;
  if (!data || data.symbol !== symbol) return <Empty title="Loading…" />;
  const iv = data.vix_vs_rv;
  return (
    <div className="flex flex-col gap-3">
      <Segmented
        label="Index"
        value={symbol}
        options={data.symbols.map((value) => ({ value, label: value }))}
        onChange={setSymbol}
        className="self-start"
      />
      <Panel
        title="Machine-learning direction signal"
        aside={
          <span className="muted">Adopted only if it beats simple rules</span>
        }
      >
        <MlPanel symbol={symbol} study={data.ml} onDone={load} />
      </Panel>
      <Panel
        title="Returns by time of day"
        aside={
          <Covers
            sessions={data.time_of_day.sessions}
            start={data.time_of_day.start}
            end={data.time_of_day.end}
          />
        }
      >
        <TimeOfDay study={data.time_of_day} />
      </Panel>
      <Panel
        title="Implied against realised volatility"
        aside={
          iv && <Covers sessions={iv.sessions} start={iv.start} end={iv.end} />
        }
      >
        {iv ? (
          <Volatility study={iv} name="India VIX" />
        ) : data.atm_iv_vs_rv ? (
          <Volatility study={data.atm_iv_vs_rv} name="ATM IV" />
        ) : (
          <p className="m-0 p-3 text-[13px] text-ink-2">
            India VIX tracks NIFTY only. {symbol}&apos;s own ATM IV has{" "}
            {data.atm_iv_sessions} sessions so far; this study needs 21.
          </p>
        )}
      </Panel>
      <Panel
        title="Correlations between indices"
        aside={
          <Covers
            sessions={data.correlations.sessions}
            start={data.correlations.start}
            end={data.correlations.end}
          />
        }
      >
        <Correlations data={data} />
      </Panel>
      <Panel
        title="Opening gaps: filled or held"
        aside={
          <Covers
            sessions={data.gaps.sessions}
            start={data.gaps.start}
            end={data.gaps.end}
          />
        }
      >
        <Gaps study={data.gaps} />
      </Panel>
      <Panel
        title="Index pairs"
        aside={
          <span className="muted">A screen, not a tradeable backtest</span>
        }
      >
        <div className="flex flex-col gap-3 py-3">
          {data.pairs.map((pair) => (
            <Pair key={pair.names.join("-")} pair={pair} />
          ))}
        </div>
        <p className="panel-note">
          Index levels cannot be traded directly. A pair trade needs futures,
          whose history here starts on 28 Oct 2026, too short to test. The rule
          shorts the spread above a z-score of 2 and buys it below −2, exits
          inside 0.5, refits the hedge ratio weekly from the previous year, and
          pays 4 bps per change of position.
        </p>
      </Panel>
    </div>
  );
}
