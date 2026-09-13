import React, { useEffect, useRef, useState } from 'react';
import type { FormEvent, DragEvent } from 'react';
import { api, exportDownload } from './api';
import { date, Empty, friendly, Icon, Modal, short } from './components';
import type { Candidate, ContentView, Document, Event, EvidenceView, Run, RunMode, RunSummary,
  SystemInfo, Workspace } from './types';

type Tab = 'sources' | 'discovery' | 'assessment' | 'amplification' | 'provenance';
// Identifies which workspace/run selection an in-flight request was issued for.
type Mark = { workspace: number; run: number };
const tabs: { id: Tab; label: string; icon: string }[] = [
  { id: 'sources', label: 'Sources', icon: 'file' }, { id: 'discovery', label: 'Discovery', icon: 'search' },
  { id: 'assessment', label: 'Assessment', icon: 'shield' }, { id: 'amplification', label: 'Amplification', icon: 'spark' },
  { id: 'provenance', label: 'Provenance', icon: 'layers' },
];

export default function App() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState('');
  const [documents, setDocuments] = useState<Document[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [tab, setTab] = useState<Tab>('sources');
  const [busy, setBusy] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [creating, setCreating] = useState(false);
  const [evidence, setEvidence] = useState<EvidenceView | null>(null);
  const [content, setContent] = useState<ContentView | null>(null);
  const [dragging, setDragging] = useState(false);
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [mode, setMode] = useState<RunMode>('reference');
  const fileInput = useRef<HTMLInputElement>(null);
  // A reply that arrives after the user moved on must be discarded, never applied to the current view.
  // Every asynchronous handler marks the selection it belongs to and checks that mark before setting state.
  // Switching workspace advances both counters; selecting another run advances only the run counter, so a
  // pending document read stays valid across a run change but never survives a workspace change.
  const epoch = useRef<Mark>({ workspace: 0, run: 0 });
  const enterWorkspace = () => (epoch.current = { workspace: epoch.current.workspace + 1, run: epoch.current.run + 1 });
  const enterRun = () => (epoch.current = { ...epoch.current, run: epoch.current.run + 1 });
  const here = () => epoch.current;
  const sameWorkspace = (mark: Mark) => epoch.current.workspace === mark.workspace;
  const sameRun = (mark: Mark) => epoch.current.run === mark.run;
  const workspace = workspaces.find(w => w.id === workspaceId);
  const bundle = run?.result;
  const liveReady = !!system?.llm.enabled && (system?.modes ?? []).includes('live');
  const connectionLabel = liveReady
    ? `Bedrock · ${system?.llm.model || 'model not named'}`
    : 'No cloud connection';
  // A saved run keeps its own labelling: a reference run never reads as live because the
  // selector happens to be on Live AI.
  const bannerLive = bundle ? !!bundle.provider : mode === 'live';
  const bannerEngine = run?.engine_version || (mode === 'live' ? 'live-converse-v0.1' : system?.engine || 'reference-rules-v0.1');
  const running = !!busy || run?.status === 'running' || run?.status === 'pending';
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : 'The operation could not be completed.');

  useEffect(() => {
    let active = true;
    api<SystemInfo>('/system').then(info => {
      if (!active) return;
      setSystem(info);
      setMode(info.modes.includes(info.mode) ? info.mode : 'reference');
    }).catch(() => { /* The mode selector falls back to reference-only. */ });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    api<Workspace[]>('/workspaces').then(items => {
      if (!active) return;
      setWorkspaces(items);
      const saved = new URLSearchParams(window.location.search).get('workspace');
      setWorkspaceId(items.find(w => w.id === saved)?.id || items.at(-1)?.id || '');
    }).catch(fail).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!workspaceId) return;
    const mark = { ...enterWorkspace() };
    setLoading(true); setRun(null); setError(''); setNotice(''); setDocuments([]); setRuns([]); setEvents([]); setSelectedIds([]);
    setEvidence(null); setContent(null);
    window.history.replaceState(null, '', `?workspace=${workspaceId}`);
    Promise.all([api<Document[]>(`/workspaces/${workspaceId}/documents`), api<RunSummary[]>(`/workspaces/${workspaceId}/runs`)])
      .then(async ([docs, history]) => {
        const latest = history[0] ? await api<Run>(`/runs/${history[0].id}`) : null;
        const logs = await api<Event[]>(latest ? `/runs/${latest.id}/provenance` : `/workspaces/${workspaceId}/provenance`);
        if (!sameWorkspace(mark)) return;
        setDocuments(docs); setSelectedIds(docs.slice(0, 20).map(d => d.id)); setRuns(history); setRun(latest); setEvents(logs);
      }).catch(e => { if (sameWorkspace(mark)) fail(e); }).finally(() => { if (sameWorkspace(mark)) setLoading(false); });
  }, [workspaceId]);

  // A refreshed browser can observe a synchronous run still finishing on the server.
  // Poll existing state only: no background pipeline or fake progress percentage.
  useEffect(() => {
    if (!run || !['running', 'pending'].includes(run.status)) return;
    const polled = run.id;
    const mark = { ...here() };
    const timer = window.setInterval(async () => {
      try {
        const latest = await api<Run>(`/runs/${polled}`);
        if (!sameRun(mark)) return;
        setRun(latest);
        if (['running', 'pending'].includes(latest.status)) return;
        // The run just reached a terminal status. Bring its history summary and provenance up to date,
        // leaving the user's workspace, run and tab selection exactly as it is. The interval is already
        // cleared by the status change, so this last in-flight refresh must finish on the mark, not on
        // the effect's lifetime.
        const [history, logs] = await Promise.all([
          api<RunSummary[]>(`/workspaces/${latest.workspace_id}/runs`),
          api<Event[]>(`/runs/${polled}/provenance`),
        ]);
        if (!sameRun(mark)) return;
        setRuns(history); setEvents(logs);
      } catch (e) { if (sameRun(mark)) fail(e); }
    }, 1500);
    return () => clearInterval(timer);
  }, [run?.id, run?.status]);

  async function createWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy('Creating workspace…'); setError('');
    const form = new FormData(event.currentTarget);
    try {
      const item = await api<Workspace>('/workspaces', { method: 'POST', body: JSON.stringify({
        name: form.get('name'), focal_unit: form.get('focal_unit') || 'My workspace', goal: form.get('goal') || '',
      }) });
      setWorkspaces(previous => [...previous, item]); setWorkspaceId(item.id); setTab('sources'); setCreating(false);
    } catch (e) { fail(e); } finally { setBusy(''); }
  }
  async function refreshDocuments(mark: Mark) {
    const [docs, logs] = await Promise.all([
      api<Document[]>(`/workspaces/${workspaceId}/documents`),
      api<Event[]>(run ? `/runs/${run.id}/provenance` : `/workspaces/${workspaceId}/provenance`),
    ]);
    if (sameWorkspace(mark)) { setDocuments(docs); setSelectedIds(docs.slice(0, 20).map(d => d.id)); }
    if (sameRun(mark)) setEvents(logs);
  }
  async function upload(files: File[]) {
    if (!workspaceId || !files.length || running) return;
    const mark = { ...here() };
    setError(''); setNotice(''); let completed = 0;
    try {
      for (const [index, file] of files.entries()) {
        if (!sameWorkspace(mark)) return;
        setBusy(`Importing document ${index + 1} of ${files.length}…`);
        if (file.size > 5 * 1024 * 1024) throw new Error(`${file.name} exceeds the 5 MiB limit.`);
        const body = new FormData(); body.append('file', file);
        await api(`/workspaces/${workspaceId}/documents`, { method: 'POST', body }); completed += 1;
      }
      if (sameWorkspace(mark)) setNotice(`${completed} document${completed === 1 ? '' : 's'} imported. Select sources and run KDAA.`);
    } catch (e) { if (sameWorkspace(mark)) fail(e); } finally {
      try { await refreshDocuments(mark); } catch (e) { if (sameWorkspace(mark)) fail(e); }
      if (sameWorkspace(mark)) setBusy('');
      if (fileInput.current) fileInput.current.value = '';
    }
  }
  async function loadDemo() {
    const mark = { ...here() };
    setBusy('Loading fictional demo sources…'); setError(''); setNotice('');
    try {
      const samples = await api<{ filename: string; text: string }[]>('/demo');
      for (const sample of samples) {
        if (!sameWorkspace(mark)) return;
        const body = new FormData(); body.append('file', new File([sample.text], sample.filename, { type: 'text/markdown' }));
        await api(`/workspaces/${workspaceId}/documents`, { method: 'POST', body });
      }
      await refreshDocuments(mark);
      if (!sameWorkspace(mark)) return;
      setTab('sources');
      setNotice('Fictional demo documents imported through the normal upload path. Run KDAA to analyze their content.');
    } catch (e) { if (sameWorkspace(mark)) fail(e); } finally { if (sameWorkspace(mark)) setBusy(''); }
  }
  async function startRun() {
    const mark = { ...enterRun() };
    setBusy(mode === 'live' ? 'Asking the model to read your sources…'
                            : 'Running discovery → assessment → amplification…');
    setError(''); setNotice('');
    try {
      const result = await api<Run>(`/workspaces/${workspaceId}/runs`, { method: 'POST',
        body: JSON.stringify({ document_ids: selectedIds, mode }) });
      const [history, logs] = await Promise.all([
        api<RunSummary[]>(`/workspaces/${workspaceId}/runs`), api<Event[]>(`/runs/${result.id}/provenance`)]);
      if (!sameRun(mark)) return;
      setRun(result); setTab('discovery'); setRuns(history); setEvents(logs);
      if (result.error) setError(result.error.message);
    } catch (e) { if (sameRun(mark)) fail(e); } finally { if (sameRun(mark)) setBusy(''); }
  }
  async function chooseRun(id: string) {
    const mark = { ...enterRun() };
    setBusy('Opening saved run…'); setError('');
    try {
      const [selected, logs] = await Promise.all([api<Run>(`/runs/${id}`), api<Event[]>(`/runs/${id}/provenance`)]);
      if (!sameRun(mark)) return;
      setRun(selected); setEvents(logs);
    } catch (e) { if (sameRun(mark)) fail(e); } finally { if (sameRun(mark)) setBusy(''); }
  }
  async function inspectEvidence(id: string) {
    if (!run) return;
    const mark = { ...here() };
    const runId = run.id;
    try {
      const [view, logs] = await Promise.all([
        api<EvidenceView>(`/runs/${runId}/evidence/${id}`), api<Event[]>(`/runs/${runId}/provenance`)]);
      if (!sameRun(mark)) return;
      setEvidence(view); setEvents(logs);
    } catch (e) { if (sameRun(mark)) fail(e); }
  }
  async function viewDocument(id: string) {
    // Document content belongs to the workspace, not to the selected run, so a run change leaves it valid.
    const mark = { ...here() };
    try {
      const view = await api<ContentView>(`/documents/${id}/content`);
      if (sameWorkspace(mark)) setContent(view);
    } catch (e) { if (sameWorkspace(mark)) fail(e); }
  }
  async function download(format: 'json' | 'markdown') {
    if (!run) return;
    const mark = { ...here() };
    const runId = run.id;
    try {
      await exportDownload(runId, format);
      const logs = await api<Event[]>(`/runs/${runId}/provenance`);
      if (sameRun(mark)) setEvents(logs);
    } catch (e) { if (sameRun(mark)) fail(e); }
  }
  const evidenceButtons = (candidate: Candidate) => <div className="evidence-links">{candidate.evidence_ids.map((id, i) =>
    <button className="text-button" key={id} onClick={() => inspectEvidence(id)} aria-label={`Inspect evidence ${i + 1} for ${candidate.title}`}>
      <Icon name="file" size={14}/> Evidence {i + 1}<span aria-hidden="true">↗</span>
    </button>)}</div>;
  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault(); setDragging(false); void upload(Array.from(event.dataTransfer.files));
  }

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="/" aria-label="KDAA home"><span className="brand-mark">K</span><span>KDAA<span className="brand-sub">Knowledge workspace</span></span></a>
      <div className="sidebar-label">YOUR WORKSPACES <span>{workspaces.length}</span></div>
      <nav className="workspace-nav" aria-label="Workspaces">
        {workspaces.map(item => <button key={item.id} disabled={!!busy} className={`workspace-link ${workspaceId === item.id ? 'active' : ''}`} onClick={() => { setWorkspaceId(item.id); setTab('sources'); }}>
          <Icon name="layers" size={17}/><span>{item.name}</span>{workspaceId === item.id && <span className="active-dot"/>}
        </button>)}
      </nav>
      <button className="new-workspace" disabled={!!busy} onClick={() => setCreating(true)}><Icon name="plus" size={17}/> New workspace</button>
      <div className="sidebar-bottom"><div className={`local-label ${liveReady ? 'live' : ''}`}><span className="status-dot"/> {liveReady ? 'LOCAL + LIVE AI EDITION' : 'LOCAL REFERENCE EDITION'}</div><p>Keep the source.<br/>Question the claim.<br/>Explore the possibility.</p><a href="/docs" target="_blank" rel="noreferrer"><Icon name="code" size={16}/> API reference <span>↗</span></a><small>v0.1.0 · Single-user, local only</small></div>
    </aside>
    <main>
      <div className="topbar"><span>KNOWLEDGE DISCOVERY, ASSESSMENT &amp; AMPLIFICATION</span>
        <span className={`local-pill ${liveReady ? 'live' : ''}`}><span className="status-dot"/>{connectionLabel}</span></div>
      <div className="main-content">
        <header className="page-head"><div><div className="eyebrow">EVIDENCE FIRST. POSSIBILITIES NEXT.</div><h1>{workspace?.name || 'Make more of what you know.'}</h1><p>{workspace?.goal || (workspace ? 'Explore what your source material could become.' : 'Find candidate knowledge assets in your documents — and keep every claim connected to its source.')}</p></div>
          {workspace && <div className="head-actions">
            <div className="mode-select" role="radiogroup" aria-label="Analysis engine">
              {(['reference', 'live'] as RunMode[]).map(option => {
                const available = option === 'reference' || liveReady;
                return <button key={option} role="radio" aria-checked={mode === option} disabled={!available || running || loading}
                  className={`mode-option ${mode === option ? 'selected' : ''}`} onClick={() => setMode(option)}
                  title={available ? undefined : 'Live AI is not configured on this server. See the README to enable Bedrock.'}>
                  <Icon name={option === 'live' ? 'spark' : 'shield'} size={15}/>{option === 'live' ? 'Live AI' : 'Reference'}
                </button>;
              })}
            </div>
            <button className="button secondary" disabled={running || loading} onClick={loadDemo}><Icon name="layers" size={17}/> Load demo</button><button className="button primary" disabled={running || loading || !selectedIds.length} onClick={startRun}><Icon name={running ? 'clock' : 'arrow'} size={18}/>{running ? 'Working…' : mode === 'live' ? 'Run live AI' : 'Run KDAA'}</button></div>}
        </header>
        <div className={`reference-banner ${bannerLive ? 'live' : ''}`}><Icon name={bannerLive ? 'spark' : 'shield'} size={18}/><div><strong>{bannerLive ? 'Live AI mode' : 'Reference mode'}</strong><span>{bannerLive
          ? 'A language model reads your sources. Every quotation is re-checked against the stored text; unresolved ones are rejected. Candidates and drafts are provisional, not confirmed capabilities, and no score is produced.'
          : 'Input-driven rules, not live AI. All candidates are provisional. This is not a reproduction of Paper B.'}</span></div><code>{bannerEngine}</code></div>
        {error && <div className="alert error" role="alert"><span>{error}</span><button className="icon-button" aria-label="Dismiss error" onClick={() => setError('')}><Icon name="close" size={16}/></button></div>}
        {notice && <div className="alert notice" role="status"><Icon name="check" size={16}/>{notice}</div>}
        {busy && <div className="progress" role="status"><span className="spinner"/>{busy}</div>}
        {!workspace ? <section className="welcome panel"><div className="welcome-content"><div className="eyebrow">START WITH YOUR SOURCES</div><h2>A document is a trace.<br/>Its possibilities are worth exploring.</h2><p>Create a workspace, add a few documents, and follow the path from cited evidence to provisional candidates and practical next steps.</p><button className="button primary" onClick={() => setCreating(true)} disabled={loading}><Icon name="plus" size={18}/> Create your first workspace</button></div><div className="welcome-steps">{[{ n: '01', name: 'Discover', copy: 'Surface candidates from actual source text.' }, { n: '02', name: 'Assess', copy: 'Inspect support, relevance, and missing evidence.' }, { n: '03', name: 'Amplify', copy: 'Propose a next step with a human verification gate.' }].map(s => <div key={s.n}><span>{s.n}</span><section><h3>{s.name}</h3><p>{s.copy}</p></section></div>)}</div></section> : <>
          <div className="metrics"><div className="metric"><span className="metric-icon"><Icon name="file"/></span><div><span className="metric-label">Source documents</span><strong>{documents.length}<small>{selectedIds.length} selected for the next run</small></strong></div></div>
            <div className="metric"><span className="metric-icon"><Icon name="search"/></span><div><span className="metric-label">Provisional candidates</span><strong>{bundle ? bundle.hypotheses.length : '—'}<small>{bundle ? 'In the selected run · not confirmed assets' : 'Run discovery to inspect candidates'}</small></strong></div></div>
            <div className="metric"><span className="metric-icon"><Icon name="shield"/></span><div><span className="metric-label">Distinct quoted excerpts</span><strong>{bundle ? bundle.unique_excerpt_count : '—'}<small>Traceable support, not independent proof</small></strong></div></div></div>
          <div className="run-toolbar"><div><Icon name="clock" size={17}/><label htmlFor="history">Run history</label><select id="history" aria-label="Run history" disabled={!runs.length || !!busy} value={run?.id || ''} onChange={e => chooseRun(e.target.value)}>{!runs.length && <option value="">No runs yet</option>}{runs.map(r => <option key={r.id} value={r.id}>{date(r.created_at)} · {r.status} · {short(r.id)}</option>)}</select></div><div className="exports"><span>Export</span><button disabled={!run || !!busy} onClick={() => download('json')} aria-label="Export JSON">JSON</button><button disabled={!run || !!busy} onClick={() => download('markdown')} aria-label="Export Markdown">Markdown<Icon name="download" size={14}/></button></div></div>
          <section className="panel workspace-panel">
            <div className="tabs" role="tablist" aria-label="KDAA workflow">{tabs.map(t => <button key={t.id} id={`tab-${t.id}`} role="tab" aria-selected={tab === t.id} aria-controls="workspace-tabpanel" className={tab === t.id ? 'selected' : ''} onClick={() => setTab(t.id)}><Icon name={t.icon} size={16}/>{t.label}{t.id === 'sources' && <span>{documents.length}</span>}</button>)}</div>
            <div className="tab-content" id="workspace-tabpanel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
              {loading ? <Empty title="Opening workspace…" icon="clock">Loading saved documents and runs.</Empty> : tab === 'sources' ? <>
                <div className="section-head"><div><h2>Build an evidence base</h2><p>Import source traces. They are not automatically validated knowledge assets.</p></div><span className="subtle">{workspace.focal_unit}</span></div>
                <div className={`dropzone ${dragging ? 'dragging' : ''}`} onDragOver={e => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
                  <span className="upload-symbol"><Icon name="upload" size={24}/></span><div><strong>Drop your documents here</strong><p>TXT, Markdown, text-based PDF, or DOCX · up to 5 MiB each</p></div><button className="button secondary" disabled={running} onClick={() => fileInput.current?.click()}>Browse files</button><input ref={fileInput} type="file" multiple accept=".txt,.md,.pdf,.docx" aria-label="Upload documents" onChange={e => upload(Array.from(e.target.files || []))} hidden/>
                </div>
                {documents.length ? <><div className="source-list-head"><span>{selectedIds.length} selected · maximum 20 documents / 250,000 characters per run</span><button className="text-button" disabled={running} onClick={() => setSelectedIds(selectedIds.length ? [] : documents.slice(0, 20).map(d => d.id))}>{selectedIds.length ? 'Clear selection' : 'Select first 20'}</button></div><div className="source-list">{documents.map((doc, index) => {
                  const duplicate = documents.slice(0, index).some(d => d.text_sha256 === doc.text_sha256);
                  return <div className="source-row" key={doc.id}><input type="checkbox" aria-label={`Select ${doc.filename}`} checked={selectedIds.includes(doc.id)} disabled={running} onChange={e => setSelectedIds(prev => e.target.checked ? (prev.length < 20 ? [...prev, doc.id] : prev) : prev.filter(id => id !== doc.id))}/><span className="file-type">{doc.filename.split('.').at(-1)?.toUpperCase()}</span><div className="source-name"><button onClick={() => viewDocument(doc.id)}>{doc.filename}</button><span>{doc.character_count.toLocaleString()} characters · {Math.max(1, Math.round(doc.byte_count / 1024))} KB · {date(doc.created_at)}</span>{doc.warnings.map(w => <small key={w}>{w}</small>)}</div><span className={`badge ${duplicate ? 'neutral' : 'green'}`}>{duplicate ? 'Duplicate text' : 'Source stored'}</span><button className="icon-button" aria-label={`Read ${doc.filename}`} onClick={() => viewDocument(doc.id)}><Icon name="search" size={17}/></button></div>;
                })}</div></> : <Empty title="No source documents yet" icon="file">Upload a document or use <strong>Load demo</strong> to try three fictional examples.</Empty>}
                <p className="footnote">Original files and normalized text are stored separately. Duplicate imports remain auditable, but identical normalized text is analyzed only once.</p>
              </> : tab === 'provenance' ? <>
                <div className="section-head"><div><h2>The record behind the result</h2><p>Imports, source reads, stage completion, and exports. Application traceability — not tamper-proof certification.</p></div><span className="badge neutral">{events.length} events</span></div>
                <div className="event-list">{events.map((event, index) => <div className="event-row" key={event.id}><div className="event-track"><span>{index + 1}</span></div><div className="event-body"><div><strong>{friendly(event.action)}</strong><time>{date(event.timestamp)}</time></div><p>{event.actor} · <code>{short(event.id)}</code>{event.source_ids.length > 0 && ` · ${event.source_ids.length} source reference(s)`}</p><details><summary>Event details</summary><pre>{JSON.stringify(event, null, 2)}</pre></details></div></div>)}</div>
                {!events.length && <Empty title="No events yet">An event trail appears as you import sources and run KDAA.</Empty>}
              </> : !bundle ? <Empty title={run?.error ? 'This run did not complete' : 'Start with a reference run'} icon="search">{run?.error?.message || 'Select source documents and choose Run KDAA. Discovery, assessment, and amplification will appear here.'}</Empty> : <>
                <div className="section-head"><div><h2>{tab === 'discovery' ? 'Candidate knowledge assets' : tab === 'assessment' ? 'What the evidence does — and does not — support' : 'A useful next step, not a promised outcome'}</h2><p>{tab === 'discovery' ? 'Bounded hypotheses proposed from explicit source references. Inspect the evidence before acting.' : tab === 'assessment' ? 'Separate exact traceability, lexical goal relevance, and unresolved verification needs.' : 'Type-specific proposals stay linked to their sources and require human verification.'}</p></div><span className="badge neutral">{bundle.hypotheses.length} candidates</span></div>
                <p className="snapshot-note"><Icon name="clock" size={14}/> This saved run uses {bundle.input_document_count} document(s), {bundle.unique_text_count} unique text(s). {bundle.duplicate_text_count > 0 ? `${bundle.duplicate_text_count} duplicate(s) not counted again.` : ''} Later imports do not change it.</p>
                {!bundle.hypotheses.length && <Empty title="No supported candidates under these rules" icon="search">This does not mean the sources have no value. The reference engine recognizes a small set of explicit English terms, not all knowledge or meaning.</Empty>}
                <div className="candidate-list">{bundle.hypotheses.map((candidate, index) => {
                  const assessment = bundle.assessments.find(a => a.candidate_id === candidate.id)!;
                  const action = bundle.amplifications.find(a => a.candidate_id === candidate.id)!;
                  const firstEvidence = bundle.evidence.find(e => e.id === candidate.evidence_ids[0]);
                  return <article className="candidate-card" key={candidate.id}><div className="candidate-top"><span className="candidate-number">{String(index + 1).padStart(2, '0')}</span><span className={`badge type-${candidate.candidate_type}`}>{friendly(candidate.candidate_type)}</span><span className="provisional">Provisional · ownership unresolved</span></div><h3>{candidate.title}</h3>
                    {tab === 'discovery' && <><p>{candidate.claim}</p>{firstEvidence && <blockquote>{firstEvidence.quote}</blockquote>}{candidate.matched_terms.length > 0 && <div className="trigger-terms"><span>Literal matches</span>{candidate.matched_terms.map(term => <code key={term}>{term}</code>)}</div>}
                      {candidate.discovery_basis === 'model_reading' && <p className="subtle">Found by a model reading the source, not by literal term matching.</p>}</>}
                    {tab === 'assessment' && <><div className="assessment-grid"><div><span>Source traceability</span><strong><Icon name="check" size={16}/> Exact quotes verified</strong></div><div><span>Distinct excerpts</span><strong>{assessment.distinct_excerpt_count} <small>not independent proof</small></strong></div><div><span>Goal relevance</span><strong>{assessment.semantic ? 'Explained below' : friendly(assessment.goal_relevance)}</strong><small>{assessment.semantic ? 'a model reading, not a score' : assessment.overlapping_goal_terms.join(', ') || 'lexical overlap only'}</small></div></div><p>{assessment.semantic ? assessment.semantic.goal_relevance_explanation : assessment.explanation}</p>
                      {assessment.semantic && assessment.semantic.possible_uses.length > 0 && <div className="verification"><strong>Possible uses</strong><ul>{assessment.semantic.possible_uses.map(text => <li key={text}>{text}</li>)}</ul></div>}
                      {assessment.semantic && assessment.semantic.limitations.length > 0 && <div className="verification"><strong>Limitations</strong><ul>{assessment.semantic.limitations.map(text => <li key={text}>{text}</li>)}</ul></div>}
                      <div className="verification"><strong>Still to verify</strong><ul>{assessment.missing_evidence.map(text => <li key={text}>{text}</li>)}</ul></div></>}
                    {tab === 'amplification' && <><div className="proposal"><span>PROPOSED ARTIFACT</span><h4>{action.proposed_artifact}</h4><p>{action.next_action}</p></div>
                      {action.draft && <div className="draft">
                        <div className="draft-head"><span className="badge amber">GENERATED DRAFT</span><strong>{action.draft.title}</strong></div>
                        <p className="draft-note">{action.draft.provenance_note}</p>
                        <pre className="draft-body">{action.draft.content}</pre>
                        {action.draft.proposed_elements.length > 0 && <div className="verification"><strong>Proposed, not supported by the quoted evidence</strong><ul>{action.draft.proposed_elements.map(text => <li key={text}>{text}</li>)}</ul></div>}
                        {action.draft.unresolved_evidence_refs.length > 0 && <div className="verification"><strong>Grounding references that did not resolve</strong><p className="subtle">The model cited {action.draft.unresolved_evidence_refs.length} evidence reference(s) that are not verified evidence for this candidate. They are recorded, not substituted.</p><ul>{action.draft.unresolved_evidence_refs.map((ref, i) => <li key={`${ref}-${i}`}><code>{ref}</code></li>)}</ul></div>}
                        <p className="subtle">{action.draft.grounded_evidence_ids.length > 0 ? <>Grounded in {action.draft.grounded_evidence_ids.length} verified excerpt(s)</> : <><strong>Cites no verified evidence.</strong> Treat the whole draft as proposed content.</>} · generated by <code>{action.draft.generated_by}</code></p>
                      </div>}
                      <div className="verification"><strong>Human verification gate</strong><p>{action.verification_gate}</p></div><p className="subtle">{action.baseline}</p></>}
                    {evidenceButtons(candidate)}
                  </article>;
                })}</div><details className="limitations"><summary>{bundle.provider ? 'Model, limitations and rejected quotations' : 'Reference rules and limitations'}</summary>
                  {bundle.notes.map(n => <p key={n}>{n}</p>)}
                  {bundle.provider && <p className="subtle">Produced by <code>{bundle.provider.provider}</code> · model <code>{bundle.provider.model}</code>{bundle.provider.region && <> · region <code>{bundle.provider.region}</code></>} · {bundle.provider.calls} model call(s){Object.keys(bundle.provider.usage).length > 0 && <> · tokens {Object.entries(bundle.provider.usage).map(([k, v]) => `${k}=${v}`).join(', ')}</>}</p>}
                  {bundle.rejections.length > 0 && <div className="rejections"><strong>{bundle.rejections.length} model quotation(s) rejected by evidence validation</strong><ul>{bundle.rejections.map((r, i) => <li key={`${r.quote_sha256}-${i}`}><code>{r.reason}</code> {r.candidate_title && <>· {r.candidate_title} </>}· {r.detail}{r.quote_preview && <blockquote>{r.quote_preview}</blockquote>}</li>)}</ul></div>}
                  </details>
              </>}
            </div>
          </section>
        </>}
        <footer className="page-footer"><span>KDAA · Trace → Hypothesis → Assessment → Opportunity</span><span>Local files. Inspectable rules. No hidden model calls.</span></footer>
      </div>
    </main>
    {creating && <Modal title="Create a workspace" onClose={() => { if (!busy) setCreating(false); }}><form onSubmit={createWorkspace} className="workspace-form">{error && <div className="error-banner" role="alert">{error}</div>}<p>A workspace keeps a bounded set of sources, a goal, and its own run history.</p><label>Workspace name<input name="name" required maxLength={120} placeholder="e.g. Research assets" autoFocus/></label><label>Focal unit<input name="focal_unit" maxLength={200} defaultValue="My research group" placeholder="Whose knowledge are we exploring?"/></label><label>What could this material help you do?<textarea name="goal" maxLength={2000} rows={3} placeholder="e.g. Develop a reproducible teaching exercise from existing software and notes."/></label><p className="footnote">Optional goal; only literal term overlap is assessed in reference mode.</p><div className="modal-actions"><button type="button" className="button secondary" disabled={!!busy} onClick={() => setCreating(false)}>Cancel</button><button className="button primary" type="submit" disabled={!!busy}>Create workspace<Icon name="arrow" size={17}/></button></div></form></Modal>}
    {evidence && <Modal title="Evidence inspector" onClose={() => setEvidence(null)}><div className="inspector-meta"><span className="badge green"><Icon name="check" size={14}/> Exact quote verified</span><strong>{evidence.filename}</strong><p>Normalized text · paragraph {evidence.evidence.paragraph} · offsets [{evidence.evidence.start}, {evidence.evidence.end})</p></div><pre className="source-context"><span>{evidence.before}</span><mark>{evidence.quote}</mark><span>{evidence.after}</span></pre><div className="inspector-details"><p><strong>Locator</strong> {evidence.locator_unit}. These are not PDF page coordinates.</p><p><strong>Normalized SHA-256</strong><code>{evidence.evidence.text_sha256}</code></p><p><strong>Evidence ID</strong><code>{evidence.evidence.id}</code></p><p className="footnote">An exact quotation verifies source linkage, not the truth of the claim or ownership of a capability.</p></div></Modal>}
    {content && <Modal title="Stored source text" onClose={() => setContent(null)}><div className="inspector-meta"><strong>{content.filename}</strong><p>{content.normalization_version}</p><a className="text-button" href={`/api/v1/documents/${content.document_id}/download`} download>Download original<Icon name="download" size={15}/></a></div><pre className="source-context full-source">{content.text}</pre><div className="inspector-details"><p><strong>Normalized SHA-256</strong><code>{content.text_sha256}</code></p></div></Modal>}
  </div>;
}
