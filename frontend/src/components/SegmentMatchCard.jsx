function scoreInfo(score) {
  if (score == null || score < 0.55) return { label: 'No Match', color: 'bg-[#ffdad6] text-[#ba1a1a] border border-[#ba1a1a]/20' }
  if (score < 0.80)                  return { label: 'Needs Review', color: 'bg-amber-100 text-amber-800 border border-amber-200' }
  return                              { label: 'High Confidence', color: 'bg-emerald-100 text-emerald-800 border border-emerald-200' }
}

export default function SegmentMatchCard({ segment, citationImageByText = new Map(), listView = false }) {
  const {
    segment_id,
    segment_type,
    segment_text,
    matched_citation_text,
    match_score,
    matched_images = [],
  } = segment

  const { label, color } = scoreInfo(match_score)

  const thumbB64 = matched_images[0]?.thumbnail_base64 ||
    (matched_citation_text ? citationImageByText.get(matched_citation_text)?.thumbnail_base64 : null)
  const imgSrc = thumbB64 ? `data:image/png;base64,${thumbB64}` : null

  const quote = segment_text?.length > 200 ? segment_text.slice(0, 200) + '…' : segment_text

  if (listView) {
    return (
      <div className="bg-white border border-slate-200 rounded-lg flex gap-4 overflow-hidden hover:shadow-sm transition-shadow">
        <div className="w-24 h-20 shrink-0 bg-slate-100 flex items-center justify-center overflow-hidden">
          {imgSrc ? (
            <img src={imgSrc} alt="" className="w-full h-full object-cover grayscale" />
          ) : (
            <span className="material-symbols-outlined text-slate-300 text-2xl">image_not_supported</span>
          )}
        </div>
        <div className="flex-1 min-w-0 py-3 pr-4">
          <div className="flex items-center gap-2 mb-1">
            <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap ${color}`}>{label}</span>
            <span className="text-[10px] uppercase tracking-wider text-slate-400 font-medium">{segment_id}</span>
          </div>
          <p className="text-sm text-slate-900 italic leading-snug line-clamp-2">"{quote}"</p>
          {matched_citation_text && (
            <p className="text-xs text-slate-400 mt-1 truncate">↳ {matched_citation_text}</p>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="bg-white border border-slate-200 rounded-xl overflow-hidden flex flex-col hover:shadow-md transition-shadow group">
      <div className="relative h-48 bg-slate-100 flex items-center justify-center overflow-hidden">
        {imgSrc ? (
          <img
            src={imgSrc}
            alt="Evidence screenshot"
            className="w-full h-full object-cover grayscale group-hover:scale-105 transition-transform duration-300"
          />
        ) : (
          <div className="flex flex-col items-center justify-center gap-2">
            <span className="material-symbols-outlined text-slate-300 text-5xl">image_not_supported</span>
            <span className="text-xs text-slate-400">No visual evidence</span>
          </div>
        )}
        <span className={`absolute top-3 right-3 text-[10px] font-semibold px-2 py-1 rounded-full backdrop-blur-sm ${color}`}>
          {label}
        </span>
        {match_score != null && (
          <span className="absolute bottom-3 left-3 text-[10px] font-semibold bg-black/50 text-white px-2 py-0.5 rounded">
            {(match_score * 100).toFixed(0)}%
          </span>
        )}
      </div>

      <div className="flex-1 flex flex-col p-5">
        <div className="flex items-center gap-2 mb-3">
          <span className="material-symbols-outlined text-slate-400 text-sm">label</span>
          <span className="text-[10px] font-semibold uppercase tracking-widest text-slate-400">{segment_id}</span>
          <span className="ml-auto text-[10px] uppercase tracking-wider text-slate-400 capitalize">{segment_type}</span>
        </div>

        <blockquote className="text-sm text-slate-800 italic leading-relaxed flex-1 mb-3 line-clamp-4">
          "{quote}"
        </blockquote>

        {matched_citation_text && (
          <div className="pt-3 border-t border-slate-100">
            <p className="text-[11px] text-slate-400 font-medium uppercase tracking-wider mb-0.5">Matched Citation</p>
            <p className="text-xs text-slate-600 line-clamp-2">{matched_citation_text}</p>
          </div>
        )}
      </div>
    </div>
  )
}

