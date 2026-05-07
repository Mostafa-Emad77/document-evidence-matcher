import { useRef, useState } from 'react'

export default function UploadPanel({
  onUpload,
  error,
  saveDocxArtifact = null,
  hasStoredRun = false,
  onViewLastResults = null,
}) {
  const htmlInputRef = useRef(null)
  const pdfInputRef  = useRef(null)
  const [htmlFile, setHtmlFile] = useState(null)
  const [pdfFile,  setPdfFile]  = useState(null)
  const [htmlDrag, setHtmlDrag] = useState(false)
  const [highlightDensity, setHighlightDensity] = useState(1) // 0=minimal, 1=medium, 2=maximum, 3=dense

  function handleHtmlChange(e) { setHtmlFile(e.target.files?.[0] || null) }
  function handlePdfChange(e)  { setPdfFile(e.target.files?.[0] || null) }

  function handleHtmlDrop(e) {
    e.preventDefault()
    setHtmlDrag(false)
    const file = e.dataTransfer.files?.[0]
    if (file && (file.name.endsWith('.htm') || file.name.endsWith('.html'))) setHtmlFile(file)
  }

  function handleSubmit() {
    if (!htmlFile) return
    const levelMap = ['minimal', 'medium', 'maximum', 'dense']
    onUpload({ htmlFile, pdfFile, spanColoringLevel: levelMap[highlightDensity] })
  }

  const canParse = !!htmlFile

  return (
    <div className="p-8 max-w-[1200px] mx-auto">
      {/* Page header */}
      <div className="mb-8 pb-6 border-b border-slate-200">
        <h2 className="font-serif text-5xl font-semibold text-slate-900 mb-3 tracking-tight">Document Upload</h2>
        <p className="text-lg text-slate-500 max-w-2xl leading-relaxed">
          Upload your document to automatically identify and highlight key claims and evidence.
          A Word-exported HTML file is required. You can optionally include a PDF with screenshots
          for visual reference.
        </p>
      </div>

      {/* Error banner */}
      {error && (
        <div className="mb-6 flex items-start gap-3 bg-[#ffdad6] border border-[#ba1a1a]/20 rounded-lg px-4 py-3">
          <span className="material-symbols-outlined text-[#ba1a1a] text-xl mt-0.5">error</span>
          <p className="text-sm text-[#93000a] font-medium">{error}</p>
        </div>
      )}

      {hasStoredRun && typeof onViewLastResults === 'function' && (
        <div className="mb-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 bg-emerald-50 border border-emerald-200 rounded-lg px-4 py-3">
          <div className="flex items-start gap-3">
            <span className="material-symbols-outlined text-emerald-800 text-xl mt-0.5">check_circle</span>
            <p className="text-sm text-emerald-950 font-medium">
              A result is already saved on the server. You can open it again without re-uploading the HTML.
            </p>
          </div>
          <button
            type="button"
            onClick={onViewLastResults}
            className="shrink-0 text-xs font-semibold uppercase tracking-widest bg-emerald-800 text-white px-4 py-2 rounded hover:bg-emerald-900 transition-colors"
          >
            View last results
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left: drop zones */}
        <div className="lg:col-span-8 flex flex-col gap-6">

          {/* HTML drop zone */}
          <div
            className={[
              'bg-white border rounded-lg p-8 relative cursor-pointer group transition-colors',
              htmlDrag ? 'border-black bg-slate-50' : 'border-slate-200 hover:border-slate-400',
            ].join(' ')}
            onClick={() => !htmlFile && htmlInputRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setHtmlDrag(true) }}
            onDragLeave={() => setHtmlDrag(false)}
            onDrop={handleHtmlDrop}
          >
            <span className="absolute top-4 right-4 text-[10px] font-semibold uppercase tracking-widest bg-slate-100 text-slate-500 px-2 py-1 rounded">
              Required
            </span>

            {htmlFile ? (
              <div className="flex items-center gap-4 bg-slate-50 border border-slate-200 rounded-lg p-4">
                <div className="w-12 h-12 bg-slate-900 flex items-center justify-center rounded-lg shrink-0">
                  <span className="material-symbols-outlined text-white icon-filled">code_blocks</span>
                </div>
                <div className="flex-1 min-w-0">
                  <p className="font-semibold text-slate-900 truncate">{htmlFile.name}</p>
                  <p className="text-sm text-slate-500 mt-0.5">{(htmlFile.size / 1024).toFixed(0)} KB • Loaded</p>
                </div>
                <button
                  className="text-[#ba1a1a] hover:bg-[#ffdad6] p-2 rounded transition-colors"
                  onClick={(e) => { e.stopPropagation(); setHtmlFile(null); if (htmlInputRef.current) htmlInputRef.current.value = '' }}
                  title="Remove"
                >
                  <span className="material-symbols-outlined">delete</span>
                </button>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center text-center py-8">
                <div className="w-16 h-16 rounded-full bg-slate-100 flex items-center justify-center mb-4 group-hover:bg-slate-200 transition-colors">
                  <span className="material-symbols-outlined text-slate-700 text-3xl">code_blocks</span>
                </div>
                <h3 className="font-serif text-2xl font-medium text-slate-900 mb-2">Source HTML File</h3>
                <p className="text-slate-500 mb-6 max-w-sm">Drag and drop your Word-exported HTML file here, or click to browse.</p>
                <button
                  className="bg-white border border-slate-300 text-slate-900 text-xs font-semibold uppercase tracking-widest px-6 py-2 rounded hover:bg-slate-50 transition-colors"
                  onClick={(e) => { e.stopPropagation(); htmlInputRef.current?.click() }}
                >
                  Select File
                </button>
              </div>
            )}
          </div>

          {/* PDF drop zone */}
          <div className="bg-white border border-slate-200 rounded-lg p-6 relative">
            <span className="absolute top-4 right-4 text-[10px] font-semibold uppercase tracking-widest bg-slate-100 text-slate-500 px-2 py-1 rounded">
              Optional
            </span>

            {pdfFile ? (
              <div className="flex items-center gap-4 bg-slate-50 border border-slate-200 rounded-lg p-4">
                <div className="w-12 h-12 bg-emerald-700 flex items-center justify-center rounded-lg shrink-0">
                  <span className="material-symbols-outlined text-white icon-filled">picture_as_pdf</span>
                </div>
                <div className="flex-1 min-w-0">
                  <p className="font-semibold text-slate-900 truncate">{pdfFile.name}</p>
                  <p className="text-sm text-slate-500 mt-0.5">{(pdfFile.size / 1024 / 1024).toFixed(1)} MB • Uploaded just now</p>
                </div>
                <button
                  className="text-[#ba1a1a] hover:bg-[#ffdad6] p-2 rounded transition-colors"
                  onClick={() => { setPdfFile(null); if (pdfInputRef.current) pdfInputRef.current.value = '' }}
                  title="Remove"
                >
                  <span className="material-symbols-outlined">delete</span>
                </button>
              </div>
            ) : (
              <div
                className="flex flex-col items-center justify-center text-center py-6 cursor-pointer"
                onClick={() => pdfInputRef.current?.click()}
              >
                <div className="w-12 h-12 rounded-full bg-slate-100 flex items-center justify-center mb-3">
                  <span className="material-symbols-outlined text-slate-400 text-2xl">picture_as_pdf</span>
                </div>
                <p className="text-slate-500 text-sm mb-3">Reference Screenshots PDF (for visual citation matching)</p>
                <button className="bg-white border border-slate-300 text-slate-900 text-xs font-semibold uppercase tracking-widest px-5 py-2 rounded hover:bg-slate-50 transition-colors">
                  Select PDF
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Right: action panel */}
        <div className="lg:col-span-4 flex flex-col gap-4">
          {/* Action card */}
          <div className="bg-slate-100 border border-slate-200 rounded-lg p-6">
            <h3 className="font-serif text-2xl font-medium text-slate-900 mb-2">Ready to Parse</h3>
            <p className="text-sm text-slate-500 mb-4 leading-relaxed">
              Ensure all files meet the required formatting standards before proceeding.
            </p>
            <p className="text-[10px] uppercase tracking-wider text-slate-400 mb-4 leading-relaxed">
              {saveDocxArtifact === true ? (
                <span className="italic">
                  Stored outputs: JSON plus annotated Word (with highlights and matched screenshots where available).
                </span>
              ) : saveDocxArtifact === false ? (
                <span>
                  Stored output: <strong className="text-slate-600">JSON only</strong> on this server. Annotated Word is
                  off — set <code className="text-[10px] bg-slate-200 px-1 rounded">AUTOCON_SAVE_DOCX=true</code> to
                  also save .docx files.
                </span>
              ) : (
                <span className="text-slate-400">Checking server storage settings…</span>
              )}
            </p>
            <div className="space-y-2 mb-6">
              <div className="flex justify-between items-center py-2 border-b border-slate-200">
                <span className="text-sm text-slate-500">Source File</span>
                <span className={`text-sm font-semibold ${htmlFile ? 'text-emerald-700' : 'text-[#ba1a1a]'}`}>
                  {htmlFile ? 'Ready' : 'Missing'}
                </span>
              </div>
              <div className="flex justify-between items-center py-2 border-b border-slate-200">
                <span className="text-sm text-slate-500">Linked PDF Reference</span>
                <span className={`text-sm font-semibold ${pdfFile ? 'text-emerald-700' : 'text-slate-400'}`}>
                  {pdfFile ? 'Attached' : 'None'}
                </span>
              </div>
            </div>

            {/* Highlight Density Slider */}
            <div className="mb-6">
              <div className="flex justify-between items-center mb-2">
                <span className="text-sm font-semibold text-slate-700">Highlight Density</span>
                <span className="text-xs font-medium text-slate-500">
                  {highlightDensity === 0
                    ? 'Minimal'
                    : highlightDensity === 1
                    ? 'Medium'
                    : highlightDensity === 2
                    ? 'Maximum'
                    : 'Dense'}
                </span>
              </div>
              <input
                type="range"
                min="0"
                max="3"
                step="1"
                value={highlightDensity}
                onChange={(e) => setHighlightDensity(parseInt(e.target.value))}
                className="w-full h-2 bg-slate-300 rounded-lg appearance-none cursor-pointer accent-black"
              />
              <div className="grid grid-cols-4 text-[10px] text-slate-400 mt-1">
                <span>Minimal</span>
                <span className="text-center">Medium</span>
                <span className="text-center">Maximum</span>
                <span className="text-right">Dense</span>
              </div>
              <p className="text-xs text-slate-500 mt-2 leading-relaxed">
                {highlightDensity === 0 
                  ? 'Strict: Only the strongest semantic matches. Higher precision, fewer highlights.'
                  : highlightDensity === 1
                  ? 'Balanced: Good coverage with reasonable precision.'
                  : highlightDensity === 2
                  ? 'Permissive: Maximum highlights including weaker semantic matches.'
                  : 'High recall: extracts the most concept links between description and quote, including looser semantic matches. Higher coverage, more highlights.'}
              </p>
            </div>
            <button
              onClick={handleSubmit}
              disabled={!canParse}
              className={[
                'w-full py-4 rounded text-xs font-semibold uppercase tracking-widest flex items-center justify-center gap-2 transition-all',
                canParse
                  ? 'bg-black text-white hover:bg-slate-800'
                  : 'bg-slate-300 text-slate-400 cursor-not-allowed',
              ].join(' ')}
            >
              <span className="material-symbols-outlined text-lg">play_arrow</span>
              Start Parsing
            </button>
          </div>

          {/* Guidelines card */}
          <div className="bg-white border border-slate-200 rounded-lg p-5">
            <h4 className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-4 flex items-center gap-2">
              <span className="material-symbols-outlined text-sm text-slate-400">info</span>
              Formatting Guidelines
            </h4>
            <ul className="space-y-4">
              <li className="flex gap-3">
                <span className="material-symbols-outlined text-emerald-700 text-sm mt-0.5 shrink-0">check_circle</span>
                <div>
                  <p className="text-sm font-semibold text-slate-900">Clean HTML Required</p>
                  <p className="text-sm text-slate-500 mt-1">Export directly from Word using 'Web Page, Filtered' for best results.</p>
                </div>
              </li>
              <li className="flex gap-3">
                <span className="material-symbols-outlined text-emerald-700 text-sm mt-0.5 shrink-0">check_circle</span>
                <div>
                  <p className="text-sm font-semibold text-slate-900">Remove Track Changes</p>
                  <p className="text-sm text-slate-500 mt-1">Accept all changes and remove comments before exporting.</p>
                </div>
              </li>
            </ul>
          </div>
        </div>
      </div>

      <input ref={htmlInputRef} type="file" accept=".htm,.html" className="hidden" onChange={handleHtmlChange} />
      <input ref={pdfInputRef}  type="file" accept=".pdf"       className="hidden" onChange={handlePdfChange} />
    </div>
  )
}

