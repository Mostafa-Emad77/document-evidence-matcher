export default function Layout({ view, onNavigate, segmentCount, result, children }) {
  const navItems = [
    { id: 'upload',  label: 'Upload',  icon: 'upload_file' },
    { id: 'results', label: 'Results', icon: 'fact_check' },
    { id: 'history', label: 'History', icon: 'history' },
  ]

  return (
    <div className="flex min-h-screen bg-[#f7f9fb]">
      {/* ── Sidebar ── */}
      <nav className="hidden md:flex fixed left-0 top-0 h-full w-64 flex-col border-r border-slate-200 bg-white z-40">
        {/* Brand */}
        <div className="p-6 border-b border-slate-200">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-8 h-8 rounded-full bg-black flex items-center justify-center">
              <span className="material-symbols-outlined text-white text-sm icon-filled">smart_toy</span>
            </div>
            <div>
              <h1 className="font-serif font-black text-xl text-slate-900 leading-none">Semantic Evidence Bot</h1>
              <p className="text-[10px] uppercase tracking-wider text-slate-500 mt-0.5">AI-Powered Analysis</p>
            </div>
          </div>
          <button
            onClick={() => onNavigate('upload')}
            className="w-full bg-black text-white py-2 px-4 rounded text-xs font-semibold uppercase tracking-widest flex items-center justify-center gap-2 hover:bg-slate-800 transition-colors"
          >
            <span className="material-symbols-outlined text-sm">add</span>
            New Project
          </button>
        </div>

        {/* Nav links */}
        <ul className="flex-1 py-4 flex flex-col gap-0.5">
          {navItems.map(({ id, label, icon }) => {
            const active = view === id || (view === 'processing' && id === 'upload')
            return (
              <li key={id}>
                <button
                  onClick={() => onNavigate(id)}
                  className={[
                    'w-full flex items-center gap-3 px-6 py-3 text-left transition-all',
                    active
                      ? 'bg-slate-100 border-l-4 border-emerald-600 text-slate-900 font-bold'
                      : 'text-slate-500 hover:text-slate-900 hover:bg-slate-50 border-l-4 border-transparent',
                  ].join(' ')}
                >
                  <span className={`material-symbols-outlined text-xl ${active ? 'icon-filled' : ''}`}>{icon}</span>
                  <span className="text-xs uppercase tracking-widest font-serif">{label}</span>
                </button>
              </li>
            )
          })}
        </ul>
      </nav>

      {/* ── Main ── */}
      <div className="flex-1 flex flex-col md:ml-64">
        {/* Top bar */}
        <header className="flex justify-between items-center w-full px-8 py-3 bg-white border-b border-slate-200 z-30 sticky top-0">
          <div className="md:hidden font-serif font-bold text-lg text-slate-900">Semantic Evidence Bot</div>
          <div className="hidden md:block" />
          <div className="flex items-center gap-3">
            {view === 'results' && result && (
              <>
                <a
                  href="/api/output/docx"
                  download
                  className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-widest text-slate-900 border border-slate-300 px-3 py-2 rounded hover:bg-slate-50 transition-colors"
                >
                  <span className="material-symbols-outlined text-sm">description</span>
                  Export Annotated Doc
                </a>
                <a
                  href="/api/output/json"
                  download
                  className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-widest bg-black text-white px-3 py-2 rounded hover:bg-slate-800 transition-colors"
                >
                  <span className="material-symbols-outlined text-sm">code</span>
                  Export JSON
                </a>
              </>
            )}
            {(view === 'upload' || view === 'processing') && (
              <span className="text-sm font-serif text-slate-500">
                {segmentCount != null ? `${segmentCount} segments processed` : ''}
              </span>
            )}
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1">
          {children}
        </main>
      </div>
    </div>
  )
}
