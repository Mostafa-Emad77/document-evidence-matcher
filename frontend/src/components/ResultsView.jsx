import { useState } from 'react'
import SegmentMatchCard from './SegmentMatchCard'
import { formatParseDuration } from '../formatDuration'

const FILTERS = [
  { id: 'all',    label: 'All' },
  { id: 'high',   label: 'High Confidence' },
  { id: 'review', label: 'Needs Review' },
  { id: 'noimg',  label: 'No Image' },
]

const NER_COLOR_CLASSES = [
  'bg-amber-100 border-amber-300 text-amber-900',
  'bg-cyan-100 border-cyan-300 text-cyan-900',
  'bg-emerald-100 border-emerald-300 text-emerald-900',
  'bg-fuchsia-100 border-fuchsia-300 text-fuchsia-900',
  'bg-slate-200 border-slate-300 text-slate-900',
  'bg-sky-100 border-sky-300 text-sky-900',
  'bg-rose-100 border-rose-300 text-rose-900',
]

function scoreLabel(score) {
  if (score == null)  return 'no-match'
  if (score >= 0.80)  return 'high'
  if (score >= 0.55)  return 'review'
  return 'no-match'
}

function hasImage(segment) {
  return segment.matched_images?.length > 0 && segment.matched_images[0]?.thumbnail_base64
}

function nerColorClass(groupId = '') {
  const match = /^g(\d+)$/i.exec(groupId || '')
  const idx = match ? Math.max(0, Number(match[1]) - 1) : 0
  return NER_COLOR_CLASSES[idx % NER_COLOR_CLASSES.length]
}

function clip(text = '', max = 120) {
  if (!text) return ''
  return text.length > max ? `${text.slice(0, max)}…` : text
}

function buildNerData(ql) {
  const spans = Array.isArray(ql?.claim_spans) ? ql.claim_spans : []
  const linksByKey = new Map()

  spans.forEach((span, idx) => {
    const desc = (span?.description_unit || ql?.description_text?.slice(span?.description_start, span?.description_end) || '').trim()
    const quote = (span?.quote_span_text || ql?.quote_text?.slice(span?.quote_start, span?.quote_end) || '').trim()
    if (!desc || !quote) return

    const groupId = span?.group_id || `g${idx + 1}`
    const key = `${groupId}::${desc}::${quote}`
    if (!linksByKey.has(key)) {
      linksByKey.set(key, {
        groupId,
        desc,
        quote,
        quoteStart: Number.isFinite(span?.quote_start) ? span.quote_start : Number.MAX_SAFE_INTEGER,
      })
    }
  })

  const links = Array.from(linksByKey.values()).sort((a, b) => a.quoteStart - b.quoteStart)
  const entities = Array.from(new Set(links.map((l) => l.desc)))
  return { links, entities }
}

export default function ResultsView({ result }) {
  const [filter, setFilter]     = useState('all')
  const [tab, setTab]           = useState('evidence')   // 'evidence' | 'descriptions'

  // build citation image lookup once
  const citationImageByText = new Map(
    (result.citation_groups || [])
      .filter((c) => c?.citation_text && c?.images?.length)
      .map((c) => [c.citation_text, c.images[0]])
  )

  // Only show quote segments in main grid (they carry evidence); narration goes to separate section
  const allSegments = result.segments || []
  const quoteSegments = allSegments.filter((s) => s.segment_type === 'quote')
  const total = quoteSegments.length
  const parseTimeLabel = formatParseDuration(result?.parse_duration_seconds)

  const filtered = quoteSegments.filter((s) => {
    if (filter === 'all')    return true
    if (filter === 'high')   return scoreLabel(s.match_score) === 'high'
    if (filter === 'review') return scoreLabel(s.match_score) === 'review'
    if (filter === 'noimg')  return !hasImage(s)
    return true
  })

  return (
    <div className="flex-1 flex flex-col">
      {/* Sticky results header */}
      <div className="sticky top-[57px] bg-white border-b border-slate-200 z-20 px-8 py-3 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <h2 className="font-serif text-2xl font-medium text-slate-900">Results Review</h2>
          <span className="text-xs font-semibold bg-slate-100 text-slate-500 px-2 py-1 rounded">
            {total} Quote Segments
          </span>
          {parseTimeLabel && (
            <span
              className="text-xs font-semibold bg-emerald-50 text-emerald-800 border border-emerald-200 px-2 py-1 rounded tabular-nums"
              title="Server time for pipeline + document generation"
            >
              Parse: {parseTimeLabel}
            </span>
          )}
        </div>
        {/* Tab switcher */}
        <div className="flex items-center gap-1 border border-slate-200 rounded-lg p-0.5 bg-slate-50">
          <button
            onClick={() => setTab('evidence')}
            className={[
              'flex items-center gap-1.5 px-4 py-1.5 rounded text-xs font-semibold uppercase tracking-widest transition-colors',
              tab === 'evidence' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700',
            ].join(' ')}
          >
            <span className="material-symbols-outlined text-sm">image_search</span>
            Matched Evidence
          </button>
          <button
            onClick={() => setTab('descriptions')}
            className={[
              'flex items-center gap-1.5 px-4 py-1.5 rounded text-xs font-semibold uppercase tracking-widest transition-colors',
              tab === 'descriptions' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700',
            ].join(' ')}
          >
            <span className="material-symbols-outlined text-sm">menu_book</span>
            Description Pairs
            {result.quote_table?.length > 0 && (
              <span className="ml-1 bg-emerald-100 text-emerald-800 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                {result.quote_table.length}
              </span>
            )}
          </button>
        </div>
      </div>

      <div className="p-8 max-w-[1440px] mx-auto w-full">

        {/* ── TAB: Matched Evidence ── */}
        {tab === 'evidence' && (
          <>
            {/* Filter pills */}
            <div className="flex flex-wrap gap-2 mb-6 pb-6 border-b border-slate-200">
              {FILTERS.map(({ id, label }) => (
                <button
                  key={id}
                  onClick={() => setFilter(id)}
                  className={[
                    'px-4 py-2 rounded-full text-xs font-semibold uppercase tracking-widest transition-colors',
                    filter === id
                      ? 'bg-black text-white'
                      : 'border border-slate-300 text-slate-700 hover:bg-slate-50',
                  ].join(' ')}
                >
                  {label}
                </button>
              ))}
            </div>

            {filtered.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-24 text-slate-400">
                <span className="material-symbols-outlined text-6xl mb-4">search_off</span>
                <p className="text-lg">No segments match this filter.</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6">
                {filtered.map((seg) => (
                  <SegmentMatchCard
                    key={seg.segment_id}
                    segment={seg}
                    citationImageByText={citationImageByText}
                    listView={false}
                  />
                ))}
              </div>
            )}
          </>
        )}

        {/* ── TAB: Description–Quote Pairs ── */}
        {tab === 'descriptions' && (
          <>
            {result.quote_table?.length > 0 ? (
              <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
                <div className="px-6 py-4 border-b border-slate-200 bg-slate-50 flex items-center gap-3">
                  <span className="material-symbols-outlined text-slate-500">menu_book</span>
                  <span className="font-semibold text-slate-900">Description–Quote Pairs</span>
                  <span className="text-xs font-semibold bg-slate-200 text-slate-600 px-2 py-0.5 rounded ml-1">
                    {result.quote_table.length} pairs
                  </span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-slate-50 border-b border-slate-200">
                      <tr>
                        <th className="text-left px-6 py-3 text-xs font-semibold uppercase tracking-widest text-slate-500 w-[36%]">Description</th>
                        <th className="text-left px-6 py-3 text-xs font-semibold uppercase tracking-widest text-slate-500 w-[36%]">Quote</th>
                        <th className="text-left px-6 py-3 text-xs font-semibold uppercase tracking-widest text-slate-500 w-[28%]">NER Links</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.quote_table.map((ql, i) => {
                        const { links, entities } = buildNerData(ql)
                        return (
                          <tr key={ql.quote_id} className={i % 2 === 0 ? 'bg-white' : 'bg-slate-50'}>
                            <td className="px-6 py-4 text-slate-600 align-top border-b border-slate-100">{ql.description_text}</td>
                            <td className="px-6 py-4 text-slate-900 italic align-top border-b border-slate-100">{ql.quote_text}</td>
                            <td className="px-6 py-4 align-top border-b border-slate-100">
                              {links.length > 0 ? (
                                <div className="flex flex-col gap-3">
                                  <div className="flex flex-wrap gap-1.5">
                                    {entities.map((entity, entityIdx) => (
                                      <span
                                        key={`${ql.quote_id}-entity-${entityIdx}`}
                                        className="text-[11px] font-medium px-2 py-0.5 rounded-full border border-slate-300 bg-slate-100 text-slate-700"
                                      >
                                        {entity}
                                      </span>
                                    ))}
                                  </div>
                                  <div className="flex flex-col gap-1.5">
                                    {links.map((link, linkIdx) => (
                                      <div key={`${ql.quote_id}-link-${linkIdx}`} className="text-xs text-slate-700 leading-snug">
                                        <span className={`inline-block px-1.5 py-0.5 rounded border mr-1.5 font-semibold ${nerColorClass(link.groupId)}`}>
                                          {link.groupId.toUpperCase()}
                                        </span>
                                        <span className="font-medium text-slate-900">{clip(link.desc, 60)}</span>
                                        <span className="mx-1 text-slate-400">→</span>
                                        <span className="text-slate-600">{clip(link.quote, 80)}</span>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              ) : (
                                <span className="text-xs text-slate-400">No extracted entity links</span>
                              )}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center py-24 text-slate-400">
                <span className="material-symbols-outlined text-6xl mb-4">menu_book</span>
                <p className="text-lg">No description–quote pairs found.</p>
              </div>
            )}
          </>
        )}

      </div>
    </div>
  )
}

