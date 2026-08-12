import * as React from "react"
import { useQuery } from "@tanstack/react-query"
import { RiArrowLeftSLine } from "@remixicon/react"
import { Link, useParams } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { compareDocument, getDocument } from "@/lib/api"

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "cancelled"])

function RunCard({ run }) {
  return (
    <section className="rounded-2xl border bg-card p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold capitalize">{run.provider}</h2>
          <p className="mt-1 text-xs text-muted-foreground">{run.model_id}</p>
        </div>
        <span className="rounded-full bg-secondary px-2 py-0.5 text-xs capitalize">
          {run.status}
        </span>
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-3 text-xs">
        <div>
          <dt className="text-muted-foreground">Latency</dt>
          <dd className="mt-1 font-medium">
            {run.latency_ms === null ? "—" : `${run.latency_ms} ms`}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Tokens</dt>
          <dd className="mt-1 font-medium">
            {run.input_tokens === null
              ? "—"
              : `${run.input_tokens + (run.output_tokens ?? 0)}`}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Quality flags</dt>
          <dd className="mt-1 font-medium">{run.quality_issue_count ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Human corrections</dt>
          <dd className="mt-1 font-medium">{run.correction_count ?? "—"}</dd>
        </div>
      </dl>
    </section>
  )
}

export function ComparePage() {
  const { documentId } = useParams()
  const [started, setStarted] = React.useState(false)
  const documentQuery = useQuery({
    queryKey: ["document", documentId],
    queryFn: ({ signal }) => getDocument(documentId, { signal }),
  })
  const comparisonQuery = useQuery({
    queryKey: ["comparison", documentId],
    queryFn: () => compareDocument(documentId),
    enabled: started,
    refetchInterval: (query) => {
      const runs = query.state.data?.runs || []
      return runs.length === 2 &&
        runs.every((run) => TERMINAL_STATUSES.has(run.status))
        ? false
        : 2_000
    },
  })

  return (
    <section className="mx-auto max-w-4xl">
      <Button
        className="mb-5 -ml-2"
        nativeButton={false}
        render={<Link to="/" />}
        variant="ghost"
      >
        <RiArrowLeftSLine /> Back
      </Button>
      <p className="text-xs font-medium tracking-wider text-muted-foreground uppercase">
        Development only
      </p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight">
        Compare models
      </h1>
      <p className="mt-2 truncate text-sm text-muted-foreground">
        {documentQuery.data?.original_filename || "Loading document…"}
      </p>

      {!started ? (
        <Button
          className="mt-6"
          disabled={documentQuery.isPending || Boolean(documentQuery.error)}
          onClick={() => setStarted(true)}
          size="lg"
        >
          Start comparison
        </Button>
      ) : comparisonQuery.isPending ? (
        <p className="mt-6 text-sm text-muted-foreground">
          Queuing comparison…
        </p>
      ) : comparisonQuery.error ? (
        <p className="mt-6 text-sm text-destructive" role="alert">
          {comparisonQuery.error.message}
        </p>
      ) : (
        <>
          <div className="mt-6 grid gap-5 md:grid-cols-2">
            {comparisonQuery.data.runs.map((run) => (
              <RunCard key={run.run_id} run={run} />
            ))}
          </div>
          {comparisonQuery.data.agreement ? (
            <p className="mt-5 rounded-xl border bg-card p-4 text-sm">
              Exact observation agreement:{" "}
              <span className="font-semibold">
                {Math.round(comparisonQuery.data.agreement.match_rate * 100)}%
              </span>{" "}
              ({comparisonQuery.data.agreement.matching_observations} of{" "}
              {comparisonQuery.data.agreement.compared_observations})
            </p>
          ) : null}
        </>
      )}
    </section>
  )
}
