import { useEffect, useRef, useState } from 'react'

const STEPS = [
  'Extracting Narration',
  'Linking Citations',
  'Matching Screenshots',
  'Generating Final Report',
]

export default function ProcessingView({ fileName, startedAt = null }) {
  const [stepIndex, setStepIndex] = useState(0)
  const [elapsedSec, setElapsedSec] = useState(0)
  const intervalRef = useRef(null)

  // Live elapsed clock while parsing (parent sets startedAt when POST begins).
  useEffect(() => {
    if (startedAt == null) {
      setElapsedSec(0)
      return undefined
    }
    const updateElapsed = () =>
      setElapsedSec(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)))
    updateElapsed()
    const id = setInterval(updateElapsed, 500)
    return () => clearInterval(id)
  }, [startedAt])

  // Advance steps while the API call is in flight (illustrative only).
  useEffect(() => {
    intervalRef.current = setInterval(() => {
      setStepIndex((prev) => (prev < STEPS.length - 1 ? prev + 1 : prev))
    }, 12000)
    return () => clearInterval(intervalRef.current)
  }, [])

  // Derived progress percentage (capped; real completion is when the API returns).
  const pct = Math.min(95, Math.round(((stepIndex + 1) / STEPS.length) * 100))

  // SVG circular progress constants
  const r   = 45
  const circ = 2 * Math.PI * r          // ≈ 282.7
  const offset = circ * (1 - pct / 100)

  return (
    <div className="flex-1 flex flex-col items-center justify-center min-h-[80vh] px-8">
      {/* Header */}
      <div className="mb-10 text-center">
        <h1 className="font-serif text-3xl font-semibold text-slate-900 mb-2">Parsing Document</h1>
        <p className="text-lg text-slate-500">{fileName || 'article.html'}</p>
      </div>

      {/* Circular progress */}
      <div className="relative w-56 h-56 mb-10">
        <svg className="w-full h-full -rotate-90" viewBox="0 0 100 100">
          <circle cx="50" cy="50" r={r} fill="none" stroke="#e0e3e5" strokeWidth="2" />
          <circle
            cx="50" cy="50" r={r}
            fill="none"
            stroke="#006c4a"
            strokeWidth="2"
            strokeDasharray={circ}
            strokeDashoffset={offset}
            strokeLinecap="round"
            style={{ transition: 'stroke-dashoffset 1.2s ease' }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="font-serif text-5xl font-semibold text-slate-900 leading-none">{pct}%</span>
          <span className="text-xs font-semibold uppercase tracking-widest text-slate-500 mt-2">Complete</span>
        </div>
      </div>

      {/* Step list */}
      <div className="w-full max-w-sm bg-white border border-slate-200 rounded-xl p-5 mb-8 shadow-sm">
        <div className="flex flex-col gap-4">
          {STEPS.map((label, i) => {
            const done    = i < stepIndex
            const active  = i === stepIndex
            const pending = i > stepIndex
            return (
              <div key={label} className={`flex items-center gap-4 ${pending ? 'opacity-40' : ''}`}>
                {done ? (
                  <span className="material-symbols-outlined text-emerald-700 icon-filled text-xl shrink-0">check_circle</span>
                ) : (
                  <span className="material-symbols-outlined text-slate-400 text-xl shrink-0">radio_button_unchecked</span>
                )}
                <span className={`text-base ${active ? 'font-semibold text-slate-900' : 'text-slate-600'}`}>
                  {label}{active ? '…' : ''}
                </span>
                {active && (
                  <span className="ml-auto flex gap-1">
                    {[0,1,2].map((d) => (
                      <span
                        key={d}
                        className="w-1.5 h-1.5 rounded-full bg-emerald-700 animate-bounce"
                        style={{ animationDelay: `${d * 0.15}s` }}
                      />
                    ))}
                  </span>
                )}
              </div>
            )
          })}
        </div>
      </div>

      <div className="text-center space-y-1">
        {startedAt != null && (
          <p className="text-sm font-semibold text-slate-700 tabular-nums">
            Elapsed: {elapsedSec}s
          </p>
        )}
        <p className="text-sm text-slate-400">
          Typical runs are about 1–3 minutes for long documents (AI matching and evidence).
        </p>
      </div>
    </div>
  )
}
