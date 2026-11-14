import { Download, Sparkles } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  api,
  type Dossier,
  type DossierSection,
  type JobInfo,
} from "../../api";
import { dateTimeIst } from "../../format";
import { Alert, Button, ButtonLink, JobStatus, Table } from "../ui";

const POLL_MS = 2000;
const SOURCE: Record<DossierSection["source"], string | null> = {
  code: null,
  ai: "Written by the AI model",
  mixed: "Code and AI model",
};

function Section({ section }: { section: DossierSection }) {
  const source = SOURCE[section.source];
  return (
    <section className="flex min-w-0 flex-col gap-2 border-t border-line px-3 py-3 first:border-t-0">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="m-0 text-[13.5px] font-semibold text-ink">
          <span className="mr-2 font-mono text-[12px] text-ink-3 tabular-nums">
            {section.number}.
          </span>
          {section.title}
        </h3>
        {source && <span className="text-[11.5px] text-ink-3">{source}</span>}
      </header>
      {section.text && (
        <p className="m-0 max-w-[72ch] text-[13px] leading-relaxed text-ink">
          {section.text}
        </p>
      )}
      {section.items && section.items.length > 0 && (
        <dl className="m-0 grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-[max-content_1fr]">
          {section.items.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-[12.5px] text-ink-2">{label}</dt>
              <dd className="m-0 mb-1 text-[12.5px] text-ink sm:mb-0">
                {value}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {section.bullets && section.bullets.length > 0 && (
        <ul className="m-0 flex max-w-[72ch] flex-col gap-1 pl-4 text-[13px] leading-relaxed text-ink">
          {section.bullets.map((bullet, index) => (
            <li key={index}>{bullet}</li>
          ))}
        </ul>
      )}
      {section.table && section.table.rows.length > 0 && (
        <Table>
          <thead>
            <tr>
              {section.table.columns.map((column, index) => (
                <th key={index} className={index ? "right" : undefined}>
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {section.table.rows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, index) => (
                  <td key={index} className={index ? "right" : undefined}>
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {section.note && (
        <p className="m-0 text-[12px] text-ink-3">{section.note}</p>
      )}
    </section>
  );
}

/** The strategy's fifteen-point research dossier, with its AI notes and a Markdown export. */
export function DossierPanel({ specId }: { specId: string }) {
  const [dossier, setDossier] = useState<Dossier | null>(null);
  const [job, setJob] = useState<JobInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .labDossier(specId)
      .then(setDossier)
      .catch((err: Error) => setError(err.message));
  }, [specId]);
  useEffect(load, [load]);

  const running = job?.state === "running";
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      api
        .labJob()
        .then(({ job: latest }) => {
          setJob(latest);
          if (latest && latest.state !== "running") load();
        })
        .catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, load]);

  if (!dossier) return error ? <Alert tone="critical">{error}</Alert> : null;

  const write = () => {
    setError(null);
    api
      .labDossierNotes(specId)
      .then(() => api.labJob().then(({ job: latest }) => setJob(latest)))
      .catch((err: Error) => setError(err.message));
  };

  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2.5">
        <Button
          icon={Sparkles}
          disabled={running || !dossier.tested || !dossier.ai.enabled}
          onClick={write}
          title={
            !dossier.ai.enabled
              ? "The AI model is switched off"
              : !dossier.tested
                ? "Backtest the strategy first"
                : undefined
          }
        >
          {dossier.notes_written_at ? "Rewrite AI notes" : "Write AI notes"}
        </Button>
        <ButtonLink icon={Download} href={api.labDossierUrl(specId)} download>
          Download Markdown
        </ButtonLink>
        <span className="text-[12px] text-ink-3">
          {dossier.notes_written_at
            ? `AI notes written ${dateTimeIst(dossier.notes_written_at)}`
            : "Every figure comes from code. The AI writes only points 2, 14 and 15."}
        </span>
      </div>
      {error && (
        <div className="p-3">
          <Alert tone="critical">{error}</Alert>
        </div>
      )}
      {job && (
        <div className="p-3">
          <JobStatus job={job} />
        </div>
      )}
      {dossier.sections.map((section) => (
        <Section key={section.number} section={section} />
      ))}
    </div>
  );
}
