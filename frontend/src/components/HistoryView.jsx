import { useState } from 'react'
import { formatParseDuration } from '../formatDuration'

function formatTimestamp(value) {
  if (!value) return 'Unknown date'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return 'Unknown date'
  return parsed.toLocaleString('en-GB', {
    year: 'numeric', month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

export default function HistoryView({ result, historyRuns = [], onNavigate, onDeleteRun }) {
  const hasHistory = historyRuns.length > 0
  const [deletingRunId, setDeletingRunId] = useState(null)
  const [deleteError, setDeleteError] = useState('')

  // Keep current-session result as a fallback only when history API is empty.
  const fallbackRuns = result ? [{
    run_id: null,
    source_file: result?.metadata?.source_file || 'Parsed Document',
    segment_count: result?.segments?.length ?? 0,
    quote_count: result?.segments?.filter((s) => s.segment_type === 'quote').length ?? 0,
    parse_duration_seconds: result?.parse_duration_seconds ?? null,
    created_at: new Date().toISOString(),
    json_available: true,
    docx_available: true,
  }] : []

  const runs = hasHistory ? historyRuns : fallbackRuns
  const hasResult = runs.length > 0

  async function handleDelete(runId) {
    if (!runId) return
    const ok = window.confirm('Delete this stored run? This cannot be undone.')
    if (!ok) return

    setDeleteError('')
    setDeletingRunId(runId)
    try {
      const res = await fetch(`/api/history/${runId}`, { method: 'DELETE' })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Delete failed (${res.status})`)
      }
      onDeleteRun?.(runId)
    } catch (err) {
      setDeleteError(err.message || 'Delete failed')
    } finally {
      setDeletingRunId(null)
    }
  }

  return (
    <div className="p-8 max-w-[1200px] mx-auto">
      {/* Page header */}
      <div className="mb-8 pb-6 border-b border-slate-200">
        <h2 className="font-serif text-5xl font-semibold text-slate-900 mb-3 tracking-tight">Archive Ledger</h2>
        <p className="text-lg text-slate-500 max-w-2xl leading-relaxed">
          Review all parse operations and export processed artifacts. Download annotated documents
          and structured JSON outputs from your historical sessions.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left: Active Configuration */}
        <div className="lg:col-span-4 flex flex-col gap-4">
          <div className="bg-white border border-slate-200 rounded-xl p-6">
            <h3 className="font-serif text-xl font-medium text-slate-900 mb-4">Active Configuration</h3>

            <div className="mb-5">
              <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 mb-2">Target Formats</p>
              <div className="flex flex-wrap gap-2">
                <span className="text-xs font-semibold bg-slate-100 border border-slate-200 text-slate-700 px-3 py-1 rounded-full">JSON_RAW</span>
                <span className="text-xs font-semibold bg-slate-100 border border-slate-200 text-slate-700 px-3 py-1 rounded-full">Annotated Doc</span>
              </div>
            </div>

            <div className="mb-5">
              <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 mb-2">Confidence Threshold</p>
              <div className="flex items-center gap-3">
                <input type="range" min="0" max="100" defaultValue="55" className="flex-1 accent-emerald-700" />
                <span className="text-sm font-semibold text-slate-700 w-8 text-right">55%</span>
              </div>
              <div className="flex justify-between text-[10px] text-slate-400 mt-1">
                <span>Low</span><span>High</span>
              </div>
            </div>

            <div className="mb-5">
              <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 mb-2">Storage Destination</p>
              <p className="text-sm text-slate-600">Local Disk (backend/storage)</p>
            </div>

            {/* System status */}
            <div className="bg-slate-50 border border-slate-200 rounded-lg px-4 py-3 flex items-center gap-3">
              <span className="relative flex h-2.5 w-2.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-500 opacity-75" />
                <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-600" />
              </span>
              <span className="text-xs font-semibold text-slate-700">Parser Online</span>
              <span className="ml-auto text-[10px] text-slate-400">v1.0</span>
            </div>
          </div>
        </div>

        {/* Right: Processed Artifacts */}
        <div className="lg:col-span-8">
          <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
              <h3 className="font-serif text-xl font-medium text-slate-900">Processed Artifacts</h3>
              {hasResult && (
                <span className="text-xs font-semibold bg-slate-100 text-slate-500 px-2 py-1 rounded">
                  {runs.length} Result{runs.length === 1 ? '' : 's'}
                </span>
              )}
            </div>

            {deleteError && (
              <div className="px-6 py-3 text-xs text-red-700 bg-red-50 border-b border-red-100">
                {deleteError}
              </div>
            )}

            {hasResult ? (
              <div className="divide-y divide-slate-100">
                {runs.map((run) => {
                  const fileName = run.source_file || 'Parsed Document'
                  const quoteCount = Number.isFinite(run.quote_count) ? run.quote_count : null
                  const segCount = Number.isFinite(run.segment_count) ? run.segment_count : null
                  const hasRunId = !!run.run_id
                  const parseLabel = formatParseDuration(run.parse_duration_seconds)

                  const jsonHref = hasRunId
                    ? `/api/output/json/${run.run_id}`
                    : '/api/output/json'
                  const docxHref = hasRunId
                    ? `/api/output/docx/${run.run_id}`
                    : '/api/output/docx'

                  return (
                    <div key={run.run_id || `${fileName}-${run.created_at}`} className="flex items-start gap-4 px-6 py-5 hover:bg-slate-50 transition-colors">
                      <div className="w-10 h-10 bg-slate-900 flex items-center justify-center rounded-lg shrink-0">
                        <span className="material-symbols-outlined text-white icon-filled text-sm">description</span>
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1 flex-wrap">
                          <span className="font-semibold text-slate-900 truncate">{fileName}</span>
                          <span className="text-[10px] font-semibold bg-emerald-100 text-emerald-800 border border-emerald-200 px-2 py-0.5 rounded-full whitespace-nowrap">
                            Stored
                          </span>
                        </div>
                        <p className="text-xs text-slate-500 mb-1">{formatTimestamp(run.created_at)}</p>
                        <p className="text-xs text-slate-400">
                          {quoteCount == null || segCount == null
                            ? 'Counts unavailable for this older run'
                            : `${quoteCount} quotes matched · ${segCount} total segments`}
                          {parseLabel && (
                            <span className="text-slate-500"> · Parse {parseLabel}</span>
                          )}
                        </p>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <a
                          href={jsonHref}
                          download
                          className="flex items-center gap-1.5 text-xs font-semibold border border-slate-300 text-slate-700 px-3 py-1.5 rounded hover:bg-slate-50 transition-colors"
                        >
                          <span className="material-symbols-outlined text-sm">download</span>
                          JSON
                        </a>
                        {run.docx_available ? (
                          <a
                            href={docxHref}
                            download
                            className="flex items-center gap-1.5 text-xs font-semibold bg-black text-white px-3 py-1.5 rounded hover:bg-slate-800 transition-colors"
                          >
                            <span className="material-symbols-outlined text-sm">download</span>
                            Annotated Doc
                          </a>
                        ) : (
                          <span className="text-xs text-slate-400 border border-slate-200 px-3 py-1.5 rounded">
                            No DOCX
                          </span>
                        )}
                        {hasRunId && (
                          <button
                            type="button"
                            onClick={() => handleDelete(run.run_id)}
                            disabled={deletingRunId === run.run_id}
                            className="flex items-center gap-1.5 text-xs font-semibold border border-red-200 text-red-700 px-3 py-1.5 rounded hover:bg-red-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            <span className="material-symbols-outlined text-sm">delete</span>
                            {deletingRunId === run.run_id ? 'Deleting' : 'Delete'}
                          </button>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              /* Empty state */
              <div className="flex flex-col items-center justify-center py-20 text-center px-8">
                <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mb-4">
                  <span className="material-symbols-outlined text-slate-400 text-3xl">archive</span>
                </div>
                <h4 className="font-serif text-xl font-medium text-slate-900 mb-2">No parse operations yet</h4>
                <p className="text-slate-500 text-sm mb-6 max-w-xs">
                  Start by uploading a document. Processed results will appear here for download.
                </p>
                <button
                  onClick={() => onNavigate('upload')}
                  className="bg-black text-white text-xs font-semibold uppercase tracking-widest px-6 py-3 rounded hover:bg-slate-800 transition-colors"
                >
                  Upload Document
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
