import { useState, useEffect, useCallback } from 'react'
import Layout from './components/Layout'
import UploadPanel from './components/UploadPanel'
import ProcessingView from './components/ProcessingView'
import ResultsView from './components/ResultsView'
import HistoryView from './components/HistoryView'

export default function App() {
  const [view, setView] = useState('upload')      // 'upload' | 'processing' | 'results' | 'history'
  const [result, setResult] = useState(null)
  const [historyRuns, setHistoryRuns] = useState([])
  const [uploadedFileName, setUploadedFileName] = useState('')
  const [parseStartedAt, setParseStartedAt] = useState(null)
  const [error, setError] = useState(null)

  function refreshHistory() {
    fetch('/api/history?limit=50')
      .then((r) => r.ok ? r.json() : { runs: [] })
      .then((data) => setHistoryRuns(Array.isArray(data?.runs) ? data.runs : []))
      .catch(() => setHistoryRuns([]))
  }

  function refreshLatest() {
    fetch('/api/latest')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => setResult(data || null))
      .catch(() => setResult(null))
  }

  const navigate = useCallback(
    (id) => {
      if (id === 'results') {
        if (result) {
          setView('results')
          return
        }
        fetch('/api/latest')
          .then((r) => (r.ok ? r.json() : null))
          .then((data) => {
            if (data) {
              setResult(data)
              setView('results')
            }
          })
          .catch(() => {})
        return
      }
      setView(id)
    },
    [result],
  )

  // Restore last result from server storage so History/exports work after page reload
  useEffect(() => {
    refreshLatest()
    refreshHistory()
  }, [])

  async function handleUpload(payload) {
    setError(null)
    setUploadedFileName(payload.htmlFile.name)
    setParseStartedAt(Date.now())
    setView('processing')
    try {
      const form = new FormData()
      form.append('html_file', payload.htmlFile)
      if (payload.pdfFile) form.append('screenshots_pdf', payload.pdfFile)
      form.append('span_coloring_level', payload.spanColoringLevel || 'medium')
      const res = await fetch('/api/parse', { method: 'POST', body: form })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Server error ${res.status}`)
      }
      const data = await res.json()
      setResult(data)
      setParseStartedAt(null)
      refreshHistory()
      setView('results')
    } catch (e) {
      setError(e.message)
      setParseStartedAt(null)
      setView('upload')
    }
  }

  function handleHistoryDelete() {
    refreshHistory()
    refreshLatest()
  }

  const segmentCount = result?.segments?.length ?? null

  return (
    <Layout view={view} onNavigate={navigate} segmentCount={segmentCount} result={result}>
      {view === 'upload' && (
        <UploadPanel
          onUpload={handleUpload}
          error={error}
          hasStoredRun={historyRuns.length > 0 || !!result}
          onViewLastResults={() => navigate('results')}
        />
      )}
      {view === 'processing' && (
        <ProcessingView fileName={uploadedFileName} startedAt={parseStartedAt} />
      )}
      {view === 'results' && result && (
        <ResultsView result={result} />
      )}
      {view === 'history' && (
        <HistoryView result={result} historyRuns={historyRuns} onNavigate={setView} onDeleteRun={handleHistoryDelete} />
      )}
    </Layout>
  )
}
