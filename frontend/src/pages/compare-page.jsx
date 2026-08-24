import * as React from "react"
import { useQuery } from "@tanstack/react-query"
import { RiArrowLeftSLine } from "@remixicon/react"
import { Link, useParams } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { compareDocument, getDocument } from "@/lib/api"
import { adaptivePollingInterval } from "@/lib/polling"

const TERMINAL_STATUSES = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "needs_confirmation",
  "unsupported",
])
const COMPARISON_COMPLETE = new Set(["succeeded"])

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
          <dt className="text-muted-foreground">Estimated cost</dt>
          <dd className="mt-1 font-medium">
            {run.estimated_cost_usd === null
              ? "—"
              : `$${Number(run.estimated_cost_usd).toFixed(6)}`}
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

function ObservationDifferences({ agreement }) {
  if (!agreement.difference_count) {
    return (
      <p className="mt-5 rounded-xl border bg-card p-4 text-sm">
        No observation differences found.
      </p>
    )
  }
  return (
    <section className="mt-5 overflow-hidden rounded-xl border bg-card">
      <div className="border-b px-4 py-3">
        <h2 className="font-semibold">Observation differences</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          {agreement.difference_count > agreement.differences.length
            ? `Showing ${agreement.differences.length} of ${agreement.difference_count}`
            : `${agreement.difference_count} differing observations`}
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-xl text-left text-xs">
          <thead>
            <tr className="border-b text-muted-foreground">
              <th className="px-4 py-2 font-medium">Observation</th>
              <th className="px-4 py-2 text-center font-medium">Gemini</th>
              <th className="px-4 py-2 text-center font-medium">OpenAI</th>
            </tr>
          </thead>
          <tbody>
            {agreement.differences.map((difference, index) => (
              <tr
                className="border-b bg-amber-500/5 last:border-b-0"
                key={`${difference.kind}-${difference.label}-${difference.value}-${index}`}
              >
                <td className="px-4 py-3">
                  <p className="font-medium">
                    {difference.label || difference.kind.replaceAll("_", " ")}
                  </p>
                  <p className="mt-1 break-words text-muted-foreground">
                    {difference.value}
                  </p>
                </td>
                <td className="px-4 py-3 text-center font-medium">
                  {difference.gemini_count}
                </td>
                <td className="px-4 py-3 text-center font-medium">
                  {difference.openai_count}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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
      const comparisonStatus =
        runs.length === 2 &&
        runs.every((run) => TERMINAL_STATUSES.has(run.status))
          ? "succeeded"
          : "processing"
      return adaptivePollingInterval(
        {
          state: {
            ...query.state,
            data: query.state.data ? { status: comparisonStatus } : undefined,
          },
        },
        COMPARISON_COMPLETE
      )
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
      <h1 className="text-3xl font-semibold tracking-tight">Compare models</h1>
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
            <>
              <p className="mt-5 rounded-xl border bg-card p-4 text-sm">
                Exact observation agreement:{" "}
                <span className="font-semibold">
                  {Math.round(comparisonQuery.data.agreement.match_rate * 100)}%
                </span>{" "}
                ({comparisonQuery.data.agreement.matching_observations} of{" "}
                {comparisonQuery.data.agreement.compared_observations})
              </p>
              <ObservationDifferences
                agreement={comparisonQuery.data.agreement}
              />
            </>
          ) : null}
        </>
      )}
    </section>
  )
}
