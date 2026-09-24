import { Link, useParams } from "react-router-dom";
import { useApi, useApiMutation } from "../api/hooks";
import type { ApplicationDetail as Detail } from "../api/types";
import { CaseHeader, DecisionsCard, HistoryCard, ScoreCards } from "../components/CaseView";
import { Button, ErrorBox, Loading, PageHeader } from "../components/ui";

export default function ApplicationDetail() {
  const { id } = useParams();
  const app = useApi<Detail>(`/applications/${id}`);
  const appeal = useApiMutation<{ _id: number }>("POST", (b) => `/applications/${b._id}/appeal`, ["/applications", "/reviews", "/dashboard", "/me"]);

  if (app.error) return <ErrorBox error={app.error} />;
  if (!app.data) return <Loading lines={8} />;
  const a = app.data;
  const inQueue = a.status === "in_review" || a.status === "appealed";
  return (
    <>
      <PageHeader title={`Application #${a.application_id}`} subtitle={<Link to="/applications">← All applications</Link>}
        actions={<>
          {a.status === "deferred" && (
            <Button kind="primary" disabled={appeal.isPending} onClick={() => appeal.mutate({ _id: a.application_id })}>
              {appeal.isPending ? "Recording…" : "Record an appeal"}
            </Button>
          )}
          {inQueue && <Link className="btn primary" to={`/review?id=${a.application_id}`}>Open in review queue</Link>}
          <Link className="btn" to={`/households?id=${a.household_id}`}>Household</Link>
        </>} />
      {appeal.error && <ErrorBox error={appeal.error} />}
      {appeal.isSuccess && <div className="callout info">Appeal recorded. The application is back in the review queue.</div>}
      <CaseHeader a={a} />
      <ScoreCards a={a} />
      <div className="grid cols-2">
        <HistoryCard a={a} />
        <DecisionsCard a={a} />
      </div>
    </>
  );
}
