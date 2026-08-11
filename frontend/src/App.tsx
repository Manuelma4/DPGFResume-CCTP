import {
  AlertCircle,
  ArrowLeft,
  Building2,
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleUserRound,
  Clock3,
  Download,
  ExternalLink,
  FileCheck2,
  FilePlus2,
  FileSpreadsheet,
  FileText,
  FolderClock,
  History,
  LayoutDashboard,
  LoaderCircle,
  Lock,
  LogOut,
  Plus,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  Sparkles,
  Trash2,
  UploadCloud,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ChangeEvent, DragEvent, FormEvent, ReactNode } from 'react';
import {
  ApiError,
  createAnalysis,
  deleteAnalysis,
  generateExport,
  getAnalysis,
  getCurrentUser,
  listAnalyses,
  reprocessAnalysis,
  saveAnalysis,
} from './api';
import type {
  Analysis,
  AnalysisListItem,
  AnalysisStatus,
  AuthUser,
  DpgfLine,
  Lot,
} from './types';

type Route = { name: 'dashboard' } | { name: 'new' } | { name: 'analysis'; id: string };

const UNIT_OPTIONS = ['Ens', 'U', 'ml', 'm²', 'm³', 'kg', 'h', 'mois'];

function readRoute(): Route {
  const match = window.location.pathname.match(/^\/analyses\/([a-f0-9]{32})\/?$/);
  if (match) return { name: 'analysis', id: match[1] };
  if (window.location.pathname === '/nouveau') return { name: 'new' };
  return { name: 'dashboard' };
}

function routePath(route: Route): string {
  if (route.name === 'analysis') return `/analyses/${route.id}`;
  if (route.name === 'new') return '/nouveau';
  return '/';
}

function formatDate(value: string): string {
  if (!value) return '—';
  return new Intl.DateTimeFormat('fr-FR', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}

function formatBytes(value: number): string {
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} Ko`;
  return `${(value / (1024 * 1024)).toFixed(1)} Mo`;
}

const statusLabels: Record<AnalysisStatus, string> = {
  queued: 'En attente',
  processing: 'Extraction',
  needs_review: 'À contrôler',
  ready: 'Prêt',
  failed: 'Échec',
};

function StatusBadge({ status }: { status: AnalysisStatus }) {
  const icon = status === 'ready'
    ? <CheckCircle2 size={14} />
    : status === 'failed'
      ? <AlertCircle size={14} />
      : status === 'queued' || status === 'processing'
        ? <LoaderCircle className="spin" size={14} />
        : <Clock3 size={14} />;
  return <span className={`status status-${status}`}>{icon}{statusLabels[status]}</span>;
}

function EmptyMessage({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <div className="empty-message">
      <span className="empty-icon">{icon}</span>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}

function AuthGate({ children }: { children: (user: AuthUser) => ReactNode }) {
  const [state, setState] = useState<
    | { status: 'loading' }
    | { status: 'ok'; user: AuthUser }
    | { status: 'unauthenticated' }
    | { status: 'error'; message: string }
  >({ status: 'loading' });

  useEffect(() => {
    const controller = new AbortController();
    getCurrentUser(controller.signal)
      .then((user) => setState({ status: 'ok', user }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && error.status === 401) {
          setState({ status: 'unauthenticated' });
        } else {
          setState({ status: 'error', message: error instanceof Error ? error.message : 'Connexion indisponible' });
        }
      });
    return () => controller.abort();
  }, []);

  if (state.status === 'ok') return children(state.user);
  return (
    <main className="auth-page">
      <section className="auth-panel">
        <img src="/api/assets/logo" alt="MODUO" className="auth-logo" />
        {state.status === 'loading' && (
          <>
            <LoaderCircle className="spin auth-loader" />
            <h1>Préparation de votre espace</h1>
            <p>Vérification de la session MODUO…</p>
          </>
        )}
        {state.status === 'unauthenticated' && (
          <>
            <p className="eyebrow">Économie de la construction</p>
            <h1>DPGF Résumé CCTP</h1>
            <p>Connectez-vous avec votre compte MODUO pour retrouver vos dossiers et vos exports.</p>
            <a className="button primary" href="/api/auth/login">
              <CircleUserRound size={18} /> Se connecter
            </a>
          </>
        )}
        {state.status === 'error' && (
          <>
            <AlertCircle className="auth-error" />
            <h1>Connexion indisponible</h1>
            <p>{state.message}</p>
            <button className="button secondary" onClick={() => window.location.reload()}>
              <RefreshCw size={17} /> Réessayer
            </button>
          </>
        )}
      </section>
    </main>
  );
}

function AppShell({ user, route, navigate, children }: {
  user: AuthUser;
  route: Route;
  navigate: (route: Route) => void;
  children: ReactNode;
}) {
  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand" onClick={() => navigate({ name: 'dashboard' })}>
          <img src="/api/assets/logo" alt="" />
          <span className="brand-rule" />
          <span className="brand-copy">
            <strong>DPGF Résumé</strong>
            <small>CCTP → Excel</small>
          </span>
        </button>
        <nav className="main-nav" aria-label="Navigation principale">
          <button
            className={route.name === 'dashboard' ? 'active' : ''}
            onClick={() => navigate({ name: 'dashboard' })}
          >
            <LayoutDashboard size={17} /> Vue d'ensemble
          </button>
          <button
            className={route.name === 'new' ? 'active' : ''}
            onClick={() => navigate({ name: 'new' })}
          >
            <FilePlus2 size={17} /> Nouveau dossier
          </button>
        </nav>
        <div className="user-menu">
          <span className="user-avatar">{(user.name || user.username || 'U').slice(0, 1).toUpperCase()}</span>
          <span className="user-copy">
            <strong>{user.name || user.username || 'Utilisateur'}</strong>
            <small>{user.is_local ? 'Mode local sécurisé' : user.email}</small>
          </span>
          {!user.is_local && (
            <a href="/api/auth/logout" title="Se déconnecter"><LogOut size={18} /></a>
          )}
        </div>
      </header>
      <main className="main-content">{children}</main>
      <footer className="footer">
        <span><ShieldCheck size={15} /> Documents isolés par utilisateur</span>
        <span>Application MODUO · DPGF Résumé CCTP</span>
      </footer>
    </div>
  );
}

function Dashboard({ navigate }: { navigate: (route: Route) => void }) {
  const [items, setItems] = useState<AnalysisListItem[]>([]);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback((query = '') => {
    setLoading(true);
    listAnalyses(query)
      .then(setItems)
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : 'Historique indisponible'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const timer = window.setTimeout(() => load(search), 250);
    return () => window.clearTimeout(timer);
  }, [search, load]);

  const totals = useMemo(() => ({
    projects: items.length,
    ready: items.filter((item) => item.status === 'ready').length,
    lots: items.reduce((sum, item) => sum + item.lot_count, 0),
    lines: items.reduce((sum, item) => sum + item.line_count, 0),
  }), [items]);

  async function remove(event: React.MouseEvent, item: AnalysisListItem) {
    event.stopPropagation();
    if (!window.confirm(`Supprimer définitivement le dossier « ${item.project_name} » ?`)) return;
    try {
      await deleteAnalysis(item.id);
      setItems((current) => current.filter((candidate) => candidate.id !== item.id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Suppression impossible');
    }
  }

  return (
    <div className="page dashboard-page">
      <section className="welcome-strip">
        <div>
          <p className="eyebrow">Économie de la construction</p>
          <h1>Transformez vos CCTP en DPGF fiables.</h1>
          <p>Extraction structurée, contrôle poste par poste et Excel standardisé prêt à consulter.</p>
        </div>
        <button className="button primary large" onClick={() => navigate({ name: 'new' })}>
          <Plus size={19} /> Générer un DPGF
        </button>
      </section>

      <section className="metric-grid" aria-label="Indicateurs">
        <article><span className="metric-icon navy"><FolderClock /></span><div><strong>{totals.projects}</strong><small>Dossiers enregistrés</small></div></article>
        <article><span className="metric-icon green"><FileCheck2 /></span><div><strong>{totals.ready}</strong><small>Prêts à exporter</small></div></article>
        <article><span className="metric-icon amber"><Building2 /></span><div><strong>{totals.lots}</strong><small>Lots analysés</small></div></article>
        <article><span className="metric-icon blue"><FileSpreadsheet /></span><div><strong>{totals.lines}</strong><small>Postes DPGF</small></div></article>
      </section>

      <section className="panel history-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Traçabilité</p>
            <h2>Historique des dossiers</h2>
          </div>
          <label className="search-box">
            <Search size={17} />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Projet, référence ou client…" />
          </label>
        </div>
        {error && <div className="inline-alert error"><AlertCircle size={17} />{error}</div>}
        {loading && <div className="loading-row"><LoaderCircle className="spin" /> Chargement de l'historique…</div>}
        {!loading && items.length === 0 && (
          <EmptyMessage icon={<History />} title={search ? 'Aucun résultat' : 'Votre historique est vide'}>
            {search ? 'Modifiez votre recherche.' : 'Créez votre premier dossier pour commencer.'}
          </EmptyMessage>
        )}
        {!loading && items.length > 0 && (
          <div className="history-table">
            <div className="history-header">
              <span>Projet</span><span>Documents</span><span>Résultat</span><span>Mise à jour</span><span>Statut</span><span />
            </div>
            {items.map((item) => (
              <div
                className="history-row"
                key={item.id}
                role="button"
                tabIndex={0}
                onClick={() => navigate({ name: 'analysis', id: item.id })}
                onKeyDown={(event) => { if (event.key === 'Enter') navigate({ name: 'analysis', id: item.id }); }}
              >
                <span className="project-cell">
                  <span className="project-file"><FileText size={20} /></span>
                  <span>
                    <strong>{item.project_name}</strong>
                    <small>
                      {[item.project_reference, item.client_name].filter(Boolean).join(' · ') || 'Sans référence'}
                      {item.owner_name && !item.can_edit ? ` · de ${item.owner_name}` : ''}
                    </small>
                  </span>
                </span>
                <span><strong>{item.document_count}</strong><small>CCTP</small></span>
                <span><strong>{item.lot_count} lot{item.lot_count > 1 ? 's' : ''}</strong><small>{item.line_count} postes · {item.review_count} à revoir</small></span>
                <span><strong>{formatDate(item.updated_at)}</strong><small>{item.phase}</small></span>
                <span><StatusBadge status={item.status} /></span>
                <span className="row-actions">
                  {item.can_edit ? (
                    <button onClick={(event) => void remove(event, item)} title="Supprimer"><Trash2 size={16} /></button>
                  ) : (
                    <span className="read-only-badge" title={`Lecture seule · ${item.owner_name || 'autre utilisateur'}`}>
                      <Lock size={16} />
                    </span>
                  )}
                  <ChevronRight size={18} />
                </span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function NewAnalysis({ navigate }: { navigate: (route: Route) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [projectName, setProjectName] = useState('');
  const [projectReference, setProjectReference] = useState('');
  const [clientName, setClientName] = useState('');
  const [phase, setPhase] = useState('DCE');
  const [dueDate, setDueDate] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  function addFiles(incoming: File[]) {
    const allowed = incoming.filter((file) => /\.(pdf|docx)$/i.test(file.name));
    const next = [...files];
    allowed.forEach((file) => {
      if (!next.some((item) => item.name === file.name && item.size === file.size)) next.push(file);
    });
    setFiles(next);
    if (allowed.length !== incoming.length) setError('Seuls les fichiers PDF et Word (.docx) sont acceptés.');
    else setError('');
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    addFiles(Array.from(event.dataTransfer.files));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!projectName.trim()) return setError('Renseignez le nom du projet.');
    if (!files.length) return setError('Ajoutez au moins un CCTP PDF ou Word.');
    setSubmitting(true);
    setError('');
    try {
      const result = await createAnalysis({
        projectName, projectReference, clientName, phase, dueDate, files,
      });
      navigate({ name: 'analysis', id: result.id });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Création impossible');
      setSubmitting(false);
    }
  }

  return (
    <div className="page narrow-page">
      <button className="back-link" onClick={() => navigate({ name: 'dashboard' })}><ArrowLeft size={17} /> Retour à l'historique</button>
      <div className="page-title">
        <div><p className="eyebrow">Nouveau traitement</p><h1>Créer un dossier DPGF</h1><p>Un document par lot ou plusieurs CCTP : l'application construit un classeur homogène.</p></div>
        <span className="step-chip">Étape 1 sur 2 · Import</span>
      </div>
      <form className="creation-layout" onSubmit={submit}>
        <section className="panel form-panel">
          <div className="section-title"><span>1</span><div><h2>Informations du projet</h2><p>Ces données seront reprises dans chaque feuille Excel.</p></div></div>
          <div className="form-grid">
            <label className="field field-wide"><span>Nom du projet *</span><input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="Ex. BONDUELLE — Bâtiment Gay Lussac" autoFocus /></label>
            <label className="field"><span>Référence</span><input value={projectReference} onChange={(event) => setProjectReference(event.target.value)} placeholder="25_189" /></label>
            <label className="field"><span>Client / maître d'ouvrage</span><input value={clientName} onChange={(event) => setClientName(event.target.value)} placeholder="Nom du client" /></label>
            <label className="field"><span>Phase</span><select value={phase} onChange={(event) => setPhase(event.target.value)}><option>PRO</option><option>DCE</option><option>ACT</option><option>EXE</option></select></label>
            <label className="field"><span>Échéance</span><input type="date" value={dueDate} onChange={(event) => setDueDate(event.target.value)} /></label>
          </div>
        </section>
        <section className="panel form-panel">
          <div className="section-title"><span>2</span><div><h2>Documents CCTP</h2><p>PDF texte ou Word .docx · jusqu'à 30 documents par dossier.</p></div></div>
          <input ref={inputRef} className="sr-only" type="file" multiple accept=".pdf,.docx" onChange={(event: ChangeEvent<HTMLInputElement>) => addFiles(Array.from(event.target.files || []))} />
          <div
            className={`dropzone ${dragging ? 'dragging' : ''}`}
            onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            onClick={() => inputRef.current?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={(event) => { if (event.key === 'Enter') inputRef.current?.click(); }}
          >
            <span className="upload-icon"><UploadCloud /></span>
            <h3>Déposez les CCTP ici</h3>
            <p>ou cliquez pour sélectionner les fichiers</p>
            <span className="file-types">PDF · DOCX</span>
          </div>
          {files.length > 0 && (
            <div className="selected-files">
              <div className="selected-heading"><strong>{files.length} document{files.length > 1 ? 's' : ''}</strong><button type="button" onClick={() => setFiles([])}>Tout retirer</button></div>
              {files.map((file, index) => (
                <div className="selected-file" key={`${file.name}-${file.size}`}>
                  <span className={`file-extension ${file.name.toLowerCase().endsWith('.pdf') ? 'pdf' : 'word'}`}>{file.name.split('.').pop()?.toUpperCase()}</span>
                  <span><strong>{file.name}</strong><small>{formatBytes(file.size)} · Lot {index + 1} pressenti</small></span>
                  <button type="button" onClick={() => setFiles((current) => current.filter((_, fileIndex) => fileIndex !== index))}><X size={17} /></button>
                </div>
              ))}
            </div>
          )}
        </section>
        {error && <div className="inline-alert error"><AlertCircle size={18} />{error}</div>}
        <div className="form-actions">
          <p><ShieldCheck size={17} /> Les sources et résultats sont conservés dans votre historique privé.</p>
          <button className="button primary large" type="submit" disabled={submitting}>
            {submitting ? <LoaderCircle className="spin" size={19} /> : <Sparkles size={19} />}
            {submitting ? 'Création du dossier…' : 'Extraire et structurer'}
          </button>
        </div>
      </form>
    </div>
  );
}

function ProcessingView({ analysis }: { analysis: Analysis }) {
  return (
    <div className="processing-card panel">
      <div className="processing-visual">
        <span className="document-stack"><FileText /><FileSpreadsheet /></span>
        <span className="processing-ring"><LoaderCircle className="spin" /></span>
      </div>
      <p className="eyebrow">Analyse en cours</p>
      <h2>Construction du DPGF</h2>
      <p>Lecture de la hiérarchie, repérage des prestations et préparation des lignes contrôlables.</p>
      <div className="progress-block">
        <div><strong>{analysis.progress}%</strong><span>{analysis.status === 'queued' ? 'En attente de traitement' : 'Extraction des CCTP'}</span></div>
        <div className="progress-track"><span style={{ width: `${Math.max(3, analysis.progress)}%` }} /></div>
      </div>
      <div className="processing-documents">
        {analysis.documents.map((document) => (
          <span key={document.id}><FileText size={15} />{document.original_name}<Check size={14} /></span>
        ))}
      </div>
      <small>Vous pouvez quitter cette page : le dossier reste dans l'historique.</small>
    </div>
  );
}

// Mirrors the backend's natural code ordering (app/parser.py:_natural_code_key):
// split on '.'/'-', compare numeric segments as numbers and the rest as text,
// numeric segments always sort before non-numeric ones.
type CodeKeyPart = [0, number] | [1, string];

function naturalCodeKey(code: string): CodeKeyPart[] {
  return code
    .split(/[.-]/)
    .filter(Boolean)
    .map((part): CodeKeyPart => (/^\d+$/.test(part) ? [0, Number(part)] : [1, part.toLocaleLowerCase('fr')]));
}

function compareCodeKeys(a: CodeKeyPart[], b: CodeKeyPart[]): number {
  const length = Math.max(a.length, b.length);
  for (let index = 0; index < length; index += 1) {
    const partA = a[index];
    const partB = b[index];
    if (!partA) return -1;
    if (!partB) return 1;
    if (partA[0] !== partB[0]) return partA[0] - partB[0];
    if (partA[1] === partB[1]) continue;
    return partA[1] < partB[1] ? -1 : 1;
  }
  return 0;
}

// A line added with a known hierarchical code (e.g. "3.1.2") belongs right
// after the last existing line whose code sorts before it — not at the
// bottom of the table. Lines without a code yet (a fresh blank section) keep
// the previous append-at-the-end behaviour since there is nothing to
// position against.
function insertLineByCode(lines: DpgfLine[], newLine: DpgfLine): DpgfLine[] {
  const code = newLine.code.trim();
  if (!code) return [...lines, newLine];
  const newKey = naturalCodeKey(code);
  const index = lines.findIndex((line) => {
    if (!line.code.trim()) return false;
    return compareCodeKeys(newKey, naturalCodeKey(line.code)) < 0;
  });
  if (index === -1) return [...lines, newLine];
  const next = [...lines];
  next.splice(index, 0, newLine);
  return next;
}

function lineTemplate(kind: 'section' | 'item', sourceId: string, level = 2): DpgfLine {
  return {
    id: `manual_${crypto.randomUUID().replaceAll('-', '').slice(0, 14)}`,
    kind,
    level,
    code: '',
    designation: kind === 'section' ? 'Nouvelle section' : 'Nouveau poste',
    description: '',
    unit: kind === 'item' ? 'Ens' : null,
    quantity: null,
    included: true,
    confidence: 1,
    review_status: 'validated',
    review_reason: '',
    source_id: sourceId,
    source_page: null,
    source_excerpt: 'Ligne ajoutée manuellement',
    origin: 'manual',
  };
}

interface ManualObjectDraft {
  code: string;
  designation: string;
  unit: string;
  quantity: string;
  level: number;
  included: boolean;
}

const emptyManualObject: ManualObjectDraft = {
  code: '',
  designation: '',
  unit: 'Ens',
  quantity: '',
  level: 3,
  included: true,
};

function AnalysisWorkspace({ id, navigate }: { id: string; navigate: (route: Route) => void }) {
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [activeLotId, setActiveLotId] = useState('');
  const [filter, setFilter] = useState<'all' | 'to_review' | 'validated'>('all');
  const [search, setSearch] = useState('');
  const [selectedLine, setSelectedLine] = useState<DpgfLine | null>(null);
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [reprocessing, setReprocessing] = useState(false);
  const [saved, setSaved] = useState(false);
  const [manualObject, setManualObject] = useState<ManualObjectDraft | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const data = await getAnalysis(id, signal);
      setAnalysis(data);
      setActiveLotId((current) => current || data.lots[0]?.id || '');
      setError('');
      return data;
    } catch (caught) {
      if (!signal?.aborted) setError(caught instanceof Error ? caught.message : 'Dossier introuvable');
      return null;
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    if (!analysis || !['queued', 'processing'].includes(analysis.status)) return;
    const timer = window.setInterval(() => void load(), 1400);
    return () => window.clearInterval(timer);
  }, [analysis, load]);

  const activeLot = analysis?.lots.find((lot) => lot.id === activeLotId) || analysis?.lots[0];
  const visibleLines = useMemo(() => {
    if (!activeLot) return [];
    const term = search.trim().toLocaleLowerCase('fr');
    return activeLot.lines.filter((line) => {
      if (filter !== 'all' && line.review_status !== filter) return false;
      if (!term) return true;
      return `${line.code} ${line.designation} ${line.description}`.toLocaleLowerCase('fr').includes(term);
    });
  }, [activeLot, filter, search]);

  function updateActiveLot(updater: (lot: Lot) => Lot) {
    setAnalysis((current) => {
      if (!current) return current;
      return { ...current, lots: current.lots.map((lot) => lot.id === activeLot?.id ? updater(lot) : lot) };
    });
    setSaved(false);
  }

  function updateLine(id: string, patch: Partial<DpgfLine>) {
    updateActiveLot((lot) => ({ ...lot, lines: lot.lines.map((line) => line.id === id ? { ...line, ...patch } : line) }));
    setSelectedLine((line) => line?.id === id ? { ...line, ...patch } : line);
  }

  function addManualObject(event: FormEvent) {
    event.preventDefault();
    if (!manualObject || !manualObject.designation.trim() || !activeLot) return;
    const line = lineTemplate('item', activeLot.source_id, manualObject.level);
    line.code = manualObject.code.trim();
    line.designation = manualObject.designation.trim();
    line.unit = manualObject.unit;
    line.quantity = manualObject.quantity === '' ? null : Number(manualObject.quantity);
    line.included = manualObject.included;
    line.source_excerpt = 'Objet ajouté manuellement avant la génération du DPGF.';
    line.review_reason = '';
    updateActiveLot((lot) => ({ ...lot, lines: insertLineByCode(lot.lines, line) }));
    setManualObject(null);
    setFilter('all');
    setSearch('');
  }

  async function persist(): Promise<Analysis | null> {
    if (!analysis) return null;
    setSaving(true);
    setError('');
    try {
      const result = await saveAnalysis(analysis);
      setAnalysis(result);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2200);
      return result;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Enregistrement impossible');
      return null;
    } finally {
      setSaving(false);
    }
  }

  async function exportExcel() {
    const current = await persist();
    if (!current) return;
    setExporting(true);
    try {
      const result = await generateExport(current.id);
      window.location.assign(result.download_url);
      setAnalysis((value) => value ? { ...value, export_name: 'generated' } : value);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Export impossible');
    } finally {
      setExporting(false);
    }
  }

  async function reprocess() {
    if (!analysis) return;
    const accepted = window.confirm(
      "Ré-analyser les CCTP avec les règles actuelles ? Une révision de sauvegarde sera créée et les objets manuels seront conservés.",
    );
    if (!accepted) return;
    setReprocessing(true);
    setError('');
    try {
      await reprocessAnalysis(analysis.id);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Ré-analyse impossible');
    } finally {
      setReprocessing(false);
    }
  }

  if (loading) return <div className="page"><div className="loading-page"><LoaderCircle className="spin" /> Chargement du dossier…</div></div>;
  if (!analysis) return <div className="page"><button className="back-link" onClick={() => navigate({ name: 'dashboard' })}><ArrowLeft size={17} /> Historique</button><EmptyMessage icon={<AlertCircle />} title="Dossier indisponible">{error}</EmptyMessage></div>;
  if (analysis.status === 'queued' || analysis.status === 'processing') {
    return <div className="page narrow-page"><button className="back-link" onClick={() => navigate({ name: 'dashboard' })}><ArrowLeft size={17} /> Retour à l'historique</button><ProcessingView analysis={analysis} /></div>;
  }
  if (analysis.status === 'failed') {
    return <div className="page narrow-page"><button className="back-link" onClick={() => navigate({ name: 'dashboard' })}><ArrowLeft size={17} /> Retour à l'historique</button><EmptyMessage icon={<AlertCircle />} title="Le traitement a échoué">{analysis.error || 'Aucun document n’a pu être exploité.'}</EmptyMessage></div>;
  }

  const canEdit = analysis.can_edit;

  return (
    <div className="page analysis-page">
      <div className="analysis-topline">
        <button className="back-link" onClick={() => navigate({ name: 'dashboard' })}><ArrowLeft size={17} /> Historique</button>
        <div className="analysis-actions">
          {canEdit ? (
            <>
              <button className="button ghost" onClick={() => void reprocess()} disabled={saving || exporting || reprocessing}>
                {reprocessing ? <LoaderCircle className="spin" size={17} /> : <RefreshCw size={17} />}
                Ré-analyser
              </button>
              <a className="button ghost" href="/api/template"><Download size={17} /> Modèle</a>
              <button className="button secondary" onClick={() => void persist()} disabled={saving}>
                {saving ? <LoaderCircle className="spin" size={17} /> : saved ? <Check size={17} /> : <Save size={17} />}
                {saved ? 'Enregistré' : 'Enregistrer'}
              </button>
              <button className="button primary" onClick={() => void exportExcel()} disabled={saving || exporting}>
                {exporting ? <LoaderCircle className="spin" size={18} /> : <FileSpreadsheet size={18} />}
                Générer l'Excel
              </button>
            </>
          ) : (
            <span className="read-only-banner">
              <Lock size={16} />
              Lecture seule{analysis.owner?.name ? ` · dossier de ${analysis.owner.name}` : ''}
            </span>
          )}
        </div>
      </div>
      <section className="analysis-heading">
        <div>
          <div className="heading-meta"><StatusBadge status={analysis.status} /><span>Mis à jour {formatDate(analysis.updated_at)}</span></div>
          <h1>{analysis.project.name}</h1>
          <p>{[analysis.project.reference, analysis.project.client, analysis.project.phase].filter(Boolean).join(' · ')}</p>
        </div>
        <div className="quality-score">
          <span className="score-ring" style={{ '--score': `${Math.round(analysis.stats.average_confidence * 100)}%` } as React.CSSProperties}>
            <strong>{Math.round(analysis.stats.average_confidence * 100)}%</strong>
          </span>
          <span><strong>Indice d'extraction</strong><small>{analysis.stats.to_review} poste{analysis.stats.to_review > 1 ? 's' : ''} à contrôler</small></span>
        </div>
      </section>

      <section className="compact-metrics">
        <article><Building2 /><span><strong>{analysis.stats.lots}</strong><small>Lots</small></span></article>
        <article><FileSpreadsheet /><span><strong>{analysis.stats.items}</strong><small>Postes</small></span></article>
        <article><CheckCircle2 /><span><strong>{analysis.stats.validated}</strong><small>Validés</small></span></article>
        <article className={analysis.stats.to_review ? 'attention' : ''}><AlertCircle /><span><strong>{analysis.stats.to_review}</strong><small>À contrôler</small></span></article>
        <article><Sparkles /><span><strong>{analysis.processing.llm_used ? 'LIHA + règles' : 'Règles'}</strong><small>Méthode</small></span></article>
      </section>

      {analysis.warnings.length > 0 && (
        <details className="warnings-panel">
          <summary><AlertCircle size={17} /> {analysis.warnings.length} point{analysis.warnings.length > 1 ? 's' : ''} d'attention <ChevronRight size={16} /></summary>
          <ul>{analysis.warnings.map((warning, index) => <li key={`${warning.source_id}-${index}`}><strong>{warning.source_name}</strong>{warning.message}</li>)}</ul>
        </details>
      )}

      <section className="workspace-panel panel">
        <aside className="lot-sidebar">
          <div className="sidebar-heading"><span>Lots détectés</span><strong>{analysis.lots.length}</strong></div>
          <div className="lot-list">
            {analysis.lots.map((lot) => {
              const reviewCount = lot.lines.filter((line) => line.kind === 'item' && line.review_status !== 'validated').length;
              return (
                <button key={lot.id} className={lot.id === activeLot?.id ? 'active' : ''} onClick={() => { setActiveLotId(lot.id); setSelectedLine(null); }}>
                  <span className="lot-number">{lot.code || '—'}</span>
                  <span><strong>{lot.title}</strong><small>{lot.lines.filter((line) => line.kind === 'item').length} postes</small></span>
                  {reviewCount > 0 ? <em>{reviewCount}</em> : <CheckCircle2 size={16} />}
                </button>
              );
            })}
          </div>
          <div className="source-list">
            <span>Documents sources</span>
            {analysis.documents.map((document) => (
              <a key={document.id} href={`/api/v1/analyses/${analysis.id}/sources/${document.id}`} target="_blank" rel="noreferrer">
                <FileText size={15} /><span>{document.original_name}<small>{formatBytes(document.size)}{document.page_count ? ` · ${document.page_count} p.` : ''}</small></span><ExternalLink size={13} />
              </a>
            ))}
          </div>
        </aside>
        <div className="table-workspace">
          <div className="lot-heading">
            <div><p className="eyebrow">Lot {activeLot?.code || 'sans code'}</p><h2>{activeLot?.title || 'Lot'}</h2></div>
            <div className="lot-fields">
              <label>Code<input value={activeLot?.code || ''} disabled={!canEdit} onChange={(event) => updateActiveLot((lot) => ({ ...lot, code: event.target.value }))} /></label>
              <label>Intitulé<input value={activeLot?.title || ''} disabled={!canEdit} onChange={(event) => updateActiveLot((lot) => ({ ...lot, title: event.target.value }))} /></label>
            </div>
          </div>
          <div className="table-toolbar">
            <label className="search-box compact"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Rechercher un poste…" /></label>
            <div className="filter-tabs">
              <button className={filter === 'all' ? 'active' : ''} onClick={() => setFilter('all')}>Tous</button>
              <button className={filter === 'to_review' ? 'active' : ''} onClick={() => setFilter('to_review')}>À contrôler</button>
              <button className={filter === 'validated' ? 'active' : ''} onClick={() => setFilter('validated')}>Validés</button>
            </div>
            {canEdit && (
              <div className="add-menu">
                <button onClick={() => updateActiveLot((lot) => ({ ...lot, lines: insertLineByCode(lot.lines, lineTemplate('section', lot.source_id, 2)) }))}><Plus size={15} /> Section</button>
                <button className="add-object-button" onClick={() => setManualObject({ ...emptyManualObject })}><Plus size={15} /> Ajouter un objet</button>
              </div>
            )}
          </div>
          <div className="dpgf-table">
            <div className="dpgf-header"><span>Code</span><span>Désignation</span><span>Unité</span><span>Quantité</span><span>Contrôle</span><span>Source</span><span /></div>
            {visibleLines.length === 0 && <EmptyMessage icon={<Search />} title="Aucune ligne visible">Modifiez la recherche ou le filtre actif.</EmptyMessage>}
            {visibleLines.map((line) => line.kind === 'section' ? (
              <div className="section-row" key={line.id} style={{ '--indent': Math.max(0, line.level - 1) } as React.CSSProperties}>
                <input aria-label="Code de section" value={line.code} disabled={!canEdit} onChange={(event) => updateLine(line.id, { code: event.target.value })} />
                <input aria-label="Désignation de section" value={line.designation} disabled={!canEdit} onChange={(event) => updateLine(line.id, { designation: event.target.value })} />
                {canEdit && (
                  <button onClick={() => updateActiveLot((lot) => ({ ...lot, lines: lot.lines.filter((item) => item.id !== line.id) }))} title="Supprimer"><Trash2 size={15} /></button>
                )}
              </div>
            ) : (
              <div className={`dpgf-row ${line.review_status === 'to_review' ? 'needs-review' : ''}`} key={line.id}>
                <input className="code-input" value={line.code} disabled={!canEdit} onChange={(event) => updateLine(line.id, { code: event.target.value })} placeholder="—" />
                <div className="designation-input" style={{ paddingLeft: `${Math.max(0, line.level - 2) * 14}px` }}>
                  <input aria-label={`Désignation ${line.code || 'du poste'}`} value={line.designation} disabled={!canEdit} onChange={(event) => updateLine(line.id, { designation: event.target.value })} />
                  {!line.included && <small>Option / variante — hors offre de base</small>}
                  {line.review_reason && <small>{line.review_reason}</small>}
                </div>
                <select aria-label={`Unité ${line.code || line.designation}`} value={line.unit || 'Ens'} disabled={!canEdit} onChange={(event) => updateLine(line.id, { unit: event.target.value })}>
                  {UNIT_OPTIONS.map((unit) => <option key={unit}>{unit}</option>)}
                </select>
                <input className="number-input" type="number" step="any" value={line.quantity ?? ''} disabled={!canEdit} onChange={(event) => updateLine(line.id, { quantity: event.target.value === '' ? null : Number(event.target.value) })} placeholder="—" />
                <button
                  className={`review-toggle ${line.review_status}`}
                  disabled={!canEdit}
                  onClick={() => updateLine(line.id, { review_status: line.review_status === 'validated' ? 'to_review' : 'validated', review_reason: line.review_status === 'validated' ? 'Marqué à contrôler' : '' })}
                  title={line.review_status === 'validated' ? 'Marquer à contrôler' : 'Valider'}
                >
                  {line.review_status === 'validated' ? <Check size={15} /> : <AlertCircle size={15} />}
                  {line.review_status === 'validated' ? 'Validé' : 'À revoir'}
                </button>
                <button className="source-button" onClick={() => setSelectedLine(line)}>
                  <FileText size={15} /><span>{line.origin === 'manual' ? 'Manuel' : line.source_page ? `p. ${line.source_page}` : 'Word'}</span>
                </button>
                {canEdit && (
                  <button className="delete-line" onClick={() => updateActiveLot((lot) => ({ ...lot, lines: lot.lines.filter((item) => item.id !== line.id) }))} title="Supprimer"><Trash2 size={15} /></button>
                )}
              </div>
            ))}
          </div>
          <div className="table-footer">
            <span>{visibleLines.filter((line) => line.kind === 'item').length} postes affichés</span>
            <label className="include-note"><input type="checkbox" checked readOnly /> Les lignes sont incluses dans l'offre de base par défaut</label>
          </div>
        </div>
      </section>
      {error && <div className="toast error"><AlertCircle size={17} />{error}<button onClick={() => setError('')}><X size={15} /></button></div>}
      {selectedLine && (
        <div className="source-drawer-backdrop" onClick={() => setSelectedLine(null)}>
          <aside className="source-drawer" onClick={(event) => event.stopPropagation()}>
            <div className="drawer-heading"><div><p className="eyebrow">Traçabilité source</p><h3>{selectedLine.code || 'Poste'} · {selectedLine.designation}</h3></div><button onClick={() => setSelectedLine(null)}><X /></button></div>
            <div className="source-reference"><FileText /><span><strong>{analysis.documents.find((document) => document.id === selectedLine.source_id)?.original_name || 'Document source'}</strong><small>{selectedLine.source_page ? `Page ${selectedLine.source_page}` : 'Document Word'}</small></span></div>
            <div className="excerpt"><span>Extrait associé</span><blockquote>{selectedLine.source_excerpt || 'Aucun extrait enregistré.'}</blockquote></div>
            <div className="confidence-detail"><span>Confiance de l'extraction</span><strong>{Math.round(selectedLine.confidence * 100)}%</strong><div><i style={{ width: `${selectedLine.confidence * 100}%` }} /></div></div>
            <label className="include-control">
              <input
                type="checkbox"
                checked={selectedLine.included}
                disabled={!canEdit}
                onChange={(event) => updateLine(selectedLine.id, { included: event.target.checked })}
              />
              <span><strong>Inclure dans l'offre de base</strong><small>Décochez pour conserver l'objet comme option ou variante.</small></span>
            </label>
            {selectedLine.description && <div className="excerpt"><span>Contexte</span><p>{selectedLine.description}</p></div>}
            {canEdit && (
              <button className="button primary full" onClick={() => { updateLine(selectedLine.id, { review_status: 'validated', review_reason: '' }); setSelectedLine(null); }}><CheckCircle2 size={17} /> Valider ce poste</button>
            )}
          </aside>
        </div>
      )}
      {manualObject && (
        <div className="modal-backdrop" onClick={() => setManualObject(null)}>
          <form className="manual-object-modal" onSubmit={addManualObject} onClick={(event) => event.stopPropagation()}>
            <div className="modal-heading">
              <span className="modal-icon"><FilePlus2 /></span>
              <div><p className="eyebrow">Ajout avant export</p><h3>Ajouter un objet au DPGF</h3><p>Le poste sera enregistré dans le dossier et intégré à l'Excel.</p></div>
              <button type="button" onClick={() => setManualObject(null)}><X /></button>
            </div>
            <div className="manual-object-grid">
              <label className="field"><span>Code</span><input value={manualObject.code} onChange={(event) => setManualObject({ ...manualObject, code: event.target.value })} placeholder="Ex. 3.4.6" autoFocus /></label>
              <label className="field"><span>Niveau hiérarchique</span><select value={manualObject.level} onChange={(event) => setManualObject({ ...manualObject, level: Number(event.target.value) })}><option value={2}>Sous-section</option><option value={3}>Poste</option><option value={4}>Sous-poste</option></select></label>
              <label className="field field-wide"><span>Désignation *</span><input value={manualObject.designation} onChange={(event) => setManualObject({ ...manualObject, designation: event.target.value })} placeholder="Décrivez la prestation à chiffrer" required /></label>
              <label className="field"><span>Unité</span><select value={manualObject.unit} onChange={(event) => setManualObject({ ...manualObject, unit: event.target.value })}>{UNIT_OPTIONS.map((unit) => <option key={unit}>{unit}</option>)}</select></label>
              <label className="field"><span>Quantité</span><input type="number" min="0" step="any" value={manualObject.quantity} onChange={(event) => setManualObject({ ...manualObject, quantity: event.target.value })} placeholder="À compléter si connue" /></label>
            </div>
            <label className="include-control modal-include">
              <input type="checkbox" checked={manualObject.included} onChange={(event) => setManualObject({ ...manualObject, included: event.target.checked })} />
              <span><strong>Inclure dans l'offre de base</strong><small>Décochez pour créer une option ou une variante hors total de base.</small></span>
            </label>
            <div className="modal-actions">
              <button className="button ghost" type="button" onClick={() => setManualObject(null)}>Annuler</button>
              <button className="button primary" type="submit" disabled={!manualObject.designation.trim()}><Plus size={17} /> Ajouter l'objet</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

function Workspace({ user }: { user: AuthUser }) {
  const [route, setRoute] = useState<Route>(readRoute);
  const navigate = useCallback((next: Route) => {
    window.history.pushState({}, '', routePath(next));
    setRoute(next);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }, []);
  useEffect(() => {
    const onPopState = () => setRoute(readRoute());
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);
  return (
    <AppShell user={user} route={route} navigate={navigate}>
      {route.name === 'dashboard' && <Dashboard navigate={navigate} />}
      {route.name === 'new' && <NewAnalysis navigate={navigate} />}
      {route.name === 'analysis' && <AnalysisWorkspace id={route.id} navigate={navigate} />}
    </AppShell>
  );
}

export function App() {
  return <AuthGate>{(user) => <Workspace user={user} />}</AuthGate>;
}
